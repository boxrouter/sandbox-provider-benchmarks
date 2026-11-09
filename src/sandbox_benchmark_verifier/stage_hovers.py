# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Stage hovers: what a legend stage times, as Start, End and Source lines, from the reader copy."""

from __future__ import annotations

import re
from collections.abc import Mapping
from difflib import SequenceMatcher
from functools import cache
from pathlib import Path
from typing import Any

from sandbox_benchmark_verifier.catalog import _assets
from sandbox_benchmark_verifier.names import Tables, load_tables
from sandbox_benchmark_verifier.placeholders import with_blocks
from sandbox_benchmark_verifier.yaml_load import load_yaml

# What a hover template line is in its card; the web renders each role its own way.
HOVER_ROLES = frozenset(
    "header value note body row measure subrow detail heading command unit more".split()
)
LINES = ("start", "end")
# The row of `stage_hovers` that is not a stage: the Source of the document's timing boundaries.
BOUNDARY = "boundary"
# Words a stage line never carries: the job host, a total, what was recorded, a "That" opener.
_BANNED = re.compile(r"(?i)\bjob[- ]host\b|\bsummed?\b|\brecorded\b|^that\b")
_OWN_CLOCK = "sandbox's own clock"
# The one runtime value a line may hold: the command of the variant the page has selected.
_PLACEHOLDER = re.compile(r"\{([^{}]*)\}")


@cache
def _tables(root: Path) -> Tables:
    """The names tables of a root, read once for the process; callers must not change them."""
    return load_tables(root)


@cache
def _reader_methods(root: Path) -> dict[str, dict[str, Any]]:
    """The reader copy's method entries, by the id of the document each method measures."""
    path = root / "reader/methods.yaml"
    if not path.is_file():
        return {}
    source = load_yaml(path.read_text(encoding="utf-8"))
    tables = _tables(root)
    documents = {
        str(row["id"]): str(
            tables.aliases.get("document", {}).get(row["document"], row["document"])
        )
        for row in tables.names
        if row["kind"] in ("test", "index") and "document" in row
    }
    return {
        documents[method]: entry
        for method, entry in source.get("methods", {}).items()
        if method in documents
    }


@cache
def _reader_stages(root: Path) -> dict[str, dict[str, Any]]:
    """The reader copy's stage rows, by the id of the document the method measures.

    A row may take a line from a shared block."""
    path = root / "reader/methods.yaml"
    if not path.is_file():
        return {}
    blocks = load_yaml(path.read_text(encoding="utf-8")).get("common", {}).get("blocks", {})
    return {
        document: with_blocks(entry["stages"], blocks)
        for document, entry in _reader_methods(root).items()
        if "stages" in entry
    }


def _reader_method(document: Mapping[str, Any]) -> Mapping[str, Any]:
    """The reader entry of a document's method; the names table aliases the id to the reader's."""
    root = _assets()
    identifier = str(document.get("id"))
    aliases = _tables(root).aliases.get("document", {})
    return _reader_methods(root).get(aliases.get(identifier, identifier), {})


def boundary_clock(entry: Mapping[str, Any]) -> str | None:
    """The clock a method's timing boundaries use: its `boundary_clock`, else its only `clock`."""
    clocks = entry.get("clock", [])
    return str(entry.get("boundary_clock") or (clocks[0] if len(clocks) == 1 else "")) or None


def stage_hovers(document: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    """The rows of every stage the document declares and the reader copy describes, by stage id.

    A document with timing boundaries also has the row `boundary`: its Source, the clock the
    boundaries are read from. It replaces the clock clause the boundary texts used to carry."""
    root = _assets()
    rows = _reader_stages(root).get(str(document.get("id")), {})
    stages = document.get("procedure", {}).get("stages", [])
    result = {
        str(stage["id"]): dict(rows[str(stage["id"])])
        for stage in stages
        if isinstance(stages, list) and isinstance(stage, Mapping)
        if str(stage.get("id")) in rows
    }
    clock = boundary_clock(_reader_method(document))
    if clock and document.get("timing_boundaries"):
        result[BOUNDARY] = {"clock": clock}
    return result


def boundary_source_errors(document: Mapping[str, Any]) -> list[str]:
    """A document with timing boundaries has one clock for them, named in the names table."""
    identifier = document.get("id")
    entry = _reader_method(document)
    if not entry or not document.get("timing_boundaries"):
        return []
    clock = boundary_clock(entry)
    known = {row["id"] for row in _tables(_assets()).names if row["kind"] == "clock"}
    if clock is None:
        return [
            f"{identifier}: its boundaries have several clocks; the reader names boundary_clock"
        ]
    if clock not in known:
        return [f"{identifier}: the boundary clock {clock} is not a names clock"]
    if clock not in entry.get("clock", []):
        return [f"{identifier}: the boundary clock {clock} is not one of the method's clocks"]
    return []


def _clock_errors(document: Mapping[str, Any], stage: Mapping[str, Any], clock: str) -> list[str]:
    """Source agrees with the timers of the steps the stage records, when those agree."""
    steps = document.get("procedure", {}).get("steps", [])
    records = set(stage.get("record_steps", []))
    own = [
        _OWN_CLOCK in " ".join(str(value) for value in step["timer"].values())
        for step in steps
        if isinstance(step, Mapping)
        and isinstance(step.get("timer"), Mapping)
        and records & set(step.get("record_steps", []))
    ]
    if not own or all(own) != any(own):
        return []  # no timer, or a stage that mixes two clocks: no one value is the whole truth
    expected = "sandbox" if all(own) else "runner"
    if clock != expected:
        return [f"stage {stage['id']} is timed by the {expected} clock, not {clock}"]
    return []


def stage_hover_errors(document: Mapping[str, Any], *, limit: int) -> list[str]:
    """Every declared stage has its lines: short, capitalised, plain; the first has no context."""
    identifier = document.get("id")
    declared = document.get("procedure", {}).get("stages", [])
    stages = [stage for stage in declared if isinstance(stage, Mapping)]
    reader = _reader_stages(_assets()).get(str(identifier))
    if reader is None or not stages:
        return []
    errors = [
        f"{identifier}: stage {stage['id']} has no hover lines in the reader copy"
        for stage in stages
        if str(stage["id"]) not in reader
    ]
    errors.extend(
        f"{identifier}: the reader copy describes stage {name}, which the document does not declare"
        for name in reader
        if name not in {str(stage["id"]) for stage in stages}
    )
    for position, stage in enumerate(stages):
        row = reader.get(str(stage["id"]))
        if row is None:
            continue
        if position == 0 and "start_after" in row:
            errors.append(
                f"{identifier}: stage {stage['id']} is first in its bar and has no start_after"
            )
        for key in (*LINES, "start_after"):
            text = row.get(key)
            if text is None:
                continue
            if len(text) > limit:
                size = f"{len(text)} characters, over {limit}"
                errors.append(f"{identifier}: stage {stage['id']} {key} is {size}")
            if not (text[:1].isupper() or text.startswith("`")):  # a command stays as typed
                errors.append(f"{identifier}: stage {stage['id']} {key} starts with a capital")
            if unknown := [name for name in _PLACEHOLDER.findall(text) if name != "command"]:
                errors.append(f"{identifier}: stage {stage['id']} {key} holds {{{unknown[0]}}}")
            if "{command}" in text and not any(
                isinstance(variant, Mapping) and "command" in variant
                for variant in document.get("procedure", {}).get("variants", [])
            ):
                errors.append(
                    f"{identifier}: stage {stage['id']} {key} names a command no variant has"
                )
            if _BANNED.search(text):
                errors.append(
                    f"{identifier}: stage {stage['id']} {key} uses a word the lines never carry"
                )
        errors.extend(
            f"{identifier}: {error}" for error in _clock_errors(document, stage, row["clock"])
        )
    return errors


def _normal(text: str) -> str:
    return " ".join(text.replace("`", "").casefold().split())


def _similar(first: str, second: str) -> float:
    return SequenceMatcher(None, _normal(first), _normal(second)).ratio()
