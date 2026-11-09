# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: LicenseRef-PolyForm-Shield-1.0.0
"""Run configured file-operation repetitions after an untimed setup.

Repetitions are sequential. A failed repetition ends its sequence and the remaining
operations are recorded as not reached.
"""

import hashlib
from collections.abc import Callable, Mapping
from typing import Any

from sandbox_benchmarks.interface import CheckFailed, StepFailed, TrialContext, Workload


def procedure(trial: TrialContext, workload: Workload) -> None:
    setup = workload.data["setup"]
    # The sandbox's first command, so its one-off work falls here, untimed. On the byte
    # command path, as file and process calls are; a failure is the provider's.
    trial.setup(
        "setup", setup["command"], timeout_s=setup["timeout_s"], output="bytes", owner="provider"
    )
    for step_id, primitive in PRIMITIVES:
        primitive(trial, step_id, workload.data)


def file_list(trial: TrialContext, step_id: str, data: Mapping[str, Any]) -> None:
    """The setup's directory, listed: each listing is exactly its names, sorted, and nothing
    else; an error or another list is the provider's failure."""
    mine = data[step_id]
    expected = tuple(mine["names"] % number for number in range(mine["count"]))

    def once(repetition: int) -> None:
        names = trial.list_files(step_id, mine["path"])
        if names != expected:
            evidence = {
                "type": "ListMismatch",
                "repetition": repetition,
                "count": len(names),
                "missing": sorted(set(expected) - set(names))[:10],
                "extra": sorted(set(names) - set(expected))[:10],
            }
            raise CheckFailed("provider", step_id, evidence)

    repeat(trial, step_id, mine["repetitions"], once)


def file_write(trial: TrialContext, step_id: str, data: Mapping[str, Any]) -> None:
    """The payload, written to its path again and again; it is checked against its SHA-256
    first, on the job host, a mismatch being the benchmark script's failure."""
    mine = data[step_id]
    spec = data["payloads"][mine["payload"]]
    content = payload(spec)
    if (found := hashlib.sha256(content).hexdigest()) != spec["sha256"]:
        raise CheckFailed("script", step_id, {"type": "PayloadMismatch", "sha256": found})

    def once(repetition: int) -> None:
        try:
            trial.write_file(step_id, spec["path"], content)
        except StepFailed as failed:
            raise _resource_limit(failed) from None

    repeat(trial, step_id, mine["repetitions"], once)


def file_read(trial: TrialContext, step_id: str, data: Mapping[str, Any]) -> None:
    """The payload's file, read whole again and again: the bytes back must have its SHA-256,
    checked on the job host after each call returns; another digest is the provider's
    failure. The first read also proves the last write."""
    mine = data[step_id]
    spec = data["payloads"][mine["payload"]]

    def once(repetition: int) -> None:
        try:
            back = trial.read_file(step_id, spec["path"])
        except StepFailed as failed:
            raise _resource_limit(failed) from None
        if (found := hashlib.sha256(back).hexdigest()) != spec["sha256"]:
            evidence = {
                "type": "DigestMismatch",
                "repetition": repetition,
                "sha256": found,
                "bytes": len(back),
            }
            raise CheckFailed("provider", step_id, evidence)

    repeat(trial, step_id, mine["repetitions"], once)


def payload(spec: Mapping[str, Any]) -> bytes:
    """A payload's bytes: the first `bytes` of SHAKE256 over its ASCII seed, in memory."""
    return hashlib.shake_256(spec["seed"].encode("ascii")).digest(spec["bytes"])


def _resource_limit(failed: StepFailed) -> StepFailed:
    """A `resource_limit` refusal is a procedure-input failure: the payload size
    is selected by the procedure."""
    if failed.evidence.get("code") == "resource_limit":
        return CheckFailed("script", failed.step, failed.evidence)
    return failed


def repeat(trial: TrialContext, step_id: str, times: int, once: Callable[[int], None]) -> None:
    """Send `once(1)` to `once(times)` back to back; at the first failure, record the later
    repetitions as not reached and end the trial."""
    for repetition in range(1, times + 1):
        try:
            once(repetition)
        except StepFailed:
            for _ in range(repetition, times):
                trial.not_reached(step_id)
            raise


PRIMITIVES: tuple[tuple[str, Callable[[TrialContext, str, Mapping[str, Any]], None]], ...] = (
    ("file-list", file_list),
    ("file-write-4kib", file_write),
    ("file-read-4kib", file_read),
)
"""Each primitive's step id and function, in the trial's fixed order."""
