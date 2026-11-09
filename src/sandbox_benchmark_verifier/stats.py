# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""The methodology's own statistics: nearest-rank percentiles and the seeded median interval."""

import math
import random
from collections.abc import Sequence
from typing import Any


def nearest_rank(values: Sequence[float], percentile: float) -> float:
    """Return the nearest-rank percentile from numeric observations."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(percentile * len(ordered)) - 1)]


def bootstrap_ci(
    values: Sequence[float], seed: int, resamples: int = 10_000, level: float = 0.95
) -> tuple[float, float] | None:
    """Percentile-bootstrap interval for nearest-rank median, using a fixed supplied seed."""
    if len(values) < 2:
        return None
    rng = random.Random(seed)
    rank = max(1, math.ceil(len(values) / 2)) - 1
    medians = sorted(sorted(rng.choices(values, k=len(values)))[rank] for _ in range(resamples))
    cut = (1 - level) / 2
    return medians[math.floor(cut * (resamples - 1))], medians[
        math.ceil((1 - cut) * (resamples - 1))
    ]


def numeric(value: Any) -> float | None:
    """Convert a numeric JSON scalar, excluding booleans."""
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None
