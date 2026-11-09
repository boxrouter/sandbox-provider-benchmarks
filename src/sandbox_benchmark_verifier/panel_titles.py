# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""The one panel-title template, and the checks every generated title passes.

``title.template`` in the conventions owns the form. A bracketed group is dropped when any part
inside it is empty. The ``panel-title-pattern`` rule owns the separator.
"""

import re
from collections.abc import Mapping
from pathlib import Path

from sandbox_benchmark_verifier.conventions import conventions_view
from sandbox_benchmark_verifier.names import tables_view

RULE_ID = "panel-title-pattern"
_GROUP = re.compile(r"\[([^\[\]]*)\]")
_PART = re.compile(r"\{([a-z_]+)\}")


def title_template(root: Path | None = None) -> str:
    """The owner template that fills every panel's title."""
    return str(conventions_view(root)["title"]["template"])


def title_separator(root: Path | None = None) -> str:
    """The separator between a title and its variant."""
    for rule in conventions_view(root).get("presentation", {}).get("rules", []):
        if rule["id"] == RULE_ID:
            return str(rule["parameters"]["separator"])
    raise ValueError(f"the conventions declare no {RULE_ID} rule")


def fill_title(template: str, parts: Mapping[str, str]) -> str:
    """Fill the template; a bracketed group with an empty or absent part inside it is dropped."""

    def group(match: re.Match[str]) -> str:
        inside = match.group(1)
        return "" if any(not parts.get(key) for key in _PART.findall(inside)) else inside

    return _PART.sub(lambda match: parts[match.group(1)], _GROUP.sub(group, template))


def stands_alone(root: Path | None, variant: str) -> bool:
    """Whether a variant is the full name of a test or view, so it names the panel by itself."""
    kinds = set(conventions_view(root)["title"]["whole_name_kinds"])
    return any(
        row["kind"] in kinds and row["name"] == variant and "option" not in row.get("selector", {})
        for row in tables_view(root).names
    )


def holds_measure(root: Path | None, variant: str) -> bool:
    """Whether a test's name already holds the measure (Sequential TTI): its title is the name."""
    exempt = set(conventions_view(root)["title"]["measure_in_name"])
    return any(
        row["kind"] == "test" and row["name"] == variant and row["id"] in exempt
        for row in tables_view(root).names
    )


def panel_title(root: Path | None, **parts: str) -> str:
    """One panel's title from its owner parts: category, unit, measure, tool and variant.

    A variant that is the full name of a test or view is the title's subject: the category, the
    category unit and the separator are left out.
    """
    variant = parts.get("variant", "")
    if variant and stands_alone(root, variant):
        if holds_measure(root, variant):
            return variant
        parts = {**parts, "category": variant, "unit": "", "variant": ""}
    return fill_title(title_template(root), {"separator": title_separator(root), **parts})


def title_errors(root: Path | None, title: str, **parts: str) -> list[str]:
    """Why a generated title is not the template filled with its parts (empty when it is)."""
    expected = panel_title(root, **parts)
    errors = [] if title == expected else [f"{RULE_ID}: {title!r} is not {expected!r}"]
    if " - " in title or "{" in title or "  " in title:
        errors.append(f"{RULE_ID}: {title!r} uses a retired separator or an unfilled part")
    alone = stands_alone(root, parts.get("variant", ""))
    if parts.get("variant") and title.count(title_separator(root)) != (0 if alone else 1):
        errors.append(f"{RULE_ID}: {title!r} must carry its variant once, after the separator")
    return errors
