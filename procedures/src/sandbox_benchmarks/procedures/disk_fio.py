# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: LicenseRef-PolyForm-Shield-1.0.0
"""Run the configured fio rows, retaining tool-owned values and validity checks.

Setup and result collection are untimed. Command runtimes use the shared script helper;
row ordering is seeded by the trial id. The supplied job file is verified before upload.
"""

import json
import random
import statistics
from collections.abc import Mapping
from typing import Any

from sandbox_benchmarks.interface import (
    CheckFailed,
    CommandOutput,
    Missed,
    StepFailed,
    TrialContext,
    Workload,
    output_evidence,
)
from sandbox_benchmarks.kit import command_script, disk_tools
from sandbox_benchmarks.kit.probe import readings

DIRECTORY = "/workspace/fio"
MIB = 1024**2
SETUP_TIMEOUT_S = 30
PREP_TIMEOUT_S = 180
CACHE_TIMEOUT_S = 120
CEILING_BYTES_S = 15.75e9
"""One PCIe 5.0 x4 NVMe device's link limit (1.7 item 2)."""
JSON = "#json\n"

DIRECT_PROBE = f"""\
mkdir -p {DIRECTORY} && cd {DIRECTORY} || exit 1
we=$(dd if=/dev/zero of=probe bs=4k count=256 oflag=direct 2>&1); w=$?
r=not-run
if [ "$w" -eq 0 ]; then re=$(dd if=probe of=/dev/null bs=4k iflag=direct 2>&1); r=$?; fi
rm -f probe
echo "direct_write $w"
echo "direct_read $r"
[ "$w" -eq 0 ] || echo "direct_write_error $(printf '%s' "$we" | tail -n 1)"
[ "$r" = 0 ] || [ "$r" = not-run ] || echo "direct_read_error $(printf '%s' "$re" | tail -n 1)"
"""


def prep_command(data: Mapping[str, Any]) -> str:
    """The data file's first write, with the memory figures read around it."""
    return (
        f"cd {DIRECTORY} || exit 1\n"
        + disk_tools.MEMORY.format(p="before_")
        + "./fio --output-format=json --eta=never --name=prep --filename=data.bin"
        f" --size={disk_tools.fio_size(data['file_bytes'])} --rw=write --bs=1m --ioengine=libaio"
        f" --iodepth={data['queue_depth']} --direct=1 --end_fsync=1"
        " --refill_buffers=1 > prep.json || exit 1\n"
        + disk_tools.MEMORY.format(p="after_")
        + "echo \"disk_free_after_prep $(df -B1 --output=avail . | tail -n 1 | tr -d ' ')\"\n"
        + f"printf '{JSON}' && cat prep.json\n"
    )


def cache_check(data: Mapping[str, Any]) -> str:
    """The cached-versus-direct read probe and the check of the data file's contents."""
    size = disk_tools.fio_size(data["cache_probe_bytes"])
    return f"""\
cd {DIRECTORY} || exit 1
f="./fio --output-format=json --eta=never --filename=cache.bin --size={size}"
$f --name=a --rw=write --bs=1m --ioengine=psync --direct=0 --end_fsync=1 > c_a.json || exit 1
$f --name=b --rw=read --bs=1m --ioengine=psync --direct=0 --invalidate=0 > c_b.json || exit 1
$f --name=c --rw=read --bs=1m --ioengine=libaio --iodepth=64 --direct=1 > c_c.json || exit 1
$f --name=d --rw=randread --bs=4k --ioengine=psync --iodepth=1 --direct=1 --runtime=5 \\
  --time_based=1 > c_d.json || exit 1
python3 - <<'EOF'
import hashlib, json, zlib
def job(name):
    document = json.load(open(f"c_{{name}}.json"))
    return document["jobs"][0], document.get("disk_util", [])
b, b_util = job("b")
c, _ = job("c")
d, _ = job("d")
with open("data.bin", "rb") as file:
    data = file.read(64 << 20)
blocks = {{hashlib.sha256(data[i:i + 4096]).digest() for i in range(0, len(data), 4096)}}
print(json.dumps({{
    "b_bw_bytes": b["read"]["bw_bytes"],
    "b_ios": b["read"]["total_ios"],
    "b_device_read_ios": sum(device["read_ios"] for device in b_util) if b_util else None,
    "c_bw_bytes": c["read"]["bw_bytes"],
    "d_clat_ns": d["read"]["clat_ns"]["percentile"],
    "blocks": len(data) // 4096,
    "distinct_blocks": len(blocks),
    "zlib_ratio": len(zlib.compress(data, 1)) / len(data),
}}))
EOF
"""


def procedure(trial: TrialContext, workload: Workload) -> None:
    data = workload.data
    found = disk_tools.facts(trial, DIRECTORY)
    disk_tools.install(trial, DIRECTORY, data["tool"])
    probe = readings(trial.setup("direct-probe", DIRECT_PROBE, timeout_s=SETUP_TIMEOUT_S).stdout)
    for name, value in probe.items():
        trial.observe(name, value, "measured")
    if probe.get("direct_write") != "0" or probe.get("direct_read") != "0":
        raise Missed("/workspace refuses direct I/O (O_DIRECT)")
    disk_tools.fits(found, data["free_bytes"])
    assert trial.description.directory is not None
    job_file = (trial.description.directory / data["job_file"]).read_bytes()
    trial.write("job-file", f"{DIRECTORY}/disk.fio", job_file, timeout_s=SETUP_TIMEOUT_S)
    prepped = run_to_file(trial, "prep", prep_command(data), PREP_TIMEOUT_S)
    figures, _, prep_json = prepped.partition(JSON)
    prep_bw = fio_job(prep_json, "prep")["write"]["bw_bytes"]
    trial.observe("prep_write_bw_bytes", prep_bw, "measured")
    disk_tools.files_in_memory(trial, found, readings(figures), data["file_bytes"])

    rows = list(data["rows"].items())
    random.Random(trial.trial_id).shuffle(rows)
    for position, (step_id, row) in enumerate(rows, 1):
        environment = " ".join(
            f"{name}={value}" for name, value in row_environment(data, row).items()
        )
        command = (
            f"cd {DIRECTORY} && {environment} {DIRECTORY}/fio"
            f" --output-format=json --output={step_id}.json --eta=never disk.fio"
        )
        run_row(trial, step_id, command, position)
        read = trial.read(
            f"json-{step_id}", f"{DIRECTORY}/{step_id}.json", timeout_s=SETUP_TIMEOUT_S
        )
        text = read.decode(errors="replace")
        job = fio_job(text, step_id)
        direction = "read" if "read" in row["rw"] else "write"
        if job["error"] != 0 or job["job_runtime"] < data["min_interval_seconds"] * 1000:
            evidence = {"type": "FioRow", "error": job["error"], "job_runtime": job["job_runtime"]}
            raise CheckFailed(
                "provider", step_id, evidence | output_evidence(CommandOutput(0, text, "", None))
            )
        log = trial.setup(
            f"log-{step_id}",
            f"cd {DIRECTORY} && cat log_bw.log && rm -f log_*",
            timeout_s=SETUP_TIMEOUT_S,
        ).stdout
        trial.add_result(step_id, row_result(job, direction, row, log, prep_bw))

    summary = json.loads(run_to_file(trial, "cache-check", cache_check(data), CACHE_TIMEOUT_S))
    p50 = summary.pop("d_clat_ns").get("50.000000")
    summary["d_clat_p50_ns"] = p50
    for name, value in summary.items():
        if value is None:
            trial.observe(f"c1_{name}", None, "unknown", "not reported by fio")
        else:
            trial.observe(f"c1_{name}", value, "measured")
    if (
        summary["distinct_blocks"] != summary["blocks"]
        or summary["zlib_ratio"] < data["min_zlib_ratio"]
    ):
        raise CheckFailed("script", "cache-check", {"type": "ShrinkableContents", **summary})
    disk_tools.check(trial, "direct_cached", direct_cached(summary))
    latency: disk_tools.Outcome = (
        "inconclusive" if p50 is None else "fired" if p50 < 10_000 else "not_fired"
    )
    disk_tools.check(trial, "direct_latency", latency)


def row_environment(data: Mapping[str, Any], row: Mapping[str, Any]) -> dict[str, Any]:
    """The values the job file reads from the environment for one row."""
    return {
        "RW": row["rw"],
        "BS": row["bs"],
        "SIZE": disk_tools.fio_size(data["file_bytes"]),
        "IODEPTH": data["queue_depth"],
        "RAMP": data["ramp_seconds"],
        "RUNTIME": data["runtime_seconds"],
    }


def run_to_file(trial: TrialContext, purpose: str, command: str, limit_s: int) -> str:
    """Run an untimed command that can run past 50 s (the data file's first write, the cache
    checks) as a background script, polled, and return what it printed: it writes its
    output to a file, read back, since a script returns only the last 4096 bytes. A command that
    did not exit 0 is the provider's failure."""
    path = f"{DIRECTORY}/{purpose}.out"
    found, ran = command_script.run_one(
        trial,
        f"{{\n{command}\n}} > {path}",
        cwd=DIRECTORY,
        limit_s=limit_s,
        purpose=purpose,
        keep=False,
    )
    if found is None:
        evidence = {"type": "ScriptEnded", "logged": 0, "commands": 1}
        raise StepFailed("provider", purpose, evidence)
    if found.exit_code != 0:
        kind = "Timeout" if command_script.reached_limit(found, limit_s) else "ExitCode"
        evidence = {"type": kind, "exit_code": found.exit_code, "output": ran.tails.get(1, "")}
        raise StepFailed("provider", purpose, evidence)
    return trial.read(f"{purpose}-output", path, timeout_s=SETUP_TIMEOUT_S).decode(errors="replace")


def run_row(trial: TrialContext, step_id: str, command: str, position: int) -> None:
    """Run a row's fio as a one-command script, timed by the
    sandbox's clock; fio's JSON is written to a file and read back untimed."""
    command_script.run_step(
        trial,
        step_id,
        command,
        cwd=DIRECTORY,
        purpose=f"fio-{step_id}",
        result={"position": position},
    )


def fio_job(stdout: str, step: str) -> dict[str, Any]:
    """fio's JSON's one job; JSON that can't be read after exit 0 is the benchmark script's
    failure."""
    try:
        job = json.loads(stdout[stdout.index("{") :])["jobs"][0]
    except (ValueError, KeyError, IndexError) as error:
        raise CheckFailed(
            "script", step, {"type": "BadFioJson", "error": str(error)[:300]}
        ) from None
    return dict(job)


def row_result(
    job: Mapping[str, Any], direction: str, row: Mapping[str, Any], log: str, prep_bw: float
) -> dict[str, Any]:
    """A row's published value and both units, fio's extras, its per-second throughput and
    its checks (1.6, 1.7)."""
    measured = job[direction]
    mib_s = measured["bw_bytes"] / MIB
    iops = measured["iops"]
    series = per_second(log)
    checks: dict[str, str] = {
        "throttled": throttled(series),
        "queue_not_full": "fired" if job["iodepth_level"].get(">=64", 0) < 90 else "not_fired",
        "cpu_bound": "fired" if job["usr_cpu"] + job["sys_cpu"] > 90 else "not_fired",
    }
    if row["bs"] == "1m":
        checks["ceiling"] = "fired" if measured["bw_bytes"] > CEILING_BYTES_S else "not_fired"
    found: dict[str, Any] = {}
    if row["rw"] == "write":
        found["first_write_ratio"] = ratio = prep_bw / measured["bw_bytes"]
        checks["first_write"] = "fired" if not 0.5 <= ratio <= 2 else "not_fired"
    return found | {
        "value": mib_s if row["value"] == "mib_s" else iops,
        "mib_s": mib_s,
        "iops": iops,
        "job_runtime_ms": job["job_runtime"],
        "elapsed_s": job["elapsed"],
        "usr_cpu": job["usr_cpu"],
        "sys_cpu": job["sys_cpu"],
        "iodepth_level": job["iodepth_level"],
        "iodepth_submit": job["iodepth_submit"],
        "iodepth_complete": job["iodepth_complete"],
        "clat_ns": measured["clat_ns"].get("percentile"),
        "bw_kib_s_per_second": series,
        "checks": checks,
    }


def per_second(log: str) -> list[int]:
    """fio's averaged bandwidth log: one KiB/s value per second (`msec, value, ...` lines)."""
    return [int(line.split(",")[1]) for line in log.splitlines() if line.count(",") >= 2]


def throttled(series: list[int]) -> disk_tools.Outcome:
    """Fired when the last 10 s average more than 30% below the first 10 s (1.7 item 4)."""
    if len(series) < 20:
        return "inconclusive"
    first, last = statistics.fmean(series[:10]), statistics.fmean(series[-10:])
    return "fired" if last < 0.7 * first else "not_fired"


def direct_cached(summary: Mapping[str, Any]) -> disk_tools.Outcome:
    """C1 b against c (1.7 item 2): fired when a cache hit is proven (device reads for at most
    5% of b's reads) and the direct read ran at least half as fast; inconclusive without a
    visible block device."""
    device = summary["b_device_read_ios"]
    if device is None or device > 0.05 * summary["b_ios"]:
        return "inconclusive"
    return "fired" if summary["c_bw_bytes"] >= 0.5 * summary["b_bw_bytes"] else "not_fired"
