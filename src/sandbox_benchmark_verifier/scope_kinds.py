# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Which visible panel kind each test and index of the canonical order draws."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def panel_scope_kind(row: Mapping[str, Any], rows: list[Any]) -> str | None:
    """Map the required slots in the text specification to the visible panel kind."""
    if row.get("kind") == "index":
        panel_kind = "time_vs_cost" if row.get("id") == "time-vs-cost" else "index"
    elif row.get("kind") != "test":
        panel_kind = None
    else:
        panel_kind = "sandbox_operations"
    parent_id = str(row.get("parent", ""))
    visited: set[str] = set()
    while (
        panel_kind == "sandbox_operations"
        and row.get("kind") == "test"
        and parent_id
        and parent_id not in visited
    ):
        visited.add(parent_id)
        parents = [
            item
            for item in rows
            if isinstance(item, Mapping)
            and item.get("id") == parent_id
            and item.get("kind") in {"section", "subsection", "tab"}
        ]
        if not parents:
            break
        parent = parents[0]
        if parent.get("id") in {"coding-workloads", "ci-workloads", "rl-workloads"}:
            panel_kind = "workload"
        if parent.get("id") == "machine-performance":
            panel_kind = "machine_performance"
        if parent.get("id") == "sandbox-operations":
            break
        parent_id = str(parent.get("parent", ""))
    return panel_kind


def scope_kinds(rows: list[Any]) -> dict[str, dict[str, str]]:
    return {
        kind: {
            str(row["id"]): scope
            for row in rows
            if row.get("kind") == kind
            if (scope := panel_scope_kind(row, rows)) is not None
        }
        for kind in ("test", "index")
    }
