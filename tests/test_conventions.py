# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Shared reader-text conventions live in one data source."""

import json
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import yaml

from sandbox_benchmark_verifier.catalog import _assets
from sandbox_benchmark_verifier.content import (
    content_criteria,
    criteria_errors,
    rendered_content_errors,
    slot_errors,
)
from sandbox_benchmark_verifier.conventions import (
    REFERENCE,
    SOURCE,
    check_conventions,
    compatibility_errors,
    compatibility_exports,
    load_conventions,
    reference_text,
)
from sandbox_benchmark_verifier.text_predicates import format_violation


def slot_convention_errors(
    text: str,
    slot_id: str,
    *,
    conventions: dict[str, Any] | None = None,
    label: str | None = None,
    context: str = "panel",
) -> list[str]:
    record = {"slot": slot_id, "text": text, **({"label": label} if label else {})}
    return [
        format_violation(finding)
        for finding in check_conventions(
            {"slots": [record], "context": context}, conventions=conventions
        )
        if finding["severity"] == "error" and finding["status"] == "fail"
    ]


def test_bundle_criteria_exports_the_exact_convention_source() -> None:
    conventions = load_conventions()
    assert content_criteria()["conventions"] == conventions


@pytest.mark.parametrize(
    ("slot", "text", "label", "expected"),
    [
        ("description", "One elapsed time is recorded per successful job.", None, False),
        ("description", "one elapsed time is recorded.", None, True),
        ("description", "Elapsed time per job.", None, False),
        ("description", "Time is recorded. Cost is recorded.", None, False),
        ("description", "Time is recorded. Cost is recorded. Both are final.", None, True),
        ("description", "The page shows a timing result.", None, True),
        ("description", "The result uses internal_field.", None, True),
        ("description", "The value is {value}.", None, True),
        ("description", "The chart shows a typical value.", None, True),
        ("caption", "The chart shows results.", None, True),
        ("details.latest-result", "Vercel leads with a value.", None, True),
        ("tooltip", "The result uses the middle successful value.", "Median", False),
        ("tooltip", "The median uses the middle successful value.", "Median", True),
        ("tooltip", "", None, True),
        ("tooltip", "The result is " + "long " * 30 + "here.", None, True),
        ("title", "Elapsed Time", None, False),
        ("title", "elapsed time", None, True),
        ("subtitle", "Lower is better", None, True),
        ("subtitle", "Lower Is Better", None, True),
    ],
)
def test_shared_resolved_slot_api(slot: str, text: str, label: str | None, expected: bool) -> None:
    assert bool(slot_convention_errors(text, slot, label=label)) is expected


def test_the_retired_word_is_allowed_in_no_context() -> None:
    text = "The typical value combines the workload measurements."
    assert slot_convention_errors(text, "description")
    assert slot_convention_errors(text, "description", context="statistics-glossary")


def test_reference_is_generated_and_a_source_edit_changes_validation_and_reference(
    tmp_path: Path,
) -> None:
    root = _assets()
    assert (root / REFERENCE).read_text(encoding="utf-8") == reference_text(load_conventions())
    for source in (SOURCE, "schemas/text-conventions.schema.json"):
        target = tmp_path / source
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / source, target)
    path = tmp_path / SOURCE
    conventions = yaml.safe_load(path.read_text(encoding="utf-8"))
    conventions["slots"]["description"]["max_length"] = 20
    path.write_text(yaml.safe_dump(conventions), encoding="utf-8")
    changed = load_conventions(tmp_path)
    sentence = "One elapsed time is recorded per successful job."
    assert slot_convention_errors(sentence, "description") == []
    assert slot_convention_errors(sentence, "description", conventions=changed)
    assert reference_text(changed) != reference_text(load_conventions())


def test_public_conventions_omit_editorial_decision_history() -> None:
    def mapping_keys(value: Any) -> set[str]:
        if isinstance(value, dict):
            return set(value) | set().union(*(mapping_keys(item) for item in value.values()))
        if isinstance(value, list):
            return set().union(*(mapping_keys(item) for item in value))
        return set()

    forbidden = {"decision", "decisions", "decided"}
    root = _assets()
    source = yaml.safe_load((root / SOURCE).read_text(encoding="utf-8"))
    assert mapping_keys(source).isdisjoint(forbidden)
    assert mapping_keys(load_conventions()).isdisjoint(forbidden)


def test_new_predicate_rules_execute_without_rule_specific_code() -> None:
    from_copy = load_conventions()
    rule = dict(from_copy["rules"][0])
    rule.update(id="test.new-limit", text="Use at most two characters.")
    rule["predicate"] = {"kind": "max_chars", "value": 2}
    from_copy["rules"] = [rule]
    findings = check_conventions(
        {"slots": [{"slot": "description", "text": "Long"}]}, conventions=from_copy
    )
    assert findings[0]["rule_id"] == "test.new-limit"
    assert findings[0]["slot"] == "description"


@pytest.mark.parametrize(
    "mutation", ["unknown", "order", "empty", "empty_limits", "nine_limits", "missing_full_method"]
)
def test_typed_details_shape_rejects_invalid_parts(mutation: str) -> None:
    parts: list[dict[str, Any]] = [
        {"kind": "setup", "text": "A monotonic clock times the command."},
        {"kind": "trial_steps", "text": "The test records elapsed time."},
        {"kind": "limitations", "items": ["The result excludes image preparation."]},
        {
            "kind": "sources",
            "links": [{"label": "Full Method", "href": "https://example.org/method"}],
        },
    ]
    assert not check_conventions({"slots": [], "details": parts})
    if mutation == "unknown":
        parts[0]["kind"] = "invented"
    elif mutation == "order":
        parts[0], parts[1] = parts[1], parts[0]
    elif mutation == "empty":
        parts[0]["text"] = "  "
    elif mutation == "empty_limits":
        parts[2]["items"] = []
    elif mutation == "nine_limits":
        parts[2]["items"] = ["A limit."] * 9
    else:
        parts[3]["links"] = [{"label": "Other", "href": "https://example.org/method"}]
    assert any(
        row["rule_id"] == "details.typed-shape"
        for row in check_conventions({"slots": [], "details": parts})
    )


def test_hero_subtitle_uses_required_short_description_exact_period_and_metric_direction() -> None:
    record: dict[str, Any] = {
        "slot": "subtitle",
        "text": "Create to first command. Lower is better.",
        "values": {"hero_description": "Create to first command", "direction": "Lower"},
        "metric_direction": "lower_is_better",
    }
    assert check_conventions({"slots": [record]}) == []
    wrong = {**record, "text": record["text"].replace(". ", " - ")}
    assert any(
        row["rule_id"] == "hero.exact-separator" for row in check_conventions({"slots": [wrong]})
    )
    assert any(
        row["rule_id"] == "hero.metric-direction"
        for row in check_conventions(
            {"slots": [{**record, "metric_direction": "higher_is_better"}]}
        )
    )
    assert check_conventions({"slots": [{**record, "values": {}}]})


@pytest.mark.parametrize(
    "slot",
    [
        "description",
        "tooltip",
        "details.introduction",
        "section-intro",
        "hero-description",
        "chart.status-footer",
        "methodology-reader-entry",
    ],
)
def test_all_reader_slots_start_with_capitals(slot: str) -> None:
    findings = check_conventions({"slots": [{"slot": slot, "text": "lower text is recorded."}]})
    assert any(row["rule_id"] == "reader.capital-start" for row in findings)


def test_readability_and_jargon_are_advisory_without_failing_text() -> None:
    text = "The calculation combines computational measurements per workload."
    findings = check_conventions({"slots": [{"slot": "description", "text": text}]})
    advisory = {row["rule_id"] for row in findings if row["severity"] == "advisory"}
    assert {"reader.readability", "reader.jargon"} <= advisory
    assert slot_convention_errors(text, "description") == []


def test_diagnostics_redact_boundary_terms_and_preserve_rule_slot_and_fix_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PUBLIC_TEXT_BLOCKLIST", "SyntheticPrivateWord")
    findings = check_conventions(
        {"slots": [{"slot": "description", "text": "syntheticprivateword is recorded."}]}
    )
    finding = next(row for row in findings if row["rule_id"] == "reader.capital-start")
    assert "syntheticprivateword" not in str(finding).lower()
    assert "[redacted]" in finding["offending_text"]
    assert "capital" in finding["message"]


def test_compatibility_rules_have_no_second_authored_contract_copy() -> None:
    contract = json.loads((_assets() / "contracts/text/1.0.0.json").read_text())
    assert {"slots", "panels", "method_pages", "sentence_rule", "detail_headings"}.isdisjoint(
        contract
    )
    criteria = json.loads(json.dumps(content_criteria()))
    assert compatibility_errors(criteria) == []
    for slot, rule in criteria["conventions"]["slots"].items():
        for field, value in rule.items():
            assert criteria["slots"][slot][field] == value


@pytest.mark.parametrize("kind", list(content_criteria()["panels"]))
@pytest.mark.parametrize("retired_slot", load_conventions()["panel"]["forbidden_slots"])
def test_each_compatibility_panel_detects_reintroduced_retired_requirements(
    kind: str, retired_slot: str
) -> None:
    criteria = content_criteria()
    criteria["panels"][kind]["required_slots"].append(retired_slot)
    assert retired_slot not in criteria["conventions"]["panel"]["required_slots"]
    assert any(f"{kind}.required_slots" in error for error in criteria_errors(criteria))


def test_compatibility_slot_changes_cannot_silently_restore_caption_sentences() -> None:
    criteria = content_criteria()
    criteria["slots"]["caption"]["form"] = "sentence"
    assert criteria["conventions"]["slots"]["caption"]["form"] == "forbidden"
    assert any("caption" in error for error in criteria_errors(criteria))


def test_a_source_policy_edit_changes_all_generated_views_without_aliasing() -> None:
    policy = deepcopy(load_conventions())
    policy["slots"]["description"]["max_length"] = 20
    policy["panel"]["required_slots"].append("details.sources")
    exports = compatibility_exports(policy)
    assert exports["slots"]["description"]["max_length"] == 20
    assert exports["panels"]["workload"]["required_slots"] == policy["panel"]["required_slots"]
    exports["slots"]["description"]["max_length"] = 1
    exports["panels"]["workload"]["required_slots"].clear()
    assert policy["slots"]["description"]["max_length"] == 20
    assert policy["panel"]["required_slots"]


@pytest.mark.parametrize("slot", load_conventions()["panel"]["forbidden_slots"])
def test_retired_slots_fail_the_rendered_contract_without_becoming_required(slot: str) -> None:
    criteria = content_criteria()
    scope: dict[str, Any] = {
        "kind": "benchmark",
        "slots": [
            {"slot": "title", "text": "Demo"},
            {"slot": "description", "text": "The command runs in a fresh sandbox."},
            {"slot": "axis-title", "text": "Time (s)"},
        ],
    }
    assert rendered_content_errors(criteria, scope) == []
    scope["slots"].append({"slot": slot, "text": "This slot is retired."})
    assert any(error.startswith(slot + ":") for error in rendered_content_errors(criteria, scope))


def test_rendered_hero_uses_the_existing_hero_contract_for_any_methodology_kind() -> None:
    criteria = content_criteria()
    scope: dict[str, Any] = {
        "kind": "index",
        "variant": "hero",
        "slots": [
            {"slot": "title", "text": "Example heading"},
            {"slot": "subtitle", "text": "An example explanatory subtitle."},
        ],
    }

    assert rendered_content_errors(criteria, scope) == []
    scope["slots"].pop()
    assert rendered_content_errors(criteria, scope) == ["missing required slot subtitle"]


def _panel(description: str, axis: str = "Time (ms)") -> dict[str, Any]:
    return {
        "kind": "benchmark",
        "slots": [
            {"slot": "title", "text": "Demo"},
            {"slot": "description", "text": description},
            {"slot": "axis-title", "text": axis},
        ],
    }


def test_every_direction_phrase_ends_with_a_full_stop() -> None:
    subtitle = load_conventions()["subtitle"]
    for phrase in (*subtitle["directions"].values(), subtitle["time_vs_cost_direction"]):
        assert phrase.endswith(".") and not phrase.endswith(".."), phrase


def test_direction_ends_the_subtitle_once_and_never_sits_on_an_axis() -> None:
    criteria = content_criteria()
    good = _panel("The command runs in a fresh sandbox. Lower is better.")
    good["directions"] = ["Lower is better."]
    assert rendered_content_errors(criteria, good) == []
    on_axis = _panel(
        "The command runs in a fresh sandbox. Lower is better.", "Time (ms) Lower is better."
    )
    on_axis["directions"] = ["Lower is better."]
    assert any(
        e.startswith("axis-title: direction") for e in rendered_content_errors(criteria, on_axis)
    )
    repeated = _panel("Lower is better. Lower is better.")
    repeated["directions"] = ["Lower is better."]
    assert any("exactly one direction" in e for e in rendered_content_errors(criteria, repeated))
    no_stop = _panel("The command runs in a fresh sandbox. Lower is better")
    no_stop["directions"] = ["Lower is better."]
    assert any("exactly one direction" in e for e in rendered_content_errors(criteria, no_stop))
    missing = _panel("The command runs in a fresh sandbox.")
    missing["directions"] = ["Lower is better."]
    assert any("exactly one direction" in e for e in rendered_content_errors(criteria, missing))


def test_the_direction_and_inline_code_checks_read_their_conventions() -> None:
    criteria = deepcopy(content_criteria())
    on_axis = _panel("Lower is better. Lower is better.", "`Time` (ms) Lower is better.")
    on_axis["directions"] = ["Lower is better."]
    assert {e.split(":")[0] for e in rendered_content_errors(criteria, on_axis)} >= {
        "axis-title",
        "description",
    }
    criteria["conventions"]["axis"]["max_direction_occurrences"] = 1
    criteria["conventions"]["subtitle"]["max_direction_occurrences"] = 2
    criteria["conventions"]["inline_code"]["mark"] = "~"
    assert rendered_content_errors(criteria, on_axis) == []


def test_rendered_text_has_no_raw_backticks_and_heroes_may_title_their_axis() -> None:
    criteria = content_criteria()
    raw = _panel("Sequential `true` commands expose latency.")
    assert any("raw backtick" in e for e in rendered_content_errors(criteria, raw))
    hero: dict[str, Any] = {
        "kind": "index",
        "variant": "hero",
        "slots": [
            {"slot": "title", "text": "Example heading"},
            {"slot": "subtitle", "text": "An example explanatory subtitle."},
            {"slot": "axis-title", "text": "Time (ms)"},
        ],
    }
    assert rendered_content_errors(criteria, hero) == []


def _presentation_rule(conventions: dict[str, Any], rule_id: str) -> dict[str, Any]:
    rules: list[dict[str, Any]] = conventions["presentation"]["rules"]
    return next(rule for rule in rules if rule["id"] == rule_id)


def test_a_rule_parameter_is_declared_and_named_in_its_prose(tmp_path: Path) -> None:
    root = _assets()
    for source in (SOURCE, "schemas/text-conventions.schema.json"):
        target = tmp_path / source
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / source, target)
    path = tmp_path / SOURCE
    conventions = yaml.safe_load(path.read_text(encoding="utf-8"))
    rule = _presentation_rule(conventions, "bar-offset-fixed")
    rule["parameters"]["spare_px"] = 3
    path.write_text(yaml.safe_dump(conventions), encoding="utf-8")
    with pytest.raises(ValueError, match="never named"):
        load_conventions(tmp_path)
    del rule["parameters"]["spare_px"]
    rule["text"] += " Add {parameters.missing_px} px."
    path.write_text(yaml.safe_dump(conventions), encoding="utf-8")
    with pytest.raises(ValueError, match="undeclared parameter missing_px"):
        load_conventions(tmp_path)


def test_a_jargon_pattern_that_is_not_a_string_is_refused(tmp_path: Path) -> None:
    policy = load_conventions()
    root = _assets()
    for source in (SOURCE, "schemas/text-conventions.schema.json"):
        target = tmp_path / source
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / source, target)
    policy["readability"]["jargon_patterns"].append(policy["rules"][-1])
    (tmp_path / SOURCE).write_text(yaml.safe_dump(policy), encoding="utf-8")
    with pytest.raises(ValueError, match="not of type 'string'"):
        load_conventions(tmp_path)


def test_authored_subtitles_may_use_two_sentences_while_generated_text_needs_a_verb() -> None:
    slots = content_criteria()["slots"]
    two = "Rollouts of one coding task share a prepared snapshot. No model is called."
    assert slot_errors(two, slots["description"]) == []
    assert slot_errors(two, slots["section-intro"]) == []
    three = "One sentence here. Another one here. A third one here."
    assert any("at most 2 sentences" in error for error in slot_errors(three, slots["description"]))
    # The verb list applies to generated text only; the fragment rule no longer exists.
    noun_phrase = "Elapsed time per job."
    assert slot_errors(noun_phrase, slots["description"]) == []
    assert slot_convention_errors(noun_phrase, "description") == []
    assert any(
        "finite predicate" in error
        for error in slot_errors(noun_phrase, slots["description"], generated=True)
    )
    assert slot_convention_errors(noun_phrase, "description", context="generated")
    rule_ids = {rule["id"] for rule in content_criteria()["conventions"]["rules"]}
    assert "sentence.no-fragment" not in rule_ids
    assert "fragment_pattern" not in content_criteria()["conventions"]["sentence_rule"]


def test_the_frontier_subtitle_template_starts_with_an_index_verb() -> None:
    conventions = load_conventions()
    rule = next(
        rule
        for rule in conventions["methodology_style"]["rules"]
        if rule["id"] == "methodology-style.introduction-sentence"
    )
    assert conventions["subtitle"]["frontier_template"].split()[0] in rule["index_verbs"]


def test_the_drilldown_marks_have_one_owner_and_the_rule_names_them() -> None:
    """The drill-down's marks come from ranking_status_footer; the columns rule cites them."""
    policy = content_criteria()["ranking_status_footer"]
    rules = {rule["id"]: rule for rule in load_conventions()["presentation"]["rules"]}
    text = rules["drilldown-statistic-columns"]["text"]
    for key in ("substituted_marker", "worst_trial_marker"):
        assert policy[key]
        assert f"ranking_status_footer.{key}" in text


def test_the_details_pane_owner_has_the_composite_labels_and_one_composite_sentence(
    bundle: dict[str, Any],
) -> None:
    """the labels, the line and the sentence each have one owner."""
    composite = load_conventions()["details"]["composite"]
    assert (
        composite["overview_headings"]["details.setup"]
        == composite["unit_headings"]["details.setup"]
    )
    block = composite["sentence_source"].removeprefix("reader.common.blocks.").split(".")
    sentence = bundle["reader"]["common"]["blocks"][block[0]][block[1]]
    common = bundle["reader"]["methods"]["common"]["text"]["notes"]["workload_value"]
    assert sentence in common


def test_the_composite_details_part_is_a_slot_the_first_part_and_a_shape() -> None:
    """bench-panels, 2026-11-04: web draws details.composite first; the owner names it everywhere
    the other Details parts are named, so the render check accepts the order web draws."""
    criteria = content_criteria()
    conventions = criteria["conventions"]
    assert "details.composite" in criteria["slots"]
    assert conventions["details"]["part_order"][0] == "details.composite"
    parts = conventions["shapes"]["details"]["parts"]
    assert parts[0]["kind"] == "composite"
    assert parts[0]["slot"] == "details.composite"
    assert conventions["panel"]["slot_order"].index("details.composite") < conventions["panel"][
        "slot_order"
    ].index("details.setup")
