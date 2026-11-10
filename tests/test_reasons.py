# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""The reasons table: a publication text code is one owner sentence or says nothing."""

import json
import re
from pathlib import Path

from sandbox_benchmark_verifier.content import content_criteria, criteria_errors
from sandbox_benchmark_verifier.conventions import load_conventions
from sandbox_benchmark_verifier.reader_concepts import hover_source_errors
from sandbox_benchmark_verifier.text_predicates import check_program

ROOT = Path(__file__).resolve().parents[1]


def _findings(text: str, slot: str = "tooltip.reason.example") -> list[str]:
    scope = {"slots": [{"slot": slot, "text": text}], "context": "generated"}
    return [
        item["rule_id"]
        for item in check_program(scope, load_conventions(ROOT))
        if item["severity"] == "error" and item["status"] == "fail"
    ]


def test_the_rule_catches_each_raw_value_and_passes_the_owner_sentence() -> None:
    assert "tooltip.no-raw-values" in _findings('<excluded_top_up_unused>{"n":2}')
    assert "tooltip.no-raw-values" in _findings("Time to ready fio-disk-seq-write-1m-wait failed.")
    assert "tooltip.no-raw-values" in _findings("A trace is excluded. (Traces: 10)")
    assert "tooltip.no-raw-values" not in _findings(
        "An unexpected command result excludes the trace; 20 traces are affected."
    )
    assert "tooltip.no-raw-values" not in _findings("A higher-is-better result is shown.")


def test_every_reason_is_a_sentence_that_passes_the_rules_or_says_nothing() -> None:
    criteria = content_criteria()
    assert criteria_errors(criteria) == []
    reasons = criteria["reasons"]
    for code, entry in reasons.items():
        if "template" not in entry:
            continue
        rendered = re.sub(r"\{[a-z_]+\}", "create", entry["template"])
        assert "tooltip.no-raw-values" not in _findings(rendered, entry["slot"]), code


def test_hover_templates_state_each_body_once(tmp_path: Path) -> None:
    assert hover_source_errors() == []
    entries = {
        "a": {"owner": "methodology", "template": "One.", "runtime_fields": [], "form": "sentence"},
        "b": {"owner": "methodology", "template": "One.", "runtime_fields": [], "form": "sentence"},
        "c": {"template_ref": "b"},
        "d": {"template_ref": "missing"},
    }
    folder = tmp_path / "contracts/text"
    folder.mkdir(parents=True)
    (folder / "hover-templates.json").write_text(
        json.dumps({"templates": entries}), encoding="utf-8"
    )
    errors = hover_source_errors(tmp_path)
    assert any(error.startswith("b: same body as a") for error in errors)
    assert any(error.startswith("d: a reference names") for error in errors)
    assert not any(error.startswith("c:") for error in errors)


def test_hover_templates_keep_retired_templates_out() -> None:
    criteria = content_criteria()
    assert criteria_errors(criteria) == []
    templates = criteria["hover_templates"]
    assert "chart_trial_count_one" not in templates
    assert "deepswe_segment_tooltip_time" not in templates
    assert not any(code.startswith("segment_hover_suffix") for code in templates)


# Rule no-count-facts (Andre, 2026-11-10, NC-both): the page states no count as n of m. The
# failures hover says how many trials failed and why, not out of how many. The texts below still
# read "{k} of {n}": a score's rank (rule score-rank), and three that are open with Andre: a dot's
# position ("Trial 3 of 20"), a provider page's capability count ("12 of 20 supported") and the
# runner's burst failure cause ("3 of 10 sandboxes ready"). A new one fails here.
N_OF_M = re.compile(r"\{[^{}]+\} of \{[^{}]+\}|\b\d[\d,]* of \d[\d,]*\b")
N_OF_M_KEPT = {
    "methodology_page.pages.rank",
    "hover_templates.chart_trial_of",
    "provider_capabilities.count",
    "reasons.burst_failure_k_of_n",
}


def _strings(value: object, path: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], str]]:
    if isinstance(value, dict):
        return [item for key, child in value.items() for item in _strings(child, (*path, str(key)))]
    if isinstance(value, list):
        return [item for child in value for item in _strings(child, path)]
    return [(path, value)] if isinstance(value, str) else []


def test_no_page_text_states_a_count_as_n_of_m() -> None:
    criteria = content_criteria()
    assert (
        criteria["hover_templates"]["chart_failures"]["template"]
        == "{failed} trials failed ({causes})."
    )
    found = {
        ".".join(path[:3] if path[0] == "methodology_page" else path[:2])
        for path, text in _strings({k: v for k, v in criteria.items() if k != "conventions"})
        if N_OF_M.search(text)
    }
    assert found == N_OF_M_KEPT
