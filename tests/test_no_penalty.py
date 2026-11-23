# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""A missing unit takes the slowest (or highest-cost) value any provider recorded, no factor.

A missing workload is left out of the provider's composite.
"""

import re
from pathlib import Path
from typing import Any

import pytest

from sandbox_benchmark_verifier.text import _methodologies

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("identifier", ["time-index", "cost-index"])
def test_the_composites_carry_no_penalty_and_fill_inside_each_workload(identifier: str) -> None:
    document = {item["id"]: item for item in _methodologies(ROOT)}[identifier]
    assert "missing_unit_penalty" not in document["statistics"]
    composition = document["composition"]
    # A missing unit takes a value some provider recorded on that same unit, inside its workload,
    # and only when the failure is the provider's own.
    assert "any provider had on it" in composition["per_input_value"]
    assert "owner other than the provider" in composition["per_input_value"]
    # A workload the provider has no result on is left out of its composite, nothing substituted
    # (founder decision, 2026-11-22).
    assert "left out of that provider's composite" in composition["inclusion"]
    assert "nothing is substituted" in composition["inclusion"]
    assert not re.search(r"(?i)penalty|multipl", str(composition))


def test_no_reader_text_names_a_penalty_or_a_multiplier(bundle: dict[str, Any]) -> None:
    criteria = bundle["content_criteria"]
    texts = {
        **criteria["fact_list"],
        **{
            f"{kind}.{key}": value
            for kind in ("time", "cost")
            for key, value in criteria["index_copy"]["details"][kind].items()
        },
    }
    for name, text in texts.items():
        assert not re.search(r"(?i)penalty|\d+(?:\.\d+)?x\b|multipl", text), name
    # The one remaining word is the label of a retired field that earlier, immutable versions carry.
    for path in (ROOT / "contracts/text").glob("*.json"):
        text = path.read_text(encoding="utf-8").lower()
        text = text.replace("missing_unit_penalty", "").replace("missing unit penalty", "")
        assert "penalty" not in text, path.name
