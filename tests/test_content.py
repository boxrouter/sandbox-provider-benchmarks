# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Failure controls for the source-owned content contract and DOM capture API."""

import copy
from typing import Any

import pytest

from sandbox_benchmark_verifier.catalog import load_methodology
from sandbox_benchmark_verifier.content import (
    content_criteria,
    criteria_errors,
    document_content_errors,
    document_reader_literal_errors,
    reader_literal_errors,
    rendered_content_errors,
    slot_errors,
)
from sandbox_benchmark_verifier.exclusion_reasons import exclusion_reason_errors
from sandbox_benchmark_verifier.methodology_page import omit_paths
from sandbox_benchmark_verifier.placeholders import PLACEHOLDER_OPENERS

pytestmark = pytest.mark.unit


def test_the_verb_rule_rejects_a_generated_sentence_without_one_and_leaves_authored_text() -> None:
    rule = content_criteria()["slots"]["description"]
    fragment = "Time to finish the command."
    assert slot_errors(fragment, rule, generated=True) == ["sentence has no finite predicate"]
    assert slot_errors(fragment, rule) == []
    assert slot_errors("The command runs in a fresh sandbox.", rule, generated=True) == []


@pytest.mark.parametrize("kind", [opener[1:-1] for opener in PLACEHOLDER_OPENERS])
def test_a_slot_with_any_unresolved_placeholder_kind_fails(kind: str) -> None:
    rule = content_criteria()["slots"]["description"]
    assert "unresolved source placeholder" in slot_errors(f"The image is {{{kind}:x}}.", rule)


def test_source_rules_reject_numeric_facts_and_duplicate_slots() -> None:
    document = {
        "id": "demo",
        "version": "1.0.0",
        "notes": {
            "introduction": "The test runs 10 trials.",
            "setup": "The test runs 10 trials.",
        },
    }
    errors = document_content_errors(document, documents={}, names={})
    assert any("numeric fact requires a parameter placeholder" in error for error in errors)
    assert any("duplicates demo@1.0.0.notes.introduction" in error for error in errors)


def capture() -> dict[str, Any]:
    scope: dict[str, Any] = {
        "kind": "workload",
        "display_unit": "ms",
        "slots": [
            {"slot": "title", "text": "Demo"},
            {"slot": "description", "text": "The command runs in a fresh sandbox."},
            {"slot": "axis-title", "text": "Time (ms)"},
            {
                "slot": "details.setup",
                "text": "A monotonic clock times the fixed command.",
            },
            {"slot": "details.limitations", "text": "The test excludes model inference."},
            {"slot": "details.sources", "text": "Full Method"},
        ],
    }

    return scope


def test_complete_dom_capture_obeys_the_shared_contract() -> None:
    assert rendered_content_errors(content_criteria(), capture()) == []


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "order", "unit", "page-fact"])
def test_dom_capture_rejects_each_contract_violation(mutation: str) -> None:
    scope = copy.deepcopy(capture())
    if mutation == "missing":
        scope["slots"].pop(1)
    elif mutation == "duplicate":
        scope["slots"][3]["text"] = scope["slots"][1]["text"]
    elif mutation == "order":
        scope["slots"][3], scope["slots"][4] = scope["slots"][4], scope["slots"][3]
    elif mutation == "unit":
        scope["display_unit"] = "GiB/s"
    else:
        scope["page_facts"] = [{"id": "region", "text": "Virginia"}]
        scope["slots"][4]["text"] = "A monotonic clock times the command in Virginia."
    assert rendered_content_errors(content_criteria(), scope)


def test_public_fact_explanation_metadata_is_validated_and_exported() -> None:
    criteria = content_criteria()
    assert criteria_errors(criteria) == []
    broken = copy.deepcopy(criteria)
    broken["fact_explanations"]["asked_vs_measured_asked"]["template"] = "Read {invented}."
    assert criteria_errors(broken)


def test_details_must_add_information() -> None:
    criteria = content_criteria()
    scope = capture()
    scope["slots"][3]["text"] = scope["slots"][1]["text"] + " This is timed."
    assert any("near-duplicate" in error for error in rendered_content_errors(criteria, scope))


def test_repeated_headings_and_alphabetical_tabs_fail_the_shared_guard() -> None:
    scope = capture()
    scope["headings"] = [{"text": "Statistics", "level": 2}, {"text": "Statistics", "level": 3}]
    assert any(
        "repeated heading" in error for error in rendered_content_errors(content_criteria(), scope)
    )
    scope.pop("headings")
    scope["taxonomy_lists"] = [
        {
            "kind": "job",
            "parent": "ci-jobs",
            "ids": ["fastapi", "gin", "gson", "jq", "spotify-player", "zustand"],
        }
    ]
    assert any(
        "taxonomy order" in error for error in rendered_content_errors(content_criteria(), scope)
    )


def test_index_descriptions_use_their_owner_copy_and_normal_sentence_rules() -> None:
    criteria = content_criteria()
    assert "index-description" not in criteria["literal_templates"]
    descriptions = criteria["index_copy"]["descriptions"]
    assert len(set(descriptions.values())) == len(descriptions)
    scope = capture()
    scope["kind"] = "index"
    for key, description in descriptions.items():
        if key == "time_vs_cost":
            continue  # read by the locked time-vs-cost versions only; no chart draws it
        scope["slots"][1]["text"] = description
        assert rendered_content_errors(criteria, scope) == []
    scope["kind"] = "index"
    scope["slots"][1]["text"] = "one value per provider"
    assert any("sentence" in error for error in rendered_content_errors(criteria, scope))


def status_footer() -> dict[str, Any]:
    return {
        "slot": "chart.status-footer",
        "status": "too_few_runs",
        "providers": [{"provider": "Daytona"}],
        "text": "Not Ranked: Daytona",
    }


def test_ranking_status_uses_one_footer_line() -> None:
    criteria = content_criteria()
    scope = capture()
    scope["slots"].extend([{"slot": "chart.bar-row", "text": "Daytona 52 s"}, status_footer()])
    assert rendered_content_errors(criteria, scope) == []
    footer = scope["slots"][-1]
    footer.update(
        status="not_displayed", providers=[{"provider": "Daytona", "reason": "10 trials"}]
    )
    footer["text"] = "Not Displayed: Daytona (10 trials)"
    assert rendered_content_errors(criteria, scope) == []


@pytest.mark.parametrize("status", ["too_few_runs", "shown_only"])
def test_the_not_ranked_line_names_its_providers_only(status: str) -> None:
    """Andre, 2026-11-08: no "100 of 108" or "0 of 18" count after a Not ranked provider."""
    scope = capture()
    footer = status_footer()
    footer["status"] = status
    footer["providers"] = [{"provider": "Daytona", "reason": "5 of 6 jobs measured"}]
    footer["text"] = "Not Ranked: Daytona (5 of 6 jobs measured)"
    scope["slots"].append(footer)
    assert any(
        "the Not Ranked line names its providers only" in error
        for error in rendered_content_errors(content_criteria(), scope)
    )
    assert "coverage_reason_template" not in content_criteria()["ranking_status_footer"]


def test_an_incomplete_footer_starts_with_the_marker_and_says_provider_failures() -> None:
    criteria = content_criteria()
    marker = criteria["ranking_status_footer"]["incomplete_marker"]
    scope = capture()
    footer = status_footer()
    scope["slots"].extend([{"slot": "chart.bar-row", "text": "Daytona 52 s"}, footer])
    footer["providers"] = [{"provider": "Daytona", "reason": "gin and gson"}]
    for status in ("filled", "filled_units"):
        footer["status"] = status
        footer["text"] = f"{marker} Provider Failures: Daytona (gin and gson)"
        assert rendered_content_errors(criteria, scope) == []
        footer["text"] = "Provider Failures: Daytona (gin and gson)"
        assert any("differs from the owned" in e for e in rendered_content_errors(criteria, scope))
        footer["text"] = f"{marker} Job issues: Daytona (gin and gson)"
        assert any("differs from the owned" in e for e in rendered_content_errors(criteria, scope))


def test_the_footer_ends_with_the_owner_hint_exactly_when_its_names_open_a_drill_down() -> None:
    criteria = content_criteria()
    scope = capture()
    footer = status_footer()
    scope["slots"].extend([{"slot": "chart.bar-row", "text": "Daytona 52 s"}, footer])
    footer["opens_details"] = True
    assert any(
        "differs from the owned" in error for error in rendered_content_errors(criteria, scope)
    )
    footer["text"] += criteria["ranking_status_footer"]["open_hint"]
    assert rendered_content_errors(criteria, scope) == []
    footer["opens_details"] = False
    assert any(
        "differs from the owned" in error for error in rendered_content_errors(criteria, scope)
    )


def test_every_status_label_and_retired_chip_fails_inside_a_bar_row() -> None:
    criteria = content_criteria()
    labels = {entry["label"] for entry in criteria["ranking_statuses"].values()}
    labels.update(criteria["ranking_status_footer"]["retired_labels"])
    for label in labels:
        scope = capture()
        scope["slots"].append({"slot": "chart.bar-row", "text": f"Daytona 52 s {label}"})
        assert any(
            "ranking status belongs only in the chart footer" in error
            for error in rendered_content_errors(criteria, scope)
        )


def test_a_status_label_with_a_unit_slot_fails_with_any_unit_word_in_a_bar_row() -> None:
    criteria = content_criteria()
    for text in (
        "Daytona 52 s Trace issues",
        "Daytona 52 s Job Failures",
        "Daytona 52 s Incomplete jobs",
    ):
        scope = capture()
        scope["slots"].append({"slot": "chart.bar-row", "text": text})
        assert any(
            "ranking status belongs only in the chart footer" in error
            for error in rendered_content_errors(criteria, scope)
        )


@pytest.mark.parametrize("mutation", ["copy", "duplicate", "ranked"])
def test_ranking_footer_rejects_rewrites_and_duplicate_lines(mutation: str) -> None:
    scope = capture()
    footer = status_footer()
    if mutation == "copy":
        footer["text"] = "Not Ranked: Daytona (50 of 6 trials measured)"
    elif mutation == "ranked":
        footer["status"] = "ranked"
    else:
        scope["slots"].append(copy.deepcopy(footer))
    scope["slots"].append(footer)
    assert any(
        "chart.status-footer:" in error
        for error in rendered_content_errors(content_criteria(), scope)
    )


@pytest.mark.parametrize(
    "reason",
    [
        "Daytona (container) has 5 of the 6 runs it needs to be ranked.",
        "5 of 6 runs measured",
        "Not included",
        "5 of 6 jobs measured.",
    ],
)
def test_footer_rejects_repeated_names_generic_reasons_and_run_units(reason: str) -> None:
    criteria = content_criteria()
    scope = capture()
    footer = status_footer()
    footer["status"] = "filled"
    footer["providers"][0]["reason"] = reason
    marker = criteria["ranking_status_footer"]["incomplete_marker"]
    footer["text"] = f"{marker} Provider Failures: Daytona ({reason})"
    scope["slots"].append(footer)
    assert any(
        "chart.status-footer:" in error
        for error in rendered_content_errors(criteria, scope)
        if "differs from the owned" not in error
    )


def test_not_displayed_without_a_reason_has_no_parenthetical() -> None:
    scope = capture()
    scope["slots"].append(
        {
            "slot": "chart.status-footer",
            "status": "not_displayed",
            "providers": [{"provider": "Blaxel"}],
            "text": "Not Displayed: Blaxel",
        }
    )
    assert rendered_content_errors(content_criteria(), scope) == []


def test_status_reason_templates_reject_a_second_provider_name() -> None:
    criteria = content_criteria()
    policy = criteria["ranking_status_footer"]
    policy["names_reason_template"] = "{provider}: {names}"
    policy["reason_templates"]["filled"]["runtime_fields"].append("provider")
    assert "filled: reason template must omit provider and class names" in criteria_errors(criteria)


@pytest.mark.parametrize(
    "literal",
    [
        "Virginia",
        "us-east-1",
        "Oregon",
        "The Dalles",
        "Los Angeles",
        "Sao Paulo",
        "São Paulo",
        "24 Oct",
        "2026-10-24",
        "4 vCPU / 8 GiB",
    ],
)
def test_reader_literals_reject_header_fact_values(literal: str) -> None:
    assert reader_literal_errors(f"The measurement uses {literal}.", content_criteria())


def test_selection_facts_require_declared_placeholders_and_do_not_repeat_the_header() -> None:
    criteria = content_criteria()
    template = "The measurement uses {region}."
    assert reader_literal_errors(template, criteria)
    assert reader_literal_errors(template, criteria, ["region"]) == []
    assert (
        reader_literal_errors("The measurement uses {param:demo.environment.region}.", criteria)
        == []
    )
    for region in ("Virginia", "Oregon"):
        scope = capture()
        scope["slots"][4]["text"] = template.format(region=region)
        scope["page_facts"] = [{"id": "region", "text": region}]
        assert any(
            "repeats page fact region" in error
            for error in rendered_content_errors(criteria, scope)
        )


def test_fact_scan_checks_option_hover_and_nested_reader_notes() -> None:
    document: dict[str, Any] = {
        "id": "demo",
        "version": "1.0.0",
        "procedure": {
            "variants": [
                {
                    "id": "selected",
                    "description": "The request starts in Virginia.",
                    "notes": {"introduction": "The test uses 4 vCPU and 8 GiB."},
                }
            ]
        },
    }
    errors = document_reader_literal_errors(document, content_criteria())
    assert any("literal region fact" in error for error in errors)
    assert sum("literal size fact" in error for error in errors) == 2
    document["procedure"]["variants"][0]["description"] = "The request uses {region}."
    assert any(
        "undeclared runtime placeholder" in error
        for error in document_reader_literal_errors(document, content_criteria())
    )


def test_hover_templates_export_owned_text_and_only_declared_runtime_fields() -> None:
    criteria = content_criteria()
    entries = criteria["hover_templates"]
    assert criteria_errors(criteria) == []
    assert "segment_hover_suffix_median" not in entries
    assert "{name:" not in entries["hover_total_sum_of_tests"]["template"]
    entries["chart_statistic_value"]["runtime_fields"] = []
    assert any("hover template fields" in error for error in criteria_errors(criteria))


def test_fact_guard_distinguishes_regions_from_dom_ids_and_runtime_fields() -> None:
    criteria = content_criteria()
    assert reader_literal_errors("bm-score-0", criteria) == []
    assert reader_literal_errors("The source is {value_0}.", criteria, ["value_0"]) == []
    assert reader_literal_errors("The source is us-east-1.", criteria)


def test_scope_kinds_and_hero_slots_follow_the_existing_owner_metadata() -> None:
    criteria = content_criteria()
    assert "hero-title" not in criteria["slots"]
    assert "hero-subtitle" not in criteria["slots"]
    scope: dict[str, Any] = {
        "kind": "hero",
        "slots": [
            {"slot": "title", "text": "Composite Workload Time"},
            {"slot": "subtitle", "text": "Composite time. Lower is better"},
        ],
    }
    assert rendered_content_errors(criteria, scope) == []
    scope["slots"].append({"slot": "chart", "text": ""})
    assert rendered_content_errors(criteria, scope) == []
    scope["slots"].pop()
    scope["slots"].pop()
    assert "missing required slot subtitle" in rendered_content_errors(criteria, scope)


def test_dynamic_hover_reasons_export_counts_without_fixed_provider_or_test_units() -> None:
    criteria = content_criteria()
    entries = criteria["hover_templates"]
    assert criteria_errors(criteria) == []
    assert "traces" not in entries["index_filled_warning"]["template"]
    assert "github_actions_bar_failures" not in entries


def test_scoring_hover_slots_cover_included_excluded_and_unavailable_scores() -> None:
    criteria = content_criteria()
    assert criteria_errors(criteria) == []
    entries = criteria["hover_templates"]
    expected = {
        "how-we-score": [],
        "provider-section-average": ["counted"],
        "provider-section-average-exclusions": ["left"],
        "provider-section-average-unavailable": [],
    }
    for code, fields in expected.items():
        row = entries[code]
        assert row["owner"] == "methodology"
        assert row["runtime_fields"] == fields
        assert row["form"] == "sentence"
        assert row["slot"] == f"tooltip.{code}"
    assert "mean" in entries["provider-section-average"]["template"]
    assert "best performer" in entries["how-we-score"]["template"]
    assert "in development" not in entries["provider-section-average-unavailable"]["template"]


def not_displayed_footer(rows: list[dict[str, Any]], text: str) -> dict[str, Any]:
    return {
        "slot": "chart.status-footer",
        "status": "not_displayed",
        "providers": rows,
        "text": text,
    }


def test_the_not_displayed_line_names_each_providers_own_status_in_lower_case() -> None:
    criteria = content_criteria()
    statuses = criteria["ranking_statuses"]
    assert {name for name, item in statuses.items() if item.get("footer_group")} == {
        "not_offered",
        "account_limit",
        "in_development",
        "not_measured",
    }
    rows = [
        {"provider": "Blaxel", "status": "in_development"},
        {"provider": "Modal gVisor", "status": "not_offered"},
        {"provider": "Daytona", "status": "not_measured"},
        {"provider": "Vercel", "status": "not_displayed"},
        {"provider": "Boat", "reason": "10 trials"},
    ]
    scope = capture()
    scope["slots"].append(
        not_displayed_footer(
            rows,
            "Not Displayed: Blaxel (not supported), Modal gVisor (not offered), "
            "Daytona (not measured), Vercel, Boat (10 trials)",
        )
    )
    assert rendered_content_errors(criteria, scope) == []


@pytest.mark.parametrize(
    ("row", "text", "error"),
    [
        (
            {
                "provider": "Blaxel",
                "status": "in_development",
                "reason": "Blaxel pauses an idle sandbox by itself",
            },
            "Not Displayed: Blaxel (Blaxel pauses an idle sandbox by itself)",
            "the reason belongs in the drill-down",
        ),
        (
            {"provider": "Blaxel", "reason": "Blaxel pauses an idle sandbox by itself."},
            "Not Displayed: Blaxel (Blaxel pauses an idle sandbox by itself.)",
            "must be a fragment",
        ),
        (
            {"provider": "Blaxel", "reason": "Blaxel pauses an idle sandbox by itself, so no"},
            "Not Displayed: Blaxel (Blaxel pauses an idle sandbox by itself, so no)",
            "not a sentence",
        ),
        (
            {"provider": "Blaxel", "status": "in_development"},
            "Not Displayed: Blaxel (Not supported)",
            "differs from the owned status footer template",
        ),
        (
            {"provider": "Blaxel", "status": "filled"},
            "Not Displayed: Blaxel",
            "does not belong on the not_displayed footer line",
        ),
    ],
)
def test_the_not_displayed_line_rejects_reasons_and_foreign_statuses(
    row: dict[str, Any], text: str, error: str
) -> None:
    scope = capture()
    scope["slots"].append(not_displayed_footer([row], text))
    assert any(error in found for found in rendered_content_errors(content_criteria(), scope))


def provider_notes(rows: list[dict[str, Any]], text: str, opens: bool = True) -> dict[str, Any]:
    return {
        "slot": "chart.provider-notes",
        "providers": rows,
        "opens_details": opens,
        "text": text,
    }


def test_the_provider_notes_line_names_the_providers_only_and_ends_with_the_owner_hint() -> None:
    criteria = content_criteria()
    hint = criteria["ranking_status_footer"]["open_hint"]
    scope = capture()
    scope["slots"].append(
        provider_notes(
            [{"provider": "Blaxel"}, {"provider": "Modal gVisor"}], f"Blaxel, Modal gVisor{hint}"
        )
    )
    assert rendered_content_errors(criteria, scope) == []


@pytest.mark.parametrize(
    ("row", "text", "error"),
    [
        (
            {"provider": "Blaxel", "reason": "Billed on active CPU time"},
            "Blaxel (Billed on active CPU time). Click provider to see details.",
            "its note is in the drill-down",
        ),
        ({"provider": "Blaxel"}, "Blaxel", "differs from the owned provider notes template"),
    ],
)
def test_the_provider_notes_line_rejects_a_note_in_the_line_and_a_missing_hint(
    row: dict[str, Any], text: str, error: str
) -> None:
    scope = capture()
    scope["slots"].append(provider_notes([row], text))
    assert any(error in found for found in rendered_content_errors(content_criteria(), scope))


def test_methodology_labels_are_owned_and_reject_raw_schema_labels() -> None:
    criteria = content_criteria()
    labels = criteria["methodology_page"]["field_labels"]
    assert labels["unit"] == "Unit"
    assert labels["description"] == "Description"
    assert labels["inherits"] == "Based On"
    assert "unpublished_schema_key" not in labels
    broken = copy.deepcopy(criteria)
    broken["methodology_page"]["field_labels"]["unit"] = "unit"
    assert any("not schema keys" in error for error in criteria_errors(broken))


def test_methodology_field_labels_cover_the_packaged_documents() -> None:
    labels = content_criteria()["methodology_page"]["field_labels"]
    missing: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key not in {"id", "version", "effective"} and key not in labels:
                    missing.add(key)
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    policy = content_criteria()["methodology_page"]
    for row in load_methodology():
        public = copy.deepcopy(row.document)
        omit_paths(public, policy["private_evidence_paths"])
        visit(public)
    assert not missing, f"Methodology fields have no owner label: {sorted(missing)}"


def test_methodology_formulas_define_every_variable_and_have_spoken_text() -> None:
    criteria = content_criteria()
    formulas = criteria["methodology_page"]["formulas"]
    assert {"time-index", "cost-index"} <= set(formulas)
    expressions = [row["tex"] for row in formulas.values()]
    assert len(expressions) == len(set(expressions)), "two formulas share one expression"
    broken = copy.deepcopy(criteria)
    formula = broken["methodology_page"]["formulas"]["time-index"]
    formula["variables"] = []
    assert any("variables" in error for error in criteria_errors(broken))
    broken = copy.deepcopy(criteria)
    broken["methodology_page"]["formulas"]["time-index"]["variables"].append(
        {"symbol": "z", "definition": "A symbol the expression lacks."}
    )
    assert any(
        "every variable must be written exactly" in error for error in criteria_errors(broken)
    )
    # A bare letter where the formula writes a subscript is not the formula's symbol.
    broken = copy.deepcopy(criteria)
    broken["methodology_page"]["formulas"]["time-index"]["variables"][1]["symbol"] = "t"
    assert any(
        "every variable must be written exactly" in error for error in criteria_errors(broken)
    )
    # One symbol, two meanings on one page.
    broken = copy.deepcopy(criteria)
    broken["methodology_page"]["formulas"]["exec-latency"]["variables"][0]["symbol"] = "T_i"
    broken["methodology_page"]["formulas"]["exec-latency"]["tex"] += "; T_i = 1"
    assert any("has two meanings" in error for error in criteria_errors(broken))


def test_methodology_pages_omit_versions_and_keep_inheritance_unversioned() -> None:
    criteria = content_criteria()
    byline = criteria["methodology_page"]["byline"]
    assert set(byline) == {"builds_on", "from"}
    assert byline["builds_on"] == "Builds on {document}"
    broken = copy.deepcopy(criteria)
    broken["methodology_page"]["byline"]["effective"] = "Effective {date}"
    assert any("must not show document versions" in error for error in criteria_errors(broken))


def test_calendar_dates_have_one_owner_format_and_a_complete_range() -> None:
    criteria = content_criteria()
    date = criteria["conventions"]["presentation"]["calendar_date"]
    assert date["example"] == "Oct 26, 2026"
    assert date["options"]["timeZone"] == "UTC"
    broken = copy.deepcopy(criteria)
    broken["conventions"]["presentation"]["calendar_date"]["range_template"] = "{from}"
    assert "calendar date ranges require the from and to fields" in criteria_errors(broken)


def test_a_hover_template_that_restates_a_clock_is_refused() -> None:
    criteria = content_criteria()
    assert criteria_errors(criteria) == []
    template = next(iter(criteria["hover_templates"].values()))
    template["template"] = (
        "The job-host clock measures the request until the provider acknowledges it."
    )
    template["authored_template"] = template["template"]
    template["runtime_fields"] = []
    assert any("restate a clock" in error for error in criteria_errors(criteria))


def test_a_sentence_may_start_with_a_tool_name_the_owner_writes_in_lowercase() -> None:
    rule = content_criteria()["slots"]["description"]
    assert slot_errors("fio's command measures sequential read throughput.", rule) == []
    assert slot_errors("stress-ng's link stressor measures link creation.", rule) == []
    assert slot_errors("fiord is not a tool name.", rule) == [
        "a sentence must start with a capital and end with a period"
    ]
    assert slot_errors("fio's command runs.", rule, proper_names=()) == [
        "a sentence must start with a capital and end with a period"
    ]


@pytest.mark.parametrize("reason", ["Provider failure", "Test in development"])
def test_a_displayed_reason_is_one_of_the_generic_set(reason: str) -> None:

    assert exclusion_reason_errors(content_criteria(), reason) == []


@pytest.mark.parametrize(
    "reason",
    [
        "excluded: our benchmark account couldn't reach Maven, not a Blaxel default",
        "snapshots restore in the original sandbox; Core cannot create the four new sandboxes",
        "a provider failure, replaced by a later trial",
        "provider failure",
        "Provider failure.",
        "",
    ],
)
def test_a_displayed_reason_outside_the_generic_set_is_refused(reason: str) -> None:
    errors = exclusion_reason_errors(content_criteria(), reason)
    assert any("generic" in error for error in errors)


def test_the_generic_set_is_two_reasons_the_methodology_owns() -> None:
    rules = content_criteria()["exclusion_reason_rules"]
    assert rules == {
        "generic_reasons": {
            "provider_failure": "Provider failure",
            "test_in_development": "Test in development",
        }
    }


@pytest.mark.parametrize(
    ("tex", "refused"),
    [
        (r"T_{i,j} = e_{i,j} - s_{i,j}", True),
        (r"U = a - b; \quad G = l - k; \quad R = U - G", True),
        (r"F = e - s_{\mathrm{resume}} - r", True),
        (r"T_i = \max_{j} \left( e_{i,j} \right) - \min_{j} \left( s_{i,j} \right)", False),
        (
            r"\tilde{t} = \operatorname{median}_{i,c} (t_{i,c}), \quad L_{i,c} = t_{i,c} - r_{i,c}",
            False,
        ),
        (r"T = \sum_{k=1}^{K} \left( e_k - s_k \right)", False),
    ],
)
def test_a_test_formula_that_only_subtracts_is_refused_and_common_is_exempt(
    tex: str, refused: bool
) -> None:
    criteria = content_criteria()
    broken = copy.deepcopy(criteria)
    formulas = broken["methodology_page"]["formulas"]
    formulas["time-index"]["tex"] = tex
    assert any("only subtracts" in error for error in criteria_errors(broken)) is refused
    formulas["time-index"]["tests"] = ["sbs.common"]
    assert not any("only subtracts" in error for error in criteria_errors(broken))


def test_the_provider_capabilities_panel_and_hidden_line_words_have_an_owner() -> None:
    """docs-header, 2026-11-04: web typed these words; they are published and resolved here."""
    criteria = content_criteria()
    panel = criteria["provider_capabilities"]
    assert panel["title"] == "{provider}'s Provider Capabilities"
    assert panel["count"] == "{on} of {total} supported"
    assert panel["supported"] == "Supported"
    # A capability the provider lacks is its own string, not the ranking status (same words today).
    assert panel["not_supported"] == "Not Supported"
    assert criteria["ranking_status_footer"]["hidden_label"] == "Hidden"
    assert criteria_errors(criteria) == []


def test_the_hash_link_card_lines_have_tooltip_owner_records() -> None:
    """bench-panels, 2026-11-04: the card's kind, hash, copy and copied lines resolve against a
    hover template generated from methodology_page.hash_link, so the words keep one owner."""
    criteria = content_criteria()
    card = criteria["methodology_page"]["hash_link"]
    templates = criteria["hover_templates"]
    assert templates["hash_link_kind"]["template"] == card["kind"]
    assert templates["hash_link_copy"]["template"] == card["copy"]
    assert templates["hash_link_copied"]["template"] == card["copied"]
    assert templates["hash_link_hash"]["runtime_fields"] == ["hash"]
    for code in ("hash_link_kind", "hash_link_hash", "hash_link_copy", "hash_link_copied"):
        assert templates[code]["slot"] == f"tooltip.{code}"
        assert templates[code]["owner"] == "methodology"
        assert templates[code]["form"] == "fragment"  # the card draws no action-label line
