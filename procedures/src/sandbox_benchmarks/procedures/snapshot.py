# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: LicenseRef-PolyForm-Shield-1.0.0
"""Create a snapshot, restore configured copies and verify their marker files.

The context supplies creation, restoration and confirmed cleanup operations.
"""

from typing import Any

from sandbox_benchmarks.interface import (
    CheckFailed,
    CreateIntent,
    TrialContext,
    Workload,
    output_evidence,
)


def create_intents(workload: Workload) -> tuple[CreateIntent, ...]:
    """The workload's one fresh create, naming the pinned snapshot settings it runs under."""
    return (CreateIntent("create", ("snapshots",), {"snapshots": workload.data["snapshots"]}),)


def procedure(trial: TrialContext, workload: Workload) -> None:
    data = workload.data
    marker = data["marker"]
    snapshot: dict[str, Any] | None = None
    try:
        with trial.sandbox(1):
            trial.setup("marker", marker["write"], timeout_s=marker["timeout_s"], owner="provider")
            snapshot = trial.snapshot("snapshot")
        # Snapshot's data names no count: its one restore predates the fork (workload version).
        for n in range(2, 2 + data.get("restores", 1)):
            with trial.sandbox(n, snapshot=snapshot):
                trial.run("exec", data["command"])
                read = trial.setup(
                    "marker-check", marker["read"], timeout_s=marker["timeout_s"], owner="provider"
                )
                if read.stdout != marker["text"]:
                    evidence = {"type": "MarkerMismatch", **output_evidence(read)}
                    raise CheckFailed("provider", "create-from-snapshot", evidence)
    finally:
        if snapshot is not None:
            trial.delete_snapshot(snapshot)
