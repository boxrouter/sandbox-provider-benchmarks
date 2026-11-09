# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Source grammar projections preserve identity and numerical meaning."""

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from sandbox_benchmark_verifier import metric_labels
from sandbox_benchmark_verifier.chart_bindings import chart_bindings
from sandbox_benchmark_verifier.metric_labels import (
    compile_metric_labels,
    expand_metric_labels,
    label_for,
    label_for_chart,
)
from sandbox_benchmark_verifier.names import load_tables
from sandbox_benchmark_verifier.retained import retained_versions
from sandbox_benchmark_verifier.versions import version_key

ROOT = Path(__file__).resolve().parents[1]


def _current_version(bundle: dict[str, Any], identifier: str) -> str:
    methodology_id = identifier
    if methodology_id not in bundle["methodologies"]:
        methodology_id = next(
            old
            for old, current in load_tables(ROOT).aliases.get("document", {}).items()
            if current == identifier and old in bundle["methodologies"]
        )
    version = max(bundle["methodologies"][methodology_id], key=version_key)
    assert isinstance(version, str)
    return version


def test_index_forms_and_statistic_are_source_owned(bundle: dict[str, Any]) -> None:
    row = next(
        row for row in bundle["names"] if row["kind"] == "index" and row["id"] == "time-index"
    )
    metric_id = next(
        key
        for key in expand_metric_labels(bundle["metric_labels"])
        if key.startswith("time-index@")
        and "axis" in expand_metric_labels(bundle["metric_labels"])[key]["labels"]
    )
    catalog = bundle["metric_labels"]
    assert label_for(catalog, metric_id, "long", {"target": "index"}) == row["name"]
    assert label_for(catalog, metric_id, "axis", {"target": "index", "unit": "s"}) == label_for(
        catalog, metric_id, "axis", {"target": "metric", "unit": "s"}
    )
    caption = label_for(catalog, metric_id, "caption", {"target": "index", "statistic": "mean"})
    assert caption == "The chart shows composite values per provider; lower is better."
    assert caption == label_for(
        catalog, metric_id, "caption", {"target": "metric", "statistic": "mean"}
    )
    assert caption.endswith(".") and ";" in caption


def test_compiler_uses_quantity_reference_and_nullable_denominator(bundle: dict[str, Any]) -> None:
    source = deepcopy(bundle)
    key = next(key for key in source["methodologies"] if key == "time-index")
    version = next(iter(source["methodologies"][key]))
    metric = source["methodologies"][key][version]["text"]["metrics"][0]
    metric.update(quantity="time", per=None, unit="ms", direction="lower_is_better")
    for row in source["names"]:
        if row["kind"] == "view" and row["id"] == "time":
            row["name"] = "Interval"
    catalog = compile_metric_labels(source, load_tables(ROOT).aliases)
    identity = f"{key}@{version}/{metric['name']}"
    assert label_for(catalog, identity, "short") == "Interval"
    assert label_for(catalog, identity, "axis", {"target": "metric"}) == "Interval (ms)"


def test_rate_sampling_does_not_introduce_another_divisor(bundle: dict[str, Any]) -> None:
    source = deepcopy(bundle)
    document_id = "stream-triad"
    versions = source["methodologies"][document_id]
    version = next(reversed(versions))
    metric = versions[version]["text"]["metrics"][0]
    catalog = compile_metric_labels(source, load_tables(ROOT).aliases)
    result = label_for(catalog, f"{document_id}@{version}/{metric['name']}", "axis")

    assert metric["unit"] == "GB/s"
    assert result is not None and result == "Bandwidth (GB/s)"
    assert "trial" not in result.lower()


def test_deepswe_current_version_has_source_owned_time_and_cost_axes(
    bundle: dict[str, Any],
) -> None:
    catalog = bundle["metric_labels"]
    time_axis = label_for_chart(
        catalog, ("deepswe", _current_version(bundle, "deepswe")), "time-trace", "axis"
    )
    cost_axis = label_for_chart(
        catalog, ("deepswe", _current_version(bundle, "deepswe")), "cost-trace", "axis"
    )
    assert time_axis == "Time (ms)"
    assert cost_axis == "Cost (USD)"
    assert (
        label_for_chart(
            catalog, ("deepswe", _current_version(bundle, "deepswe")), "cost-trace", "caption"
        )
        == "The chart shows mean values per trace; lower is better."
    )


def test_missing_required_interpolation_and_unknown_identity_are_not_shown(
    bundle: dict[str, Any],
) -> None:
    catalog = bundle["metric_labels"]
    identity = next(
        key for key, row in expand_metric_labels(catalog).items() if "caption" in row["labels"]
    )
    assert label_for(catalog, identity, "caption", {"statistic": "unresolved"}) is None
    assert label_for(catalog, "unresolved", "axis") is None


def test_short_view_and_hero_are_different_source_contexts(bundle: dict[str, Any]) -> None:
    catalog = bundle["metric_labels"]
    identity = next(
        key
        for key in expand_metric_labels(catalog)
        if key.startswith("sequential-tti@")
        and "hero" in expand_metric_labels(catalog)[key]["labels"].get("long", {})
    )
    hero = next(
        row["name"]
        for row in bundle["names"]
        if row["kind"] == "hero" and row["id"] == "sequential-tti"
    )
    assert label_for(catalog, identity, "long", {"target": "hero"}) == hero


def test_comparison_reuses_each_source_quantity(bundle: dict[str, Any]) -> None:
    catalog = bundle["metric_labels"]
    left = next(
        key
        for key, row in expand_metric_labels(catalog).items()
        if row["document"] == "time-index" and row.get("quantity") == "time"
    )
    right = next(
        key
        for key, row in expand_metric_labels(catalog).items()
        if row["document"] == "cost-index" and row.get("quantity") == "cost"
    )
    expected = (
        catalog["comparison_template"]
        .replace("{left.quantity}", label_for(catalog, left, "short") or "")
        .replace("{right.quantity}", label_for(catalog, right, "short") or "")
    )
    assert label_for(catalog, left, "short", {"comparison": right}) == expected
    assert label_for(catalog, left, "short", {"comparison": "unknown"}) is None


def test_legacy_display_uses_only_unambiguous_declared_dimensions(bundle: dict[str, Any]) -> None:
    source = deepcopy(bundle)
    versions = source["methodologies"]["time-index"]
    version = next(iter(versions))
    metric = versions[version]["text"]["metrics"][0]
    metric.update(quantity="Historical interval wording", per=None)
    identity = metric["id"]
    catalog = compile_metric_labels(source, load_tables(ROOT).aliases)
    expected = next(
        row["name"] for row in source["names"] if row["kind"] == "view" and row["id"] == "time"
    )
    assert label_for(catalog, identity, "short") == expected
    metric["unit"] = "unregistered-unit"
    catalog = compile_metric_labels(source, load_tables(ROOT).aliases)
    assert label_for(catalog, identity, "short") is None


def test_legacy_denominator_resolves_its_existing_name_row(bundle: dict[str, Any]) -> None:
    source = deepcopy(bundle)
    metric = next(reversed(source["methodologies"]["time-index"].values()))["text"]["metrics"][0]
    row = next(
        row for row in source["names"] if row["kind"] == "per-unit" and row["id"] == "trace-set"
    )
    metric["per"] = row["name"]
    catalog = compile_metric_labels(source, load_tables(ROOT).aliases)
    assert row["name"].title() in (
        label_for(catalog, metric["id"], "long", {"target": "metric"}) or ""
    )


def test_each_resolved_caption_is_a_source_complete_sentence(bundle: dict[str, Any]) -> None:
    catalog = bundle["metric_labels"]
    metrics = expand_metric_labels(catalog)
    for identity in metrics:
        caption = label_for(catalog, identity, "caption", {"statistic": "median"})
        axis = label_for(catalog, identity, "axis")
        if caption:
            assert caption.endswith(".")
            assert "; " in caption
            aggregate = (
                bundle["content_criteria"]["index_copy"]["caption_statistic"]
                if metrics[identity]["target"] == "index"
                else catalog["statistics_sentence"]["median"]
            )
            assert aggregate in caption
            assert caption.startswith("The chart shows ")
            assert " time" not in caption.lower()
            assert " cost" not in caption.lower()
        if axis:
            assert catalog["statistics"]["median"] not in axis
            direction: Any = metrics[identity].get("direction")
            assert direction not in axis


@pytest.mark.parametrize(
    ("document", "chart", "per"),
    [
        ("deepswe", "cost-trace", "trace"),
        ("r2e-gym", "cost-trial", "trial"),
        ("ci-jobs", "cost-job", "job"),
        ("terminal-bench-4-0", "cost-session", "trace"),
    ],
)
def test_exact_cost_chart_binding_uses_the_existing_cost_definition(
    bundle: dict[str, Any], document: str, chart: str, per: str
) -> None:
    catalog = bundle["metric_labels"]
    version = _current_version(bundle, document)
    binding = catalog["chart_bindings"][f"{document}@{version}"][chart]
    metric = expand_metric_labels(catalog)[binding["metric"]]
    assert metric["quantity"] == "cost" and metric["unit"] == "USD" and metric["per"] == per
    assert metric["derived_from"]["field"] == "cost.per_success"
    caption = label_for_chart(
        catalog, (document, version), chart, "caption", {"statistic": "median"}
    )
    assert caption == f"The chart shows mean values per {per}; lower is better."
    assert label_for_chart(catalog, (document, version), chart, "axis", {"unit": "$"}) == "Cost ($)"


def test_trace_and_trace_set_charts_do_not_share_an_ambiguous_metric(
    bundle: dict[str, Any],
) -> None:
    catalog = bundle["metric_labels"]
    version = _current_version(bundle, "deepswe")
    trace = label_for_chart(catalog, ("deepswe", version), "time-trace", "caption")
    trace_set = label_for_chart(catalog, ("deepswe", version), "time-trace-set", "caption")
    assert trace == "The chart shows median values per trace; lower is better."
    assert trace_set == "The chart shows total values per trace set; lower is better."
    assert label_for_chart(catalog, ("deepswe", version), "time", "caption") is None


def test_rl_pipeline_is_a_verified_composition_not_rollout_tool_time(
    bundle: dict[str, Any],
) -> None:
    catalog = bundle["metric_labels"]
    version = _current_version(bundle, "r2e-gym")
    binding = catalog["chart_bindings"][f"r2e-gym@{version}"]["time-overall"]
    assert binding["metric"].endswith("/pipeline_time")
    metric = expand_metric_labels(catalog)[binding["metric"]]
    assert metric["per"] == "trial"
    assert metric["derived_from"]["composition"]["terms"]["cmd"] == "command-runtime"
    assert metric["derived_from"]["composition"]["terms"]["destroy"] == "request-elapsed"
    assert label_for_chart(catalog, ("r2e-gym", version), "time-overall", "caption") == (
        "The chart shows geometric mean values per trial; lower is better."
    )
    broken = deepcopy(bundle)
    broken["methodologies"]["r2e-gym"][version]["timed_steps"].remove("destroy")
    with pytest.raises(ValueError, match="exactly the timed stages"):
        compile_metric_labels(broken, load_tables(ROOT).aliases)


def test_selected_statistic_and_fixed_typical_aggregate_remain_distinct(
    bundle: dict[str, Any],
) -> None:
    catalog = bundle["metric_labels"]
    version = _current_version(bundle, "deepswe")
    assert label_for_chart(
        catalog, ("deepswe", version), "time-trace", "caption", {"statistic": "mean"}
    ) == ("The chart shows mean values per trace; lower is better.")
    assert label_for_chart(
        catalog, ("deepswe", version), "time-typical-trace", "caption", {"statistic": "mean"}
    ) == ("The chart shows geometric mean values per trace; lower is better.")


def test_unit_only_metric_stays_time_and_exec_latency_reads_its_own_quantity(
    bundle: dict[str, Any],
) -> None:
    catalog = bundle["metric_labels"]
    assert (
        label_for_chart(
            catalog, ("deepswe", _current_version(bundle, "deepswe")), "time-trace", "axis"
        )
        == "Time (ms)"
    )
    latency = next(
        key
        for key in expand_metric_labels(catalog)
        if key.startswith(f"exec-latency@{_current_version(bundle, 'exec-latency')}/total_time")
    )
    assert label_for(catalog, latency, "axis", {"target": "metric"}) == "Latency (ms)"
    assert label_for(catalog, latency, "long", {"target": "metric"}) == "Latency per Call"


def test_the_latest_version_of_every_bound_document_has_its_chart_bindings(
    bundle: dict[str, Any],
) -> None:
    """A new document version without bindings leaves its panels with no cost axis or direction."""

    renamed = load_tables().aliases.get("document", {})
    bound = {binding["document"] for binding in chart_bindings()["bindings"]}
    missing = []
    for document in sorted(bound & set(bundle["methodologies"])):
        newest = max(bundle["methodologies"][document], key=version_key)
        canonical = renamed.get(document, document)
        if f"{canonical}@{newest}" not in bundle["metric_labels"]["chart_bindings"]:
            missing.append(f"{document}@{newest}")
    assert missing == []


def test_every_chart_bound_metric_keeps_its_short_axis_and_caption_labels(
    bundle: dict[str, Any],
) -> None:
    catalog = bundle["metric_labels"]
    for reference, charts in catalog["chart_bindings"].items():
        for chart, binding in charts.items():
            labels = expand_metric_labels(catalog)[binding["metric"]]["labels"]
            for form in ("short", "axis", "caption"):
                assert form in labels, (reference, chart, form)


def test_locked_per_words_read_as_the_unit_words(bundle: dict[str, Any]) -> None:
    metrics = expand_metric_labels(bundle["metric_labels"])
    for reference, unit in (
        (f"terminal-bench-4-0@{_current_version(bundle, 'terminal-bench-4-0')}/tool_time", "trace"),
        (f"r2e-gym@{_current_version(bundle, 'r2e-gym')}/tool_time", "task"),
        (f"r2e-gym@{_current_version(bundle, 'r2e-gym')}/reward_time", "task"),
    ):
        assert f"per {unit}" in metrics[reference]["labels"]["caption"]["metric"], reference


def test_an_id_shaped_quantity_has_a_grammar_name(bundle: dict[str, Any]) -> None:
    """An id-shaped quantity such as `provider_score` needs a grammar name, or the page shows it."""
    quantities = load_tables(ROOT).metric_label_grammar["quantities"]
    unnamed = sorted(
        {
            (document_id, metric["quantity"])
            for document_id, versions in bundle["methodologies"].items()
            for metric in next(reversed(versions.values()))["reader_definition"].get("metrics", [])
            if re.fullmatch(r"[a-z]+(_[a-z]+)+", metric.get("quantity", ""))
            and metric["quantity"] not in quantities
        }
    )
    assert not unnamed


def test_a_metric_quantity_that_is_also_a_section_id_keeps_its_own_label(
    bundle: dict[str, Any],
) -> None:
    """`operations` labels the Sandbox Operations section, and the At a Glance
    line of a metric counting operations read that name. A scoped `quantity.<value>` label wins."""
    labels = json.loads((ROOT / "contracts/text/methodology-page.json").read_text())["enum_labels"]
    clashing = sorted(
        {
            metric["quantity"]
            for versions in bundle["methodologies"].values()
            for metric in next(reversed(versions.values()))["reader_definition"].get("metrics", [])
            if metric.get("quantity") in labels
            and "{name:section." in labels[metric["quantity"]]
            and f"quantity.{metric['quantity']}" not in labels
        }
    )
    assert not clashing


def test_an_id_shaped_metric_value_has_a_page_label(bundle: dict[str, Any]) -> None:
    """An id-shaped metric value such as `time_score` needs an enum label, or the page shows it."""
    labels = json.loads((ROOT / "contracts/text/methodology-page.json").read_text())["enum_labels"]
    unlabelled = sorted(
        {
            (document_id, field, metric[field])
            for document_id, versions in bundle["methodologies"].items()
            for metric in next(reversed(versions.values()))["reader_definition"].get("metrics", [])
            for field in ("quantity", "per", "unit", "direction")
            if re.fullmatch(r"[a-z][a-z0-9]*([_.][a-z0-9]+)+", str(metric.get(field, "")))
            and metric[field] not in labels
        }
    )
    assert not unlabelled


def test_hard_links_axis_reads_hard_link_rate(bundle: dict[str, Any]) -> None:
    source = deepcopy(bundle)
    versions = source["methodologies"]["hard-links"]
    version = next(reversed(versions))
    metric = next(m for m in versions[version]["text"]["metrics"] if m.get("unit") == "links/s")
    catalog = compile_metric_labels(source, load_tables(ROOT).aliases)
    identity = f"hard-links@{version}/{metric['name']}"
    assert label_for(catalog, identity, "axis") == "Hard-Link Rate (links/s)"
    assert label_for(catalog, identity, "long") == "Hard-Link Rate"


def test_every_version_resolves_the_labels_of_its_own_entry(
    bundle: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The release holds one entry per run of equal versions; every (document, version) metric
    resolves the same labels, in every form and target, as from the per-version entries the
    compiler builds, which earlier releases held."""
    entries: dict[str, Any] = {}
    group = metric_labels.group_metrics

    def capture(metrics: dict[str, Any]) -> list[dict[str, Any]]:
        entries.update(deepcopy(metrics))
        return group(metrics)

    monkeypatch.setattr(metric_labels, "group_metrics", capture)
    catalog = compile_metric_labels(deepcopy(bundle), load_tables(ROOT).aliases)
    assert expand_metric_labels(catalog) == entries
    kept = {f"{identifier}@{version}" for identifier, version in retained_versions(ROOT)}
    assert all(
        len([version for version in versions if f"{identifier}@{version}" not in kept]) == 1
        for identifier, versions in bundle["methodologies"].items()
    )
    cases = [
        (identity, form, {"target": target, **({"statistic": statistic} if statistic else {})})
        for identity, entry in entries.items()
        for form, targets in entry["labels"].items()
        for target in targets
        for statistic in (None, "mean")
    ]
    after = [label_for(catalog, *case) for case in cases]
    monkeypatch.setattr(
        metric_labels, "metric_entry", lambda _catalog, identity: entries.get(identity)
    )
    before = [label_for(catalog, *case) for case in cases]
    assert after == before
    assert sum(label is not None for label in after) > len(entries)
