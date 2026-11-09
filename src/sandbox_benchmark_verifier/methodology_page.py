# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Owner reader projection, separate from immutable scientific definitions."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from sandbox_benchmark_verifier.catalog import (
    common_document,
    context_region_errors,
    ordinary_documents,
)
from sandbox_benchmark_verifier.text_predicates import lookup
from sandbox_benchmark_verifier.versions import version_key


def omit_paths(definition: dict[str, Any], paths: Sequence[str]) -> None:
    for path in paths:
        parent_path, _, key = path.rpartition(".")
        target = definition
        for part in parent_path.split(".") if parent_path else []:
            target = target.get(part, {})
            if not isinstance(target, dict):
                break
        if isinstance(target, dict):
            target.pop(key, None)


def drop_unknown(value: Any) -> None:
    """Leave out every fact whose value is "unknown": the definition does not state it, so the
    reader has nothing to draw (a row reading "Unknown" is not a fact)."""
    if isinstance(value, dict):
        for key in [key for key, item in value.items() if item == "unknown"]:
            del value[key]
        for item in value.values():
            drop_unknown(item)
    elif isinstance(value, list):
        for item in value:
            drop_unknown(item)


def inherited_cost(
    document: Mapping[str, Any], documents: Mapping[tuple[str, str], Mapping[str, Any]]
) -> dict[str, Any]:
    """A document's cost block with the common methodology's statement filled in where it is silent.

    Only a document whose cost method is the common one takes it: a benchmark that publishes no
    cost, or whose cost is not applicable, keeps its own block.
    """
    cost = deepcopy(dict(document.get("cost") or {}))
    # Definitions released before sbs.common 1.0.6 state their whole cost block themselves.
    inherited = document.get("inherits")
    parent = (
        documents.get((inherited["id"], str(inherited["version"])))
        if isinstance(inherited, dict)
        else None
    )
    common = (parent or {}).get("cost")
    stated_by_common = parent is not None and (version_key(parent["version"]) >= (1, 0, 6))
    if (
        stated_by_common
        and isinstance(common, dict)
        and cost
        and cost.get("method") == common.get("method")
    ):
        for key, value in common.items():
            cost.setdefault(key, deepcopy(value))
    return cost


def _formulas(identifier: str, policy: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The formulas a document is shown with, in the owner's order: its own, and those listing it
    under ``tests``."""
    return [
        {"id": key, **{name: deepcopy(value) for name, value in row.items() if name != "tests"}}
        for key, row in policy["formulas"].items()
        if identifier == key or identifier in row.get("tests", [])
    ]


PAGE_KINDS = ("test", "index")  # the rows whose names-table `page` puts them on another's page


def _page_kind(rows: Sequence[Mapping[str, Any]], identifier: str) -> str:
    """Whether a page's test is a test or an index (the composites share one page)."""
    return next(
        (str(row["kind"]) for row in rows if row["kind"] in PAGE_KINDS and row["id"] == identifier),
        "test",
    )


def _page_metric_name(
    test: str, metric: Mapping[str, Any], alone: bool, policy: Mapping[str, Any], kind: str = "test"
) -> str:
    """A metric's heading on a page of several tests: its test's name, then, when the test has
    several metrics, the metric's own name."""
    own = policy["metric_names"].get(metric.get("name"), metric.get("name", ""))
    return f"{{name:{kind}.{test}}}" if alone else f"{{name:{kind}.{test}}}: {own}"


def member_documents(
    definition: Mapping[str, Any],
    host: str,
    documents: Mapping[tuple[str, str], Mapping[str, Any]],
    rows: Sequence[Mapping[str, Any]],
    document_aliases: Mapping[str, str],
) -> list[tuple[str, Mapping[str, Any]]]:
    """Page members as of this exact host, selected inside its inherited common first."""
    members = [row for row in rows if row["kind"] in PAGE_KINDS and row.get("page") == host]
    if not members:
        return []
    inherited = definition.get("inherits")
    if not isinstance(inherited, Mapping):
        raise ValueError("page members need the host's exact inherited common")
    parent = common_document(list(documents.values()), inherited)
    if str(parent["effective"]) > str(definition["effective"]):
        raise ValueError("page member common takes effect after its host")
    if errors := context_region_errors(definition, parent):
        raise ValueError("; ".join(errors))
    found = []
    for row in members:
        identity = document_aliases.get(row["document"], row["document"])
        candidates = [
            candidate
            for candidate in documents.values()
            if document_aliases.get(str(candidate["id"]), str(candidate["id"])) == identity
        ]
        selected = ordinary_documents(
            [parent, *({**candidate, "id": identity} for candidate in candidates)],
            common=inherited,
            effective=str(definition["effective"]),
        ).get(identity)
        if selected:
            original = next(
                candidate for candidate in candidates if candidate["version"] == selected["version"]
            )
            found.append((row["id"], original))
    return found


def _with_page_members(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    definition: dict[str, Any],
    host: str,
    documents: Mapping[tuple[str, str], Mapping[str, Any]],
    rows: Sequence[Mapping[str, Any]],
    document_aliases: Mapping[str, str],
    policy: Mapping[str, Any],
) -> None:
    """A page that draws several tests lists each test's metrics under the test's name, and
    their variants with its own.

    Each metric carries its `test`, whose Metrics paragraph the page draws under the test's
    heading; the page lists a test's metric names only where that paragraph does not name them
    (rule grouped-page-named-metrics).

    The tests are those whose names row says their page is the host test's. Each contributes its
    document's version in force when this version took effect; a version that predates every
    version of a member draws no member.
    """
    metrics: list[dict[str, Any]] = []
    variants: list[dict[str, Any]] = []
    for test, newest in member_documents(definition, host, documents, rows, document_aliases):
        held = newest.get("metrics", [])
        variants.extend(deepcopy(list(newest.get("procedure", {}).get("variants", []))))
        metrics.extend(
            {
                **deepcopy(dict(metric)),
                "test": test,
                "reader_name": _page_metric_name(
                    test, metric, len(held) == 1, policy, _page_kind(rows, test)
                ),
            }
            for metric in held
        )
    if metrics:
        own = definition.setdefault("metrics", [])
        for metric in own:
            metric["test"] = host
            metric["reader_name"] = _page_metric_name(
                host, metric, len(own) == 1, policy, _page_kind(rows, host)
            )
        own.extend(metrics)
    if variants:
        procedure = definition.setdefault("procedure", {})
        listed = procedure.setdefault("variants", [])
        listed.extend(variant for variant in variants if variant not in listed)


def reader_definition(  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
    document: Mapping[str, Any],
    documents: Mapping[tuple[str, str], Mapping[str, Any]],
    policy: Mapping[str, Any],
    canonical_id: str | None = None,
    names: Sequence[Mapping[str, Any]] = (),
    document_aliases: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Resolve inherited facts once and identify their source without changing records."""
    definition = deepcopy(dict(document))
    provenance: dict[str, Any] = {}
    inherited = definition.get("inherits")
    if isinstance(inherited, dict):
        parent = documents.get((inherited["id"], str(inherited["version"])))
        if parent is None:
            raise ValueError(f"unknown inherited methodology: {inherited['id']}")

        def fill(target: dict[str, Any], source: Mapping[str, Any], path: str) -> None:
            for key, value in source.items():
                child_path = f"{path}.{key}" if path else key
                if key not in target:
                    target[key] = deepcopy(value)
                    provenance[child_path] = dict(inherited)
                elif isinstance(target[key], dict) and isinstance(value, dict):
                    fill(target[key], value, child_path)

        # A calculation (a document that combines other documents' results) runs no trials of its
        # own, so it takes none of the shared trial's environment, clocks or lifecycle.
        template = policy.get("calculation_template", {})
        calculation = bool(template) and lookup(document, template["when"]) is not None
        fields = [
            key
            for key in policy["inherited_fields"]
            if not (calculation and key in template["not_inherited"])
        ]
        fill(definition, {key: parent[key] for key in fields if key in parent}, "")
    if "cost" in document:
        definition["cost"] = inherited_cost(document, documents)
    identifier = canonical_id or str(document["id"])
    if identifier in policy["document_names"]:
        definition["name"] = policy["document_names"][identifier]
    if identifier == "reliability" or identifier.startswith("reliability."):
        for metric in definition.get("metrics", []):
            if not metric.get("name"):
                metric["name"] = policy["metric_names"]["reliability"]
    for metric in definition.get("metrics", []):
        name = policy["metric_names"].get(metric.get("name"))
        if name:
            metric["reader_name"] = name
    procedure = definition.get("procedure", {})
    for key in ("variants", "stages"):
        if key in procedure:
            definition.pop(key, None)
    _with_page_members(definition, identifier, documents, names, document_aliases or {}, policy)
    if formulas := _formulas(identifier, policy):
        definition.setdefault("composition", {})["formulas"] = formulas
    omit_paths(
        definition,
        [*policy.get("private_evidence_paths", []), *policy.get("retired_reader_paths", [])],
    )
    drop_unknown(definition)
    definition["reader_sources"] = provenance
    return definition
