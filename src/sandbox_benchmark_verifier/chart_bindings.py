# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Explicit chart-to-output bindings for methodology documents."""

import json
from collections.abc import Mapping
from typing import Any

from sandbox_benchmark_verifier.catalog import _assets
from sandbox_benchmark_verifier.versions import version_key


def chart_bindings() -> dict[str, Any]:
    """Read the presentation bindings, one per document."""
    source: dict[str, Any] = json.loads(
        (_assets() / "contracts/text/chart-bindings.json").read_text(encoding="utf-8")
    )
    documents = [entry["document"] for entry in source["bindings"]]
    for document in sorted({name for name in documents if documents.count(name) > 1}):
        raise ValueError(f"chart binding {document} is declared twice")
    return source


def _derived_metric(
    document: str, name: str, definition: Mapping[str, Any], entry: Mapping[str, Any]
) -> dict[str, Any]:
    source: dict[str, Any] = {"document": document}
    if definition["kind"] == "cost":
        cost = entry.get("cost", {})
        if (
            not cost.get("per_success")
            or cost.get("method") == "not_published"
            or definition["unit"] != "USD"
        ):
            raise ValueError(f"{document}: per-success cost is undeclared or unit is invalid")
        source.update(field=definition["field"])
        quantity = "cost"
    else:
        terms = definition["terms"]
        if (
            set(terms) != set(entry.get("timed_steps", []))
            or set(terms.values()) - {"command-runtime", "request-elapsed"}
            or definition["unit"] != "ms"
        ):
            raise ValueError(f"{document}: composition must cover exactly the timed stages")
        source.update(field="procedure.steps", composition={"operator": "sum", "terms": terms})
        quantity = "time"
    return {
        "document": document,
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
    """Resolve charts and verified compositions without a first-metric fallback.

    Each chart names its metric by the name the document gives it.
    """
    bindings: dict[str, dict[str, dict[str, str]]] = {}
    derived: list[dict[str, Any]] = []
    for binding in chart_bindings()["bindings"]:
        document = binding["document"]
        if document not in bundle["methodologies"]:
            continue  # A synthetic conformance bundle can contain a subset of methods.
        versions = bundle["methodologies"][document]
        entry = versions[max(versions, key=version_key)]
        canonical = aliases.get("document", {}).get(document, document)
        charts, metrics = _document_charts(binding, entry, canonical)
        bindings[canonical] = charts
        derived.extend(metrics)
    return bindings, derived


def _document_charts(
    binding: Mapping[str, Any], entry: Mapping[str, Any], canonical: str
) -> tuple[dict[str, dict[str, str]], list[dict[str, Any]]]:
    derived = []
    metrics = {metric["name"] for metric in entry["text"].get("metrics", [])}
    for name, definition in binding.get("derived_metrics", {}).items():
        metric = _derived_metric(binding["document"], name, definition, entry)
        metric["document"] = canonical
        derived.append(metric)
        metrics.add(name)
    charts = {}
    for chart, target in binding["charts"].items():
        if target["metric"] not in metrics:
            raise ValueError(f"{canonical}/{chart}: unknown metric {target['metric']}")
        charts[chart] = {
            "metric": target["metric"],
            "statistic": target["statistic"],
        }
    return charts, derived
