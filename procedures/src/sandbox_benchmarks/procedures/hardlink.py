# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: LicenseRef-PolyForm-Shield-1.0.0
"""Measure link creation and removal using stress-ng's own link-stressor rate.

Read untimed mount and memory facts, install the verified tool, run the workload command,
and read its numeric metrics. An unreadable report is a script failure; a missing or
nonpositive link rate after a successful command is a provider failure.
"""

import math
import re
from typing import Any

from sandbox_benchmarks.interface import CheckFailed, TrialContext, Workload, output_evidence
from sandbox_benchmarks.kit import disk_tools
from sandbox_benchmarks.kit.probe import readings

DIRECTORY = "/workspace/hardlink"
STEP = "hardlink"
RATE = "links-createdremoved-per-sec"
READ_TIMEOUT_S = 30
PROBE_TIMEOUT_S = 60
EXTRAS = {
    "bogo_ops": "bogo-ops",
    "wall_clock_s": "wall-clock-time",
    "user_s": "user-time",
    "system_s": "system-time",
}
"""Recorded beside the rate, from the stressor's YAML entry."""


def procedure(trial: TrialContext, workload: Workload) -> None:
    data = workload.data
    found = disk_tools.facts(trial, DIRECTORY)
    disk_tools.fits(found, data["free_bytes"])
    figures = trial.setup(
        "memory-probe",
        f"cd {DIRECTORY} || exit 1\n{disk_tools.memory_probe(data['probe_bytes'])}",
        timeout_s=PROBE_TIMEOUT_S,
    ).stdout
    disk_tools.files_in_memory(
        trial, found, readings(figures), data["probe_bytes"], shmem_only=True
    )
    disk_tools.install(trial, DIRECTORY, data["tool"])
    ran = trial.run(STEP, data["command"])
    back = trial.setup(
        "results",
        f"cat {DIRECTORY}/out.yaml && echo '#after' && cd {DIRECTORY} && "
        "echo \"inodes_free_after $(df --output=iavail . | tail -n 1 | tr -d ' ')\"",
        timeout_s=READ_TIMEOUT_S,
    ).stdout
    text, _, after = back.partition("#after\n")
    for name, value in readings(after).items():
        trial.observe(name, value, "measured")
    metrics = link_metrics(text)
    rate = metrics.get(RATE)
    if not isinstance(rate, int | float) or rate <= 0:
        raise CheckFailed("provider", STEP, {"type": "NoLinksRate", **output_evidence(ran)})
    trial.add_result(
        STEP, {"value": rate, "links_per_s": rate} | {k: metrics.get(v) for k, v in EXTRAS.items()}
    )


def link_metrics(text: str) -> dict[str, Any]:
    """Read the pinned stress-ng format's flat, numeric metrics rows, not general YAML."""
    try:
        lines = text.splitlines()
        [start] = [i for i, line in enumerate(lines) if line == "metrics:"]
        entries: list[dict[str, Any]] = []
        for line in lines[start + 1 :]:
            if not line.strip():
                continue
            if not line[0].isspace():
                break
            if stressor := re.fullmatch(r"    - stressor: ([a-zA-Z0-9_-]+)", line):
                entries.append({"stressor": stressor[1]})
                continue
            metric = re.fullmatch(
                r"      ([a-zA-Z0-9_-]+): "
                r"([+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)",
                line,
            )
            if metric is None or not entries or metric[1] in entries[-1]:
                raise ValueError("invalid or duplicate numeric metric row")
            raw = metric[2]
            value = float(raw) if any(c in raw for c in ".eE") else int(raw)
            if not math.isfinite(value):
                raise ValueError("nonfinite numeric metric")
            entries[-1][metric[1]] = value
        [entry] = [entry for entry in entries if entry["stressor"] == "link"]
    except (ValueError, OverflowError) as error:
        raise CheckFailed(
            "script", "results", {"type": "BadYaml", "error": str(error)[:300]}
        ) from None
    return entry
