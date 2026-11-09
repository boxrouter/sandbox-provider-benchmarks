# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: LicenseRef-PolyForm-Shield-1.0.0
"""Burst time to first command: one new image, then N sandboxes created at once."""

from sandbox_benchmarks.interface import TrialContext, Workload


def procedure(trial: TrialContext, workload: Workload) -> None:
    """Build one image, then create the workload's sandboxes from it at once."""
    with trial.prepare_image(workload.data["base_image"]) as image:
        trial.burst("ready", workload.data["sandboxes"], trial.image_settings(image))
