# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Validate the exact input selection of a derived index."""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from sandbox_benchmark_verifier.catalog import (
    LATEST_AS_OF_EFFECTIVE,
    RETROSPECTIVE_EXACT,
    common_document,
    context_region_errors,
    ordinary_in_context,
)
from sandbox_benchmark_verifier.independence import COMMON_ID
from sandbox_benchmark_verifier.versions import version_key

__all__ = [
    "LATEST_AS_OF_EFFECTIVE",
    "RETROSPECTIVE_EXACT",
    "composition_input_errors",
    "select_composite_outputs",
]

INPUT_SELECTIONS = frozenset({LATEST_AS_OF_EFFECTIVE, RETROSPECTIVE_EXACT})


def _effective_date(document: Mapping[str, Any]) -> date | None:
    value = document.get("effective")
    if not isinstance(value, str):
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.isoformat() == value else None


def _common_ref(document: Mapping[str, Any]) -> tuple[str, str] | None:
    inherited = document.get("inherits")
    if not isinstance(inherited, Mapping):
        return None
    identifier, version = inherited.get("id"), inherited.get("version")
    if isinstance(identifier, str) and isinstance(version, str):
        return identifier, version
    return None


def _latest_version(
    identifier: str,
    index_date: date,
    documents: Sequence[Mapping[str, Any]],
    common: Mapping[str, str],
) -> str | None:
    available: list[tuple[tuple[int, ...], str]] = []
    for document in documents:
        if document.get("id") != identifier:
            continue
        if not ordinary_in_context(document, common):
            continue
        effective = _effective_date(document)
        version = document.get("version")
        if effective is None or effective > index_date or not isinstance(version, str):
            continue
        try:
            key = version_key(version)
        except ValueError:
            continue
        available.append((key, version))
    return max(available, key=lambda candidate: candidate[0])[1] if available else None


def _common_error(
    reference_name: str,
    index_common: tuple[str, str] | None,
    selected: Mapping[str, Any],
) -> str | None:
    selected_common = _common_ref(selected)
    if selected_common is None or selected_common[0] != COMMON_ID:
        return f"exact input does not inherit {COMMON_ID}: {reference_name}"
    if index_common is not None and selected_common != index_common:
        return f"input common mismatch: {reference_name}"
    return None


def _input_region_errors(
    index: Mapping[str, Any],
    selected: Mapping[str, Any],
    by_ref: Mapping[tuple[str, str], list[Mapping[str, Any]]],
) -> list[str]:
    reference = _common_ref(index)
    if reference is None or _common_ref(selected) != reference:
        return []
    parents = by_ref.get(reference, [])
    return context_region_errors(selected, parents[0]) if len(parents) == 1 else []


def _input_errors(
    item: Any,
    index: Mapping[str, Any],
    mode: str,
    by_ref: Mapping[tuple[str, str], list[Mapping[str, Any]]],
    documents: Sequence[Mapping[str, Any]],
) -> list[str]:
    if not isinstance(item, Mapping):
        return ["composition input must be an object"]
    identifier, version = item.get("id"), item.get("version")
    if not isinstance(identifier, str):
        return ["composition input has no valid id"]
    if not isinstance(version, str):
        return [f"input {identifier} has no version"]

    reference_name = f"{identifier}@{version}"
    matches = by_ref.get((identifier, version), [])
    if not matches:
        return [f"unknown exact input: {reference_name}"]
    if len(matches) != 1:
        return [f"duplicate exact input methodology: {reference_name}"]

    index_date = _effective_date(index)
    errors: list[str] = []
    selected_date = _effective_date(matches[0])
    if selected_date is None:
        errors.append(f"exact input has an invalid effective date: {reference_name}")
    elif index_date is not None and selected_date > index_date:
        errors.append(f"input is effective after index effective date: {reference_name}")

    common_error = _common_error(reference_name, _common_ref(index), matches[0])
    if common_error is not None:
        errors.append(common_error)
    errors.extend(_input_region_errors(index, matches[0], by_ref))
    if mode == LATEST_AS_OF_EFFECTIVE and index_date is not None:
        latest = _latest_version(
            identifier, index_date, documents, dict(index.get("inherits") or {})
        )
        if latest is None:
            errors.append(f"no input version is effective by index date: {identifier}")
        elif version != latest:
            errors.append(f"input is not latest as of index effective date: {reference_name}")
    return errors


def _plot_axis_errors(plots: Any, inputs: list[Any]) -> list[str]:
    """Require each plot axis to point to one exact member of its own input vector."""
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
        identifier, version = reference.get("id"), reference.get("version")
        if not isinstance(identifier, str) or not isinstance(version, str):
            errors.append(f"plot axis {axis} has no exact methodology reference")
            continue
        same_id = [
            item for item in inputs if isinstance(item, Mapping) and item.get("id") == identifier
        ]
        exact = [item for item in same_id if item.get("version") == version]
        label = f"{identifier}@{version}"
        if len(exact) > 1:
            errors.append(f"plot axis {axis} is ambiguous in composition inputs: {label}")
        elif len(exact) == 1:
            continue
        elif same_id:
            errors.append(f"plot axis {axis} is stale in composition inputs: {label}")
        else:
            errors.append(f"plot axis {axis} is missing from composition inputs: {label}")
    return errors


def composition_input_errors(
    index: Mapping[str, Any], documents: Sequence[Mapping[str, Any]]
) -> list[str]:
    """Check that an index cites eligible, exact methodology inputs.

    Documents are expected to have passed the methodology schema. The checks here
    repeat the owner-level relationships for consumers that decide eligibility.
    """
    composition = index.get("composition")
    if not isinstance(composition, Mapping):
        return ["index has no composition"]
    if index.get("category") != "index":
        return ["input selection requires category index"]

    mode = composition.get("input_selection", LATEST_AS_OF_EFFECTIVE)
    if not isinstance(mode, str) or mode not in INPUT_SELECTIONS:
        return ["unknown input selection"]

    inputs = composition.get("inputs")
    if not isinstance(inputs, list) or not inputs:
        return ["composition inputs must be a non-empty list"]

    index_date = _effective_date(index)
    errors: list[str] = []
    if index_date is None:
        errors.append("index has an invalid effective date")

    index_common = _common_ref(index)
    if index_common is None or index_common[0] != COMMON_ID:
        errors.append(f"index must inherit a version of {COMMON_ID}")

    by_ref: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for document in documents:
        identifier, version = document.get("id"), document.get("version")
        if isinstance(identifier, str) and isinstance(version, str):
            by_ref[(identifier, version)].append(document)

    for item in inputs:
        errors.extend(_input_errors(item, index, mode, by_ref, documents))

    errors.extend(_plot_axis_errors(composition.get("plots"), inputs))

    return errors


def select_composite_outputs(
    documents: Sequence[Mapping[str, Any]],
    *,
    common: Mapping[str, str],
    outputs: Sequence[Mapping[str, str]],
) -> dict[str, Mapping[str, Any]]:
    """Resolve only explicitly cited graphs inside their exact common context.

    This establishes definition eligibility, not the availability of measured inputs. A consumer
    still validates the actual contributing leaves for each emitted scope before execution.
    """
    parent = common_document(documents, common)
    by_ref: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for document in documents:
        by_ref[(str(document.get("id")), str(document.get("version")))].append(document)
    selected: dict[str, Mapping[str, Any]] = {}
    visiting: set[tuple[str, str]] = set()

    def visit(reference: Mapping[str, str]) -> Mapping[str, Any]:
        key = reference["id"], reference["version"]
        if key in visiting:
            raise ValueError("selected composition has a dependency cycle")
        matches = by_ref.get(key, [])
        if len(matches) != 1:
            raise ValueError(f"missing or ambiguous exact output input: {key}")
        document = matches[0]
        if document.get("inherits") != common:
            raise ValueError(f"selected input common mismatch: {key}")
        if errors := context_region_errors(document, parent):
            raise ValueError("; ".join(errors))
        if key[0] in selected:
            if selected[key[0]]["version"] != key[1]:
                raise ValueError(f"conflicting selected input versions: {key[0]}")
            return document
        visiting.add(key)
        if "composition" in document:
            if errors := composition_input_errors(document, documents):
                raise ValueError("; ".join(errors))
            for item in document["composition"]["inputs"]:
                visit(item)
        visiting.remove(key)
        selected[key[0]] = document
        return document

    for reference in outputs:
        if (
            not {"id", "version"} <= reference.keys()
            or set(reference) - {"id", "version", "scope", "input"}
            or {"scope", "input"} <= reference.keys()
            or any(not isinstance(value, str) or not value for value in reference.values())
        ):
            raise ValueError("output needs an exact reference and at most one owner selector")
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
