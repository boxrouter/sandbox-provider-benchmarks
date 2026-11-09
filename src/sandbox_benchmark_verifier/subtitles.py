# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""A workload panel's subtitle: one owner template per selection level, filled from one contract.

A single trace, job or task reads ``workload_unit_template``; the all-units variant reads
``workload_set_template``. ``contracts/text/workload-subtitles.json`` gives each counted test its
source phrase, its set sentence and its one-unit sentence, whose ``{label}`` is the selected unit's
label; the names table gives the unit word. The direction phrase is appended by its own rule.
Time-vs-cost charts read the frontier template.
"""

import json
import re
from collections.abc import Mapping
from functools import cache
from pathlib import Path
from typing import Any

from sandbox_benchmark_verifier.catalog import _assets
from sandbox_benchmark_verifier.conventions import load_conventions
from sandbox_benchmark_verifier.name_refs import canonical_name_map, resolve_name_references
from sandbox_benchmark_verifier.names import load_tables
from sandbox_benchmark_verifier.pinned_workload import pinned_workload
from sandbox_benchmark_verifier.reader_concepts import _template_documents

SUBTITLES_PATH = "contracts/text/workload-subtitles.json"
WORDS = ("source", "set_clause", "set_sentence", "unit_sentence")
LABEL = "{label}"
LEVELS = {"unit": "workload_unit_template", "set": "workload_set_template"}
_FIELDS = {
    "measure_template": {"measure", "direction"},
    "workload_unit_template": {"Measure", "unit", "unit_sentence"},
    "workload_set_template": {"measure", "unit", "source", "set_clause"},
    "frontier_template": {"name", "time", "cost"},
    "score_composite_template": {"measure"},
    "score_set_template": {"measure", "unit", "set_sentence"},
    "score_frontier_template": {"unit", "set_sentence"},
    "score_frontier_composite_template": set(),
}
_SCORE_FACTS = {
    "fact_score_definition": {"unit", "statistic"},
    "fact_score_missing_unit": {"unit"},
}
MEASURES = ("time", "cost")
# A reference to a pinned workload's parameter: the document key before ``.parameters.workload.``.
_WORKLOAD_PARAMETER = re.compile(r"\{[a-z]+:([^{}]+?)\.parameters\.workload\.")


def workload_subtitle(  # pylint: disable=too-many-arguments
    policy: Mapping[str, Any],
    level: str,
    measure: str,
    unit: str,
    words: Mapping[str, str],
    *,
    label: str = LABEL,
) -> str:
    """The subtitle of a workload panel at a selection level, without its direction phrase."""
    values = {"Measure": measure.capitalize(), "measure": measure, "unit": unit, **words}
    text = re.sub(
        r"\{([A-Za-z_]+)\}",
        lambda match: values.get(match[1], match[0]),
        str(policy[LEVELS[level]]),
    )
    return text.replace(LABEL, label)


def subtitle_word_errors(
    entries: Mapping[str, Mapping[str, str]], rows: list[dict[str, Any]]
) -> list[str]:
    """Every counted test, and nothing else, has a source phrase and two sentences; only the
    one-unit sentence holds the ``{label}`` slot, once."""
    tests = {row["id"] for row in rows if row["kind"] == "test" and "unit" in row}
    errors = [f"{SUBTITLES_PATH}: no entry for {test}" for test in sorted(tests - set(entries))]
    errors += [f"{SUBTITLES_PATH}: {test} is no counted test" for test in set(entries) - tests]
    for test, words in entries.items():
        if set(words) != set(WORDS):
            errors.append(f"{SUBTITLES_PATH}: {test} must have exactly {list(WORDS)}")
            continue
        for key in ("set_sentence", "unit_sentence"):
            if not (words[key][:1].isupper() and words[key].endswith(".")):
                errors.append(f"{SUBTITLES_PATH}: {test}.{key} is a sentence: a capital, a stop")
        if not (words["set_clause"][:1].islower() and not words["set_clause"].endswith(".")):
            errors.append(f"{SUBTITLES_PATH}: {test}.set_clause is a clause: no capital, no stop")
        slots = {key: words[key].count(LABEL) for key in WORDS}
        if slots != {"source": 0, "set_clause": 0, "set_sentence": 0, "unit_sentence": 1}:
            errors.append(f"{SUBTITLES_PATH}: {test}: only unit_sentence names {LABEL}, once")
    return errors


@cache
def workload_subtitles() -> dict[str, dict[str, str]]:
    """Each counted test's subtitle words, names and parameters resolved; ``{label}`` stays.

    Read from the packaged assets, as every other text contract is.
    """
    root = _assets()
    entries = json.loads((root / SUBTITLES_PATH).read_text(encoding="utf-8"))
    tables = load_tables(root)
    if errors := subtitle_word_errors(entries, tables.names):
        raise ValueError("; ".join(errors))
    documents = dict(_template_documents(root))
    every = {(str(doc["id"]), str(doc["version"])): doc for doc in documents.values()}
    for key in set(_WORKLOAD_PARAMETER.findall(json.dumps(entries))):
        document = documents[key]
        workload = pinned_workload(root, document, every)
        documents[key] = {
            **document,
            "parameters": {**document["parameters"], "workload": workload},
        }
    resolved: dict[str, dict[str, str]] = resolve_name_references(
        entries, names=canonical_name_map(tables), documents=documents
    )
    return resolved


def measure_direction(policy: Mapping[str, Any], measure: str, direction: str) -> str:
    """The last subtitle line of a chart without segments: measure, then lowercased direction."""
    return (
        str(policy["measure_template"])
        .replace("{measure}", measure)
        .replace("{direction}", direction[:1].lower() + direction[1:])
    )


def template_errors(policy: Mapping[str, Any]) -> list[str]:
    """The two workload templates name exactly their fields."""
    return [
        f"workload-subtitle-template: {key} must name {sorted(fields)}"
        for key, fields in _FIELDS.items()
        if set(re.findall(r"\{([A-Za-z_]+)\}", str(policy.get(key, "")))) != fields
    ]


def score_descriptions(policy: Mapping[str, Any], rows: list[dict[str, Any]]) -> dict[str, str]:
    """The composite score subtitles, one per measure, from the owner template."""
    words = {r["id"]: r["sentence"] for r in rows if r["kind"] == "view" and "sentence" in r}
    return {
        f"{view}_score": str(policy["score_composite_template"]).replace("{measure}", words[view])
        for view in ("time", "cost")
    }


def score_fact_errors(facts: Mapping[str, str]) -> list[str]:
    """The score's definition and missing-unit sentences take exactly their runtime fields."""
    return [
        f"score-ranking: {code} must name {sorted(fields)}"
        for code, fields in _SCORE_FACTS.items()
        if set(re.findall(r"\{([A-Za-z_]+)\}", str(facts.get(code, "")))) != fields
    ]


def generated_notes(root: Path, identifier: str) -> dict[str, str]:
    """The reader copy's cost_summary of a workload: its Cost subtitle at the All level."""
    rows = load_tables(root).names
    test = next((r for r in rows if r["kind"] == "test" and r["id"] == identifier), None)
    if test is None or "unit" not in test:
        return {}
    unit = next(r["name"] for r in rows if r["kind"] == "per-unit" and r["id"] == test["unit"])
    policy = load_conventions(root)["subtitle"]
    words = {r["id"]: r["sentence"] for r in rows if r["kind"] == "view" and "sentence" in r}
    return {
        "cost_summary": workload_subtitle(
            policy, "set", words["cost"], unit, workload_subtitles()[identifier]
        )
    }


def workload_subtitle_errors(
    criteria: Mapping[str, Any], text: str, workload: Mapping[str, Any], directions: list[str]
) -> list[str]:
    """A rendered workload subtitle is its level's template, then the direction phrase."""
    policy = criteria["conventions"]["subtitle"]
    expected = workload_subtitle(
        policy,
        workload["level"],
        workload["measure"],
        workload["unit"],
        criteria["workload_subtitles"][workload["test"]],
        label=workload.get("label", LABEL),
    )
    full = policy["separator"].join([expected, *directions])
    if text == full:
        return []
    return [f"workload subtitle must be {full!r}"]


def rendered_direction_errors(
    criteria: Mapping[str, Any], scope: Mapping[str, Any], slots: list[dict[str, Any]]
) -> list[str]:
    """Check subtitle direction, quantity-only axis titles and rendered inline code marks."""
    conventions = criteria["conventions"]
    policy = conventions["subtitle"]
    phrases = [*policy["directions"].values(), policy["time_vs_cost_direction"]]
    errors = [
        f"{slot['slot']}: raw backtick in rendered text"
        for slot in slots
        if conventions["inline_code"]["mark"] in slot.get("text", "")
    ]
    for slot in slots:
        if slot["slot"] == "axis-title" and (
            sum(slot.get("text", "").count(phrase) for phrase in phrases)
            > conventions["axis"]["max_direction_occurrences"]
        ):
            errors.append("axis-title: direction belongs at the end of the subtitle")
    subtitle = next((slot for slot in slots if slot["slot"] in ("subtitle", "description")), None)
    text = subtitle.get("text", "").strip() if subtitle is not None else ""
    label = scope.get("measure_label")
    directions = [
        measure_direction(policy, label, direction) if label else direction
        for direction in scope.get("directions", [])
    ]
    if subtitle is not None and scope.get("workload_subtitle"):
        return errors + workload_subtitle_errors(
            criteria, text, scope["workload_subtitle"], directions
        )
    if subtitle is None or "directions" not in scope:
        return errors
    # The measure form lowercases the direction's first letter: count phrases ignoring case.
    count = text.lower().count if label else text.count
    for phrase in directions:
        if (
            not text.endswith(policy["separator"] + phrase)
            or sum(count(p.lower() if label else p) for p in phrases)
            > policy["max_direction_occurrences"]
        ):
            errors.append(
                f"{subtitle['slot']}: must end with exactly one direction phrase {phrase!r}"
            )
    return errors
