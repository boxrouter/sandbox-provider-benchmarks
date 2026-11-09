# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""A workload panel's subtitle is its selection level's owner template, then the direction.

``APPROVED`` is the one place the approved subtitle words are typed. Other text reads the
templates in the conventions and the words in contracts/text/workload-subtitles.json; this test
compares their output with these sentences.
"""

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from sandbox_benchmark_verifier.content import content_criteria, rendered_content_errors
from sandbox_benchmark_verifier.names import _row_errors, load_tables
from sandbox_benchmark_verifier.subtitles import (
    measure_direction,
    subtitle_word_errors,
    template_errors,
    workload_subtitle,
)

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
LOWER = "Lower is better"
LOWER_SENTENCE = "Lower is better."
# A sample unit label per test, as the page's selector draws it.
LABELS = {
    "deepswe": "abs-stepped-slices",
    "terminal-bench-4-0": "batched-eval-parity",
    "ci-jobs": "fastapi (Python)",
    "r2e-gym": "numpy-apply-axis-empty",
}
# The time form; the cost form is the same with "time" as "cost".
APPROVED = {
    ("deepswe", "set"): "Aggregates a time statistic over each trace, a replay of commands from an "
    "agent's attempt to solve a repository task (selected from the DeepSWE v1.1 raw data).",
    ("terminal-bench-4-0", "set"): "Aggregates a time statistic over each trace, a replay of an "
    "agent's commands and file edits on a terminal task (selected from the Terminal-Bench 4.0 "
    "public leaderboard).",
    ("ci-jobs", "set"): "Aggregates a time statistic over each job, a replay of one repository's "
    "CI steps from a cold start (selected from open-source repositories' GitHub Actions "
    "workflows).",
    ("r2e-gym", "set"): "Aggregates a time statistic over each task, a replay of four recorded "
    "rollouts from an agent's attempts at a repository task (selected from the R2E-Gym raw data).",
    ("deepswe", "unit"): "Time per trace. This trace is a replay of commands from an agent's "
    "attempt to solve the abs-stepped-slices task.",
    ("terminal-bench-4-0", "unit"): "Time per trace. This trace is a replay of commands and file "
    "edits from an agent's attempt to solve the batched-eval-parity task.",
    ("ci-jobs", "unit"): "Time per job. This job is a replay of the fastapi (Python) "
    "repository's CI steps from a cold start.",
    ("r2e-gym", "unit"): "Time per task. This task is a replay of four recorded rollouts from an "
    "agent's attempts to solve the numpy-apply-axis-empty task.",
}


def _approved(identifier: str, level: str, measure: str) -> str:
    text = APPROVED[(identifier, level)]
    if measure == "time":
        return text
    return text.replace("a time statistic", "a cost statistic").replace("Time per", "Cost per")


def _workload(identifier: str, level: str, measure: str) -> dict[str, str]:
    rows = load_tables(ROOT).names
    test = next(r for r in rows if r["kind"] == "test" and r["id"] == identifier)
    unit = next(r["name"] for r in rows if r["kind"] == "per-unit" and r["id"] == test["unit"])
    return {
        "level": level,
        "measure": measure,
        "unit": unit,
        "test": identifier,
        "label": LABELS[identifier],
    }


def _render(criteria: dict[str, Any], workload: dict[str, str]) -> str:
    return workload_subtitle(
        criteria["conventions"]["subtitle"],
        workload["level"],
        workload["measure"],
        workload["unit"],
        criteria["workload_subtitles"][workload["test"]],
        label=workload["label"],
    )


def _panel(text: str, workload: dict[str, str]) -> dict[str, Any]:
    return {
        "kind": "benchmark",
        "slots": [
            {"slot": "title", "text": "Demo"},
            {"slot": "description", "text": text},
            {"slot": "axis-title", "text": "Time (ms)"},
        ],
        "directions": [LOWER],
        "workload_subtitle": workload,
    }


@pytest.mark.parametrize("measure", ["time", "cost"])
@pytest.mark.parametrize("level", ["set", "unit"])
@pytest.mark.parametrize("identifier", list(LABELS))
def test_each_workload_subtitle_is_the_approved_sentence(
    identifier: str, level: str, measure: str
) -> None:
    """Test, measure and selection-level combinations use the expected wording."""
    criteria = content_criteria()
    workload = _workload(identifier, level, measure)
    expected = _approved(identifier, level, measure)
    assert _render(criteria, workload) == expected
    assert rendered_content_errors(criteria, _panel(f"{expected} {LOWER}", workload)) == []
    wrong = rendered_content_errors(criteria, _panel(f"{expected[:-1]} {LOWER}", workload))
    assert any("workload subtitle must be" in error for error in wrong)


def test_the_score_ranking_reads_the_sentence_and_the_workload_subtitle_its_clause() -> None:
    criteria = content_criteria()
    policy = criteria["conventions"]["subtitle"]
    words = criteria["workload_subtitles"]["deepswe"]
    sentence = words["set_sentence"]
    assert words["set_clause"] in APPROVED[("deepswe", "set")]
    assert policy["score_set_template"].replace("{measure}", "time").replace(
        "{unit}", "trace"
    ).replace("{set_sentence}", sentence) == (f"Averages a time score over each trace. {sentence}")


def test_the_templates_name_their_fields() -> None:
    policy = dict(content_criteria()["conventions"]["subtitle"])
    assert template_errors(policy) == []
    policy["workload_set_template"] = "A {measure} statistic is aggregated from each {unit}."
    assert any("workload_set_template" in error for error in template_errors(policy))


def test_every_counted_test_has_its_subtitle_words() -> None:
    criteria = content_criteria()
    rows = load_tables(ROOT).names
    entries = copy.deepcopy(criteria["workload_subtitles"])
    assert subtitle_word_errors(entries, rows) == []
    del entries["ci-jobs"]["source"]
    entries["deepswe"]["set_sentence"] = "each trace is a replay"
    entries["r2e-gym"]["set_sentence"] = "Each {label} is a task."
    errors = subtitle_word_errors(entries, rows)
    assert any("ci-jobs must have exactly" in error for error in errors)
    assert any("deepswe.set_sentence is a sentence" in error for error in errors)
    assert any("r2e-gym: only unit_sentence" in error for error in errors)
    del entries["terminal-bench-4-0"]
    assert any("no entry for terminal-bench-4-0" in e for e in subtitle_word_errors(entries, rows))


def test_no_workload_text_keeps_the_superseded_subtitle_sentences(bundle: dict[str, Any]) -> None:
    for identifier in ("deepswe", "terminal-bench-4-0", "r2e-gym", "ci-jobs"):
        notes = bundle["reader"]["methods"][identifier]["text"]["notes"]
        assert "summary" not in notes, identifier  # the Time subtitle is the template, not a field
        for phrase in ("tool-use trace", "divided by successful", "The cost covers"):
            assert phrase not in notes["cost_summary"], identifier
    for path in ("reader/methods.yaml", "names/names.yaml"):
        assert "tool-use trace" not in (ROOT / path).read_text(encoding="utf-8"), path
    assert "tool-use trace" not in json.dumps(bundle["content_criteria"])


def test_a_counted_row_describes_its_unit_and_a_test_reads_the_contract() -> None:
    rows: list[dict[str, Any]] = [
        {"row": 1, "kind": "per-unit", "id": "job", "name": "job", "plural": "jobs"},
        {"row": 2, "kind": "index", "id": "ci", "name": "CI", "unit": "job"},
        {"row": 3, "kind": "test", "id": "ci-jobs", "name": "CI Jobs", "unit": "job"},
    ]
    assert any("unit_description" in error for error in _row_errors(rows, set()))
    rows[1]["unit_description"] = "A selected repository job."
    assert not any("unit_description" in error for error in _row_errors(rows, set()))
    rows[1]["unit_description"] = "a selected repository job"
    assert any("unit_description" in error for error in _row_errors(rows, set()))
    rows[1]["unit_description"] = "A selected repository job."
    rows[2]["unit_description"] = "A selected repository job."
    assert any("workload-subtitles.json" in error for error in _row_errors(rows, set()))


def test_the_measure_words_are_the_view_rows_sentence_forms() -> None:
    views = {
        row["id"]: row
        for row in load_tables(ROOT).names
        if row["kind"] == "view" and row["id"] in ("time", "cost")
    }
    assert {key: row["sentence"] for key, row in views.items()} == {"time": "time", "cost": "cost"}
    for row in views.values():
        assert row["name"] == row["sentence"].capitalize()


def test_a_chart_without_segments_ends_its_subtitle_naming_the_measure() -> None:

    criteria = content_criteria()
    policy = criteria["conventions"]["subtitle"]
    assert policy["measure_template"] == "{measure}, {direction}"
    assert (
        measure_direction(policy, "Time per job", LOWER_SENTENCE)
        == "Time per job, lower is better."
    )
    assert (
        measure_direction(policy, "Cost per trace", "Higher is better.")
        == "Cost per trace, higher is better."
    )
    workload = _workload("ci-jobs", "unit", "time")
    expected = _render(criteria, workload)
    scope = _panel(f"{expected} Time per job, lower is better.", workload)
    scope["directions"] = [LOWER_SENTENCE]
    scope["measure_label"] = "Time per job"
    assert rendered_content_errors(criteria, scope) == []
    scope = _panel(f"{expected} {LOWER_SENTENCE}", workload)
    scope["directions"] = [LOWER_SENTENCE]
    scope["measure_label"] = "Time per job"
    assert any("workload subtitle must be" in e for e in rendered_content_errors(criteria, scope))
    generic = _panel("Times a thing. Time per job, lower is better.", workload)
    del generic["workload_subtitle"]
    generic["directions"] = [LOWER_SENTENCE]
    generic["measure_label"] = "Time per job"
    assert rendered_content_errors(criteria, generic) == []
    generic["slots"][1]["text"] = f"Times a thing. {LOWER_SENTENCE}"
    assert any("must end with" in e for e in rendered_content_errors(criteria, generic))


def test_the_measure_template_names_its_fields() -> None:
    policy = dict(content_criteria()["conventions"]["subtitle"])
    policy["measure_template"] = "{measure} is better when lower"
    assert any("measure_template" in error for error in template_errors(policy))
