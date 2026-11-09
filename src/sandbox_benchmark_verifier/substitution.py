# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Checks on how the owner text names a substitution and the level it is shown at.

A substitution fills a gap with the worst value any provider recorded: a trace inside its workload,
a whole workload inside the composite. Every text that says so names the value it took and no
multiplier; a line that only says something is absent does not. A gap is shown one level up from
where it is: a note names what is incomplete one level down, the composite its workloads and a
workload's All tab its units, in the owner's list style.
"""

import re
from collections.abc import Mapping, Sequence
from typing import Any

SUBSTITUTION_RULE = "substitution-names-source"
LEVEL_RULE = "substitution-shown-one-level-up"
INCOMPLETE_RULE = "shared-status-lines"

_NAMES_SOURCE = re.compile(r"(?i)\b(?:slowest|highest)\b")
_MULTIPLIER = re.compile(r"(?i)penalty|\d+(?:\.\d+)?x|×")
_MENTIONS_SOURCE = re.compile(r"(?i)\b(?:slowest|highest|worst)\b|penalty|\d+(?:\.\d+)?x|×")
_SUBSTITUTION_FACTS = re.compile(r"fact_(?:no_result_(?:time|cost)|fill_.*|incomplete_.*)\Z")
_ABSENCE_FACTS = ("fact_no_result_not_measured",)
_ABSENCE_TEMPLATES = (
    "names_reason_template",
    "incomplete_footer_template",
    "open_hint",
)
# The gap's own level: the composite names its workloads, a workload's All tab its units; both read
# a list of names.
_LEVEL_FIELDS = {"filled": {"names"}, "filled_units": {"names"}}
LIST_STYLE = "conjunction"


def list_text(names: Sequence[str], style: str = LIST_STYLE) -> str:
    """Names as one phrase in the owner's list style, as Intl.ListFormat gives the page.

    "a", "a and b", "a, b, and c".
    """
    if style != LIST_STYLE:
        raise ValueError(f"unknown list style: {style}")
    if len(names) <= 2:
        return " and ".join(names)
    return f"{', '.join(names[:-1])}, and {names[-1]}"


def _fields(template: str) -> set[str]:
    return set(re.findall(r"\{([a-z_]+)\}", template))


def substitution_errors(criteria: Mapping[str, Any]) -> list[str]:
    """Errors in the criteria's substitution texts and in the levels they are shown at."""
    facts: Mapping[str, str] = criteria.get("fact_list", {})
    footer: Mapping[str, Any] = criteria.get("ranking_status_footer", {})
    errors = [
        f"{SUBSTITUTION_RULE}: {code} is a substitution that does not name the value it took"
        for code, text in facts.items()
        if _SUBSTITUTION_FACTS.match(code) and not _NAMES_SOURCE.search(text)
    ]
    errors.extend(
        f"{SUBSTITUTION_RULE}: {code} carries a multiplier; a missing result takes the value as is"
        for code, text in facts.items()
        if _SUBSTITUTION_FACTS.match(code) and _MULTIPLIER.search(text)
    )
    absences = {code: facts[code] for code in _ABSENCE_FACTS if code in facts}
    absences.update({field: footer[field] for field in _ABSENCE_TEMPLATES if field in footer})
    absences.update(
        {
            f"ranking_statuses.{status}": item["label"]
            for status, item in criteria.get("ranking_statuses", {}).items()
            if "label" in item
        }
    )
    errors.extend(
        f"{SUBSTITUTION_RULE}: {code} only says something is absent and must not name a value"
        for code, text in absences.items()
        if _MENTIONS_SOURCE.search(text)
    )
    for status, fields in _LEVEL_FIELDS.items() if footer else ():
        binding = footer["reason_templates"][status]
        found = _fields(footer[binding["field"]])
        if found != fields or set(binding["runtime_fields"]) != fields:
            errors.append(
                f"{LEVEL_RULE}: the {status} line takes {sorted(fields)}, not {sorted(found)}"
            )
    return errors + _incomplete_line_errors(footer) if footer else errors


def _incomplete_line_errors(footer: Mapping[str, Any]) -> list[str]:
    """The incomplete and Not Displayed lines: their fields and the owner sentence closing both."""
    template = str(footer.get("incomplete_footer_template", ""))
    errors = []
    if _fields(template) != {"marker", "label", "providers", "open_hint"} or _fields(
        str(footer.get("template", ""))
    ) != {"label", "providers", "open_hint"}:
        errors.append(
            f"{INCOMPLETE_RULE}: the incomplete line needs its fields and the owner's open_hint"
        )
    hint = str(footer.get("open_hint", ""))
    if not hint.startswith(". ") or not hint.endswith(".") or _fields(hint):
        errors.append(
            f"{INCOMPLETE_RULE}: open_hint is one sentence that follows the provider list"
        )
    if footer.get("list_style") != LIST_STYLE:
        errors.append(
            f"{INCOMPLETE_RULE}: list_style is {LIST_STYLE}, the one style the page renders"
        )
    if len(str(footer.get("incomplete_marker", ""))) != 1:
        errors.append(f"{INCOMPLETE_RULE}: the incomplete marker is one glyph")
    if set(footer.get("incomplete_statuses", [])) != set(_LEVEL_FIELDS):
        errors.append(f"{INCOMPLETE_RULE}: the incomplete line covers filled and filled_units only")
    return errors
