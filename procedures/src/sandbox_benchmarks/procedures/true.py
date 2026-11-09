# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: LicenseRef-PolyForm-Shield-1.0.0
"""Run the configured command repeatedly in one sandbox, as separate requests.

Each request succeeds only with its expected exit status and guest clock readings.
The first failure ends the sequence; the remaining requests are recorded as not reached.
Operation edges and guest readings are supplied by the context.
"""

from sandbox_benchmarks.interface import (
    CheckFailed,
    CommandOutput,
    StepFailed,
    TrialContext,
    Workload,
    output_evidence,
)

STEP = "true"


def procedure(trial: TrialContext, workload: Workload) -> None:
    command, calls = workload.data["command"], workload.data["calls"]
    sent = 0
    try:
        for round_ in range(1, calls + 1):
            sent += 1
            _check_clock(round_, trial.run(STEP, command))
    except StepFailed:
        for _ in range(sent, calls):
            trial.not_reached(STEP)
        raise


def _check_clock(round_: int, ran: CommandOutput) -> None:
    """No clock readings back is the provider's failure: its exec lost the command's stderr."""
    if ran.clock is None:
        evidence = {"type": "NoClockReadings", "round": round_, **output_evidence(ran)}
        raise CheckFailed("provider", STEP, evidence)
