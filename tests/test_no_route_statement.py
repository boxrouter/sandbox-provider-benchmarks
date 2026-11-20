# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Public text never says that the benchmarks run through BoxRouter, Cloud, Core or an API."""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
FOLDERS = ("methodology", "reader", "conventions", "names", "contracts/text")
ROUTE = re.compile(
    r"\b(?:through|via)\s+(?:BoxRouter|Cloud)\b|\bBoxRouter\s+(?:Cloud|Core)\b|\bthe\s+Cloud\s+API\b",
    re.IGNORECASE,
)
# The convention that forbids the phrases has to name them once.
RULE_ID = "methodology-style.no-route-statement"


def _public_lines() -> list[tuple[str, int, str]]:
    lines: list[tuple[str, int, str]] = []
    for folder in FOLDERS:
        for path in sorted((ROOT / folder).rglob("*")):
            if not path.is_file() or path.suffix not in {".yaml", ".yml", ".json"}:
                continue
            text = path.read_text(encoding="utf-8")
            if path.name == "text-conventions.yaml":
                start = text.index(f"  - id: {RULE_ID}\n")
                end = text.index("\n  - id:", start + 1)
                text = text[:start] + text[end:]
            for number, line in enumerate(text.splitlines(), start=1):
                lines.append((str(path.relative_to(ROOT)), number, line))
    return lines


def test_the_folders_exist() -> None:
    assert _public_lines()


def test_no_public_text_states_the_route() -> None:
    hits = [
        f"{path}:{number}: {line.strip()[:120]}"
        for path, number, line in _public_lines()
        if ROUTE.search(line)
    ]
    assert not hits, "Public text must not name the route the benchmarks take:\n" + "\n".join(hits)


@pytest.mark.parametrize(
    "sentence",
    [
        "Each latency includes the trip through BoxRouter Cloud to the provider.",
        "Every benchmark runs through BoxRouter.",
        "Measured via Cloud.",
        "Each benchmark runs through BoxRouter Core and pinned provider adapters.",
        "The benchmark calls the Cloud API.",
    ],
)
def test_the_pattern_catches_the_phrases(sentence: str) -> None:
    assert ROUTE.search(sentence)


def test_the_pattern_leaves_owner_wording_alone() -> None:
    assert not ROUTE.search(
        "BoxRouter's pinned standard image and BoxRouter's own benchmark script."
    )
