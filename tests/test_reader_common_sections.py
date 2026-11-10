# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""The Common methodology page draws the reader's notes over the definition's prose.

The reader owns the display prose of each shared-rule section and the definition owns the spec
(2026-11-01): a Common section that has a reader note draws that note instead of the
definition's prose (it may add owner sentences the note cannot reference), and every reader note
of the Common entry is drawn by exactly one section.
"""

import re
from pathlib import Path
from typing import Any

import pytest

from sandbox_benchmark_verifier import reader
from sandbox_benchmark_verifier.content import content_criteria
from sandbox_benchmark_verifier.versions import version_key

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
# The notes that are not section prose: the page draws them at its top.
PAGE_NOTES = {"summary", "opening", "introduction", "how_to_read"}


def _common_sections() -> dict[str, list[str]]:
    page = content_criteria()["methodology_page"]
    merged = {
        section: {**page["sections"].get(section, {}), **page["common_sections"].get(section, {})}
        for section in {*page["sections"], *page["common_sections"]}
    }
    return {section: list(policy["sources"]) for section, policy in merged.items()}


def _common_notes() -> set[str]:
    source = reader.load_reader(ROOT)
    assert source is not None
    return set(source["methods"]["common"]["notes"]) - PAGE_NOTES


def test_every_common_section_note_is_drawn_by_exactly_one_section() -> None:
    sections = _common_sections()
    for note in sorted(_common_notes()):
        drawing = [name for name, sources in sections.items() if f"notes.{note}" in sources]
        assert len(drawing) == 1, f"notes.{note} is drawn by {drawing}"


def test_a_section_that_draws_a_reader_note_draws_it_first() -> None:
    notes = {f"notes.{note}" for note in _common_notes()}
    for name, sources in _common_sections().items():
        if notes & set(sources):
            assert sources[0] in notes, f"{name} draws {sources[0]} before its reader note"


def _drawn(prefix: str) -> list[str]:
    return [
        source.removeprefix(prefix)
        for sources in _common_sections().values()
        for source in sources
        if source.startswith(prefix)
    ]


def test_every_shared_concept_is_drawn_by_exactly_one_common_section() -> None:
    """The index glossary moved into Common (meth-refactor change 6): each concept stands in the
    section that owns its rule, once."""
    concepts = set(content_criteria()["shared_concepts"])
    drawn = _drawn("concept:")
    assert sorted(drawn) == sorted(concepts)


def test_every_common_formula_is_drawn_by_exactly_one_common_section_under_its_name() -> None:
    """Common has no Metrics section: each formula stands in the section that uses it. A formula
    has its name, or follows the concept that names it."""
    page = content_criteria()["methodology_page"]
    common = {key for key, row in page["formulas"].items() if "sbs.common" in row.get("tests", [])}
    assert sorted(_drawn("formula:")) == sorted(common)
    for sources in _common_sections().values():
        for at, source in enumerate(sources):
            if not source.startswith("formula:"):
                continue
            named = page["formulas"][source.removeprefix("formula:")].get("name")
            after_concept = at > 0 and any(
                sources[back].startswith("notes.") or sources[back].startswith("concept:")
                for back in range(at)
            )
            assert named or after_concept, f"{source} has no name"


def test_common_draws_its_sections_in_the_order_of_a_trials_life() -> None:
    page = content_criteria()["methodology_page"]
    sections = _common_sections()
    ordered = sorted(
        (section for section in page["common_sections"] if sections[section]),
        key=lambda section: page["common_sections"][section]["order"],
    )
    assert ordered == [
        "trial-lifecycle",
        "measurement",
        "failures",
        "statistics",
        "provider-scores",
        "independence",
        "cost",
        "sandbox",
        "requirements",
    ]
    assert page["sections"]["trial-lifecycle"]["name"] == "Trial Lifecycle"


def test_a_test_page_draws_failures_before_statistics() -> None:
    sections = content_criteria()["methodology_page"]["sections"]
    order = sorted(sections, key=lambda section: sections[section]["order"])
    assert order.index("failures") < order.index("statistics")


def test_the_default_sandbox_section_draws_the_standard_image(bundle: dict[str, Any]) -> None:
    """The Common page shows the standard image's digest, registry and pull command."""
    assert "environment.image" in _common_sections()["sandbox"]
    versions = bundle["methodologies"]["sbs.common"]
    newest = max(versions, key=version_key)
    image = versions[newest]["reader_definition"]["environment"]["image"]
    assert image["reference"]


def _statistics_texts() -> list[str]:
    """Every text Common's Statistics section draws, one entry per source, as written."""
    criteria = content_criteria()
    notes = reader.load_reader(ROOT)["methods"]["common"]["notes"]  # type: ignore[index]
    texts: list[str] = []
    for source in _common_sections()["statistics"]:
        if source.startswith("notes."):
            texts.append(notes[source.removeprefix("notes.")])
        elif source.startswith("concept:"):
            concept = criteria["shared_concepts"][source.removeprefix("concept:")]
            texts.extend(
                str(concept[field]) for field in ("meaning", "example") if field in concept
            )
    return texts


def test_common_statistics_draws_no_sentence_twice() -> None:
    """A statistic is defined once, under its own block: the rule sentence is the definition, so
    its concept holds no second short definition (meth-refactor, Statistics)."""
    sentences = [
        " ".join(sentence.split()).casefold()
        for text in _statistics_texts()
        for sentence in re.split(r"(?<=[.!?])\s+", text.replace("\n", " "))
        if sentence.strip()
    ]
    repeated = {sentence for sentence in sentences if sentences.count(sentence) > 1}
    assert not repeated


def test_a_concept_without_a_meaning_is_followed_by_the_rule_text_that_defines_it() -> None:
    concepts = content_criteria()["shared_concepts"]
    for name, sources in _common_sections().items():
        for at, source in enumerate(sources):
            if not source.startswith("concept:"):
                continue
            key = source.removeprefix("concept:")
            if "meaning" not in concepts[key]:
                following = sources[at + 1] if at + 1 < len(sources) else ""
                assert following.startswith("notes."), f"{name}: {key} defines nothing"


def test_common_statistics_has_one_worked_example_the_ranking_floors() -> None:
    concepts = content_criteria()["shared_concepts"]
    drawn = [
        source.removeprefix("concept:")
        for source in _common_sections()["statistics"]
        if source.startswith("concept:")
    ]
    assert [key for key in drawn if "example" in concepts[key]] == ["ranking-floor"]


def _kebab(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")


def _statistics_block_names() -> list[str]:
    """The heading of each block Common's Statistics section draws, as the page shows it."""
    criteria = content_criteria()
    page = criteria["methodology_page"]
    names: list[str] = []
    for source in _common_sections()["statistics"]:
        if source.startswith("concept:"):
            names.append(criteria["shared_concepts"][source.removeprefix("concept:")]["name"])
        elif source.startswith("formula:"):
            named = page["formulas"][source.removeprefix("formula:")].get("name")
            if named:
                names.append(named)
        elif source in page["field_labels"]:
            names.append(page["field_labels"][source])
    return names


def test_every_statistics_block_has_a_heading_to_anchor_to() -> None:
    assert [_kebab(name) for name in _statistics_block_names()] == [
        "median",
        "nearest-rank-percentile",
        "mean",
        "interval",
        "ranking-floor",
        "workload-value",
        "stage-segments",
    ]


def test_statistic_links_point_at_blocks_of_the_statistics_section() -> None:
    """Test pages link the statistics they name to Common's blocks: each link terms list belongs to
    a block anchor, and no term is listed twice."""
    links = content_criteria()["methodology_page"]["statistic_links"]
    anchors = {_kebab(name) for name in _statistics_block_names()}
    assert set(links) <= anchors
    terms = [term.casefold() for words in links.values() for term in words]
    assert len(terms) == len(set(terms))
