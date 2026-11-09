# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Scans of the reader-facing sources for words the conventions retire or forbid."""

import json
import re
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from sandbox_benchmark_verifier.conventions import load_conventions
from sandbox_benchmark_verifier.versions import newest
from sandbox_benchmark_verifier.yaml_load import read_mapping

_RETIRED_WORD_SKIPPED_KEYS = frozenset(
    {"id", "parent", "document", "previous_names", "display_name_history", "rejected_labels"}
)


def _reader_strings(value: Any, where: str) -> Iterator[tuple[str, str]]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key not in _RETIRED_WORD_SKIPPED_KEYS:
                yield from _reader_strings(child, f"{where}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _reader_strings(child, f"{where}[{index}]")
    elif isinstance(value, str):
        yield where, value


def retired_word_errors(root: Path, documents: list[dict[str, Any]]) -> list[str]:
    """No reader-facing string of the names, the page contracts, the reader text or the newest
    version of a current document holds a retired word (conventions ``naming.retired_words``)."""
    if not (root / "conventions" / "text-conventions.yaml").is_file():
        return []
    words = load_conventions(root)["naming"].get("retired_words", [])
    if not words:
        return []
    pattern = re.compile(rf"\b(?:{'|'.join(map(re.escape, words))})\b", re.IGNORECASE)
    sources: dict[str, Any] = {}
    for path, skipped in (
        (root / "names" / "names.yaml", ()),
        (root / "reader" / "methods.yaml", ()),
        *(
            (path, ("retired_reader_paths",))
            for path in sorted((root / "contracts").rglob("*.json"))
        ),
    ):
        if path.is_file():
            loaded = (
                json.loads(path.read_text(encoding="utf-8"))
                if path.suffix == ".json"
                else read_mapping(path)
            )
            sources[str(path.relative_to(root))] = {
                key: child for key, child in loaded.items() if key not in skipped
            }
    retired = set(read_mapping(root / "names" / "aliases.yaml").get("document", {}))
    # The Common Methodology holds retired text under paths the contract already retires.
    current = newest(
        document
        for document in documents
        if str(document["id"]) not in retired and document["id"] != "sbs.common"
    )
    sources.update((f"{key}@{doc['version']}", doc) for key, doc in current.items())
    return [
        f"{source}{where}: {match.group(0)!r} is a retired word"
        for source, value in sources.items()
        for where, text in _reader_strings(value, "")
        if (match := pattern.search(re.sub(r"\{[^{}]*\}", " ", text)))  # a reference is not prose
    ]


def clock_name_errors(rows: list[dict[str, Any]], root: Path) -> list[str]:
    """A clock is named by whose it is; its name never mentions the private runner."""
    if not (root / "conventions" / "text-conventions.yaml").is_file():
        return []
    words = load_conventions(root)["naming"].get("clock_name_forbidden_words", [])
    return [
        f"names row {row['row']} clock {row['id']}: {row['name']!r} holds {match.group(0)!r}"
        for row in rows
        if row["kind"] == "clock" and words
        if (match := re.search(rf"\b(?:{'|'.join(map(re.escape, words))})\b", row["name"], re.I))
    ]
