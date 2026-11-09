# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""One title template fills every panel's title ."""

from pathlib import Path
from typing import Any

import pytest

from sandbox_benchmark_verifier.conventions import load_conventions
from sandbox_benchmark_verifier.panel_titles import (
    fill_title,
    panel_title,
    title_errors,
    title_template,
)

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[1]


def _rule(identifier: str) -> dict[str, Any]:
    rules: dict[str, dict[str, Any]] = {
        rule["id"]: rule for rule in load_conventions()["presentation"]["rules"]
    }
    return rules[identifier]


def test_the_template_is_category_unit_measure_tool_variant() -> None:
    assert load_conventions()["title"] == {
        "template": "{category}[ {unit}] {measure}[ ({tool})][{separator}{variant}]",
        "composite_unit_statistic": "typical",
        "whole_name_kinds": ["test", "view"],
        "measure_in_name": ["sequential-tti", "burst-tti"],
    }


def test_the_rule_uses_the_declared_title_template() -> None:
    rule = _rule("panel-title-pattern")
    assert rule["parameters"] == {"separator": " · "}
    assert rule["predicate"]["value"] == "panel-title-equals-template"
    assert rule["applies_to"] == {"slots": ["presentation.panel"]}
    assert (rule["severity"], rule["target"]) == ("error", "scope")
    assert "workload-title-pattern" not in {
        item["id"] for item in load_conventions()["presentation"]["rules"]
    }


def test_a_part_that_is_absent_drops_its_group() -> None:
    template = title_template(ROOT)
    assert fill_title(template, {"category": "R2E-Gym", "separator": " · ", "measure": "Time"}) == (
        "R2E-Gym Time"
    )
    assert panel_title(
        ROOT, category="R2E-Gym", unit="Composite", measure="Time", variant="All Tasks"
    ) == ("R2E-Gym Composite Time · All Tasks")
    assert panel_title(ROOT, category="CPU", measure="Speed", tool="7-Zip") == "CPU Speed (7-Zip)"
    assert panel_title(
        ROOT, category="Disk", measure="Speed", tool="fio", variant="Sequential Read"
    ) == ("Disk Speed (fio) · Sequential Read")


def test_a_variant_that_is_a_tests_full_name_titles_the_panel_by_itself() -> None:
    for category, variant, measure, title in [
        ("Create", "Sequential TTI", "Time", "Sequential TTI"),
        ("Create", "Burst TTI", "Time", "Burst TTI"),
        ("Files", "Read and Write", "Time", "Read and Write Time"),
        ("Snapshots", "Snapshot and Restore", "Time", "Snapshot and Restore Time"),
        ("Pause and Resume", "Delayed Resume", "Time", "Delayed Resume Time"),
        # A test whose selector has an option is a choice within its panel, not the panel's name.
        ("Disk", "Sequential Read", "Speed", "Disk Speed · Sequential Read"),
        ("R2E-Gym", "All Tasks", "Time", "R2E-Gym Time · All Tasks"),
    ]:
        assert panel_title(ROOT, category=category, variant=variant, measure=measure) == title


def test_the_retired_hyphen_form_and_unfilled_parts_are_errors() -> None:
    parts = {"category": "CPU", "measure": "Speed", "tool": "7-Zip"}
    assert title_errors(ROOT, "CPU Speed (7-Zip)", **parts) == []
    assert title_errors(ROOT, "CPU Speed - 7-Zip", **parts)
    assert title_errors(ROOT, "CPU Speed ({tool})", **parts)
