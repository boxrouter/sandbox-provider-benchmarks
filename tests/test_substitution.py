# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""A substitution names the value it took and is shown one level up from its gap."""

import copy
from typing import Any

import pytest

from sandbox_benchmark_verifier.content import content_criteria, criteria_errors
from sandbox_benchmark_verifier.substitution import list_text, substitution_errors

pytestmark = pytest.mark.unit


@pytest.fixture(name="criteria")
def _criteria() -> dict[str, Any]:
    return copy.deepcopy(content_criteria())


def test_the_owner_text_passes_both_rules(criteria: dict[str, Any]) -> None:
    assert substitution_errors(criteria) == []
    assert not [error for error in criteria_errors(criteria) if "substitution" in error]


def test_every_substitution_fact_is_statistic_aware_and_names_the_value_it_took(
    criteria: dict[str, Any],
) -> None:
    facts = criteria["fact_list"]
    assert facts["fact_no_result_time"] == (
        "No successful trial. Substituted with the slowest {statistic} "
        "from the providers that completed this {unit}."
    )
    assert facts["fact_no_result_cost"] == (
        "No successful trial. Substituted with the highest {statistic} cost "
        "from the providers that completed this {unit}."
    )
    for code in ("fact_fill_time_rule", "fact_fill_unit_time_rule"):
        assert "{statistic}" in facts[code] and "slowest" in facts[code], code
    for code in ("fact_fill_cost_rule", "fact_fill_unit_cost_rule"):
        assert "{statistic}" in facts[code] and "highest" in facts[code], code
    assert "median" not in " ".join(
        facts[code] for code in facts if "fill" in code or "result" in code
    )


def test_a_substitution_text_that_does_not_name_the_value_is_refused(
    criteria: dict[str, Any],
) -> None:
    criteria["fact_list"]["fact_no_result_time"] = "No successful trial. Used {statistic}."
    assert any("does not name the value" in error for error in substitution_errors(criteria))
    criteria["fact_list"]["fact_no_result_time"] = "No successful trial; slowest {statistic} × 1.2"
    assert any("multiplier" in error for error in substitution_errors(criteria))


def test_an_absence_line_that_names_a_value_is_refused(criteria: dict[str, Any]) -> None:
    footer = criteria["ranking_status_footer"]
    footer["names_reason_template"] = "{names} at the slowest"
    assert any("names_reason_template" in error for error in substitution_errors(criteria))
    criteria["ranking_statuses"]["filled"]["label"] = "Provider failures, filled with the slowest"
    assert any("ranking_statuses.filled" in error for error in substitution_errors(criteria))


def test_the_composite_lists_workloads_and_a_workload_counts_its_traces(
    criteria: dict[str, Any],
) -> None:
    footer = criteria["ranking_status_footer"]
    names = list_text(["R2E-Gym", "CI Jobs", "Terminal-Bench 4.0"], footer["list_style"])
    assert (
        footer["names_reason_template"].format(names=names)
        == "R2E-Gym, CI Jobs, and Terminal-Bench 4.0"
    )
    assert list_text(["gin", "gson"]) == "gin and gson"
    assert list_text(["gin"]) == "gin"
    footer["names_reason_template"] = "{names}: {n}"
    assert any("filled line takes" in error for error in substitution_errors(criteria))
    footer["names_reason_template"] = "{names}"
    footer["list_style"] = "disjunction"
    assert any("list_style" in error for error in substitution_errors(criteria))


def test_the_incomplete_line_has_a_marker_the_owner_sentence_and_no_penalty(
    criteria: dict[str, Any],
) -> None:
    footer = criteria["ranking_status_footer"]
    values = {
        "label": criteria["ranking_statuses"]["filled"]["label"],
        "providers": "Blaxel (R2E-Gym)",
    }
    incomplete = footer["incomplete_footer_template"].format(
        marker=footer["incomplete_marker"], open_hint=footer["open_hint"], **values
    )
    assert incomplete == "† Provider Failures: Blaxel (R2E-Gym). Click provider to see details."
    # Not displayed ends with the same owner sentence, from the one field.
    assert footer["template"].format(open_hint=footer["open_hint"], **values) == (
        "Provider Failures: Blaxel (R2E-Gym). Click provider to see details."
    )
    assert substitution_errors(criteria) == []
    footer["incomplete_footer_template"] = "{label}: {providers}."
    assert any("shared-status-lines" in error for error in substitution_errors(criteria))
    footer["incomplete_footer_template"] = "{marker} {label}: {providers}{open_hint}"
    footer["template"] = "{label}: {providers}. Click provider to see details."
    assert any("shared-status-lines" in error for error in substitution_errors(criteria))
    footer["template"] = "{label}: {providers}{open_hint}"
    footer["open_hint"] = ". Click provider to see details. 1.2x"
    assert any("open_hint" in error for error in substitution_errors(criteria))


def test_the_substitution_word_links_to_the_workload_value(criteria: dict[str, Any]) -> None:

    facts = criteria["fact_list"]
    for code in ("fact_no_result_time", "fact_no_result_cost"):
        assert facts[code].startswith("No successful trial. ")
        assert facts["fact_no_result_term"] in facts[code]
    assert facts["fact_no_result_term"] == "Substituted"
    assert (
        facts["fact_no_result_href"] == criteria["conventions"]["details"]["composite"]["term_href"]
    )
