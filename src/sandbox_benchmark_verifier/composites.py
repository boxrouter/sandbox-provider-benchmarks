# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Validate the input selection of a derived index."""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from sandbox_benchmark_verifier.catalog import (
    COMMON_ID,
    common_document,
    context_region_errors,
    ordinary_in_context,
)

__all__ = ["composition_input_errors", "select_composite_outputs"]


def _effective_date(document: Mapping[str, Any]) -> date | None:
    value = document.get("effective")
    if not isinstance(value, str):
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.isoformat() == value else None


def _input_errors(
    item: Any, index: Mapping[str, Any], by_id: Mapping[str, list[Mapping[str, Any]]]
) -> list[str]:
    if not isinstance(item, Mapping):
        return ["composition input must be an object"]
    identifier = item.get("id")
    if not isinstance(identifier, str):
        return ["composition input has no valid id"]

    matches = by_id.get(identifier, [])
    if not matches:
        return [f"unknown input: {identifier}"]
    if len(matches) != 1:
        return [f"duplicate input methodology: {identifier}"]

    index_date = _effective_date(index)
    errors: list[str] = []
    selected_date = _effective_date(matches[0])
    if selected_date is None:
        errors.append(f"input has an invalid effective date: {identifier}")
    elif index_date is not None and selected_date > index_date:
        errors.append(f"input is effective after index effective date: {identifier}")

    if not ordinary_in_context(matches[0]):
        errors.append(f"input does not inherit {COMMON_ID}: {identifier}")
    else:
        parents = by_id.get(COMMON_ID, [])
        if len(parents) == 1:
            errors.extend(context_region_errors(matches[0], parents[0]))
    return errors


def _plot_axis_errors(plots: Any, inputs: list[Any]) -> list[str]:
    """Require each plot axis to point to one member of its own input vector."""
    if plots is None:
        return []
    if not isinstance(plots, Mapping):
        return ["composition plots must be an object"]
    errors: list[str] = []
    for axis in ("x", "y"):
        reference = plots.get(axis)
        if not isinstance(reference, Mapping):
            errors.append(f"plot axis {axis} must be an object")
            continue
        identifier = reference.get("id")
        if not isinstance(identifier, str):
            errors.append(f"plot axis {axis} has no methodology reference")
            continue
        found = [
            item for item in inputs if isinstance(item, Mapping) and item.get("id") == identifier
        ]
        if len(found) > 1:
            errors.append(f"plot axis {axis} is ambiguous in composition inputs: {identifier}")
        elif not found:
            errors.append(f"plot axis {axis} is missing from composition inputs: {identifier}")
    return errors


def composition_input_errors(
    index: Mapping[str, Any], documents: Sequence[Mapping[str, Any]]
) -> list[str]:
    """Check that an index cites eligible methodology inputs.

    Documents are expected to have passed the methodology schema. The checks here
    repeat the owner-level relationships for consumers that decide eligibility.
    """
    composition = index.get("composition")
    if not isinstance(composition, Mapping):
        return ["index has no composition"]
    if index.get("category") != "index":
        return ["composition inputs require category index"]

    inputs = composition.get("inputs")
    if not isinstance(inputs, list) or not inputs:
        return ["composition inputs must be a non-empty list"]

    errors: list[str] = []
    if _effective_date(index) is None:
        errors.append("index has an invalid effective date")

    if not ordinary_in_context(index):
        errors.append(f"index must inherit {COMMON_ID}")

    by_id: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for document in documents:
        if isinstance(document.get("id"), str):
            by_id[document["id"]].append(document)

    for item in inputs:
        errors.extend(_input_errors(item, index, by_id))

    errors.extend(_plot_axis_errors(composition.get("plots"), inputs))

    return errors


def select_composite_outputs(
    documents: Sequence[Mapping[str, Any]], *, outputs: Sequence[Mapping[str, str]]
) -> dict[str, Mapping[str, Any]]:
    """Resolve only explicitly cited graphs.

    This establishes definition eligibility, not the availability of measured inputs. A consumer
    still validates the actual contributing leaves for each emitted scope before execution.
    """
    parent = common_document(documents)
    by_id: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for document in documents:
        by_id[str(document.get("id"))].append(document)
    selected: dict[str, Mapping[str, Any]] = {}
    visiting: set[str] = set()

    def visit(reference: Mapping[str, str]) -> Mapping[str, Any]:
        key = reference["id"]
        if key in visiting:
            raise ValueError("selected composition has a dependency cycle")
        matches = by_id.get(key, [])
        if len(matches) != 1:
            raise ValueError(f"missing or ambiguous output input: {key}")
        document = matches[0]
        if not ordinary_in_context(document):
            raise ValueError(f"selected input common mismatch: {key}")
        if errors := context_region_errors(document, parent):
            raise ValueError("; ".join(errors))
        if key in selected:
            return document
        visiting.add(key)
        if "composition" in document:
            if errors := composition_input_errors(document, documents):
                raise ValueError("; ".join(errors))
            for item in document["composition"]["inputs"]:
                visit(item)
        visiting.remove(key)
        selected[key] = document
        return document

    for reference in outputs:
        if (
            "id" not in reference
            or set(reference) - {"id", "scope", "input"}
            or {"scope", "input"} <= reference.keys()
            or any(not isinstance(value, str) or not value for value in reference.values())
        ):
            raise ValueError("output needs a reference and at most one owner selector")
        document = visit(reference)
        if document.get("category") != "index":
            raise ValueError("selected output is not an index")
        composition = document["composition"]
        if "scope" in reference:
            scopes = [*composition.get("scopes", []), *composition.get("levels", [])]
            if reference["scope"] not in {row["id"] for row in scopes}:
                raise ValueError("output names no owner scope")
        if "input" in reference and reference["input"] not in {
            row["id"] for row in composition["inputs"]
        }:
            raise ValueError("output names no owner input")
    return selected
