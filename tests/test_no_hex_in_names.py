# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Rule no-hex-in-names: no names-table name or current variant label shows a hexadecimal id."""

import re
from pathlib import Path

import pytest
import yaml

from sandbox_benchmark_verifier.names import load_tables
from sandbox_benchmark_verifier.text import _methodologies

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]


def _pattern() -> re.Pattern[str]:
    """The rule's own pattern, read from the conventions file that owns it."""
    conventions = yaml.safe_load((ROOT / "conventions" / "text-conventions.yaml").read_text())
    rule = next(r for r in conventions["presentation"]["rules"] if r["id"] == "no-hex-in-names")
    return re.compile("|".join(rule["predicate"]["value"]))


def test_names_table_has_no_hex_names() -> None:
    pattern = _pattern()
    found = [
        f"{row['kind']} {row['id']}: {row['name']}"
        for row in load_tables(ROOT).names
        if pattern.search(str(row.get("name", "")))
    ]
    assert found == []


def test_current_workload_variant_labels_have_no_hex() -> None:
    """Each document (old ids folded into their canonical one) is checked."""
    pattern = _pattern()
    canonical = load_tables(ROOT).aliases["document"]
    current = {
        canonical.get(document["id"], document["id"]): document for document in _methodologies(ROOT)
    }
    found = [
        f"{identifier} {variant.get('id')}: {variant.get('label')}"
        for identifier, document in current.items()
        for variant in (document.get("procedure") or {}).get("variants") or []
        if pattern.search(str(variant.get("label", "")))
    ]
    assert found == []
