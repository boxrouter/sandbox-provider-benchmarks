# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Checks for pinned workload parameters, shared trials and metric formulas."""

import hashlib
import json
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

RECORD_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
TARBALL_URL = re.compile(r"^https://[a-z0-9.-]+/[A-Za-z0-9._/-]+$")
FORMULA_NAMES = frozenset({"start", "end", "total", "runtime"})
AGGREGATES = frozenset({"median", "sum", "max", "min"})
"""Each takes one expression in parentheses and folds it over a step's runs, a trial's
sandboxes or a set's traces: the middle value, their sum, the largest or the smallest."""
TRIAL_AGGREGATES = frozenset({"trial_sum", "trial_max", "trial_min"})
"""Each folds the calls of every sandbox of one trial into one value, which pairs with every
entry it is added to or subtracted from: `trial_max(end(ready)) - trial_min(start(create))` is a
burst's span, `trial_sum(total(snapshot)) + total(restore)` adds a source sandbox's snapshot to
each restored sandbox. Its argument is a step term, with no fold inside."""
CALL_SELECTORS = frozenset({"first", "later"})
"""Each keeps some calls of a repeated step on each sandbox and folds nothing: `first(...)` the
call with the lowest repeat index, `later(...)` every other call. `first(total(attach))` is the
first round's attach, `median(later(total(attach)))` the median of the later rounds'. Its argument
is a step term, with no fold inside."""
TOKEN = re.compile(
    r"\s*([A-Za-z_][A-Za-z0-9_-]*(?:\|[A-Za-z_][A-Za-z0-9_-]*)*(?:\.[a-z_]+)?|[()+-])"
)
"""A step argument may list alternatives, `start(resume|data-back)`: the first of them the trial
recorded. `value(<step>)` reads the figure a tool step reports, and `value(<step>.<key>)` one of
the other figures in its `details`, as `value(cpu-7zip-1t.compress_mips)`."""


RUNNER_FILES = frozenset({"run.json"})
"""Files the benchmark runner reads (``schemas/run-definition.schema.json``); they say how a trial
is executed, not what the methodology measures, so a parameters digest does not cover them."""


def tree_digest(directory: Path) -> str:
    """SHA-256 of one ``<file sha256> <relative path>`` line per file, sorted by path bytes.

    Runner files (``run.json``) are left out."""
    lines = sorted(
        (path.relative_to(directory).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest())
        for path in directory.rglob("*")
        if path.is_file() and path.name not in RUNNER_FILES
    )
    lines.sort(key=lambda item: item[0].encode())
    return hashlib.sha256(
        "".join(f"{digest} {name}\n" for name, digest in lines).encode()
    ).hexdigest()


REPLAY_KEYS = ("repo", "commit", "workdir", "setup", "env", "replayable")
"""What a workload pins when it has no image of its own: the clone, the directory the
recording used, the untimed install, its environment and whether the standard image runs it."""


def workload_version(workload: Mapping[str, Any]) -> str:
    """A workload's version: a digest of its data, capabilities and pinned environment, never its
    label. The environment is the image, or the clone-and-setup definition that replaces it."""
    if "image" in workload:
        pinned = workload["image"]
    else:
        spec = {key: workload[key] for key in (*REPLAY_KEYS, "reason") if key in workload}
        pinned = spec or None
    text = json.dumps(
        [workload.get("data"), list(workload.get("capabilities", [])), pinned],
        sort_keys=True,
    )
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()[:16]


def workload_errors(directory: Path) -> Iterator[str]:  # pylint: disable=too-many-branches
    """Problems in ``workloads.json``: shape, duplicate ids and stale versions."""
    path = directory / "workloads.json"
    if not path.is_file():
        yield f"{directory}: parameters directory has no workloads.json"
        return
    try:
        workloads = json.loads(path.read_text())
    except json.JSONDecodeError:
        yield f"{path}: not valid JSON"
        return
    if not isinstance(workloads, list) or not workloads:
        yield f"{path}: must be a non-empty list of workloads"
        return
    seen: set[str] = set()
    for index, workload in enumerate(workloads):
        if not isinstance(workload, Mapping) or not all(
            key in workload for key in ("id", "label", "data", "version")
        ):
            yield f"{path}: workload {index} needs id, label, data and version"
            continue
        identifier = workload["id"]
        if not isinstance(identifier, str) or not RECORD_ID.match(identifier):
            yield f"{path}: workload {index} id is not a record id"
        elif identifier in seen:
            yield f"{path}: duplicate workload id {identifier}"
        else:
            seen.add(identifier)
        if "image" not in workload and any(key in workload for key in (*REPLAY_KEYS, "reason")):
            missing = [key for key in REPLAY_KEYS if key not in workload]
            if missing:
                yield f"{path}: workload {identifier} misses {missing[0]}"
            elif workload["replayable"] is False and not workload.get("reason"):
                yield f"{path}: workload {identifier} is not replayable and gives no reason"
        if "short" in workload and not (
            isinstance(workload["short"], str) and workload["short"].strip()
        ):
            yield f"{path}: workload {identifier} short must be a non-empty string"
        if workload["version"] != workload_version(workload):
            yield f"{path}: workload {identifier} version does not match its data"
        yield from tarball_errors(path, str(identifier), workload)


def tarball_errors(path: Path, identifier: str, workload: Mapping[str, Any]) -> Iterator[str]:
    """A task shipped as a prebuilt tarball: URL, checksum, work directory, untimed setup and
    whether its rollouts replay. The tarball replaces the task's image, so ``image`` must be absent
    and the tarball's checksum must appear in its URL (the object is stored under its digest)."""
    data = workload["data"]
    if not isinstance(data, Mapping) or "tarball" not in data:
        return
    where = f"{path}: workload {identifier}"
    if "image" in workload:
        yield f"{where} has both a tarball and an image"
    url = data.get("tarball")
    if not isinstance(url, str) or not TARBALL_URL.match(url):
        yield f"{where} tarball is not an https URL"
    digest = data.get("sha256")
    if not isinstance(digest, str) or not SHA256.match(digest):
        yield f"{where} sha256 is not a lowercase SHA-256"
    elif isinstance(url, str) and digest not in url:
        yield f"{where} tarball URL does not name its sha256"
    if not isinstance(data.get("workdir"), str) or not str(data.get("workdir")).startswith("/"):
        yield f"{where} workdir is not an absolute path"
    setup = data.get("setup")
    if not isinstance(setup, list) or not setup or not all(isinstance(c, str) for c in setup):
        yield f"{where} setup must be a non-empty list of shell commands"
    elif isinstance(digest, str) and not any(digest in c and "sha256sum" in c for c in setup):
        yield f"{where} setup does not check the sha256"
    if not isinstance(data.get("replayable"), bool):
        yield f"{where} replayable must be true or false"
    if not isinstance(data.get("replayable_reason"), str) or not data["replayable_reason"]:
        yield f"{where} replayable_reason is missing"


def parameter_workloads(directory: Path) -> set[str]:
    """Workload ids in a parameters directory, empty when it cannot be read."""
    try:
        found = json.loads((directory / "workloads.json").read_text())
    except (OSError, json.JSONDecodeError):
        return set()
    if not isinstance(found, list):
        return set()
    return {w["id"] for w in found if isinstance(w, Mapping) and isinstance(w.get("id"), str)}


class FormulaError(ValueError):
    """A formula outside the grammar, or one naming a step its document does not record."""


@dataclass(frozen=True)
class StepTerm:
    """`start`, `end`, `total`, `runtime` or `latency`, of the first recorded step in `steps`;
    `steps` is empty for a bare term."""

    name: str
    steps: tuple[str, ...]
    detail: str | None = None
    """For `value(<step>.<key>)`, the `details` key to read instead of `value`."""
    calls: str | None = None
    """One of `CALL_SELECTORS`, when the term reads only those calls of each sandbox."""


@dataclass(frozen=True)
class Fold:
    """One of `AGGREGATES` or `TRIAL_AGGREGATES` applied to one expression."""

    name: str
    argument: "Formula"


@dataclass(frozen=True)
class Difference:
    """`left + right` or `left - right`, left-associative."""

    left: "Formula"
    operator: str
    right: "Formula"


Formula = StepTerm | Fold | Difference


def _step_term(name: str, found: tuple[str, ...]) -> StepTerm:
    """A step term; only `value` reads a step's figure (`value(<step>.<key>)`)."""
    if name == "value":
        step, _, detail = found[0].partition(".")
        if len(found) > 1 or "." in step:
            raise FormulaError("value reads one step")
        return StepTerm(name, (step,), detail or None)
    if any("." in step for step in found):
        raise FormulaError(f"{name} takes a step, not a step's figure")
    return StepTerm(name, found)


def _selector(name: str, argument: "Formula") -> StepTerm:
    """`first(...)` or `later(...)` around one step term (`CALL_SELECTORS`)."""
    if not isinstance(argument, StepTerm) or argument.calls is not None:
        raise FormulaError(f"{name} selects the calls of a step term")
    return replace(argument, calls=name)


def parse_formula(formula: str, steps: frozenset[str] = frozenset()) -> Formula:
    """Parse a metric formula into its tree; raise `FormulaError` at its first problem.

    ``steps`` empty skips step-name checks. This is the grammar's one implementation."""
    position = 0
    tokens: list[str] = []
    while position < len(formula.rstrip()):
        match = TOKEN.match(formula, position)
        if match is None:
            raise FormulaError(f"formula has an unexpected character at {position}")
        tokens.append(match.group(1))
        position = match.end()
    cursor = 0

    def peek() -> str | None:
        return tokens[cursor] if cursor < len(tokens) else None

    def expect(value: str) -> bool:
        nonlocal cursor
        if peek() != value:
            return False
        cursor += 1
        return True

    def expression() -> Formula:
        nonlocal cursor
        tree = term()
        while (operator := peek()) in ("+", "-"):
            cursor += 1
            tree = Difference(tree, str(operator), term())
        return tree

    def term() -> Formula:
        nonlocal cursor
        name = peek()
        if name is None or name in ("(", ")", "+", "-"):
            raise FormulaError("formula expects a term")
        cursor += 1
        if name in AGGREGATES or name in TRIAL_AGGREGATES:
            argument = enclosed(name)
            if name in TRIAL_AGGREGATES and not isinstance(argument, StepTerm):
                raise FormulaError(f"{name} folds a step term, not {type(argument).__name__}")
            return Fold(name, argument)
        if name in CALL_SELECTORS:
            return _selector(name, enclosed(name))
        if name in ("value", "latency") or name in FORMULA_NAMES:
            return _step_term(name, step_argument(name, required=name in ("value", "latency")))
        raise FormulaError(f"formula uses unknown term {name}")

    def enclosed(name: str) -> Formula:
        if not expect("("):
            raise FormulaError(f"{name} needs parentheses")
        argument = expression()
        if not expect(")"):
            raise FormulaError(f"{name} is not closed")
        return argument

    def step_argument(name: str, *, required: bool) -> tuple[str, ...]:
        nonlocal cursor
        if not expect("("):
            if required:
                raise FormulaError(f"{name} needs a step in parentheses")
            return ()
        step = peek()
        if step is None or step in ("(", ")", "+", "-"):
            raise FormulaError(f"{name} needs a step")
        cursor += 1
        if steps and (unknown := sorted({s.split(".")[0] for s in step.split("|")} - steps)):
            raise FormulaError(f"{name} names unknown step {unknown[0]}")
        if not expect(")"):
            raise FormulaError(f"{name} is not closed")
        return tuple(step.split("|"))

    tree = expression()
    if cursor != len(tokens):
        raise FormulaError("formula has trailing input")
    return tree


def formula_errors(formula: str, steps: frozenset[str]) -> Iterator[str]:
    """Check a metric formula against the grammar; ``steps`` empty skips step-name checks."""
    try:
        parse_formula(formula, steps)
    except FormulaError as error:
        yield str(error)


def document_errors(
    root: Path,
    path: Path,
    document: Mapping[str, Any],
    owners: Mapping[str, Mapping[str, Any]],
) -> Iterator[str]:
    """Checks on one methodology document: parameters, shared trial and formulas."""
    identifier = document.get("id")
    parameters = document.get("parameters")
    shared = (
        document.get("procedure", {}).get("shared_trial")
        if isinstance(document.get("procedure"), Mapping)
        else None
    )
    if isinstance(parameters, Mapping):
        expected = f"parameters/{identifier}/"
        directory = root / str(parameters.get("path", ""))
        if parameters.get("path") != expected:
            yield f"{path}: parameters.path must be {expected}"
        elif not directory.is_dir():
            yield f"{path}: parameters directory {expected} is missing"
        else:
            yield from (f"{path}: {issue}" for issue in workload_errors(directory))
            if tree_digest(directory) != parameters.get("sha256"):
                yield f"{path}: parameters sha256 does not match the directory tree"
        if shared is not None:
            yield f"{path}: a shared_trial document must not hold parameters"
    if isinstance(shared, Mapping):
        owner = owners.get(str(shared.get("with")))
        if owner is None:
            yield f"{path}: shared_trial names an unknown methodology"
        elif not isinstance(owner.get("parameters"), Mapping):
            yield f"{path}: shared_trial owner {shared.get('with')} holds no parameters"
    procedure = document.get("procedure")
    steps = procedure.get("steps", []) if isinstance(procedure, Mapping) else []
    names = frozenset(
        name
        for step in steps
        if isinstance(step, Mapping)
        for name in [step.get("id"), *step.get("record_steps", [])]
        if isinstance(name, str)
    )
    metrics = document.get("metrics")
    yield from _metric_errors(path, metrics if isinstance(metrics, list) else [], names)
    if steps:
        yield from _default_view_errors(path, document)


def _default_view_errors(path: Path, document: Mapping[str, Any]) -> Iterator[str]:
    """A procedure names the view its page opens on, with its own metric and variant."""
    view = document.get("default_view")
    if not isinstance(view, Mapping):
        yield f"{path}: a procedure needs a default_view"
        return
    metrics = {m.get("name") for m in document.get("metrics") or [] if isinstance(m, Mapping)}
    if "metric" in view and view["metric"] not in metrics:
        yield f"{path}: default_view metric {view['metric']} is not one of its metrics"
    variants = {
        v.get("id") for v in document["procedure"].get("variants") or [] if isinstance(v, Mapping)
    }
    if "variant" in view and view["variant"] not in variants:
        yield f"{path}: default_view variant {view['variant']} is not one of its variants"


def _metric_errors(path: Path, metrics: Sequence[Any], names: frozenset[str]) -> Iterator[str]:
    """A procedure's time metrics each have a formula, and every formula is in the grammar."""
    for metric in metrics:
        if not isinstance(metric, Mapping):
            continue
        if not isinstance(metric.get("formula"), str):
            if names and metric.get("unit") == "ms":
                yield f"{path}: metric {metric.get('name')}: a time metric needs a formula"
            continue
        yield from (
            f"{path}: metric {metric.get('name')}: {issue}"
            for issue in formula_errors(metric["formula"], names)
        )
