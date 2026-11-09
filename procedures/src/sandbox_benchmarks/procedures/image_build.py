# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: LicenseRef-PolyForm-Shield-1.0.0
"""Prepare an image from the workload's base and record its declared facts.

The context owns image construction and cleanup, including resource-specific copies.
"""

from sandbox_benchmarks.interface import TrialContext, Workload


def procedure(trial: TrialContext, workload: Workload) -> None:
    """Build and remove one new trial image; image preparation is the only timed step."""
    data = workload.data
    base = data["base_image"]
    if data.get("mirrored"):
        base = trial.image_reference(base)
    for name, value in data.get("facts", {}).items():
        trial.observe(f"base_{name}", value, "recorded")
    with trial.prepare_image(base) as image:
        trial.observe("copy_size_specific", image.prepared.get("resources") is not None, "recorded")
