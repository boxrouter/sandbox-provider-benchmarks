# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Sentences that are true of only some measured versions: ``[[when <path> <op> <value>: text]]``.

The reader copy is one text per method. Most of it reads each version's own numbers through
``{param:measured.<path>}``, but a sentence whose claim depends on a parameter's value ("trials run
in parallel", written for versions that ran several at once) is false for the versions that did not.
A gate keeps its text for the versions where the comparison holds and drops it, and the space
before it, for the others::

    [[when measured.procedure.concurrency.trials_in_parallel > 1: Trials run in parallel.]]

The path reads the measured definition (``#`` after a list's path is its length). The operator is
one of ``==``, ``!=``, ``>``, ``>=``, ``<``, ``<=``; the value is a number or a word. A path the
measured definition lacks makes the gate false, except for a method's newest version, where it
raises, so a mistyped path fails the build instead of hiding a sentence. A gate does not hold
another gate or a ``[[steps: ...]]`` marker.
"""

from __future__ import annotations

import operator
import re
from collections.abc import Callable, Mapping
from typing import Any

from sandbox_benchmark_verifier.placeholders import _path_value

_OPERATORS: dict[str, Callable[[Any, Any], bool]] = {
    "==": operator.eq,
    "!=": operator.ne,
    ">=": operator.ge,
    "<=": operator.le,
    ">": operator.gt,
    "<": operator.lt,
}
_OPENER = "[[when "
GATE = re.compile(
    r"( ?)\[\[when measured\.([A-Za-z0-9_.-]+#?) "
    r"(==|!=|>=|<=|>|<) ([A-Za-z0-9_.-]+): ((?:(?!\[\[|\]\]).)*)\]\]",
    re.DOTALL,
)


def _value(word: str) -> int | str:
    return int(word) if re.fullmatch(r"-?\d+", word) else word


def _holds(measured: Mapping[str, Any], path: str, symbol: str, word: str, strict: bool) -> bool:
    try:
        found = _path_value(measured, path.removesuffix("#"))
    except KeyError as error:
        if strict:
            raise ValueError(
                f"[[when measured.{path}]]: the measured definition has no {path}"
            ) from error
        return False
    if path.endswith("#"):
        found = len(found)
    wanted = _value(word)
    if isinstance(found, bool) or type(found) is not type(wanted):
        return symbol == "!="
    return _OPERATORS[symbol](found, wanted)


def gated(value: Any, measured: Mapping[str, Any], *, strict: bool = False) -> Any:
    """The reader copy with each gate resolved for one measured version; a string, list item or
    note that nothing is left of is dropped."""
    if isinstance(value, str):
        text = GATE.sub(
            lambda match: (
                match[1] + match[5]
                if _holds(measured, match[2], match[3], match[4], strict)
                else ""
            ),
            value,
        )
        return (
            text
            if text == value
            else re.sub(r"\n{3,}", "\n\n", re.sub(r" +\n", "\n", text)).strip()
        )
    if isinstance(value, Mapping):
        kept = {key: gated(item, measured, strict=strict) for key, item in value.items()}
        return {key: item for key, item in kept.items() if item not in ("", [])}
    if isinstance(value, list):
        return [
            item
            for item in (gated(entry, measured, strict=strict) for entry in value)
            if item != ""
        ]
    return value


def ungated(text: str) -> str:
    """The text with every gate taken as holding: what the checks of the authored sentences read."""
    return GATE.sub(lambda match: match[1] + match[5], text)


def gate_errors(text: str) -> list[str]:
    """Gates the grammar does not accept (a misspelt operator, a path outside ``measured.``, a
    nested gate)."""
    rest = GATE.sub("", text)
    return [
        f"malformed gate near {rest[found.start() : found.start() + 60]!r}"
        for found in re.finditer(re.escape(_OPENER), rest)
    ]
