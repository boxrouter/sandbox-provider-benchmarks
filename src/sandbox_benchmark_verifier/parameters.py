# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Checks for pinned workload parameters, shared trials, formulas and measurement variants."""

import hashlib
import json
import re
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

from sandbox_benchmark_verifier.text import semantic_digest

RECORD_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
FORMULA_NAMES = frozenset({"start", "end", "total", "runtime"})
TOKEN = re.compile(r"\s*([A-Za-z_][A-Za-z0-9_-]*|[()+-])")


def tree_digest(directory: Path) -> str:
    """SHA-256 of one ``<file sha256> <relative path>`` line per file, sorted by path bytes."""
    lines = sorted(
        (path.relative_to(directory).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest())
        for path in directory.rglob("*")
        if path.is_file()
    )
    lines.sort(key=lambda item: item[0].encode())
    return hashlib.sha256(
        "".join(f"{digest} {name}\n" for name, digest in lines).encode()
    ).hexdigest()


def workload_version(workload: Mapping[str, Any]) -> str:
    """A workload's version: a digest of its data, capabilities and image, never its label."""
    text = json.dumps(
        [workload.get("data"), list(workload.get("capabilities", [])), workload.get("image")],
        sort_keys=True,
    )
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()[:16]


def workload_errors(directory: Path) -> Iterator[str]:
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
        if workload["version"] != workload_version(workload):
            yield f"{path}: workload {identifier} version does not match its data"


def parameter_workloads(directory: Path) -> set[str]:
    """Workload ids in a parameters directory, empty when it cannot be read."""
    try:
        found = json.loads((directory / "workloads.json").read_text())
    except (OSError, json.JSONDecodeError):
        return set()
    if not isinstance(found, list):
        return set()
    return {w["id"] for w in found if isinstance(w, Mapping) and isinstance(w.get("id"), str)}


def formula_errors(formula: str, steps: frozenset[str]) -> Iterator[str]:
    """Check a metric formula against the grammar; ``steps`` empty skips step-name checks."""
    position = 0
    tokens: list[str] = []
    while position < len(formula.rstrip()):
        match = TOKEN.match(formula, position)
        if match is None:
            yield f"formula has an unexpected character at {position}"
            return
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

    problems: list[str] = []

    def expression() -> None:
        term()
        while peek() in ("+", "-"):
            nonlocal cursor
            cursor += 1
            term()

    def term() -> None:
        nonlocal cursor
        name = peek()
        if name is None or name in "()+-":
            problems.append("formula expects a term")
            return
        cursor += 1
        if name == "median":
            if not expect("("):
                problems.append("median needs parentheses")
                return
            expression()
            if not expect(")"):
                problems.append("median is not closed")
        elif name == "latency" or name in FORMULA_NAMES:
            step_argument(name, required=name == "latency")
        else:
            problems.append(f"formula uses unknown term {name}")

    def step_argument(name: str, *, required: bool) -> None:
        nonlocal cursor
        if not expect("("):
            if required:
                problems.append(f"{name} needs a step in parentheses")
            return
        step = peek()
        if step is None or step in "()+-":
            problems.append(f"{name} needs a step")
            return
        cursor += 1
        if steps and step not in steps:
            problems.append(f"{name} names unknown step {step}")
        if not expect(")"):
            problems.append(f"{name} is not closed")

    expression()
    if cursor != len(tokens):
        problems.append("formula has trailing input")
    yield from problems[:1]


def _reuses_parameters(
    document: Mapping[str, Any], owners: Mapping[tuple[str, str], Mapping[str, Any]]
) -> bool:
    parameters = document.get("parameters", {})
    return any(
        owner.get("id") == document.get("id")
        and owner.get("parameters") == parameters
        and parameters.get("path") == f"parameters/{owner.get('id')}/{owner.get('version')}/"
        and semantic_digest(owner) == semantic_digest(document)
        for owner in owners.values()
    )


def document_errors(
    root: Path,
    path: Path,
    document: Mapping[str, Any],
    owners: Mapping[tuple[str, str], Mapping[str, Any]],
) -> Iterator[str]:
    """Checks on one methodology document: parameters, shared trial and formulas."""
    identifier, version = document.get("id"), document.get("version")
    parameters = document.get("parameters")
    shared = (
        document.get("procedure", {}).get("shared_trial")
        if isinstance(document.get("procedure"), Mapping)
        else None
    )
    if isinstance(parameters, Mapping):
        expected = f"parameters/{identifier}/{version}/"
        directory = root / str(parameters.get("path", ""))
        if parameters.get("path") != expected and not _reuses_parameters(document, owners):
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
        owner = owners.get((str(shared.get("with")), str(shared.get("version"))))
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
    for metric in document.get("metrics", []) if isinstance(document.get("metrics"), list) else []:
        if isinstance(metric, Mapping) and isinstance(metric.get("formula"), str):
            yield from (
                f"{path}: metric {metric.get('name')}: {issue}"
                for issue in formula_errors(metric["formula"], names)
            )


def allowed_variants(
    root: Path,
    document: Mapping[str, Any],
    owners: Mapping[tuple[str, str], Mapping[str, Any]],
) -> set[str]:
    """Variant ids a measurement of this methodology may carry."""
    procedure = document.get("procedure")
    found: set[str] = set()
    if isinstance(procedure, Mapping):
        for variant in procedure.get("variants", []):
            if isinstance(variant, Mapping) and isinstance(variant.get("id"), str):
                found.add(variant["id"])
        shared = procedure.get("shared_trial")
        if isinstance(shared, Mapping):
            owner = owners.get((str(shared.get("with")), str(shared.get("version"))))
            if owner is not None:
                found |= allowed_variants(root, owner, {})
    parameters = document.get("parameters")
    if isinstance(parameters, Mapping):
        found |= parameter_workloads(root / str(parameters.get("path", "")))
    return found


def variant_errors(
    root: Path,
    records: Sequence[tuple[Path, Any]],
    owners: Mapping[tuple[str, str], Mapping[str, Any]],
) -> Iterator[str]:
    """A measurement's ``variant`` must be a variant or parameter workload of its methodology."""
    for path, record in records:
        if not isinstance(record, Mapping) or "variant" not in record:
            continue
        method = record.get("methodology")
        if not isinstance(method, Mapping):
            continue
        document = owners.get((str(method.get("id")), str(method.get("version"))))
        if document is None:
            continue  # unknown methodology is reported elsewhere
        if record["variant"] not in allowed_variants(root, document, owners):
            yield f"{path}: variant {record['variant']} is not a variant of {method.get('id')}"
