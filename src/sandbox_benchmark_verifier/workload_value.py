# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Derived aggregates: values that no statistic over one provider's rows gives.

The one derivation today is the workload value, defined by `composition.per_input_value` in
`methodology/01-indexes/speed.yaml` and `cost.yaml`. It reads the rows of every provider of one
variant, so an aggregate that names it (`derivation`) selects the rows of the whole test, not of
its own provider. `DERIVATIONS` is the set the verifier recomputes; the schema owns the ids.
"""

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, NamedTuple

from sandbox_benchmark_verifier.catalog import load_schema
from sandbox_benchmark_verifier.stats import nearest_rank, numeric

DERIVATIONS: tuple[str, ...] = tuple(
    load_schema("aggregate")["properties"]["derivation"]["properties"]["id"]["enum"]
)
"""The derivation ids an aggregate may name and `derived_value` recomputes."""

UNIT_STATISTICS = ("median", "mean", "p95")
"""The statistics an aggregate with a derivation may name: each unit's value over its successful
trials."""

PROVIDER_OWNED = ("provider", "unknown")
"""The `composite_owner` values that leave a unit in the workload value; an absent one on a failed
row is unknown."""

REPLACED = "replaced_by_top_up"

Rows = Iterable[Mapping[str, Any]]
Units = dict[str, dict[str, list[float | None]]]
SCOPE = ("methodology", "test_id", "run_id")
"""The aggregate fields that select the rows of one test's run, whichever provider and variant."""


class Derived(NamedTuple):
    """A recomputed derived value: `value` (None where no value exists), the units it combines
    and the units omitted for a failure that is not the provider's."""

    value: float | None
    units: int
    omitted: int


class _Index:
    """One test's rows of one run, grouped once for every aggregate that reads them."""

    def __init__(self, rows: Rows) -> None:
        self.omitted: set[str] = set()
        self.units: dict[tuple[Any, str], Units] = defaultdict(lambda: defaultdict(dict))
        for row in rows:
            unit = str(row.get("workload"))
            if (
                row.get("success", True) is not True
                and row.get("exclusion_reason") != REPLACED
                and row.get("composite_owner", "unknown") not in PROVIDER_OWNED
            ):
                self.omitted.add(unit)
            held = self.units[(row.get("variant"), str(row.get("measurement")))]
            units = held[str(row.get("provider"))].setdefault(unit, [])
            if row.get("success", True) is True:
                units.append(numeric(row.get("value")))


def _scoped(aggregate: Mapping[str, Any], rows: Rows) -> list[Mapping[str, Any]]:
    return [
        row
        for row in rows
        if all(row.get(field) == aggregate[field] for field in SCOPE if field in aggregate)
    ]


def _unit_value(statistic: str, values: Sequence[float]) -> float:
    if statistic == "mean":
        return math.fsum(values) / len(values)
    return nearest_rank(values, 0.95 if statistic == "p95" else 0.5)


def _geometric_mean(values: Sequence[float]) -> float | None:
    if not values or any(value <= 0 for value in values):
        return None
    return math.exp(sum(math.log(value) for value in values) / len(values))


def _completed(held: Units) -> dict[str, dict[str, list[float]]]:
    """Each provider's completed units: a unit with a successful row, none of which lacks a value
    (a unit with such a row is absent for the provider)."""
    found: dict[str, dict[str, list[float]]] = {}
    for provider, units in held.items():
        for unit, values in units.items():
            if values and None not in values:
                found.setdefault(provider, {})[unit] = [v for v in values if v is not None]
    return found


def _provider_units(
    index: _Index, aggregate: Mapping[str, Any]
) -> dict[str, dict[str, float | None]]:
    """Each provider's value on each unit it completed (a cost None where a successful row of the
    unit has none), the units that carry a failure not the provider's left out."""
    derivation = aggregate["derivation"]
    variant, measurement = aggregate.get("variant"), str(aggregate["measurement"])
    statistic = str(aggregate["statistic"])
    values: dict[str, dict[str, float | None]] = {}
    for provider, units in _completed(
        index.units[(variant, str(derivation["completed_by"]))]
    ).items():
        kept = {unit: found for unit, found in units.items() if unit not in index.omitted}
        if not kept:
            continue
        if measurement == derivation["completed_by"]:
            values[provider] = {u: _unit_value(statistic, v) for u, v in kept.items()}
            continue
        costs = index.units[(variant, measurement)].get(provider, {})
        values[provider] = {
            u: None
            if not costs.get(u) or None in costs[u]
            else _unit_value(statistic, [c for c in costs[u] if c is not None])
            for u in kept
        }
    return values


def _combined(
    rounded: Mapping[str, Mapping[str, float | None]], provider: str
) -> list[float | None]:
    """The provider's value on every unit some provider completed; a unit it did not complete
    takes the highest value (the slowest, the most expensive) of the providers that did."""
    combined: list[float | None] = []
    for unit in sorted({u for units in rounded.values() for u in units}):
        if unit in rounded[provider]:
            combined.append(rounded[provider][unit])
            continue
        others = [v for units in rounded.values() if (v := units.get(unit)) is not None]
        combined.append(max(others) if others else None)
    return combined


def derived_value(
    aggregate: Mapping[str, Any],
    rows: Rows,
    cache: dict[tuple[Any, ...], Any] | None = None,
) -> Derived:
    """Recompute the workload value an aggregate with a `derivation` states.

    `rows` are the raw rows under the aggregate's `inputs.path`; the aggregate's methodology,
    test, run, variant and measurement select the rows of every provider. `cache` shares the
    grouping of one test's rows between aggregates."""
    derivation = aggregate["derivation"]
    places = int(derivation["decimal_places"])
    key = ("index", id(rows), repr([aggregate.get(field) for field in (*SCOPE, "inputs")]))
    shared = {} if cache is None else cache
    if key not in shared:
        shared[key] = _Index(_scoped(aggregate, rows))
    index: _Index = shared[key]
    variant_units = index.units[(aggregate.get("variant"), str(derivation["completed_by"]))]
    omitted = len(index.omitted & {u for units in variant_units.values() for u in units})
    values = _provider_units(index, aggregate)
    provider = str(aggregate["provider"])
    if provider not in values:
        return Derived(None, 0, omitted)
    rounded = {
        name: {u: None if v is None else round(v, places) for u, v in units.items()}
        for name, units in values.items()
    }
    combined = _combined(rounded, provider)
    mean = None if None in combined else _geometric_mean([v for v in combined if v is not None])
    return Derived(None if mean is None else round(mean, places), len(combined), omitted)
