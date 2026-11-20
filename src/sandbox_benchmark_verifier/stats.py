# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""The methodology's own statistics: nearest-rank percentiles, the median interval, the series."""

import functools
import math
import operator
import random
from collections.abc import Callable, Sequence
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

SERIES_FIGURES = 5
SERIES_SEED = 0
SERIES_RESAMPLES = 10_000


def nearest_rank(values: Sequence[float], percentile: float) -> float:
    """Return the nearest-rank percentile from numeric observations."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(percentile * len(ordered)) - 1)]


def bootstrap_ci(
    values: Sequence[float], seed: int, resamples: int = 10_000, level: float = 0.95
) -> tuple[float, float] | None:
    """Percentile-bootstrap interval for nearest-rank median, using a fixed supplied seed.

    Each resample draws positions into the values in ascending order, so the interval depends on
    the values and not on the order they come in. With the values sorted, a resample's median is
    the value at its median position, so the interval is two positions that depend only on the
    count, the seed and the resample count (see `_resamples`).
    """
    if len(values) < 2:
        return None
    ordered = sorted(values)
    medians, _ = _resamples(len(ordered), seed, resamples)
    low, high = _interval(medians, level)
    return ordered[low], ordered[high]


def bootstrap_mean_ci(
    values: Sequence[float], seed: int, resamples: int = 10_000, level: float = 0.95
) -> tuple[float, float] | None:
    """Percentile-bootstrap interval for the mean (cost per success), drawn as `bootstrap_ci`."""
    if len(values) < 2:
        return None
    ordered = sorted(values)
    count = len(ordered)
    _, draws = _resamples(count, seed, resamples)
    return _interval(sorted(math.fsum(drawn(ordered)) / count for drawn in draws), level)


def _interval(estimates: Sequence[Any], level: float) -> tuple[Any, Any]:
    cut = (1 - level) / 2
    last = len(estimates) - 1
    return estimates[math.floor(cut * last)], estimates[math.ceil((1 - cut) * last)]


@functools.cache
def _resamples(
    count: int, seed: int, resamples: int
) -> tuple[list[int], tuple[Callable[[Sequence[float]], tuple[float, ...]], ...]]:
    """The resamples of `count` values in ascending order, which depend only on these arguments.

    Returns each resample's median position, sorted, and a getter of each resample's values.
    The draws are `random.Random(seed).choices` over the positions, one resample after another.
    """
    rng = random.Random(seed)
    rank = max(1, math.ceil(count / 2)) - 1
    population = range(count)
    medians: list[int] = []
    draws = []
    for _ in range(resamples):
        drawn = rng.choices(population, k=count)
        draws.append(operator.itemgetter(*drawn))
        drawn.sort()
        medians.append(drawn[rank])
    medians.sort()
    return medians, tuple(draws)


def round_sig(value: float, figures: int = SERIES_FIGURES) -> float | int:
    """Round to significant figures, the one rounding of a published number; whole results are ints.

    Exact ties round away from zero, on the exact binary value (JavaScript's `toPrecision` does the
    same), so 12345 gives 12350 in both languages; Python's own `format` would round it to even.
    """
    exact = Decimal(value)
    if exact == 0:
        return 0
    exponent = exact.adjusted() - figures + 1
    rounded = float(exact.quantize(Decimal(1).scaleb(exponent), rounding=ROUND_HALF_UP))
    return int(rounded) if rounded.is_integer() else rounded


def series(
    values: Sequence[float | None],
    direction: str,
    tail_floor: int,
    seed: int = SERIES_SEED,
    resamples: int = SERIES_RESAMPLES,
) -> dict[str, Any]:
    """Summarize one quantity of one cell: the statistics of its successes, rounded once at the end.

    `values` holds one entry per attempted trial, `None` for a trial without a success. Percentiles
    are nearest rank over the successes, so the median of an even count is the lower middle value.
    `p95` is the worse-side tail (the 5th percentile where `direction` is `higher_is_better`) and
    is `None` below `tail_floor` successes. `ci` is the seeded bootstrap interval of the median,
    `None` below 2 successes. An empty series has `n` 0 and no statistics.
    """
    if direction not in ("lower_is_better", "higher_is_better"):
        raise ValueError(
            f"direction must be lower_is_better or higher_is_better, not {direction!r}"
        )
    successes = sorted(value for value in values if value is not None)
    count = len(successes)
    if count == 0:
        keys = ("median", "p25", "p75", "p95", "min", "max", "mean", "ci")
        return {"n": 0, **dict.fromkeys(keys)}
    tail = 0.95 if direction == "lower_is_better" else 0.05
    interval = bootstrap_ci(successes, seed, resamples)
    return {
        "n": count,
        "median": round_sig(nearest_rank(successes, 0.5)),
        "p25": round_sig(nearest_rank(successes, 0.25)),
        "p75": round_sig(nearest_rank(successes, 0.75)),
        "p95": round_sig(nearest_rank(successes, tail)) if count >= tail_floor else None,
        "min": round_sig(successes[0]),
        "max": round_sig(successes[-1]),
        "mean": round_sig(math.fsum(successes) / count),
        "ci": None if interval is None else [round_sig(interval[0]), round_sig(interval[1])],
    }


def cost_series(
    values: Sequence[float | None],
    tail_floor: int,
    seed: int = SERIES_SEED,
    resamples: int = SERIES_RESAMPLES,
) -> dict[str, Any]:
    """`series` of cost per success: lower is better, and `ci` is the interval of the mean."""
    result = series(values, "lower_is_better", tail_floor, seed, resamples)
    interval = bootstrap_mean_ci([value for value in values if value is not None], seed, resamples)
    result["ci"] = None if interval is None else [round_sig(interval[0]), round_sig(interval[1])]
    return result


def numeric(value: Any) -> float | None:
    """Convert a numeric JSON scalar, excluding booleans."""
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None
