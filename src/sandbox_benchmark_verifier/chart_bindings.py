# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Explicit chart-to-output bindings for immutable methodology tuples."""

import json
from collections.abc import Mapping
from typing import Any

from sandbox_benchmark_verifier.catalog import _assets


def chart_bindings() -> dict[str, Any]:
    """Read presentation bindings, one body per document and one entry per listed version.

    The file states each document's bodies once with the versions they apply to; this expands
    them to one binding per exact version, which is what compilation and consumers read.
    """
    source: dict[str, Any] = json.loads(
        (_assets() / "contracts/text/chart-bindings.json").read_text(encoding="utf-8")
    )
    bindings: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for entry in source["bindings"]:
        body = {key: value for key, value in entry.items() if key != "versions"}
        for version in entry["versions"]:
            if (entry["document"], version) in seen:
                raise ValueError(f"chart binding {entry['document']}@{version} is declared twice")
            seen.add((entry["document"], version))
            bindings.append({**body, "version": version})
    return {**source, "bindings": bindings}


def _derived_metric(
    document: str, version: str, name: str, definition: Mapping[str, Any], entry: Mapping[str, Any]
) -> dict[str, Any]:
    source: dict[str, Any] = {"document": document, "version": version}
    if definition["kind"] == "cost":
        cost = entry.get("cost", {})
        if (
            not cost.get("per_success")
            or cost.get("method") == "not_published"
            or definition["unit"] != "USD"
        ):
            raise ValueError(
                f"{document}@{version}: per-success cost is undeclared or unit is invalid"
            )
        source.update(field=definition["field"])
        quantity = "cost"
    else:
        terms = definition["terms"]
        if (
            set(terms) != set(entry.get("timed_steps", []))
            or set(terms.values()) - {"command-runtime", "request-elapsed"}
            or definition["unit"] != "ms"
        ):
            raise ValueError(
                f"{document}@{version}: composition must cover exactly the timed stages"
            )
        source.update(field="procedure.steps", composition={"operator": "sum", "terms": terms})
        quantity = "time"
    return {
        "document": document,
        "version": version,
        "name": name,
        "quantity": quantity,
        "unit": definition["unit"],
        "per": definition["per"],
        "direction": "lower_is_better",
        "statistic": definition["statistic"],
        "derived_from": source,
    }


def compile_chart_bindings(
    bundle: Mapping[str, Any], aliases: Mapping[str, Any]
) -> tuple[dict[str, dict[str, dict[str, str]]], list[dict[str, Any]]]:
    """Resolve charts and verified compositions without a first-metric fallback."""
    bindings: dict[str, dict[str, dict[str, str]]] = {}
    derived: list[dict[str, Any]] = []
    for binding in chart_bindings()["bindings"]:
        document, version = binding["document"], binding["version"]
        if document not in bundle["methodologies"]:
            continue  # A synthetic conformance bundle can contain a subset of methods.
        entry = bundle["methodologies"][document].get(version)
        if entry is None:
            raise ValueError(f"chart binding names an unknown methodology {document}@{version}")
        canonical = aliases.get("document", {}).get(document, document)
        charts, metrics = _document_charts(binding, entry, canonical)
        bindings[f"{canonical}@{version}"] = charts
        derived.extend(metrics)
    return bindings, derived


def _document_charts(
    binding: Mapping[str, Any], entry: Mapping[str, Any], canonical: str
) -> tuple[dict[str, dict[str, str]], list[dict[str, Any]]]:
    derived = []
    metrics = {metric["name"] for metric in entry["text"].get("metrics", [])}
    for name, definition in binding.get("derived_metrics", {}).items():
        metric = _derived_metric(binding["document"], binding["version"], name, definition, entry)
        metric["document"] = canonical
        derived.append(metric)
        metrics.add(name)
    charts = {}
    for chart, target in binding["charts"].items():
        if target["metric"] not in metrics:
            raise ValueError(
                f"{canonical}@{binding['version']}/{chart}: unknown metric {target['metric']}"
            )
        charts[chart] = {
            "metric": f"{canonical}@{binding['version']}/{target['metric']}",
            "statistic": target["statistic"],
        }
    return charts, derived
