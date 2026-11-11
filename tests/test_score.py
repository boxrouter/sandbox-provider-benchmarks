# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""The score ranking: its definition documents, axis words, templates and flag rule."""

import copy
import re
from pathlib import Path
from typing import Any

import pytest

from sandbox_benchmark_verifier.cli import summary_value
from sandbox_benchmark_verifier.content import content_criteria
from sandbox_benchmark_verifier.methodology_page import reader_definition
from sandbox_benchmark_verifier.names import load_tables, resolve
from sandbox_benchmark_verifier.subtitles import score_fact_errors, template_errors
from sandbox_benchmark_verifier.text import _methodologies
from sandbox_benchmark_verifier.versions import version_key

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]


def _document(identifier: str) -> dict[str, Any]:
    return max(
        (document for document in _methodologies(ROOT) if document["id"] == identifier),
        key=lambda document: version_key(document["version"]),
    )


@pytest.mark.parametrize(
    ("identifier", "measure"), [("index.speed_score", "time"), ("index.cost_score", "cost")]
)
def test_the_score_documents_define_the_arithmetic_mean_without_substitution(
    identifier: str, measure: str
) -> None:
    document = _document(identifier)
    metric = document["metrics"][0]
    assert (metric["unit"], metric["direction"]) == ("points", "higher_is_better")
    assert metric["quantity"] == f"{measure}_score"
    assert document["composition"]["aggregation"] == "arithmetic_mean"
    assert document["statistics"]["primary"] == "arithmetic_mean"
    assert "missing_unit_penalty" not in document["statistics"]


def test_the_score_templates_and_sentences_name_their_fields() -> None:
    criteria = copy.deepcopy(content_criteria())
    policy = criteria["conventions"]["subtitle"]
    assert template_errors(policy) == []
    assert score_fact_errors(criteria["fact_list"]) == []
    policy["score_set_template"] = "A {measure} score is averaged from each {unit}."
    assert any("score_set_template" in error for error in template_errors(policy))
    policy["score_frontier_composite_template"] = "A {measure} score and a cost score."
    assert any("score_frontier_composite_template" in error for error in template_errors(policy))
    policy["score_frontier_template"] = "A {measure} score and a cost score."
    assert any("score_frontier_template" in error for error in template_errors(policy))
    criteria["fact_list"]["fact_score_missing_unit"] = "A missing unit adds no points to the score."
    assert any(
        "fact_score_missing_unit" in error for error in score_fact_errors(criteria["fact_list"])
    )


def test_the_flag_rule_exists_and_is_checked_by_web() -> None:
    rules = {
        rule["id"]: rule for rule in content_criteria()["conventions"]["presentation"]["rules"]
    }
    rule = rules["score-ranking-flag"]
    assert rule["predicate"]["checked_by"] == "web"


def test_the_score_axis_titles_are_exactly_the_quantity_words(bundle: dict[str, Any]) -> None:
    metrics = bundle["metric_labels"]["metrics"]
    quantities = {r["id"]: r["name"] for r in load_tables(ROOT).names if r["kind"] == "quantity"}
    for reference, title in (
        ("time-score/typical_time_score", quantities["time-score"]),
        ("cost-score/typical_cost_score", quantities["cost-score"]),
    ):
        assert metrics[reference]["labels"]["axis"]["metric"] == title
        assert metrics[reference]["labels"]["axis"]["hero"] == title
    # A unit with a clause still gets it: only the grammar's unitless units drop it.
    assert metrics["time-index/typical_time"]["labels"]["axis"]["metric"] == "Time ({unit})"


def test_a_score_document_is_not_listed_among_another_documents_versions() -> None:
    aliases = load_tables(ROOT).aliases["document"]
    others = {key: value for key, value in aliases.items() if not key.endswith("_score")}
    for document, own in (("index.speed_score", "time-score"), ("index.cost_score", "cost-score")):
        assert aliases[document] == own
        assert own not in others.values() and own not in others


def _provider_score() -> dict[str, Any]:
    versions = [d for d in _methodologies(ROOT) if d["id"] == "index.provider_score"]
    return max(versions, key=lambda d: version_key(d["version"]))


def _children(rows: list[dict[str, Any]], parent: str) -> list[dict[str, Any]]:
    return [r for r in rows if r.get("parent") == parent]


def _leaf_documents(rows: list[dict[str, Any]], section: str) -> set[str]:
    """The documents of the section's leaves: the names table's tests under its groups."""
    document = _provider_score()["composition"]["leaves"]
    first_only = set(document["first_test_only"])
    result: set[str] = set()
    for group in _children(rows, section):
        if group.get("status") == "coming_soon":
            continue
        tests = [r for r in _children(rows, group["id"]) if r["kind"] == "test"]
        result.update(t["document"] for t in (tests[:1] if group["id"] in first_only else tests))
    return result


def test_the_provider_score_levels_are_sections_of_the_names_table() -> None:
    rows = load_tables(ROOT).names
    sections = {r["id"] for r in rows if r["kind"] == "section"}
    levels = _provider_score()["composition"]["levels"]
    assert {level["section"] for level in levels} <= sections


def test_the_provider_score_inputs_are_the_leaves_the_names_table_holds() -> None:
    rows = load_tables(ROOT).names
    inputs = {member["id"] for member in _provider_score()["composition"]["inputs"]}
    leaves: set[str] = set()
    for level in _provider_score()["composition"]["levels"]:
        leaves |= _leaf_documents(rows, level["section"])
    tables = load_tables(ROOT)
    assert {resolve("document", item, tables=tables) for item in inputs} == {
        resolve("document", item, tables=tables) for item in leaves
    }


def test_the_provider_score_leaf_rules_name_tabs_and_tests_of_the_names_table() -> None:
    rows = load_tables(ROOT).names
    leaves = _provider_score()["composition"]["leaves"]
    tabs = {r["id"] for r in rows if r["kind"] == "tab"}
    assert set(leaves["first_test_only"]) <= tabs
    for summed in leaves["summed"]:
        assert any(r["kind"] == "view" and r["id"] == summed["id"] for r in rows)
        tests = {r["id"]: r["parent"] for r in rows if r["kind"] == "test"}
        assert {tests[test] for test in summed["tests"]} == {"files"}


def test_exec_counts_once_through_its_first_test_which_is_per_100_calls() -> None:
    """The provider score has one Exec input, Latency per 100 Calls: the Exec tab's first test."""
    rows = load_tables(ROOT).names
    composition = _provider_score()["composition"]
    assert "exec" in composition["leaves"]["first_test_only"]
    first = next(r for r in rows if r["kind"] == "test" and r.get("parent") == "exec")
    exec_inputs = {i["id"] for i in composition["inputs"] if "exec" in i["id"]}
    assert exec_inputs == {first["id"]}


def test_disk_scores_its_four_tests() -> None:
    rows = load_tables(ROOT).names
    assert "disk" not in _provider_score()["composition"]["leaves"]["first_test_only"]
    disk = {r["document"] for r in rows if r.get("parent") == "disk" and r["kind"] == "test"}
    assert len(disk) == 4 and disk <= _leaf_documents(rows, "machine-performance")


def test_time_score_and_cost_score_read_in_parallel() -> None:

    details = content_criteria()["index_copy"]["details"]
    swap = ((r"\bfastest\b", "cheapest"), (r"\btime\b", "cost"))
    for field in ("what_this_measures", "how_it_is_measured", "how_to_read"):
        text = details["time_score"][field]
        for old, new in swap:
            text = re.sub(old, new, text)
        assert text == details["cost_score"][field], field


@pytest.mark.parametrize("identifier", ["index.speed_score", "index.cost_score"])
def test_fresh_score_tail_uses_completed_units_and_its_exact_inherited_floor(
    identifier: str,
) -> None:
    documents = {(d["id"], str(d["version"])): d for d in _methodologies(ROOT)}
    document = max(
        (d for (key, _), d in documents.items() if key == identifier),
        key=lambda d: version_key(d["version"]),
    )
    inherited = document["inherits"]
    common = documents[(inherited["id"], str(inherited["version"]))]
    floor = common["statistics"]["tail"]["min_successes"]
    policy = document["statistics"]["percentile_method"]
    assert not re.search(r"\b\d+\b", policy), "the score must not restate a numerical floor"
    shown = reader_definition(document, documents, content_criteria()["methodology_page"])
    assert shown["statistics"]["tail"]["min_successes"] == floor
    # A distinct later common cannot silently supply this score's floor.
    future = copy.deepcopy(common)
    future["version"] = "99.0.0"
    future["statistics"]["tail"]["min_successes"] = floor + 1
    documents[(future["id"], future["version"])] = future
    assert (
        reader_definition(document, documents, content_criteria()["methodology_page"])[
            "statistics"
        ]["tail"]["min_successes"]
        == floor
    )
    moved = copy.deepcopy(document)
    moved["inherits"] = {"id": future["id"], "version": future["version"]}
    bound = reader_definition(moved, documents, content_criteria()["methodology_page"])
    assert bound["statistics"]["tail"]["min_successes"] == floor + 1
    samples = list(range(1, floor + 1))
    assert summary_value("p95", samples) != summary_value("slowest", samples)


def test_two_provider_tail_set_witness_keeps_a_completed_low_sample_unit() -> None:
    # Independent numerical reference for the new definition, not a campaign admission simulation.
    # The retained producer witness separately proves scheduled=floor/successes=floor-5 is refused.
    documents = {(d["id"], str(d["version"])): d for d in _methodologies(ROOT)}
    score = max(
        (d for (key, _), d in documents.items() if key == "index.speed_score"),
        key=lambda d: version_key(d["version"]),
    )
    inherited = score["inherits"]
    common = documents[(inherited["id"], str(inherited["version"]))]
    floor = common["statistics"]["tail"]["min_successes"]
    units = {
        "u": {"a": [100.0] * (floor - 1), "b": [50.0] * floor},
        "v": {"a": [10.0] * floor, "b": [20.0] * floor},
    }
    tails: dict[str, dict[str, float]] = {}
    for unit, providers in units.items():
        tails[unit] = {}
        for provider, values in providers.items():
            value = summary_value("p95" if len(values) >= floor else "slowest", values)
            assert value is not None
            tails[unit][provider] = value
    for provider in ("a", "b"):
        ratios = [100 * min(values.values()) / values[provider] for values in tails.values()]
        assert summary_value("mean", ratios) == 75
    # The old separate all-provider scheduled cutoff would delete u and change the denominator.
    assert 100 * min(tails["v"].values()) / tails["v"]["a"] == 100
    assert 100 * min(tails["v"].values()) / tails["v"]["b"] == 50
    assert common["statistics"]["ranking"]["requires_all_successful"] is True
    incomplete_successes = floor - 5
    assert incomplete_successes < floor
    assert summary_value("slowest", [100.0] * incomplete_successes) == 100
    # This second witness has scheduled=floor but successes=floor-5: it is not a completed unit.
    # No aggregate or admission PASS is asserted for it here.
