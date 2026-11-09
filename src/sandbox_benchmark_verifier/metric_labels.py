# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Compile source metric grammar once for all label consumers."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from sandbox_benchmark_verifier.chart_bindings import compile_chart_bindings

_SLOT = re.compile(r"\{([\w.]+)(\?)?\}")


def _render(template: str, values: Mapping[str, str]) -> str:
    def value(match: re.Match[str]) -> str:
        key = match.group(1)
        if key in values:
            return values[key]
        if match.group(2):
            return ""
        return match.group(0)

    return " ".join(_SLOT.sub(value, template).split()).strip()


def _name(rows: list[dict[str, Any]], reference: Mapping[str, Any]) -> str | None:
    matches = [
        row
        for row in rows
        if all(
            row.get(key) == reference[key] for key in ("kind", "id", "parent") if key in reference
        )
    ]
    return str(matches[0]["name"]) if len(matches) == 1 else None


def metric_id(document_id: str, version: str, name: str) -> str:
    """The qualified reference, derived from existing immutable source identities."""
    return f"{document_id}@{version}/{name}"


def group_metrics(metrics: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One entry per document metric and run of versions whose entries are equal apart from the
    version, in the order each first appears: a release adds an entry only where a label changed."""
    groups: dict[str, dict[str, Any]] = {}
    for identity, metric in metrics.items():
        rest = {key: value for key, value in metric.items() if key != "version"}
        if identity != metric_id(rest["document"], metric["version"], rest["name"]):
            raise ValueError(f"{identity} does not name its own document, version and metric")
        key = json.dumps(rest, sort_keys=True)
        groups.setdefault(key, {**rest, "versions": []})["versions"].append(metric["version"])
    return list(groups.values())


def expand_metric_labels(catalog: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Every metric entry by its qualified reference, as the groups hold them."""
    return {
        metric_id(group["document"], version, group["name"]): {
            **{key: value for key, value in group.items() if key != "versions"},
            "version": version,
        }
        for group in catalog.get("metric_groups", [])
        for version in group["versions"]
    }


def metric_entry(catalog: Mapping[str, Any], identity: str) -> dict[str, Any] | None:
    """The entry of one qualified reference, read from its group."""
    document, _, rest = identity.partition("@")
    version, _, name = rest.partition("/")
    for group in catalog.get("metric_groups", []):
        if group["document"] == document and group["name"] == name and version in group["versions"]:
            return {
                **{key: value for key, value in group.items() if key != "versions"},
                "version": version,
            }
    return None


def _templates(
    metric: Mapping[str, Any],
    grammar: Mapping[str, Any],
    rows: list[dict[str, Any]],
    statistic_word: str = "{statistic}",
) -> dict[str, str]:
    quantity = grammar["quantities"].get(metric.get("quantity"), {})
    word = _name(rows, quantity.get("name_ref", {})) if quantity else None
    if (
        not word
        or not metric.get("unit")
        or metric.get("direction") not in grammar["tokens"]["directions"]
    ):
        return {}
    phase_ref = metric.get("phase")
    phase = _name(rows, phase_ref) if isinstance(phase_ref, Mapping) else ""
    if isinstance(phase_ref, Mapping) and not phase:
        return {}
    per = _name(rows, {"kind": "per-unit", "id": metric["per"]}) if metric.get("per") else ""
    if metric.get("per") and not per:
        return {}
    per = per or ""
    # A rate's sampling unit does not introduce another mathematical denominator.
    if metric.get("quantity") not in ("time", "cost") and metric.get("per") == "trial":
        per = ""
    result = {}
    for form, spec in grammar["forms"].items():
        title = spec["case"] == "title"
        per_word = per.title() if title else per
        bindings = {
            "quantity": word if title else word.lower(),
            "phase": phase or "",
            "phase_if_ambiguous": phase or "",
            "per_clause": " " + _render(grammar["tokens"]["per_clause"], {"per": per_word})
            if per
            else "",
            "unit_clause": ""
            if metric["unit"] in grammar["tokens"].get("unitless_units", [])
            else grammar["tokens"]["unit_clause"],
            "unit_clause_if_needed": "",
            "statistic": statistic_word,
            "direction": grammar["tokens"]["directions"][metric["direction"]],
        }
        result[form] = _render(spec["template"], bindings)
    return result


def _labels(
    metric: Mapping[str, Any],
    grammar: Mapping[str, Any],
    rows: list[dict[str, Any]],
    identities: Mapping[str, str],
    composite_word: str | None = None,
) -> dict[str, dict[str, str]]:
    statistic_word = composite_word if "index" in identities and composite_word else "{statistic}"
    labels = {
        form: {"metric": value}
        for form, value in _templates(metric, grammar, rows, statistic_word).items()
    }
    for target, identity in identities.items():
        labels.setdefault("long", {})[target] = identity
        if "short" in labels:
            labels["short"][target] = labels["short"]["metric"]
        for form in ("axis", "caption"):
            if form in labels:
                labels[form][target] = labels[form]["metric"]
    return labels


def _declared_quantities(
    bundle: Mapping[str, Any], grammar: Mapping[str, Any]
) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for versions in bundle["methodologies"].values():
        for entry in versions.values():
            for metric in entry["text"].get("metrics", []):
                if metric.get("unit") and metric.get("quantity") in grammar["quantities"]:
                    found.setdefault(metric["unit"], set()).add(metric["quantity"])
    return found


def _display_metric(
    metric: Mapping[str, Any],
    grammar: Mapping[str, Any],
    rows: list[dict[str, Any]],
    quantities: Mapping[str, set[str]],
) -> dict[str, Any]:
    resolved = dict(metric)
    if resolved.get("quantity") not in grammar["quantities"]:
        candidates = quantities.get(resolved.get("unit", ""), set())
        if len(candidates) == 1:
            resolved["quantity"] = next(iter(candidates))
        elif "time" in candidates:
            # A unit shared by time and a specific noun (latency, in ms) stays time
            # where the metric names no quantity.
            resolved["quantity"] = "time"
    if resolved.get("per"):
        matches = {
            row["id"]
            for row in rows
            if row["kind"] == "per-unit" and resolved["per"] in (row["id"], row["name"])
        }
        if len(matches) == 1:
            row = next(r for r in rows if r["kind"] == "per-unit" and r["id"] in matches)
            # A locked definition may count "per session"; the reader reads the unit word.
            resolved["per"] = row.get("reads_as", row["id"])
    return resolved


def compile_metric_labels(bundle: Mapping[str, Any], aliases: Mapping[str, Any]) -> dict[str, Any]:
    """Project immutable document/version metrics into source-owned label templates."""
    grammar = bundle.get("metric_label_grammar")
    if not isinstance(grammar, Mapping) or "tokens" not in grammar:
        return {"metric_groups": [], "statistics": {}, "contexts": {}}
    rows = list(bundle["names"])
    mapped = aliases.get("document", {})
    quantities = _declared_quantities(bundle, grammar)
    metrics: dict[str, Any] = {}
    for document_id, versions in bundle["methodologies"].items():
        canonical = mapped.get(document_id, document_id)
        identities = {
            str(row["kind"]): str(row["name"])
            for row in rows
            if mapped.get(row.get("document", row["id"]), row.get("document", row["id"]))
            == canonical
            and row["kind"] in ("test", "index", "hero")
        }
        for version, entry in versions.items():
            for metric in entry["text"].get("metrics", []):
                if metric.get("name"):
                    metric["id"] = metric_id(canonical, version, metric["name"])
                    display = _display_metric(metric, grammar, rows, quantities)
                    metrics[metric["id"]] = {
                        "target": "index" if "index" in identities else "metric",
                        "document": canonical,
                        "version": version,
                        "name": metric["name"],
                        "quantity": display.get("quantity"),
                        "unit": metric.get("unit"),
                        "statistic": metric.get("statistic"),
                        "labels": _labels(
                            display,
                            grammar,
                            rows,
                            identities,
                            bundle.get("content_criteria", {})
                            .get("index_copy", {})
                            .get("caption_statistic"),
                        ),
                        "direction": grammar["tokens"]["directions"].get(metric.get("direction")),
                    }
    return {
        # Binding the charts adds their derived metrics, so it runs before the grouping.
        "chart_bindings": _bind_charts(bundle, aliases, grammar, rows, metrics),
        "metric_groups": group_metrics(metrics),
        "comparison_template": grammar["forms"]["short"]["comparison_template"],
        "statistics": {
            str(row["id"]): str(row["name"]) for row in rows if row["kind"] == "statistic"
        },
        "statistics_sentence": {
            row["id"]: row["name"].lower() for row in rows if row["kind"] == "statistic"
        },
        "contexts": {
            key: {field: value[field] for field in ("form", "target") if field in value}
            for key, value in grammar["contexts"].items()
        },
    }


def _bind_charts(
    bundle: Mapping[str, Any],
    aliases: Mapping[str, Any],
    grammar: Mapping[str, Any],
    rows: list[dict[str, Any]],
    metrics: dict[str, Any],
) -> dict[str, dict[str, dict[str, str]]]:
    charts, derived = compile_chart_bindings(bundle, aliases)
    for metric in derived:
        identity = metric_id(metric["document"], metric["version"], metric["name"])
        metrics[identity] = {
            **metric,
            "target": "metric",
            "labels": _labels(metric, grammar, rows, {}),
        }
    return charts


def label_for_chart(
    catalog: Mapping[str, Any],
    method: tuple[str, str],
    chart: str,
    form: str,
    context: Mapping[str, str] | None = None,
) -> str | None:
    """Use the exact output and aggregate declared for this chart."""
    binding = catalog.get("chart_bindings", {}).get(f"{method[0]}@{method[1]}", {}).get(chart)
    if binding is None:
        return None
    selected = dict(context or {})
    if binding["statistic"] != "selected":
        selected["statistic"] = binding["statistic"]
    return label_for(catalog, binding["metric"], form, selected)


def label_for(
    catalog: Mapping[str, Any], identity: str, form: str, context: Mapping[str, str] | None = None
) -> str | None:
    """Resolve a compiled label without reimplementing grammar in a consumer."""
    selected = dict(context or {})
    metric = metric_entry(catalog, identity)
    if not metric:
        return None
    comparison = selected.get("comparison")
    if comparison and form == "short":
        left = label_for(catalog, identity, form)
        right = label_for(catalog, comparison, form)
        if not left or not right:
            return None
        return _render(
            catalog["comparison_template"], {"left.quantity": left, "right.quantity": right}
        )
    template = (
        metric["labels"].get(form, {}).get(selected.get("target", metric.get("target", "metric")))
    )
    if not template:
        return None
    values = {"unit": selected.get("unit", metric.get("unit") or "")}
    statistic = selected.get("statistic", metric.get("statistic"))
    if statistic in catalog["statistics"]:
        values["statistic"] = catalog["statistics"][statistic]
    if form == "caption" and statistic in catalog.get("statistics_sentence", {}):
        values["statistic"] = catalog["statistics_sentence"][statistic]
    result = _render(template, values)
    return None if _SLOT.search(result) else result
