# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""The one reader of a dotted version (document, parameter tree and reader versions)."""

from collections.abc import Iterable, Mapping
from typing import Any, TypeVar

_Document = TypeVar("_Document", bound=Mapping[str, Any])


def version_key(version: object) -> tuple[int, ...]:
    """A dotted version as comparable integers, so 1.1.10 sorts after 1.1.4."""
    return tuple(int(part) for part in str(version).split("."))


def document_version(document: Mapping[str, Any]) -> tuple[int, ...]:
    """A document's version as comparable integers, to sort documents."""
    return version_key(document["version"])


def newest(documents: Iterable[_Document]) -> dict[str, _Document]:
    """The newest version of each document, by id; the first of two equal versions is kept."""
    found: dict[str, _Document] = {}
    for document in documents:
        identifier = str(document["id"])
        known = found.get(identifier)
        if known is None or document_version(document) > document_version(known):
            found[identifier] = document
    return found
