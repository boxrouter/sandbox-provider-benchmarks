# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Sequential TTI's dropdowns: short command phrases name the commands; the picker text is owned."""

import re
from pathlib import Path

import yaml

from sandbox_benchmark_verifier.content import content_criteria
from sandbox_benchmark_verifier.names import load_tables
from sandbox_benchmark_verifier.versions import version_key

ROOT = Path(__file__).resolve().parents[1]


def _latest_variants(document_id: str) -> list[dict[str, str]]:
    documents = [
        document
        for path in (ROOT / "methodology").rglob("*.yaml")
        for document in yaml.safe_load_all(path.read_text(encoding="utf-8"))
        if isinstance(document, dict) and document.get("id") == document_id
    ]
    latest = max(documents, key=lambda item: version_key(item["version"]))
    variants: list[dict[str, str]] = latest["procedure"]["variants"]
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


def test_the_sandbox_picker_text_is_owned_and_complete() -> None:
    selector = content_criteria()["sandbox_selector"]

    assert selector["one"] == "TTI for Sandbox: [{sandbox}]"
    assert selector["many"] == "TTI for Sandboxes: [{sandbox}]"
    assert re.fullmatch(r"\{n\}", selector["count"])
    assert {selector["all"], selector["none"]} == {"All", "None"}
    assert selector["most_named"] >= 1


def test_the_named_starts_are_sandboxes_of_the_trial_and_each_is_named() -> None:
    selector = content_criteria()["sandbox_selector"]
    documents = [
        document
        for path in (ROOT / "methodology").rglob("*.yaml")
        for document in yaml.safe_load_all(path.read_text(encoding="utf-8"))
        if isinstance(document, dict) and document.get("id") == "sequential-tti"
    ]
    latest = max(documents, key=lambda item: version_key(item["version"]))
    sandboxes = latest["procedure"]["sandboxes_per_trial"]
    numbers = [start["sandbox"] for start in selector["starts"]]
    assert numbers == sorted(set(numbers))
    assert all(1 <= number <= sandboxes for number in numbers)
    assert all(start["label"] and start["short"] for start in selector["starts"])
