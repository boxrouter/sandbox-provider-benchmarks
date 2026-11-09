# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""A missing result takes the slowest (or highest-cost) value any provider recorded, no factor."""

import re
from pathlib import Path
from typing import Any

import pytest

from sandbox_benchmark_verifier.text import _methodologies
from sandbox_benchmark_verifier.versions import newest, version_key

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]


# The first versions without the penalty; every later version must keep it out.
@pytest.mark.parametrize(
    ("identifier", "first"), [("time-index", "1.1.19"), ("cost-index", "1.1.17")]
)
def test_the_latest_composites_carry_no_penalty_and_fill_inside_each_workload(
    identifier: str, first: str
) -> None:
    document = newest(_methodologies(ROOT))[identifier]
    version = version_key(document["version"])
    assert version >= version_key(first)
    assert "missing_unit_penalty" not in document["statistics"]
    composition = document["composition"]
    # A missing unit takes a value some provider recorded on that same unit, inside its workload,
    # and only when the failure is the provider's own.
    assert "any provider had on it" in composition["per_input_value"]
    assert "owner other than the provider" in composition["per_input_value"]
    assert re.search(r"takes (the|its)", composition["inclusion"])
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
