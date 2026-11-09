# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""One label per level of incomplete data, under the graph and in the drill-down."""

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from sandbox_benchmark_verifier.content import content_criteria, criteria_errors
from sandbox_benchmark_verifier.names import load_tables
from sandbox_benchmark_verifier.versions import version_key

pytestmark = pytest.mark.unit


def test_the_status_line_and_the_drill_down_both_read_provider_failures_at_every_level() -> None:

    criteria: dict[str, Any] = content_criteria()
    statuses, facts = criteria["ranking_statuses"], criteria["fact_list"]
    label = statuses["filled"]["label"]
    assert statuses["filled_units"]["label"] == label
    levels = ("fact_failed_workloads", "fact_failed_units", "fact_failed_trials")
    assert {facts[key] for key in levels} == {label}  # web reads one key per level
    assert "fact_missing_units" not in facts and "fact_missing_workloads" not in facts


def test_every_ci_job_has_a_short_name_its_full_name_ends_with() -> None:
    # Notes name jobs by their short names; the full name is "<language> (<short>)".
    jobs = [row for row in load_tables().names if row["kind"] == "job" and row["id"] != "all"]
    assert len(jobs) >= 6
    for row in jobs:
        assert row["name"].endswith(f"({row['short']})")


def test_the_tail_fallback_suffix_reads_the_floor_from_the_methodology() -> None:

    facts = content_criteria()["fact_list"]
    newest = max(
        Path("methodology").glob("00-common-*.yaml"),
        key=lambda path: version_key(path.stem.removeprefix("00-common-")),
    )
    common = yaml.safe_load(newest.read_text(encoding="utf-8"))
    floor = common["statistics"]["tail"]["min_successes"]
    assert facts["fact_tail_floor"] == str(floor)


@pytest.mark.parametrize("bad", ["false", "no", 0, 1, None])
def test_status_details_must_be_a_boolean(bad: Any) -> None:
    criteria: dict[str, Any] = content_criteria()
    assert criteria_errors(criteria) == []
    broken = copy.deepcopy(criteria)
    broken["ranking_statuses"]["in_development"]["details"] = bad
    assert "in_development: status.details must be a boolean" in criteria_errors(broken)
