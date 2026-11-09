# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""A names row carries none of the labels it rejects."""

from pathlib import Path
from typing import Any

from sandbox_benchmark_verifier.names import load_tables
from sandbox_benchmark_verifier.rejected_labels import rejected_label_errors

ROOT = Path(__file__).resolve().parents[1]


def test_no_names_row_carries_a_label_it_rejects() -> None:
    names = load_tables(ROOT).names
    assert any(row.get("rejected_labels") for row in names)
    assert rejected_label_errors(names) == []


def test_a_row_name_label_or_selector_label_that_is_rejected_is_refused() -> None:
    rows: list[dict[str, Any]] = [
        {
            "kind": "hero",
            "id": "a",
            "name": "Execution speed",
            "rejected_labels": ["Execution Speed"],
        },
        {
            "kind": "test",
            "id": "b",
            "name": "B",
            "selector": {"label": "Cold Start"},
            "rejected_labels": ["cold start"],
        },
        {"kind": "test", "id": "c", "name": "Cold Start"},
    ]
    assert rejected_label_errors(rows) == [
        "names hero.a.name is a rejected label: 'Execution speed'",
        "names test.b.selector.label is a rejected label: 'Cold Start'",
    ]
