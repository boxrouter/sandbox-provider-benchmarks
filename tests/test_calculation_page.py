# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""A document that combines other documents' results has the calculation template, and the three
scores are sections of the Common page (meth-refactor changes 5; Say It Once 46-2A).

A composite and a score run no trials of their own, so their page has no Machine Size, no Clock,
no Measurement, Statistics or Cost section, and the scores have no page.
"""

from pathlib import Path
from typing import Any

import pytest

from sandbox_benchmark_verifier.content import content_criteria
from sandbox_benchmark_verifier.names import load_tables
from sandbox_benchmark_verifier.versions import version_key

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
COMPOSITES = ("time-index", "cost-index", "time-vs-cost")
SCORE_DOCUMENTS = ("index.speed_score", "index.cost_score", "index.provider_score")


def _newest(bundle: dict[str, Any], identifier: str) -> dict[str, Any]:
    versions = bundle["methodologies"][identifier]
    newest = max(versions, key=version_key)
    definition: dict[str, Any] = versions[newest]["reader_definition"]
    return definition


@pytest.mark.parametrize("identifier", [*COMPOSITES, *SCORE_DOCUMENTS])
def test_a_calculation_takes_no_environment_clock_or_lifecycle_from_common(
    bundle: dict[str, Any], identifier: str
) -> None:
    definition = _newest(bundle, identifier)
    for field in ("environment", "clocks", "ready_check", "execution"):
        assert field not in definition, f"{identifier} inherits {field}"
    assert definition["statistics"]["primary"]


def test_a_test_keeps_the_environment_and_clock_it_inherits(bundle: dict[str, Any]) -> None:
    definition = _newest(bundle, "sequential-tti")
    assert definition["environment"]["size"]
    assert definition["clocks"]["operation"]


def test_the_calculation_template_hides_the_sections_it_has_no_facts_for() -> None:
    page = content_criteria()["methodology_page"]
    template = page["calculation_template"]
    assert template["when"] == "composition.inputs"
    assert template["hidden_sections"] == ["measurement", "statistics", "cost"]
    assert set(template["hidden_sections"]) <= set(page["sections"])
    glance = [source for group in template["at_a_glance"]["groups"] for source in group]
    assert "environment.size" not in glance
    assert not any(source.startswith("clocks.") for source in glance)
    assert "composition.scopes" in glance
    assert set(template["not_inherited"]) <= set(page["inherited_fields"])


def test_the_three_scores_are_one_section_of_common_and_have_no_page() -> None:
    page = content_criteria()["methodology_page"]
    names = load_tables(ROOT).names
    sections = {row["document"]: row["common_section"] for row in names if "common_section" in row}
    assert sections == {"index.provider_score": "provider-scores"}
    drawn = {row["document"] for row in names if row.get("common_subsection") == "provider-scores"}
    assert drawn == {"index.speed_score", "index.cost_score"}
    assert set(page["common_sections"]) & {"time-score", "cost-score"} == set()
    policy = page["common_sections"]["provider-scores"]
    assert policy["sources"]
    assert "document" not in policy, "the names table owns which document a section draws"
    assert policy["name"] == "Provider Scores"
    # Time Score and Cost Score are subsections of it: the contract lists them (their order and
    # what each hides); the names table says which hero drew them.
    subsections = {row["id"] for row in names if row.get("common_subsection") == "provider-scores"}
    assert [item["id"] for item in policy["subsections"]] == ["time-score", "cost-score"]
    assert subsections == {item["id"] for item in policy["subsections"]}
    assert [item["name"] for item in policy["subsections"]] == ["Time Score", "Cost Score"]


def test_a_score_section_follows_statistics_in_common() -> None:
    page = content_criteria()["methodology_page"]
    order = sorted(
        page["common_sections"], key=lambda key: page["common_sections"][key].get("order", 99)
    )
    assert order.index("statistics") < order.index("provider-scores") < order.index("independence")
