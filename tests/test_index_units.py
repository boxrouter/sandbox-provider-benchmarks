# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""An index input says "per X" with the unit word its workload's names row owns."""

import json
import re
from pathlib import Path

import pytest

from sandbox_benchmark_verifier.name_refs import canonical_name_map
from sandbox_benchmark_verifier.names import load_tables
from sandbox_benchmark_verifier.text import _methodologies
from sandbox_benchmark_verifier.versions import newest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
INDEXES = ("time-index", "cost-index", "index.speed_score", "index.cost_score")


@pytest.mark.parametrize("identifier", INDEXES)
def test_every_input_of_the_newest_index_version_reads_its_workloads_unit_word(
    identifier: str,
) -> None:
    names = canonical_name_map(load_tables(ROOT))
    inputs = newest(_methodologies(ROOT))[identifier]["composition"]["inputs"]
    assert inputs
    for member in inputs:
        per = re.search(r"\bper (\w+)$", member["quantity"])
        assert per, member
        assert per.group(1) == names[f"unit.{member['id']}"], member


def test_the_empty_index_sentences_take_the_member_count_from_the_caller() -> None:
    facts = json.loads((ROOT / "contracts/text/fact-list.json").read_text(encoding="utf-8"))
    codes = (
        "fact_no_result_all",
        "fact_no_result_one",
        "fact_no_result_two",
        "fact_no_result_many",
        "fact_no_exact_cost",
        "fact_no_exact_cost_one",
        "fact_no_exact_cost_two",
    )
    sentences = {code: facts[code] for code in codes}
    assert "{count}" in sentences["fact_no_result_many"]
    assert "{count}" in sentences["fact_no_exact_cost"]
    assert not any(re.search(r"\b(?:two|three|four|five)\b", text) for text in sentences.values())
