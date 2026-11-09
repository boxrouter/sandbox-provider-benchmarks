# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""A retired word reaches no reader: one scan over every string a consumer draws from the bundle.

The words are the keys of rule ``reader.vocabulary`` in the text conventions; the rule's
predicate checks a rendered slot, this scan checks the sources a consumer draws from. The current
version of each document is scanned (older versions are released history and stay as they were),
and so are the contract text, the reader copy and the metric labels. Ids are not reader text:
a value under an ``id`` key, the paths the reader omits, a rule's own text and the registry's
retired-name history are skipped.
"""

import re
from collections.abc import Iterator, Mapping
from typing import Any

from sandbox_benchmark_verifier.name_refs import reader_strings
from sandbox_benchmark_verifier.versions import version_key

RULE_ID = "reader.vocabulary"
# Subtrees of the content criteria that state rules or ids rather than reader text.
_NOT_READER_TEXT = frozenset({"conventions", "canonical_order"})
# What a consumer draws from a document row; the rest of the row is digests and links.
_ROW_TEXT = ("reader_definition", "text")
# Keys whose values name fields, not words.
_ID_KEYS = frozenset({"id", "retired_reader_paths"})


def retired_words(conventions: Mapping[str, Any]) -> list[str]:
    """The words the vocabulary rule allows in no context."""
    rule = next(item for item in conventions["rules"] if item["id"] == RULE_ID)
    return [word for word, contexts in rule["predicate"]["value"].items() if not contexts]


def _current_rows(
    bundle: Mapping[str, Any], aliases: Mapping[str, str]
) -> Iterator[tuple[str, str, Mapping[str, Any]]]:
    """The newest version of every document a page reads (not a superseded document id)."""
    methodologies = bundle["methodologies"]
    for identifier, versions in methodologies.items():
        if aliases.get(identifier) in methodologies:
            continue  # an earlier lineage of a document that has its own id now
        newest = max(versions, key=version_key)
        yield identifier, newest, versions[newest]


def retired_word_errors(
    bundle: Mapping[str, Any], conventions: Mapping[str, Any], aliases: Mapping[str, str]
) -> list[str]:
    """Every string a consumer draws that holds a retired word, with where it is."""
    words = retired_words(conventions)
    if not words:
        return []
    pattern = re.compile(rf"(?i)\b(?:{'|'.join(re.escape(word) for word in words)})\b")
    criteria = {
        key: value
        for key, value in bundle["content_criteria"].items()
        if key not in _NOT_READER_TEXT
    }
    sources: list[tuple[str, Any]] = [
        ("content_criteria", criteria),
        ("reader", bundle["reader"]),
        ("metric_labels", bundle["metric_labels"]),
    ]
    for identifier, version, row in _current_rows(bundle, aliases):
        sources.extend(
            (f"{identifier}@{version}.{field}", row[field]) for field in _ROW_TEXT if field in row
        )
    return [
        f"retired word {match.group(0)!r} in {path}: {text[:100]}"
        for name, source in sources
        for path, text in reader_strings(source, name, skip=_ID_KEYS)
        if (match := pattern.search(text))
    ]
