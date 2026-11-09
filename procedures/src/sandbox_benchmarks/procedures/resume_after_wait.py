# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: LicenseRef-PolyForm-Shield-1.0.0
"""Observe recovery of supplied data after an idle wait.

The context's capabilities determine whether pause/resume calls apply. Control and
recovery readings retain the host and guest intervals used to assess the idle period.
"""

from sandbox_benchmarks.assets import guest_script
from sandbox_benchmarks.interface import CommandOutput, Operation, TrialContext, Workload
from sandbox_benchmarks.kit.probe import readings
from sandbox_benchmarks.procedures.pause_resume import (
    DIRECTORY,
    data_back_command,
    holds,
    load_command,
)

LOAD_TIMEOUT_S = 30
STANDBY_S = 5.0
"""The least frozen time that proves standby rather than clock-reading noise."""
NO_PAUSE = "no pause to call: the sandbox is left idle and stands by by itself"
RESUME_VALUES = ("resume-to-data-back",)


def procedure(trial: TrialContext, workload: Workload) -> None:
    data = holds(workload.data)
    wait_s = workload.data["wait_s"]
    holder = guest_script("holder.py")
    load = f"mkdir -p {DIRECTORY} || exit 1\n{load_command(data, holder)}"
    loaded = readings(trial.setup("load", load, timeout_s=LOAD_TIMEOUT_S).stdout)

    read = data_back_command(data, loaded)
    control = trial.run("control", read)
    pauses = "pause_resume" in trial.capabilities
    if pauses:
        trial.pause("pause")
    else:
        trial.skip("pause", NO_PAUSE)
        trial.skip("resume", NO_PAUSE)
    trial.idle("wait", wait_s)  # Nothing is sent during the wait.
    if pauses:
        trial.resume("resume")
    back = trial.run("data-back", read)

    frozen, why = frozen_s(trial.operation("control"), control, trial.operation("data-back"), back)
    if frozen is None:
        trial.observe("frozen_s", None, "unknown", why)
    else:
        trial.observe(
            "frozen_s",
            round(frozen, 3),
            "calculated",
            formula=(
                "(back.start_ms - control.end_ms) / 1000 "
                "- (back.uptime_start_cs - control.uptime_end_cs) / 100"
            ),
            inputs=[
                "steps.control.end_ms",
                "steps.control.clock.uptime_end_cs",
                "steps.data-back.start_ms",
                "steps.data-back.clock.uptime_start_cs",
            ],
        )
    if not pauses and wait_s > 0:
        if frozen is None:
            reason = f"{trial.provider_name} standby could not be checked: {why}"
        elif frozen < STANDBY_S:
            reason = (
                f"{trial.provider_name} did not enter standby in this wait (frozen {frozen:.2f} s)"
            )
        else:
            reason = None
        if reason is not None:
            for computed_id in RESUME_VALUES:
                trial.withhold(computed_id, reason)


def frozen_s(
    control: Operation | None,
    control_output: CommandOutput,
    back: Operation | None,
    back_output: CommandOutput,
) -> tuple[float | None, str | None]:
    """How long the sandbox's monotonic clock stood still between the end of `control` and the
    start of `data-back`, by the job host's clock; or None and why it can't be told."""
    if control is None or back is None:
        return None, "no operation record for control or data-back"
    before, after = control_output.clock, back_output.clock
    if (
        before is None
        or after is None
        or before.uptime_end_cs is None
        or after.uptime_start_cs is None
    ):
        return None, "no clock reads around control or data-back"
    sandbox_s = (after.uptime_start_cs - before.uptime_end_cs) / 100
    if sandbox_s < 0:
        return None, "sandbox uptime went backwards between control and data-back"
    return back.start_s - control.end_s - sandbox_s, None
