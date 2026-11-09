# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Option hovers: a variant says what it fixes, and never its label or the filler."""

from typing import Any

from sandbox_benchmark_verifier.catalog import load_methodology
from sandbox_benchmark_verifier.content import content_criteria, criteria_errors
from sandbox_benchmark_verifier.names import load_tables
from sandbox_benchmark_verifier.option_hovers import (
    option_hover,
    option_hover_errors,
    option_hovers,
)
from sandbox_benchmark_verifier.versions import newest


def _latest(identifier: str) -> dict[str, Any]:
    return newest(item.document for item in load_methodology())[identifier]


def test_a_variant_with_a_description_shows_it_and_the_filler_shows_nothing() -> None:
    hovers = option_hovers(_latest("package-registries"))
    assert set(hovers) == {"git-clone", "npm-ci", "pip-install"}
    assert hovers["git-clone"] == "Full clone of fastapi with its Git history from GitHub."
    assert (
        option_hover({"id": "x", "description": "The workload uses the x configuration."}) is None
    )


def test_a_hover_that_repeats_its_label_or_a_sibling_is_an_error() -> None:
    def document(*variants: dict[str, Any]) -> dict[str, Any]:
        return {"id": "d", "procedure": {"variants": list(variants)}}

    label = {"id": "a", "label": "Full clone", "description": "Full clone"}
    assert any("repeats its label" in e for e in option_hover_errors(document(label), limit=140))
    twins = (
        {"id": "a", "label": "A", "description": "Installs the pinned test dependencies."},
        {"id": "b", "label": "B", "description": "Installs the pinned test dependencies."},
    )
    assert any("read alike" in e for e in option_hover_errors(document(*twins), limit=140))
    assert option_hover_errors(_latest("package-registries"), limit=140) == []


def test_selectors_carry_their_own_description() -> None:
    criteria = content_criteria()
    assert criteria_errors(criteria) == []
    assert criteria["dots_selector"] == {
        "label": "Dots for",
        "description": "Choose whether each dot shows a trial's total or one step.",
    }
    delayed = next(row for row in load_tables().names if row["id"] == "delayed-resume")
    assert delayed["selector"]["label"] == "Delay Time"
    assert delayed["selector"]["description"].startswith("Time spent waiting after pause")
