# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""How reader text writes the numbers a measured definition holds: the definition's numbers by
key, the unit table of the numbers rule, and the counts (in digits or as a word) and versions the
rule methodology-style.numbers-are-references compares with them."""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from typing import Any

from sandbox_benchmark_verifier.placeholders import NUMBER_WORDS, fio_size_bytes

IGNORED_DEFINITION_KEYS = frozenset({"version", "schema", "order", "row"})


def stem(word: str) -> str:
    return word[:-1] if word.endswith("s") and len(word) > 4 else word


def definition_numbers(value: Any, key: str = "") -> Iterator[tuple[str, float]]:
    """Every number of a definition with the name of the key that holds it."""
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        yield key, float(value)
    elif (size := fio_size_bytes(value)) is not None:
        yield (
            f"{key}_bytes",
            float(size),
        )  # fio's string sizes ("4k") are sizes too
    elif isinstance(value, Mapping):
        for name, child in value.items():
            if name not in IGNORED_DEFINITION_KEYS:
                yield from definition_numbers(child, str(name))
    elif isinstance(value, list):
        for child in value:
            yield from definition_numbers(child, key)


class Units:
    """The unit table of the numbers rule: the words a unit is written with, and its scale."""

    def __init__(self, table: Mapping[str, Mapping[str, Any]]) -> None:
        self.table = table

    def of_text(self, word: str) -> str | None:
        return next(
            (name for name, unit in self.table.items() if word.lower() in unit["text"]), None
        )

    def of_key(self, key: str) -> str | None:
        parts = key.lower().split("_")
        return next(
            (
                name
                for part in reversed(parts)
                for name, unit in self.table.items()
                if part in unit["keys"]
            ),
            None,
        )

    def matches(self, number: float, word: str, leaves: Sequence[tuple[str, float]]) -> bool:
        """The number with the unit that follows it equals a value in that unit; a bare count, a
        count of the same thing."""
        unit = self.of_text(word)
        if unit is not None:
            return any(
                (other := self.of_key(key)) is not None
                and self.same((number, unit), (value, other))
                for key, value in leaves
            )
        noun = stem(word.lower())
        # A noun is a word of three letters or more; "four in sequence" counts no "in_" key.
        return len(noun) >= 3 and any(
            value == number
            and self.of_key(key) is None
            and noun in {stem(part) for part in key.lower().split("_")}
            for key, value in leaves
        )

    def same(self, left: tuple[float, str], right: tuple[float, str]) -> bool:
        (a, unit_a), (b, unit_b) = left, right
        if self.table[unit_a]["dimension"] != self.table[unit_b]["dimension"]:
            return False
        return bool(abs(a * self.table[unit_a]["factor"] - b * self.table[unit_b]["factor"]) < 1e-9)


def unit_word(match: re.Match[str]) -> str:
    """The word after a number: the unit or noun it counts, written after a space or a hyphen."""
    groups = match.groupdict()
    return groups.get("hyphened") or groups.get("word") or ""


# A count written as a word ("four variants"), and the words after it, where a count of the
# definition's variants may name them after an adjective or two ("three cold-cache installation
# variants").
COUNT_WORD = re.compile(
    rf"(?<![\w-])(?P<number>{'|'.join(NUMBER_WORDS)})(?![\w-])(?:\s+(?P<word>[A-Za-z][A-Za-z/]*))?",
    re.IGNORECASE,
)
COUNTED = re.compile(
    rf"(?<![\w.,-])(?P<number>\d[\d,]*|{'|'.join(NUMBER_WORDS)})(?P<after>(?:\s+[\w-]+){{1,3}})",
    re.IGNORECASE,
)
VARIANT_WORD = re.compile(r"\bvariants?\b", re.IGNORECASE)
VERSION = re.compile(r"(?<![\w.])\d+\.\d+(?:\.\d+)?(?![\w.])")


def written_number(match: re.Match[str]) -> str:
    """A number as the text writes it, with the unit or noun after it."""
    joiner = "-" if match.groupdict().get("hyphened") else " "
    return f"{match['number']}{joiner}{unit_word(match)}".strip()


def number_of(text: str) -> float:
    word = text.lower()
    return (
        float(NUMBER_WORDS.index(word) + 2)
        if word in NUMBER_WORDS
        else float(text.replace(",", ""))
    )


def versions_held(document: Mapping[str, Any]) -> set[str]:
    """The versions the definition's source names ("vite 8.3.4" in source.ids)."""
    ids = document.get("source", {}).get("ids", []) if isinstance(document, Mapping) else []
    return {found for entry in ids if isinstance(entry, str) for found in VERSION.findall(entry)}
