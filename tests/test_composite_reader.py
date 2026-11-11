# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Composite reader wording cannot change the exact scientific definitions."""

from pathlib import Path
from typing import Any

from sandbox_benchmark_verifier.content import content_criteria


def test_composite_copy_and_current_reader_text(bundle: dict[str, Any]) -> None:
    copy = content_criteria()["index_copy"]
    assert copy["description"].startswith("Each provider’s composite value")
    assert copy["definition"].startswith("A composite ")


def test_the_cost_composite_and_time_vs_cost_state_one_omission_rule(
    bundle: dict[str, Any],
) -> None:
    """one fact, three conditions, one owner, one sentence for both indexes."""
    methods = bundle["reader"]["methods"]
    sentence = (
        "A provider is omitted from {name} and listed under Not Displayed if any of its counted "
        "trials in the scope lacks a provider-sourced cost, if any unit's cost on the selected "
        "statistic is zero (so the geometric mean is undefined), or if the provider completed no "
        "units in the scope."
    )

    joined = sentence.format(name="Composite Workload Cost and Composite Workload Time vs Cost")
    assert joined in methods["time-index"]["text"]["notes"]["failure_policy"].split("\n\n")
    for identifier in ("cost-index", "time-vs-cost"):
        assert "failure_policy" not in methods[identifier]["text"]["notes"], identifier
    source = Path("reader/methods.yaml").read_text(encoding="utf-8")
    assert (
        source.count("in the scope lacks a provider-sourced cost") == 1
    )  # the conditions have one owner
