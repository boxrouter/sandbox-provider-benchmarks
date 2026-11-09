# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: LicenseRef-PolyForm-Shield-1.0.0
"""Prepare the configured data, pause and resume, then verify data recovery.

The shared guest program supplies seeded file/memory contents. Lifecycle calls and
command observations come from the context; the procedure retains their validity checks.
"""

import shlex
from dataclasses import dataclass
from typing import Any

from sandbox_benchmarks.assets import guest_script
from sandbox_benchmarks.interface import CheckFailed, Missed, StepFailed, TrialContext, Workload
from sandbox_benchmarks.kit import command_script
from sandbox_benchmarks.kit.probe import FREE, UNLIMITED_BYTES, parse_number, readings

DIRECTORY = "/workspace/pause-resume"
GIB = 1024**3
DISK_SPARE = 1 * GIB
"""Free disk a storage rung needs beyond its file."""
MEMORY_SPARE = 1 * GIB
"""Available memory a memory rung needs beyond its buffer."""
PREFLIGHT_TIMEOUT_S = 30
LOAD_TIMEOUT_S = 900
CHECK_TIMEOUT_S = 30
HOLDER_READY_TENTHS = 1200
"""How long, in tenths of a second, a holder may take to fill its buffer."""
HEREDOC = "BOXROUTER_HOLDER_EOF"
SETTLE_S = 60.0
"""Untimed idle after loading, before the pause: the guest and the host finish
writing the data (Linux writes dirty pages back after 30 s by default), so the pause measures
pausing an idle sandbox, not the tail of the setup."""

PREFLIGHT = f"""\
mkdir -p {DIRECTORY} && cd {DIRECTORY} || exit 1
{FREE.rstrip()}
for f in memory.max memory.current memory/memory.limit_in_bytes memory/memory.usage_in_bytes; do
  [ -r "/sys/fs/cgroup/$f" ] && echo "cgroup_${{f##*/}} $(cat "/sys/fs/cgroup/$f")"
done
echo "boot_id $(cat /proc/sys/kernel/random/boot_id)"
"""
AFTER = """\
echo "boot_id $(cat /proc/sys/kernel/random/boot_id)"
awk '/^MemAvailable:/ {print "mem_available_kib", $2}' /proc/meminfo
"""


@dataclass(frozen=True)
class Part:
    bytes: int
    seed: int


@dataclass(frozen=True)
class Holds:
    """What a workload puts in the sandbox: a marker file, a file on disk, or data in a
    process's memory."""

    marker: Part | None = None
    disk: Part | None = None
    memory: Part | None = None
    """Data in a holder's memory, rehashed by `data-back`."""
    drop_cache: bool = False
    """Drop the page cache and read the file directly, so no read is served from memory."""


def holds(data: dict[str, Any]) -> Holds:
    kind = data["kind"]
    if kind == "empty":
        return Holds(marker=Part(data["bytes"], data["seed"]))
    if kind == "memory":
        return Holds(memory=Part(data["bytes"], data["seed"]))
    if kind == "storage":
        return Holds(disk=Part(data["bytes"], data["seed"]), drop_cache=True)
    raise ValueError(f"unknown workload kind {kind!r}")


def procedure(trial: TrialContext, workload: Workload) -> None:
    data = holds(workload.data)
    before = readings(trial.setup("preflight", PREFLIGHT, timeout_s=PREFLIGHT_TIMEOUT_S).stdout)
    for name in ("disk_free_bytes", "mem_available_kib"):
        trial.observe(name, int(before[name]), "measured")
    for name, item in before.items():
        if name.startswith("cgroup_"):
            trial.observe(name, _typed(item), "measured")
    trial.observe("boot_id_before", before["boot_id"], "measured")
    fits(data, int(before["disk_free_bytes"]), memory_available(before))

    holder = guest_script("holder.py")
    loaded = {
        name: item
        for name, item in readings(run_load(trial, load_command(data, holder))).items()
        if name in LOAD_READINGS
    }
    for name, item in loaded.items():
        trial.observe(name, _typed(item), "recorded" if name in _DECIDED else "measured")
    trial.idle("settle", SETTLE_S)  # Nothing is sent while the sandbox settles.

    trial.pause("pause")  # Nothing is sent between the pause and the resume.
    trial.resume("resume")
    back = trial.run("data-back", data_back_command(data, loaded))
    if data.memory is not None:
        same_holder(loaded, back.stdout, "data-back")

    after = readings(trial.setup("after-check", AFTER, timeout_s=CHECK_TIMEOUT_S).stdout)
    trial.observe("boot_id_after", after["boot_id"], "measured")
    trial.observe("mem_available_after_kib", int(after["mem_available_kib"]), "measured")
    if data.memory is not None and after["boot_id"] != before["boot_id"]:
        evidence = {"type": "Rebooted", "before": before["boot_id"], "after": after["boot_id"]}
        raise CheckFailed("provider", "data-back", evidence)


def run_load(trial: TrialContext, command: str) -> str:
    """Run the load as an untimed background script and return its output.
    The holder it starts outlives the script. A load that did not exit 0 is the benchmark script's
    failure."""
    found, ran = command_script.run_one(
        trial, command, cwd=DIRECTORY, limit_s=LOAD_TIMEOUT_S, purpose="load"
    )
    if found is None:
        raise StepFailed("provider", "load", {"type": "ScriptEnded", "logged": 0, "commands": 1})
    output = ran.tails.get(1, "")
    if found.exit_code != 0:
        raise StepFailed(
            "script", "load", {"type": "ExitCode", "exit_code": found.exit_code, "output": output}
        )
    return output


def same_holder(loaded: dict[str, str], stdout: str, step: str) -> None:
    """Raise the provider's failure at `step` unless the holder that answered is the one that
    was loaded and its buffer hashes as it did then."""
    pid, sha256 = stdout.split()[:2]
    if pid != loaded["holder_pid"]:
        evidence = {"type": "HolderReplaced", "before": loaded["holder_pid"], "after": pid}
        raise CheckFailed("provider", step, evidence)
    if sha256 != loaded["holder_sha256"]:
        evidence = {"type": "HashMismatch", "loaded": loaded["holder_sha256"], "back": sha256}
        raise CheckFailed("provider", step, evidence)


# What the load command reports. Its output is one stream with stderr (`command_script`), so a
# stray line is not taken for a reading.
LOAD_READINGS = frozenset(
    {
        "direct_reads",
        "page_cache_dropped",
        "page_cache_drop_error",
        "holder_pid",
        "holder_sha256",
        "holder_rss_kib",
        "mem_available_loaded_kib",
    }
)

# What the load command reports that the benchmark script decided or saw, rather than read.
_DECIDED = frozenset({"page_cache_dropped", "direct_reads", "page_cache_drop_error"})


def fits(data: Holds, disk_free: int, memory_available: int) -> None:
    """Raise Missed, with the reading, when the workload does not fit this sandbox."""
    if data.disk is not None and disk_free < data.disk.bytes + DISK_SPARE:
        need = (data.disk.bytes + DISK_SPARE) / GIB
        raise Missed(f"not enough free disk: {disk_free / GIB:.2f} GiB free, needs {need:g} GiB")
    held = data.memory
    if held is not None and memory_available < held.bytes + MEMORY_SPARE:
        need = (held.bytes + MEMORY_SPARE) / GIB
        raise Missed(
            f"not enough available memory: {memory_available / GIB:.2f} GiB available, "
            f"needs {need:g} GiB"
        )


def memory_available(before: dict[str, str]) -> int:
    """What a new process can take: MemAvailable, capped by the sandbox's cgroup limit minus
    its usage where one is set, since a container's /proc/meminfo can describe its host."""
    available = int(before["mem_available_kib"]) * 1024
    limit = parse_number(
        before.get("cgroup_memory.max") or before.get("cgroup_memory.limit_in_bytes")
    )
    usage = parse_number(
        before.get("cgroup_memory.current") or before.get("cgroup_memory.usage_in_bytes")
    )
    if limit is not None and limit < UNLIMITED_BYTES:
        available = min(available, limit - (usage or 0))
    return available


def load_command(data: Holds, holder: str) -> str:
    """Write the holder, then make the workload's data with it."""
    if HEREDOC in holder:
        raise ValueError(f"holder.py contains the heredoc delimiter {HEREDOC}")
    lines = [
        f"cd {DIRECTORY} || exit 1",
        f"cat > holder.py <<'{HEREDOC}'\n{holder.rstrip()}\n{HEREDOC}",
    ]
    if data.marker is not None:
        lines.append(f"python3 holder.py write marker.bin {data.marker.bytes} {data.marker.seed}")
        lines[-1] += " || exit 1"
    if data.disk is not None:
        lines.append(f"python3 holder.py write data.bin {data.disk.bytes} {data.disk.seed}")
        lines[-1] += " || exit 1"
    if data.drop_cache:
        lines += [
            # A direct read bypasses the page cache; where the filesystem refuses it, a plain
            # read is used and recorded.
            "if dd if=data.bin of=/dev/null iflag=direct bs=4M count=1 status=none 2>/dev/null;"
            ' then echo "direct_reads true"; else echo "direct_reads false"; fi',
            # Drop the page cache where the user may, so no read is served from memory.
            "sync",
            "if drop=$(sudo -n sh -c 'echo 3 > /proc/sys/vm/drop_caches' 2>&1);"
            ' then echo "page_cache_dropped true"; else drop="exit $?: $drop";'
            ' drop="${drop:0:1000}";'
            ' echo "page_cache_dropped false";'
            ' echo "page_cache_drop_error ${drop//[[:space:]]/ }"; fi',
        ]
    if data.memory is not None:
        size, seed = data.memory.bytes, data.memory.seed
        lines += [
            f"nohup python3 holder.py hold . {size} {seed} > holder.log 2>&1 < /dev/null &",
            "holder=$!",
            "tries=0",
            "while [ ! -e ready ]; do",
            '  kill -0 "$holder" 2>/dev/null || { cat holder.log >&2; exit 1; }',
            "  tries=$((tries + 1))",
            f'  [ "$tries" -gt {HOLDER_READY_TENTHS} ] &&',
            '    { echo "holder not ready" >&2; exit 1; }',
            "  sleep 0.1",
            "done",
            "read -r pid sha256 rss _ < ready",
            'echo "holder_pid $pid"',
            'echo "holder_sha256 $sha256"',
            'echo "holder_rss_kib $rss"',
            "awk '/^MemAvailable:/ {print \"mem_available_loaded_kib\", $2}' /proc/meminfo",
        ]
    return "\n".join(lines)


def data_back_command(data: Holds, loaded: dict[str, str]) -> str:
    """What `data-back` sends: a plain read of the file, directly from storage where the
    filesystem allows it; for the memory workload, the holder's rehash."""
    if data.memory is not None:
        return memory_command(loaded)
    if data.disk is not None and data.drop_cache:
        direct = " iflag=direct" if loaded["direct_reads"] == "true" else ""
        return f"dd if={DIRECTORY}/data.bin{direct} bs=4M of=/dev/null status=none"
    assert data.marker is not None  # holds() gives each workload kind one part
    return f"dd if={DIRECTORY}/marker.bin bs=4M of=/dev/null status=none"


def memory_command(loaded: dict[str, str]) -> str:
    """Signal the holder to rehash its whole buffer, and print its `PID SHA-256` answer."""
    pid = shlex.quote(loaded["holder_pid"])
    return f'kill -USR1 {pid} && read -r line < {DIRECTORY}/answer && echo "$line"'


def _typed(item: str) -> Any:
    if item in ("true", "false"):
        return item == "true"
    return int(item) if item.isdigit() else item
