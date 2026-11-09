# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""At a Glance names the measured version and the clock the headline number is read from."""

from pathlib import Path
from typing import Any

import pytest

from sandbox_benchmark_verifier.content import content_criteria
from sandbox_benchmark_verifier.conventions import load_conventions
from sandbox_benchmark_verifier.glance_clock import RULE_ID, clock_labels, glance_clock_errors
from sandbox_benchmark_verifier.names import load_tables
from sandbox_benchmark_verifier.reader import load_reader
from sandbox_benchmark_verifier.versions import version_key

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[1]
COMPOSITES = ("time-index", "cost-index", "time-vs-cost")


def _reader_methods() -> dict[str, Any]:
    reader = load_reader(ROOT)
    assert reader is not None
    methods: dict[str, Any] = reader["methods"]
    return methods


def test_the_glance_has_two_groups_with_the_version_and_the_headline_clock() -> None:
    page = content_criteria()["methodology_page"]
    what, how = page["at_a_glance"]["groups"]
    assert "clocks.headline" in what
    assert "version" in how
    assert "clocks.operation" not in what + how
    assert page["field_labels"]["version"] == "Version"
    assert page["field_labels"]["clocks.headline"] == "Clock"


def test_every_measuring_method_names_its_clock_and_a_composite_names_none() -> None:
    methods = _reader_methods()
    missing = [
        name
        for name, entry in methods.items()
        if name != "common" and "clock" not in entry and name not in COMPOSITES
    ]
    assert not missing
    assert not [name for name in COMPOSITES if "clock" in methods[name]]
    clocks = clock_labels(load_tables(ROOT).names)
    assert all(
        set(entry["clock"]) <= set(clocks) for entry in methods.values() if entry.get("clock")
    )


def test_a_method_names_only_clocks_that_its_stages_have() -> None:
    for name, entry in _reader_methods().items():
        if "stages" in entry and "clock" in entry:
            stage_clocks = {stage["clock"] for stage in entry["stages"].values()}
            assert set(entry["clock"]) <= stage_clocks, name


def _clocks(bundle: dict[str, Any], document: str) -> dict[str, dict[str, Any]]:
    return {
        version: row["reader_definition"]["clocks"]
        for version, row in bundle["methodologies"][document].items()
    }


def test_the_clock_row_is_the_label_of_the_headline_clock_not_its_definition(
    bundle: dict[str, Any],
) -> None:
    """ "for clock, just say 'Sandbox clock' or 'Job-host clock'"."""
    labels = clock_labels(bundle["names"])

    assert labels["sandbox"] == "Sandbox clock"
    assert labels["runner"] == "Job-host clock"
    for document in (
        "deepswe",
        "terminal-bench-4-0",
        "github-actions",
        "7-zip-1-thread",
        "stream-triad",
    ):
        for version, row in _clocks(bundle, document).items():
            assert row["headline"] == labels["sandbox"], (document, version)
    for document in ("sequential-tti", "burst-tti", "read-4kib", "exec-latency", "fork"):
        for version, row in _clocks(bundle, document).items():
            assert row["headline"] == labels["runner"], (document, version)
    for version, row in _clocks(bundle, "r2e-gym").items():
        assert row["headline"] == f"{labels['runner']} and {labels['sandbox']}", version
    assert glance_clock_errors(bundle) == []
    # A composite runs no trials of its own: the calculation template gives it no clock at all.
    for document in COMPOSITES:
        for version, row in bundle["methodologies"][document].items():
            assert "clocks" not in row["reader_definition"], (document, version)


def test_a_clock_row_that_is_a_definition_sentence_is_refused(bundle: dict[str, Any]) -> None:
    """The values At a Glance drew before 2026-11-04 fail the check; the rule names the check."""
    old = {
        "fork": "Job-host monotonic clock around create and command calls.",
        "r2e-gym": [
            "Monotonic clock of the job host, around each call.",
            "The sandbox's own clock, read just before and after the command.",
        ],
    }
    broken = {
        "names": bundle["names"],
        "methodologies": {
            document: {"1.0.0": {"reader_definition": {"clocks": {"headline": value}}}}
            for document, value in old.items()
        },
    }
    assert [error.split(" clocks.headline")[0] for error in glance_clock_errors(broken)] == [
        f"{RULE_ID}: fork@1.0.0",
        f"{RULE_ID}: r2e-gym@1.0.0",
    ]
    rules = {rule["id"] for rule in load_conventions()["presentation"]["rules"]}
    assert RULE_ID in rules


def test_the_files_page_lists_the_variants_of_all_three_tests(bundle: dict[str, Any]) -> None:
    for version, row in bundle["methodologies"]["read-4kib"].items():
        labels = [variant["label"] for variant in row["reader_definition"]["procedure"]["variants"]]
        if version_key(version) >= (1, 1, 4):
            assert labels == ["Read 4 KiB", "List 200 empty files", "Write a 4 KiB payload"], (
                version
            )
