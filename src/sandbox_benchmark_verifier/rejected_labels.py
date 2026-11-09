# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""A names row never carries a label it rejects.

A names row lists ``rejected_labels``: words ruled out as the row's label. A rejection belongs to
its row, not to the word: ``Cold Startup Time`` is rejected for Burst TTI and is the name of
Sequential TTI, and ``Cold Start`` labels a Previews field. So the check is per row: none of the
row's own labels (its name, its label, its selector's label) is one of its rejected words.
"""

from collections.abc import Iterator, Mapping, Sequence
from typing import Any


def _own_labels(row: Mapping[str, Any]) -> Iterator[tuple[str, str]]:
    for field in ("name", "label"):
        if isinstance(row.get(field), str):
            yield field, row[field]
    selector = row.get("selector")
    if isinstance(selector, Mapping) and isinstance(selector.get("label"), str):
        yield "selector.label", selector["label"]


def rejected_label_errors(names: Sequence[Mapping[str, Any]]) -> list[str]:
    """Every names row label that is one of the row's own rejected labels."""
    errors: list[str] = []
    for row in names:
        rejected = {label.casefold() for label in row.get("rejected_labels", [])}
        errors.extend(
            f"names {row['kind']}.{row['id']}.{field} is a rejected label: {text!r}"
            for field, text in _own_labels(row)
            if text.casefold() in rejected
        )
    return errors
