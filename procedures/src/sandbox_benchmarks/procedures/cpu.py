# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: LicenseRef-PolyForm-Shield-1.0.0
"""CPU measurements: 7-Zip's own benchmark at
one thread and at as many threads as vCPUs were requested, and STREAM's memory bandwidth with as
many OpenMP threads as vCPUs were requested; each published value is the tool's own figure.

The thread count is always the requested size's vCPU (the description's machine, or `--machine`),
never `nproc`, which can show the host's CPUs. Untimed first: the CPU facts (`nproc`,
the model, the sha_ni, avx2 and avx512f flags, the L3 size, the cgroup's cpu.max), then each
pinned static tool written in and checked by its SHA-256 (`disk_tools.install`). Then the three
timed rows, in an order shuffled per trial from its id, each a one-command script started in the
background and polled with `command_script`.

A row that does not exit 0 is the provider's failure, as is STREAM output that does not validate;
output our parser can't read after exit 0 is the benchmark script's. Two checks never exclude a
result (`observations.check_<name>`): `threads_differ`, fired where `nproc` is not the requested
vCPU, and `stream_array_small`, fired where one STREAM array is under four times the L3 the
sandbox reports (STREAM's run rule).
"""

import random
import re
from collections.abc import Mapping
from typing import Any

from sandbox_benchmarks.interface import CheckFailed, TrialContext, Workload
from sandbox_benchmarks.kit import command_script, disk_tools
from sandbox_benchmarks.kit.probe import readings

DIRECTORY = "/workspace/cpu"
FACTS_TIMEOUT_S = 30
FACTS = (
    f"mkdir -p {DIRECTORY} || exit 1\n"
    + """\
echo "nproc $(nproc)"
awk -F': ' '/^model name/ {print "cpu_model " $2; exit}' /proc/cpuinfo
awk -F': ' '/^cpu MHz/ {print "cpu_mhz " $2; exit}' /proc/cpuinfo
flags=" $(awk -F': ' '/^flags/ {print $2; exit}' /proc/cpuinfo) "
for f in sha_ni avx2 avx512f; do
  case $flags in *" $f "*) echo "cpu_$f true" ;; *) echo "cpu_$f false" ;; esac
done
l3=$(cat /sys/devices/system/cpu/cpu0/cache/index3/size 2>/dev/null) && echo "l3_cache $l3"
[ -r /sys/fs/cgroup/cpu.max ] && echo "cgroup_cpu.max $(cat /sys/fs/cgroup/cpu.max)"
awk '/^MemTotal:/ {print "mem_total_kib", $2}' /proc/meminfo
"""
)
"""The CPU facts as `key value` lines, a fact the sandbox does not show left out; it also makes
the tools' directory, since file writes require an existing parent directory."""
SIZE = re.compile(r"^(\d+)([KMG]?)$")
UNITS = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3}

AVERAGE = re.compile(r"^Avr:" + r"\s+(\d+)" * 4 + r"\s+\|" + r"\s+(\d+)" * 4, re.MULTILINE)
TOTAL = re.compile(r"^Tot:\s+(\d+)\s+(\d+)\s+(\d+)", re.MULTILINE)
FREQUENCY = re.compile(r"^1T CPU Freq \(MHz\):(.*)$", re.MULTILINE)
THREADS = re.compile(r"# Benchmark threads:\s+(\d+)")
KERNEL = re.compile(r"^(Copy|Scale|Add|Triad):\s+([\d.]+)", re.MULTILINE)
COUNTED = re.compile(r"^Number of Threads counted = (\d+)", re.MULTILINE)
ARRAY = re.compile(r"^Array size = (\d+) \(elements\)", re.MULTILINE)
PASSES = re.compile(r"^Each kernel will be executed (\d+) times\.", re.MULTILINE)
DOUBLE_BYTES = 8
VALIDATES = "Solution Validates"


def procedure(trial: TrialContext, workload: Workload) -> None:
    data = workload.data
    threads = trial.description.vcpu
    trial.observe("threads_requested", threads, "recorded")
    facts(trial, threads, data["stream_array_bytes"])
    for tool in data["tools"]:
        disk_tools.install(trial, DIRECTORY, tool)
    rows = list(data["rows"].items())
    random.Random(trial.trial_id).shuffle(rows)
    for position, (step_id, row) in enumerate(rows, 1):
        used = threads if row["threads"] == "requested" else row["threads"]
        command = f"cd {DIRECTORY} && " + row["command"].format(threads=used)
        ran = command_script.run_step(
            trial,
            step_id,
            command,
            cwd=DIRECTORY,
            purpose=step_id,
            result={"position": position, "threads": used},
        )
        output = ran.tails.get(1, "")
        figures = seven_zip(output, step_id) if row["tool"] == "7zip" else stream(output, data)
        trial.add_result(step_id, figures | {"output": output})


def facts(trial: TrialContext, threads: int, array_bytes: int) -> dict[str, str]:
    """Read and record the CPU facts, and the two checks they decide."""
    found = readings(
        trial.setup("cpu-facts", FACTS, timeout_s=FACTS_TIMEOUT_S, owner="provider").stdout
    )
    for name, value in found.items():
        trial.observe(name, value, "measured")
    nproc = found.get("nproc", "")
    disk_tools.check(
        trial,
        "threads_differ",
        "inconclusive"
        if not nproc.isdigit()
        else "fired"
        if int(nproc) != threads
        else "not_fired",
    )
    l3 = cache_bytes(found.get("l3_cache"))
    disk_tools.check(
        trial,
        "stream_array_small",
        "inconclusive" if l3 is None else "fired" if array_bytes < 4 * l3 else "not_fired",
    )
    return found


def cache_bytes(text: str | None) -> int | None:
    """A sysfs cache size (`32768K`) in bytes; None where it can't be read."""
    match = SIZE.match((text or "").strip())
    return None if match is None else int(match[1]) * UNITS[match[2]]


def seven_zip(output: str, step_id: str) -> dict[str, Any]:
    """7-Zip's ratings (MIPS) from its `b` output: the total is the published value, beside
    compress and decompress, their CPU usage, and 7-Zip's own one-thread frequency estimate."""
    average, total = AVERAGE.search(output), TOTAL.search(output)
    if average is None or total is None:
        raise CheckFailed("script", step_id, {"type": "No7ZipRating", "output": output[-1000:]})
    speed_c, usage_c, _, rating_c, speed_d, usage_d, _, rating_d = map(int, average.groups())
    usage, _, rating = map(int, total.groups())
    frequency = FREQUENCY.search(output)
    threads = THREADS.search(output)
    return {
        "value": rating,
        "total_mips": rating,
        "compress_mips": rating_c,
        "decompress_mips": rating_d,
        "usage_percent": usage,
        "compress_usage_percent": usage_c,
        "decompress_usage_percent": usage_d,
        "compress_kib_s": speed_c,
        "decompress_kib_s": speed_d,
        "freq_mhz": [int(v) for v in frequency[1].split()] if frequency else None,
        "benchmark_threads": int(threads[1]) if threads else None,
    }


def stream(output: str, data: Mapping[str, Any]) -> dict[str, Any]:
    """STREAM's best rates: Triad in GB/s is the published value (its MB/s is 10^6 bytes/s),
    beside Copy, Scale and Add; output that does not validate is the provider's failure. The
    array size and the number of passes the binary reports must be the workload's."""
    rates: Mapping[str, float] = {name: float(rate) for name, rate in KERNEL.findall(output)}
    if set(rates) != {"Copy", "Scale", "Add", "Triad"}:
        raise CheckFailed("script", "stream-triad", {"type": "NoStreamRates", "output": output})
    if VALIDATES not in output:
        raise CheckFailed("provider", "stream-triad", {"type": "NotValidated", "output": output})
    array, passes = ARRAY.search(output), PASSES.search(output)
    reported = {
        "array_bytes": int(array[1]) * DOUBLE_BYTES if array else None,
        "passes": int(passes[1]) if passes else None,
    }
    if reported != {
        "array_bytes": data["stream_array_bytes"],
        "passes": data["stream_passes"],
    }:
        raise CheckFailed("script", "stream-triad", {"type": "StreamParameters", **reported})
    counted = COUNTED.search(output)
    return {
        "value": round(rates["Triad"] / 1000, 3),
        **{f"{name.lower()}_gb_s": round(rate / 1000, 3) for name, rate in rates.items()},
        "threads_counted": int(counted[1]) if counted else None,
    }
