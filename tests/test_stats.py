# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""The series statistics against stats-vectors.json, which the web's statistics tests also read."""

import json
import math
import random
from pathlib import Path
from typing import Any

import pytest

import sandbox_benchmark_verifier
from sandbox_benchmark_verifier.stats import (
    bootstrap_ci,
    bootstrap_mean_ci,
    cost_series,
    round_sig,
    series,
)

VECTORS = json.loads(
    (Path(sandbox_benchmark_verifier.__file__).parent / "stats-vectors.json").read_text()
)


@pytest.mark.parametrize("case", VECTORS["series"], ids=lambda case: case["name"])
def test_series_matches_vectors(case: dict[str, Any]) -> None:
    result = series(case["values"], case["direction"], case["tail_floor"])
    assert result == case["expected"]
    assert json.dumps(result, sort_keys=True) == json.dumps(case["expected"], sort_keys=True)


@pytest.mark.parametrize("case", VECTORS["rounding"], ids=lambda case: str(case["value"]))
def test_rounding_matches_vectors(case: dict[str, Any]) -> None:
    assert round_sig(case["value"]) == case["expected"]


def test_vector_settings_are_the_module_settings() -> None:
    values = list(range(1, 21))
    unrounded = bootstrap_ci(values, VECTORS["seed"], VECTORS["resamples"])
    assert unrounded is not None
    assert series(values, "lower_is_better", 20)["ci"] == [round_sig(bound) for bound in unrounded]
    assert VECTORS["figures"] == 5


def test_statistics_come_from_unrounded_values() -> None:
    # The unrounded mean is 1.0000733, which rounds to 1.0001; rounded values would average 1.
    assert series([1.00004, 1.00004, 1.00014], "lower_is_better", 20)["mean"] == 1.0001


def test_unknown_direction_is_refused() -> None:
    with pytest.raises(ValueError, match="direction"):
        series([1.0], "lower", 20)


def _reference_median_ci(values: list[float], seed: int, resamples: int) -> tuple[float, float]:
    """The interval as first written: every resample of the sorted values sorted in full."""
    rng = random.Random(seed)
    rank = max(1, math.ceil(len(values) / 2)) - 1
    medians = sorted(sorted(rng.choices(values, k=len(values)))[rank] for _ in range(resamples))
    return medians[math.floor(0.025 * (resamples - 1))], medians[math.ceil(0.975 * (resamples - 1))]


@pytest.mark.parametrize("count", [2, 3, 4, 7, 20, 41])
def test_the_cached_median_interval_is_the_full_resampling(count: int) -> None:
    draw = random.Random(count)
    for values in (
        sorted(round(draw.lognormvariate(5, 1), 1) for _ in range(count)),
        sorted(draw.choice([1.0, 2.0, 2.0, 3.0]) for _ in range(count)),
    ):
        assert bootstrap_ci(values, 0, 2000) == _reference_median_ci(values, 0, 2000)
        assert bootstrap_ci(values[::-1], 0, 2000) == bootstrap_ci(values, 0, 2000)


def test_the_cost_interval_is_of_the_mean() -> None:
    values = [1.0, 2.0, 3.0, 10.0, 11.0]
    low, high = bootstrap_mean_ci(values, 0) or (0.0, 0.0)
    assert low < sum(values) / len(values) < high
    assert cost_series(values, 20)["ci"] == [
        round_sig(low),
        round_sig(high),
    ]
