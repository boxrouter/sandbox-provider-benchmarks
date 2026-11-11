# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Remaining facts retain owner labels, secondary lines and layout rules."""

from copy import deepcopy

from sandbox_benchmark_verifier.content import content_criteria, criteria_errors
from sandbox_benchmark_verifier.conventions import load_conventions
from sandbox_benchmark_verifier.drill_down_rows import drill_down_row_errors


def test_fact_list_keeps_retired_facts_out() -> None:
    criteria = content_criteria()
    assert criteria_errors(criteria) == []
    assert not any(
        key.startswith(("fact_probe_", "fact_effective_cores_")) for key in criteria["fact_list"]
    )


def test_fact_list_rejects_missing_labels_and_undeclared_template_fields() -> None:
    criteria = content_criteria()
    criteria["fact_list"]["fact_received"] = " "
    criteria["fact_explanations"]["asked_vs_measured_asked"]["runtime_fields"] = ["invented"]
    errors = criteria_errors(criteria)
    assert "fact_received: missing fact-list label" in errors
    assert "asked_vs_measured_asked: template fields must match declared runtime fields" in errors


def test_drill_down_row_kinds_are_one_map_beside_the_labels() -> None:
    """describing rows show for every provider; event rows only where the
    event happened. The kind of each fact is the contract map, not a list per chart."""
    criteria = content_criteria()
    kinds = criteria["drill_down_rows"]
    assert {kind for kind in kinds.values()} == {"every_result", "when_present"}
    assert criteria_errors(criteria) == []


def test_a_kind_map_entry_needs_a_known_kind_and_a_fact_label() -> None:
    criteria = content_criteria()
    broken = {
        **criteria,
        "drill_down_rows": {**criteria["drill_down_rows"], "fact_unlabelled": "always"},
    }
    errors = [error for error in criteria_errors(broken) if "drill-down-row-kinds" in error]
    assert len(errors) == 2


def test_link_style_reads_its_exception_kinds_from_the_data() -> None:
    conventions = content_criteria()["conventions"]
    assert conventions["presentation"]["link_kinds"]
    rules = {rule["id"]: rule for rule in load_conventions()["presentation"]["rules"]}
    assert "presentation.link_kinds" in rules["link-style"]["text"]


def test_a_status_reason_row_uses_the_ranking_owner_label_and_is_conditional() -> None:
    criteria = deepcopy(content_criteria())
    assert "reason" in criteria["drill_down_rows"]
    assert drill_down_row_errors(criteria) == []
    del criteria["ranking_statuses"]["not_displayed"]["label"]
    assert drill_down_row_errors(criteria) == ["drill-down-row-kinds: reason names no fact label"]
