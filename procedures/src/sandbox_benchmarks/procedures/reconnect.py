# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: LicenseRef-PolyForm-Shield-1.0.0
"""Request repeated fresh-process attachments and commands through the context.

A failed round leaves its unexecuted command and later rounds recorded as not reached.
"""

from sandbox_benchmarks.interface import StepFailed, TrialContext, Workload


def procedure(trial: TrialContext, workload: Workload) -> None:
    command, rounds = workload.data["command"], workload.data["rounds"]
    round_ = 0
    try:
        for round_ in range(1, rounds + 1):
            trial.attach("attach", "true", command, result={"round": round_})
    except StepFailed as failed:
        # The round's command never ran where its attach failed; later rounds weren't sent.
        if failed.step == "attach":
            trial.not_reached("true", result={"round": round_})
        for later in range(round_ + 1, rounds + 1):
            trial.not_reached("attach", result={"round": later})
            trial.not_reached("true", result={"round": later})
        raise
