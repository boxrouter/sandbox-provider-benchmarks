# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Evaluate a methodology metric's `formula` on recorded steps, in milliseconds.

The input is the result line's `steps[]` (`id`, `sandbox`, `call`, `start_ns`, `end_ns`,
`duration_ms`, `runtime_ms`, `outcome`). The grammar is `parameters.parse_formula`; every
behaviour here comes from the formula text, never from a document or workload id.

1. A step term reads one entry per successful recorded call of its step in a trial, keyed by
   the call's sandbox and call index: `start` and `end` from the host's monotonic stamps, `total`
   the client duration, `runtime` the sandbox's own duration, `latency` total minus runtime,
   `value` the tool's own figure (`value(<step>.<key>)` a figure in its `details`).
2. Alternatives, `start(resume|data-back)`, read the first listed step the trial recorded.
   `first(...)` keeps each sandbox's successful call with the lowest repeat index, `later(...)`
   its other successful calls; neither folds.
3. `+` and `-` pair entries with the same sandbox and call; a folded side pairs by sandbox, and a
   side folded to one value pairs with every entry of the other.
4. Each fold collapses one level, the innermost first: the calls of a trial (per sandbox), then
   the trials of a cell, then the cells (variants) of a pool. So `sum(runtime(cmd))` is one value
   per sandbox of a trial, and `sum(median(sum(runtime(cmd))))` one value per pool.

Readings the methodology does not yet state (page-data plan P0.4 writes them into the text):

- G1: the fold level comes from nesting depth alone, as in point 4.
- G2: a term over a repeated step that no fold encloses gives one sample per call, with no
  implicit sum; `exec-latency-per-100-calls` therefore needs `sum(total(true))` to mean its sum.
- G3: the call-level fold folds each sandbox's calls apart, so a value per sandbox is one sample
  (r2e-gym's rollouts). A `trial_` fold (`trial_sum`, `trial_max`, `trial_min`) folds every call
  of every sandbox of the trial into one value, which pairs with every entry of the other side.
"""

import math
from collections.abc import Callable, Mapping, Sequence
from typing import Any, NamedTuple

from sandbox_benchmark_verifier.parameters import (
    Difference,
    Fold,
    Formula,
    FormulaError,
    StepTerm,
    parse_formula,
)
from sandbox_benchmark_verifier.stats import nearest_rank

Step = Mapping[str, Any]
Trial = Sequence[Step]
Key = tuple[int, ...]
"""(sandbox, call) for a step term, (sandbox,) after the call fold, () after a deeper fold."""
Values = dict[Key, float]

TRIAL, CELL, POOL = 1, 2, 3
"""Scopes, by the fold depth they evaluate: a trial's calls, a cell's trials, a pool's cells."""

FOLDS: dict[str, Callable[[list[float]], float]] = {
    "median": lambda values: nearest_rank(values, 0.5),
    "sum": math.fsum,
    "max": max,
    "min": min,
}
TRIAL_FOLDS = {f"trial_{name}": FOLDS[name] for name in ("sum", "max", "min")}


class Sample(NamedTuple):
    """One trial-level value; `sandbox` and `call` are None once a fold has removed them."""

    sandbox: int | None
    call: int | None
    value: float


def depth(formula: str | Formula) -> int:
    """The deepest fold nesting: 0 or 1 is per trial, 2 per cell, 3 per pool (G1)."""
    tree = parse_formula(formula) if isinstance(formula, str) else formula
    if isinstance(tree, StepTerm):
        return 0
    if isinstance(tree, Fold):
        return 1 + depth(tree.argument)
    return max(depth(tree.left), depth(tree.right))


def trial_values(formula: str, steps: Trial) -> list[Sample]:
    """A per-trial formula's samples for one trial, ordered by sandbox and call."""
    values = _evaluate(_tree(formula, TRIAL), TRIAL, steps)
    return [
        Sample(key[0] if len(key) > 0 else None, key[1] if len(key) > 1 else None, value)
        for key, value in sorted(values.items())
    ]


def cell_value(formula: str, trials: Sequence[Trial]) -> float:
    """A formula with a fold over trials (depth 2), for one cell's trials."""
    return _evaluate(_tree(formula, CELL), CELL, trials)[()]


def pool_value(formula: str, cells: Sequence[Sequence[Trial]]) -> float:
    """A formula with a fold over cells (depth 3), for one pool's cells, each a list of trials."""
    return _evaluate(_tree(formula, POOL), POOL, cells)[()]


def _tree(formula: str, scope: int) -> Formula:
    tree = parse_formula(formula)
    if max(depth(tree), TRIAL) != scope:
        raise FormulaError(f"{formula} has fold depth {depth(tree)}, not {scope}")
    return tree


def _evaluate(tree: Formula, scope: int, data: Any) -> Values:
    if isinstance(tree, StepTerm):
        if scope != TRIAL:
            raise FormulaError(f"{tree.name} is outside the folds its formula needs")
        return _term(tree, data)
    if isinstance(tree, Difference):
        if max(depth(tree.left), TRIAL) != max(depth(tree.right), TRIAL):
            raise FormulaError("formula adds or subtracts values of different fold levels")
        return _combine(
            _evaluate(tree.left, scope, data), tree.operator, _evaluate(tree.right, scope, data)
        )
    if tree.name in TRIAL_FOLDS:
        if scope != TRIAL:
            raise FormulaError(f"{tree.name} is outside the folds its formula needs")
        return {(): TRIAL_FOLDS[tree.name](list(_evaluate(tree.argument, TRIAL, data).values()))}
    level = depth(tree)
    if level != scope:
        raise FormulaError(f"{tree.name} at fold depth {level} is evaluated at scope {scope}")
    fold = FOLDS[tree.name]
    if scope == TRIAL:
        groups: dict[Key, list[float]] = {}
        for key, value in _evaluate(tree.argument, TRIAL, data).items():
            groups.setdefault(key[:1], []).append(value)
        return {sandbox: fold(values) for sandbox, values in groups.items()}
    values = [
        value for child in data for value in _evaluate(tree.argument, scope - 1, child).values()
    ]
    if not values:
        raise FormulaError(f"{tree.name} has nothing to fold")
    return {(): fold(values)}


def _term(term: StepTerm, steps: Trial) -> Values:
    if not term.steps:
        raise FormulaError(f"bare {term.name} has no step to read")
    recorded = {step.get("id") for step in steps}
    chosen = next((name for name in term.steps if name in recorded), None)
    if chosen is None:
        raise FormulaError(f"{term.name} needs step {'|'.join(term.steps)}; the trial has none")
    calls = [
        step for step in steps if step.get("id") == chosen and step.get("outcome") == "success"
    ]
    if term.calls is not None:
        calls = _selected(calls, first=term.calls == "first")
    if not calls:
        raise FormulaError(f"{term.name} needs step {chosen}; the trial has no successful call")
    values: Values = {}
    for step in calls:
        key = (int(_field(step, "sandbox")), int(_field(step, "call")))
        if key in values:
            raise FormulaError(f"step {chosen} records sandbox {key[0]} call {key[1]} twice")
        values[key] = _value(term, step) if term.name == "value" else _read(term.name, step)
    return values


def _selected(calls: Sequence[Step], *, first: bool) -> list[Step]:
    """Each sandbox's call with the lowest repeat index (`first`), or its other calls."""

    def place(step: Step) -> tuple[int, int]:
        return int(_field(step, "sandbox")), int(_field(step, "call"))

    lowest: dict[int, int] = {}
    for sandbox, index in map(place, calls):
        lowest[sandbox] = min(lowest.get(sandbox, index), index)
    return [step for step in calls if (place(step)[1] == lowest[place(step)[0]]) == first]


def _value(term: StepTerm, step: Step) -> float:
    if term.detail is None:
        return _field(step, "value")
    details = step.get("details")
    return _field(details if isinstance(details, Mapping) else {}, term.detail, step)


def _read(name: str, step: Step) -> float:
    if name == "start":
        return _field(step, "start_ns") / 1e6
    if name == "end":
        return _field(step, "end_ns") / 1e6
    if name == "total":
        return _field(step, "duration_ms")
    if name == "runtime":
        return _field(step, "runtime_ms")
    return _field(step, "duration_ms") - _field(step, "runtime_ms")


def _field(row: Mapping[str, Any], name: str, step: Step | None = None) -> float:
    value = row.get(name)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise FormulaError(f"step {(step or row).get('id')} has no numeric {name}")
    return float(value)


def _combine(left: Values, operator: str, right: Values) -> Values:
    sign = 1 if operator == "+" else -1
    long, short, flip = (
        (left, right, False) if _width(left) >= _width(right) else (right, left, True)
    )
    width = _width(short)
    if width == _width(long) and set(long) != set(short):
        raise FormulaError("formula pairs steps that recorded different sandboxes or calls")
    result: Values = {}
    for key, value in long.items():
        if key[:width] not in short:
            raise FormulaError(f"formula has no value to pair with sandbox and call {key}")
        other = short[key[:width]]
        result[key] = other + sign * value if flip else value + sign * other
    return result


def _width(values: Values) -> int:
    return max((len(key) for key in values), default=0)
