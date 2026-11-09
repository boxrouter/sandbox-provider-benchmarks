# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Rule cost-scope: cost reaches a reader only for the Workloads and their composites.

"it looks inconsistent in whether cost is included in every benchmark
methodology. i said earlier that we keep out cost from sandbox ops and machine workloads."
"""

import copy
from pathlib import Path

import pytest

from sandbox_benchmark_verifier import reader
from sandbox_benchmark_verifier.names import load_tables, shows_cost

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
WORKLOADS = ("deepswe", "terminal-bench-4-0", "r2e-gym", "ci-jobs")


def _tests_in(section: str) -> list[str]:
    rows = load_tables(ROOT).names
    tabs = {r["id"] for r in rows if r["kind"] == "tab" and r.get("parent") == section}
    return [r["id"] for r in rows if r["kind"] == "test" and r.get("parent") in tabs]


def test_sandbox_operations_and_machine_performance_show_no_cost() -> None:
    tests = _tests_in("sandbox-operations") + _tests_in("machine-performance")
    assert len(tests) > 20
    assert not any(shows_cost(test) for test in tests)


def test_workloads_and_composites_show_cost() -> None:
    assert all(
        shows_cost(test) for test in (*WORKLOADS, "cost-index", "time-vs-cost", "time-index")
    )


def test_the_reader_has_no_cost_wording_for_a_test_without_cost() -> None:
    assert reader.reader_errors(ROOT) == []
    source = reader.load_reader(ROOT)
    assert source is not None
    for identifier in _tests_in("sandbox-operations") + _tests_in("machine-performance"):
        assert not reader._cost_wording(source["methods"].get(identifier, {})), identifier


def test_a_cost_note_on_a_test_without_cost_fails() -> None:
    source = reader.load_reader(ROOT)
    assert source is not None
    tampered = copy.deepcopy(source)
    tampered["methods"]["exec-latency"]["notes"]["cost_how_to_read"] = "Cost is shown in dollars."
    tampered["methods"]["stream-triad"]["notes"]["summary"] = (
        "{param:reader.common.blocks.common.cost_reading}"
    )
    errors = reader.cost_scope_errors(tampered, load_tables(ROOT).names)
    assert any("exec-latency.notes.cost_how_to_read" in error for error in errors)
    assert any("stream-triad.notes.summary" in error for error in errors)
    assert reader.cost_scope_errors(source, load_tables(ROOT).names) == []


def test_a_workload_keeps_its_cost_wording() -> None:
    source = reader.load_reader(ROOT)
    assert source is not None
    assert all(reader._cost_wording(source["methods"][workload]) for workload in WORKLOADS)
