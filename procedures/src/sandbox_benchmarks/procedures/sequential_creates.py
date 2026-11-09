# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: LicenseRef-PolyForm-Shield-1.0.0
"""Create configured sandboxes sequentially and retain first-command timing."""

from sandbox_benchmarks.interface import CheckFailed, StepFailed, TrialContext, Workload
from sandbox_benchmarks.kit.sandbox_start import UPTIME_PURPOSE

UPTIME = "cat /proc/uptime"
UPTIME_TIMEOUT_S = 30
COMMAND_STEPS = {"start-time": "first-true", "start-time-exec": "exec"}


def procedure(trial: TrialContext, workload: Workload) -> None:
    """Build one image, then create each described sandbox from it in sequence."""
    step_id = COMMAND_STEPS[trial.description.id]
    with trial.prepare_image(workload.data["base_image"]) as image:
        for n in range(1, trial.description.sandboxes + 1):
            with trial.sandbox(n, settings=trial.image_settings(image)):
                trial.run(step_id, workload.data["command"])
                if step_id == "first-true":
                    _check_start_time(trial, workload.data["within_s"])
                observe_uptime(trial, n)


def _check_start_time(trial: TrialContext, within_s: int) -> None:
    create, command = trial.operation("create"), trial.operation("first-true")
    if create is None or command is None:
        raise CheckFailed("script", "first-true", {"type": "NoOperationRecord"})
    elapsed_s = command.end_s - create.start_s
    if elapsed_s > within_s:
        raise CheckFailed(
            "provider",
            "first-true",
            {
                "type": "NotWithin",
                "elapsed_s": round(elapsed_s, 3),
                "within_s": within_s,
            },
        )


def observe_uptime(trial: TrialContext, sandbox: int) -> None:
    """Keep each sandbox's untimed uptime reading with its 1-based sandbox number."""
    observation = f"guest_uptime_s_{sandbox}"
    try:
        read = trial.setup(UPTIME_PURPOSE, UPTIME, timeout_s=UPTIME_TIMEOUT_S)
    except StepFailed as failed:
        trial.observe(
            observation, None, "unknown", f"the uptime read failed: {failed.evidence.get('type')}"
        )
        return
    try:
        uptime_s = float(read.stdout.split()[0])
    except (IndexError, ValueError):
        trial.observe(observation, None, "unknown", "/proc/uptime printed no uptime")
        return
    trial.observe(observation, uptime_s, "measured")
