# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Sequential TTI's dropdowns: short command phrases name the commands; the picker text is owned."""

from pathlib import Path

import yaml

from sandbox_benchmark_verifier.names import load_tables

ROOT = Path(__file__).resolve().parents[1]


def _latest_variants(document_id: str) -> list[dict[str, str]]:
    documents = [
        document
        for path in (ROOT / "methodology").rglob("*.yaml")
        for document in yaml.safe_load_all(path.read_text(encoding="utf-8"))
        if isinstance(document, dict) and document.get("id") == document_id
    ]
    [document] = documents
    variants: list[dict[str, str]] = document["procedure"]["variants"]
    return variants


def test_the_first_command_variants_are_named_by_a_short_command_phrase() -> None:
    """option A. Each name is the start of its command, word by word."""
    rows = [
        row
        for row in load_tables(ROOT).names
        if row.get("kind") == "variant" and row.get("parent") == "sequential-tti"
    ]
    assert [row["name"] for row in rows] == [
        "true",
        "python3 -c import",
        "node -e require",
        "git --version",
    ]
    commands = {variant["id"]: variant["command"] for variant in _latest_variants("sequential-tti")}
    for row in rows:
        words = commands[row["workload"]].split()
        assert all(
            words[index].lstrip("'\"").startswith(word)
            for index, word in enumerate(row["name"].split())
        ), row["id"]
