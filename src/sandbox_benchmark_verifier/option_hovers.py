# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Option hovers: what choosing a variant fixes, from its owning document."""

from __future__ import annotations

import re
from collections.abc import Mapping
from itertools import combinations
from typing import Any

from sandbox_benchmark_verifier.stage_hovers import _normal, _similar

# Locked documents spell a variant's label out as its description ("The workload uses the
# git clone configuration."); that says nothing the label does not, so it is not a hover.
FILLER = re.compile(r"^The workload uses the .+ configuration\.$")


def option_hover(variant: Mapping[str, Any]) -> str | None:
    """A variant's own description; the filler and an absent description say nothing."""
    description = variant.get("description")
    if isinstance(description, str) and description.strip() and not FILLER.match(description):
        return description
    return None


def option_hovers(document: Mapping[str, Any]) -> dict[str, str]:
    """Every variant of a document that has a hover, by variant id."""
    variants = document.get("procedure", {}).get("variants", [])
    hovers = {}
    for variant in variants if isinstance(variants, list) else []:
        text = option_hover(variant) if isinstance(variant, Mapping) else None
        if text is not None:
            hovers[str(variant["id"])] = text
    return hovers


def option_hover_errors(
    document: Mapping[str, Any], *, limit: int, threshold: float = 0.8
) -> list[str]:
    """A hover adds information: it is not its label, and no two options read alike."""
    identifier = document.get("id")
    variants = document.get("procedure", {}).get("variants", [])
    hovers = option_hovers(document)
    errors = []
    for variant_id, text in hovers.items():
        label = next(
            (str(v.get("label", "")) for v in variants if str(v.get("id")) == variant_id), ""
        )
        if len(text) > limit:
            errors.append(
                f"{identifier}: option {variant_id} hover is {len(text)} characters, over {limit}"
            )
        if _normal(text) == _normal(label):
            errors.append(f"{identifier}: option {variant_id} hover only repeats its label")
    authored = {
        str(variant["id"]): variant["description"]
        for variant in variants
        if isinstance(variant.get("description"), str) and not FILLER.match(variant["description"])
    }
    for (first_id, first), (second_id, second) in combinations(authored.items(), 2):
        if _similar(first, second) >= threshold:
            errors.append(
                f"{identifier}: options {first_id} and {second_id} read alike; say it once on "
                "their selector"
            )
    return errors
