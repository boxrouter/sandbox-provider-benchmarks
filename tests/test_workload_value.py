# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""The workload value: `composition.per_input_value` of the speed and cost indexes, recomputed."""

import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from sandbox_benchmark_verifier.catalog import load_schema
from sandbox_benchmark_verifier.cli import reproduce
from sandbox_benchmark_verifier.workload_value import DERIVATIONS, derived_value

pytestmark = pytest.mark.unit

Row = dict[str, Any]


def trial(
    provider: str,
    unit: str,
    time: float | None,
    cost: float | None = None,
    *,
    success: bool = True,
    owner: str | None = None,
    reason: str | None = None,
    index: int = 1,
) -> list[Row]:
    """One trial's time row and cost row."""
    base: Row = {
        "provider": provider,
        "test_id": "t",
        "run_id": "r",
        "methodology": {"id": "m"},
        "variant": "all",
        "workload": unit,
        "trial": index,
        "success": success,
        "exclusion_reason": reason,
    }
    if owner is not None:
        base["composite_owner"] = owner
    return [
        {**base, "measurement": "time", "unit": "ms", "value": time},
        {**base, "measurement": "cost", "unit": "USD", "value": cost},
    ]


def units(provider: str, times: Mapping[str, Sequence[float]], cost: float = 0.5) -> list[Row]:
    return [
        row
        for unit, values in times.items()
        for index, value in enumerate(values, start=1)
        for row in trial(provider, unit, value, cost * value, index=index)
    ]


def aggregate(provider: str, statistic: str, measurement: str = "time", places: int = 3) -> Row:
    return {
        "provider": provider,
        "test_id": "t",
        "run_id": "r",
        "methodology": {"id": "m"},
        "variant": "all",
        "measurement": measurement,
        "statistic": statistic,
        "derivation": {"id": "workload-value", "completed_by": "time", "decimal_places": places},
        "inputs": {"relative_to": "run", "path": "raw"},
    }


def mean_of(values: Sequence[float]) -> float:
    return math.exp(sum(math.log(v) for v in values) / len(values))


def value_of(rows: list[Row], **fields: Any) -> tuple[float | None, int, int]:
    return tuple(derived_value({**aggregate("a", "median"), **fields}, rows))  # type: ignore[return-value]


def test_derivation_ids_come_from_the_schema() -> None:
    declared = load_schema("aggregate")["properties"]["derivation"]["properties"]["id"]["enum"]
    assert list(DERIVATIONS) == declared == ["workload-value"]


def test_all_units_complete_is_the_geometric_mean_of_unit_medians() -> None:
    rows = units("a", {"u1": [10, 30, 20], "u2": [100, 400, 200]}) + units(
        "b", {"u1": [5], "u2": [7]}
    )
    assert value_of(rows) == (round(mean_of([20, 200]), 3), 2, 0)
    assert value_of(rows, provider="b") == (round(mean_of([5, 7]), 3), 2, 0)


def test_unit_statistic_is_median_mean_or_p95() -> None:
    rows = units("a", {"u1": [1, 2, 3, 10], "u2": [4, 4, 4, 4]})
    assert value_of(rows)[0] == round(mean_of([2, 4]), 3)  # nearest-rank median of 4 values
    assert value_of(rows, statistic="mean")[0] == round(mean_of([4, 4]), 3)
    assert value_of(rows, statistic="p95")[0] == round(mean_of([10, 4]), 3)


def test_a_unit_the_provider_did_not_complete_takes_the_slowest_provider_value() -> None:
    rows = (
        units("a", {"u1": [10]})
        + units("b", {"u1": [40], "u2": [50]})
        + units("c", {"u1": [20], "u2": [90]})
        + trial("a", "u2", None, success=False)
    )
    assert value_of(rows) == (round(mean_of([10, 90]), 3), 2, 0)
    assert value_of(rows, statistic="mean", provider="b") == (round(mean_of([40, 50]), 3), 2, 0)


def test_a_provider_that_completed_nothing_has_no_value() -> None:
    rows = units("b", {"u1": [40]}) + trial("a", "u1", None, success=False)
    assert value_of(rows) == (None, 0, 0)


def test_a_foreign_owned_failure_omits_the_unit_for_every_provider() -> None:
    rows = (
        units("a", {"u1": [10], "u2": [20]})
        + units("b", {"u1": [30], "u2": [40]})
        + trial("b", "u3", None, success=False, owner="core", index=2)
        + trial("a", "u3", 5, 1.0)
    )
    assert value_of(rows) == (round(mean_of([10, 20]), 3), 2, 1)
    assert value_of(rows, provider="b") == (round(mean_of([30, 40]), 3), 2, 1)


def test_a_provider_or_unknown_owned_failure_leaves_the_unit_in() -> None:
    for owner in ("provider", "unknown", None):
        rows = units("a", {"u1": [10], "u2": [20]}) + trial(
            "b", "u2", None, success=False, owner=owner
        )
        rows += units("b", {"u1": [30]})
        assert value_of(rows) == (round(mean_of([10, 20]), 3), 2, 0)


def test_a_failure_replaced_by_a_rerun_does_not_omit_the_unit() -> None:
    rows = units("a", {"u1": [10], "u2": [20]}) + trial(
        "a", "u2", None, success=False, owner="core", reason="replaced_by_top_up", index=2
    )
    assert value_of(rows) == (round(mean_of([10, 20]), 3), 2, 0)


def test_a_failure_in_another_variant_still_omits_the_unit() -> None:
    other = trial("a", "u2", None, success=False, owner="trace", index=2)
    for row in other:
        row["variant"] = "other"
    rows = units("a", {"u1": [10], "u2": [20]}) + other
    assert value_of(rows) == (10.0, 1, 1)


def test_a_success_without_a_value_leaves_the_unit_absent_for_the_provider() -> None:
    rows = (
        units("a", {"u1": [10]})
        + trial("a", "u2", None, 1.0, index=1)
        + trial("a", "u2", 30, 1.0, index=2)
        + units("b", {"u1": [20], "u2": [50]})
    )
    assert value_of(rows) == (round(mean_of([10, 50]), 3), 2, 0)


def test_cost_uses_the_cost_rows_of_the_units_the_time_rows_complete() -> None:
    rows = units("a", {"u1": [10, 30, 20]}, cost=0.001) + units(
        "b", {"u1": [8], "u2": [9]}, cost=0.01
    )
    found = derived_value(aggregate("a", "median", "cost", 6), rows)
    assert found == (round(mean_of([0.02, 0.09]), 6), 2, 0)


def test_cost_fill_is_the_highest_exact_cost_and_a_missing_cost_gives_no_value() -> None:
    rows = units("a", {"u1": [10]}, cost=0.01) + units("b", {"u1": [10], "u2": [10]}, cost=0.03)
    rows += units("c", {"u2": [10]}, cost=0.05)
    assert derived_value(aggregate("a", "median", "cost", 6), rows).value == round(
        mean_of([0.1, 0.5]), 6
    )
    uncosted = units("a", {"u1": [10]}) + trial("a", "u2", 10, None)
    assert derived_value(aggregate("a", "median", "cost", 6), uncosted).value is None


def test_unit_values_are_rounded_before_they_are_combined() -> None:
    rows = units("a", {"u1": [10.0004], "u2": [20.0004]})
    assert value_of(rows)[0] == round(mean_of([10.0, 20.0]), 3)


def test_reproduce_checks_a_derived_aggregate_in_a_data_tree(tmp_path: Path) -> None:
    raw = tmp_path / "data" / "r" / "raw"
    folder = tmp_path / "data" / "r" / "aggregates"
    raw.mkdir(parents=True)
    folder.mkdir()
    rows = units("a", {"u1": [10], "u2": [40]}) + units("b", {"u1": [20], "u2": [90]})
    (raw / "t-0000.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    good = {
        **aggregate("a", "median"),
        "value": 20.0,
        "n": 2,
        "excluded_n": 0,
        "unit": "ms",
        "interval": None,
        "precision": {"decimal_places": 3},
    }
    path = folder / "t-0000.jsonl"
    path.write_text(json.dumps(good) + "\n")
    assert reproduce(tmp_path) == []
    path.write_text(json.dumps({**good, "value": 21.0}) + "\n")
    assert reproduce(tmp_path) == [f"{path}: expected median 20.0, found 21.0"]
    path.write_text(json.dumps({**good, "n": 3}) + "\n")
    assert reproduce(tmp_path) == [f"{path}: expected n=2, found n=3"]
