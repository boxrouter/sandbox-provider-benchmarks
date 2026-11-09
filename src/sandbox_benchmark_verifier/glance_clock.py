# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Build the At a Glance Clock row from the names table's labels.

A method's `clock` (reader/methods.yaml) names its clocks by id; the names table's `clock`
rows own each label. Two clocks use the owner's list style. Common's `clocks` section describes
what each clock is and how it is read.
"""

from collections.abc import Iterable, Mapping, Sequence
from itertools import permutations
from typing import Any

from sandbox_benchmark_verifier.substitution import list_text

RULE_ID = "glance-clock-label"


def clock_labels(names: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    """Each clock's label in the names table, by clock id."""
    return {str(row["id"]): str(row["name"]) for row in names if row["kind"] == "clock"}


def clock_row(clocks: Sequence[str], labels: Mapping[str, str]) -> str:
    """The Clock row of a method that names these clocks: their labels, in the list style."""
    return list_text([labels[clock] for clock in clocks])


def glance_clock_errors(bundle: Mapping[str, Any]) -> list[str]:
    """Every version's Clock row (``clocks.headline``) is exactly the names table's clock labels."""
    labels = list(clock_labels(bundle["names"]).values())
    allowed = {
        list_text(list(order))
        for size in range(1, len(labels) + 1)
        for order in permutations(labels, size)
    }
    return [
        f"{RULE_ID}: {identifier}@{version} clocks.headline is {value!r}, not the names table's "
        "clock labels"
        for identifier, versions in bundle.get("methodologies", {}).items()
        for version, row in versions.items()
        if (value := (row.get("reader_definition") or {}).get("clocks", {}).get("headline"))
        is not None
        and not (isinstance(value, str) and value in allowed)
    ]
