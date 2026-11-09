# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: LicenseRef-PolyForm-Shield-1.0.0
"""Replay the supplied repository CI job inside a sandbox.

Commands retain their recorded order and expected exits. The shared replay helper owns
runtime logging and each failure's owner.
"""

import shlex

from sandbox_benchmarks.interface import TrialContext, Workload
from sandbox_benchmarks.kit import programs, replay

WORKSPACE = "/workspace"
"""Where the script starts and the checkout step clones into `src`."""
SHELL = "bash -e"
"""GitHub's default shell for a `run` step on Linux: a failing line ends the step."""
_AS_ACCOUNT = 'sudo -u {} -H --preserve-env env PATH="$PATH" {} -c '
"""How a command runs as an image's account, in a shell: with the script's environment and `PATH`,
which sudo resets."""
AS_BOX = _AS_ACCOUNT.format("box", SHELL)
"""The GitHub Actions job image's non-root account."""
AS_USER = _AS_ACCOUNT.format("user", "bash")
"""The standard image's non-root account since 0.3.0, whichever user the provider starts as."""


def procedure(trial: TrialContext, workload: Workload) -> None:
    data = workload.data
    steps = [
        replay.Step(
            step["command"] if step.get("sudo") else AS_BOX + shlex.quote(step["command"]),
            0,
            step["group"],
            {"step": step["id"], "program": programs.program(step["command"])},
            step.get("directory"),
        )
        for step in data["steps"]
    ]
    replay.run(trial, steps, cwd=WORKSPACE, env=data["env"], shell=SHELL)
