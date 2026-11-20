# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Synthetic tests for the formula evaluator, on steps in the result line's shape."""

import math
from typing import Any

import pytest

from sandbox_benchmark_verifier.catalog import load_methodology
from sandbox_benchmark_verifier.formulas import (
    Sample,
    cell_value,
    depth,
    pool_value,
    trial_values,
)
from sandbox_benchmark_verifier.parameters import FormulaError, parse_formula


def step(
    identifier: str,
    *,
    sandbox: int = 1,
    call: int = 1,
    start_ms: float = 0.0,
    end_ms: float = 0.0,
    duration_ms: float = 0.0,
    runtime_ms: float | None = None,
    outcome: str = "success",
    **figures: Any,
) -> dict[str, Any]:
    """One `steps[]` entry of a result line; `figures` adds `value` or `details`."""
    return {
        **figures,
        "id": identifier,
        "sandbox": sandbox,
        "call": call,
        "start_ns": int(start_ms * 1e6),
        "end_ns": int(end_ms * 1e6),
        "duration_ms": duration_ms,
        "runtime_ms": runtime_ms,
        "outcome": outcome,
        "clock": "client",
    }


def runs(values: list[float], sandbox: int = 1) -> list[dict[str, Any]]:
    """`cmd` calls with these sandbox runtimes."""
    return [
        step("cmd", sandbox=sandbox, call=index, duration_ms=value + 1, runtime_ms=value)
        for index, value in enumerate(values, 1)
    ]


def package_formulas() -> list[tuple[str, str, str, list[str]]]:
    """(document, metric, formula, recorded step ids) for every formula in the package."""
    found = []
    for item in load_methodology():
        document = item.document
        steps = (document.get("procedure") or {}).get("steps", [])
        recorded = sorted(
            {name for entry in steps for name in [entry.get("id"), *entry.get("record_steps", [])]}
            - {None}
        )
        for metric in document.get("metrics") or []:
            if metric.get("formula"):
                found.append((document["id"], metric["name"], metric["formula"], recorded))
    return found


def synthetic_trial(recorded: list[str]) -> list[dict[str, Any]]:
    """Every recorded step, three calls in each of two sandboxes, with every timing field."""
    return [
        step(
            name,
            sandbox=sandbox,
            call=call,
            start_ms=1000.0 * index + 10 * call,
            end_ms=1000.0 * index + 10 * call + 5,
            duration_ms=5.0,
            runtime_ms=3.0,
            value=7.0,
            details={"compress_mips": 6.0, "decompress_mips": 8.0, "rx_bytes": 9.0},
        )
        for index, name in enumerate(recorded)
        for sandbox in (1, 2)
        for call in (1, 2, 3)
    ]


FORMULAS = package_formulas()


def test_the_package_has_formulas() -> None:
    assert len(FORMULAS) >= 30


@pytest.mark.parametrize(
    ("document", "metric", "formula", "recorded"),
    FORMULAS,
    ids=[f"{document}/{metric}" for document, metric, _, _ in FORMULAS],
)
def test_every_package_formula_evaluates(
    document: str, metric: str, formula: str, recorded: list[str]
) -> None:
    del document, metric
    trial = synthetic_trial(recorded)
    level = depth(formula)
    if level <= 1:
        values = [sample.value for sample in trial_values(formula, trial)]
    elif level == 2:
        values = [cell_value(formula, [trial, trial])]
    else:
        values = [pool_value(formula, [[trial, trial], [trial]])]
    assert values
    assert all(math.isfinite(value) for value in values)


def test_the_call_fold_folds_each_sandbox_apart() -> None:
    trial = runs([1, 2, 3], sandbox=2) + runs([10, 20], sandbox=3)
    assert trial_values("sum(runtime(cmd))", trial) == [
        Sample(2, None, 6.0),
        Sample(3, None, 30.0),
    ]


def test_three_fold_levels_are_calls_then_trials_then_cells() -> None:
    task_a = [runs([1, 2]), runs([10]), runs([4, 4])]  # trial sums 3, 10, 8: median 8
    task_b = [runs([5]), runs([7])]  # trial sums 5, 7: nearest-rank median 5
    formula = "sum(median(sum(runtime(cmd))))"
    assert depth(formula) == 3
    assert cell_value("median(sum(runtime(cmd)))", task_a) == 8.0
    assert cell_value("median(sum(runtime(cmd)))", task_b) == 5.0
    assert pool_value(formula, [task_a, task_b]) == 13.0


def test_a_formula_is_evaluated_only_at_its_own_level() -> None:
    with pytest.raises(FormulaError, match="fold depth"):
        trial_values("median(sum(runtime(cmd)))", runs([1]))
    with pytest.raises(FormulaError, match="fold depth"):
        cell_value("sum(runtime(cmd))", [runs([1])])


def test_an_unfolded_repeated_step_gives_one_sample_per_call() -> None:
    trial = [step("true", call=call, duration_ms=call * 1.5) for call in (1, 2, 3)]
    assert [sample.value for sample in trial_values("total(true)", trial)] == [1.5, 3.0, 4.5]
    assert trial_values("sum(total(true))", trial) == [Sample(1, None, 9.0)]


def test_steps_pair_by_sandbox_and_call() -> None:
    trial = [
        step("create", sandbox=1, start_ms=100),
        step("first-true", sandbox=1, end_ms=350),
        step("create", sandbox=2, start_ms=400),
        step("first-true", sandbox=2, end_ms=900),
    ]
    assert trial_values("end(first-true) - start(create)", trial) == [
        Sample(1, 1, 250.0),
        Sample(2, 1, 500.0),
    ]


def test_steps_with_different_calls_do_not_pair() -> None:
    trial = [step("create", start_ms=1), step("first-true", call=2, end_ms=5)]
    with pytest.raises(FormulaError, match="different sandboxes or calls"):
        trial_values("end(first-true) - start(create)", trial)


def test_folded_values_add_and_a_folded_side_pairs_by_sandbox() -> None:
    trial = [step("attach", call=call, duration_ms=10.0 * call) for call in (1, 2, 3)] + [
        step("true", call=call, duration_ms=float(call)) for call in (1, 2, 3)
    ]
    assert trial_values("median(total(attach)) + median(total(true))", trial) == [
        Sample(1, None, 22.0)
    ]
    assert [s.value for s in trial_values("total(true) - median(total(attach))", trial)] == [
        -19.0,
        -18.0,
        -17.0,
    ]


def test_latency_is_total_minus_runtime_of_one_call() -> None:
    trial = [step("true", duration_ms=12.0, runtime_ms=2.5)]
    assert trial_values("median(latency(true))", trial) == [Sample(1, None, 9.5)]


def test_an_alternative_takes_the_first_step_the_trial_recorded() -> None:
    formula = "end(data-back) - start(resume|data-back)"
    with_resume = [step("resume", start_ms=100), step("data-back", start_ms=300, end_ms=400)]
    without_resume = [step("data-back", start_ms=300, end_ms=400)]
    assert trial_values(formula, with_resume)[0].value == 300.0
    assert trial_values(formula, without_resume)[0].value == 100.0


def test_a_missing_step_is_named() -> None:
    trial = [step("resume", start_ms=1)]
    with pytest.raises(FormulaError, match="end needs step data-back; the trial has none"):
        trial_values("end(data-back) - start(resume)", trial)


def test_a_step_without_a_successful_call_is_named() -> None:
    trial = [step("resume"), step("data-back", outcome="timeout")]
    with pytest.raises(FormulaError, match="needs step data-back; the trial has no successful"):
        trial_values("end(data-back) - start(resume)", trial)


def test_a_missing_field_is_named() -> None:
    with pytest.raises(FormulaError, match="step cmd has no numeric runtime_ms"):
        trial_values("sum(runtime(cmd))", [step("cmd")])


def test_a_bare_term_is_refused() -> None:
    assert parse_formula("start")
    with pytest.raises(FormulaError, match="bare start"):
        trial_values("start", [step("create")])


def test_median_of_an_even_count_is_the_lower_middle_value() -> None:
    trial = [step("true", call=call, duration_ms=value) for call, value in enumerate([4, 1, 3, 2])]
    assert trial_values("median(total(true))", trial)[0].value == 2.0


@pytest.mark.parametrize("document", ["fork", "snapshot-and-restore"])
def test_load_runtimes_is_the_sum_of_a_sandbox_exec_calls(document: str) -> None:
    """Today's page draws Load Runtimes as the exec totals added, one value per sandbox."""
    formula = next(
        metric["formula"]
        for item in load_methodology()
        if item.document["id"] == document
        for metric in item.document["metrics"]
        if metric["name"] == "first_command_time"
    )
    trial = [
        step("exec", sandbox=sandbox, call=call, duration_ms=10.0 * sandbox + call)
        for sandbox in (2, 3)
        for call in (1, 2, 3)
    ]
    assert trial_values(formula, trial) == [Sample(2, None, 66.0), Sample(3, None, 96.0)]


def test_a_trial_fold_folds_every_sandbox_into_one_value() -> None:
    trial = [
        step("create", sandbox=sandbox, start_ms=start, end_ms=start + 5)
        for sandbox, start in ((1, 10.0), (2, 12.0), (3, 11.0))
    ] + [
        step("ready", sandbox=sandbox, end_ms=end)
        for sandbox, end in ((1, 90.0), (2, 70.0), (3, 95.0))
    ]
    formula = "trial_max(end(ready)) - trial_min(start(create))"
    assert depth(formula) == 1
    assert trial_values(formula, trial) == [Sample(None, None, 85.0)]


def test_a_trial_fold_pairs_a_source_sandbox_with_each_restored_one() -> None:
    trial = [step("snapshot", sandbox=1, duration_ms=100.0)] + [
        entry
        for sandbox in (2, 3)
        for entry in (
            step("create-from-snapshot", sandbox=sandbox, duration_ms=10.0 * sandbox),
            step("exec", sandbox=sandbox, call=1, duration_ms=1.0),
            step("exec", sandbox=sandbox, call=2, duration_ms=2.0),
        )
    ]
    formula = "trial_sum(total(snapshot)) + total(create-from-snapshot) + sum(total(exec))"
    assert trial_values(formula, trial) == [Sample(2, 1, 123.0), Sample(3, 1, 133.0)]


def test_a_trial_fold_takes_a_step_term() -> None:
    with pytest.raises(FormulaError, match="folds a step term"):
        parse_formula("trial_sum(sum(total(exec)))")
    assert cell_value("median(trial_sum(total(exec)))", [[step("exec", duration_ms=4.0)]]) == 4.0


def test_value_reads_a_tool_figure_or_one_of_its_details() -> None:
    trial = [step("cpu", value=4206, details={"compress_mips": 2791, "decompress_mips": 5621})]
    assert trial_values("value(cpu)", trial) == [Sample(1, 1, 4206.0)]
    assert trial_values("value(cpu.compress_mips)", trial) == [Sample(1, 1, 2791.0)]
    assert trial_values("value(cpu.decompress_mips)", trial) == [Sample(1, 1, 5621.0)]
    with pytest.raises(FormulaError, match="no numeric other"):
        trial_values("value(cpu.other)", trial)
    with pytest.raises(FormulaError, match="takes a step, not a step's figure"):
        parse_formula("total(cpu.compress_mips)")
    with pytest.raises(FormulaError, match="value reads one step"):
        parse_formula("value(cpu|other)")


def test_first_and_later_select_a_repeated_step_calls_on_each_sandbox() -> None:
    trial = [
        step("attach", call=call, duration_ms=ms)
        for call, ms in ((0, 300.0), (1, 250.0), (2, 260.0), (3, 240.0))
    ]
    trial += [
        step("attach", sandbox=2, call=call, duration_ms=ms) for call, ms in ((0, 90.0), (1, 10.0))
    ]
    assert trial_values("first(total(attach))", trial) == [Sample(1, 0, 300.0), Sample(2, 0, 90.0)]
    assert trial_values("median(later(total(attach)))", trial) == [
        Sample(1, None, 250.0),
        Sample(2, None, 10.0),
    ]
    assert depth("first(total(attach))") == 0
    assert depth("median(later(total(attach)))") == 1


def test_a_call_selector_takes_one_step_term() -> None:
    with pytest.raises(FormulaError, match="selects the calls of a step term"):
        parse_formula("first(sum(total(attach)))")
    with pytest.raises(FormulaError, match="selects the calls of a step term"):
        parse_formula("later(first(total(attach)))")
    with pytest.raises(FormulaError, match="no successful call"):
        trial_values("later(total(attach))", [step("attach", call=0)])
