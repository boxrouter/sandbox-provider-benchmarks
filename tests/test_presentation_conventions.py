# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Presentation rules are one validated data file with unique ids."""

from sandbox_benchmark_verifier.conventions import load_conventions


def test_presentation_rules_load_with_unique_ids() -> None:
    ids = {rule["id"] for rule in load_conventions()["presentation"]["rules"]}
    assert {
        "hero-subtitle",
        "hero-no-details",
        "details-required",
        "fonts-one-source",
        "shared-section-rail",
        "not-displayed-sentences",
        "fact-list",
        "chart-signature",
        "chart-signature-export",
    } <= ids


def test_the_reader_rules_are_not_restated_as_presentation_rules() -> None:
    """One definition each: the reader.* rules own these; web points its rendered checks there."""
    ids = {rule["id"] for rule in load_conventions()["presentation"]["rules"]}
    assert ids.isdisjoint(
        {"capitalize-first-letter", "no-page-facts-in-slots", "no-raw-placeholders"}
    )


def test_the_segment_minimum_leaves_the_pricing_panel_at_exact_scale() -> None:
    """The pricing bar's length is the price, so only that panel kind is outside the minimum."""
    rule = next(
        rule
        for rule in load_conventions()["presentation"]["rules"]
        if rule["id"] == "segment-min-width"
    )
    assert rule["applies_to"]["slots"] == ["presentation.panel"]
    assert rule["applies_to"]["exclude_slots"] == ["presentation.pricing-panel"]
