# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""The standard image size comes from the pinned Image Build facts, never typed."""

import json
from pathlib import Path
from typing import Any

import pytest

from sandbox_benchmark_verifier.catalog import load_methodology
from sandbox_benchmark_verifier.content import content_criteria
from sandbox_benchmark_verifier.conventions import load_conventions
from sandbox_benchmark_verifier.image_sizes import byte_size_text, standard_image_bytes

ROOT = Path(__file__).resolve().parents[1]
STANDARD = (
    next(item for item in load_methodology(ROOT) if item.document.get("id") == "sbs.common")
    .document["environment"]["image"]["reference"]
    .rsplit("@", 1)[1]
)

BYTE_SIZE = load_conventions()["presentation"]["byte_size"]


@pytest.mark.parametrize("vector", BYTE_SIZE["vectors"], ids=lambda row: row["text"])
def test_sizes_follow_the_published_byte_size_rule(vector: dict[str, Any]) -> None:
    # Web's bytesText runs the same vectors, so the rule has one owner and both readers follow it.
    assert byte_size_text(vector["bytes"]) == vector["text"]


def test_the_standard_image_size_is_read_from_its_facts_and_never_typed() -> None:
    page = content_criteria()["methodology_page"]
    size = standard_image_bytes(ROOT, STANDARD)
    assert size is not None and page["standard_image"]["size"] == byte_size_text(size)
    typed = json.loads((ROOT / "contracts/text/methodology-page.json").read_text())
    assert "size" not in typed["standard_image"]
