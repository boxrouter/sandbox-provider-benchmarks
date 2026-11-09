# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""The cost split is an allocation by time share, said once for every workload."""

import copy
from typing import Any

import pytest

from sandbox_benchmark_verifier.content import content_criteria
from sandbox_benchmark_verifier.cost_split import cost_split_errors
from sandbox_benchmark_verifier.reader import reader_text
from sandbox_benchmark_verifier.versions import version_key

pytestmark = pytest.mark.unit

WORKLOADS = ("deepswe", "terminal-bench-4-0", "r2e-gym", "ci-jobs")


@pytest.fixture(name="criteria")
def _criteria() -> dict[str, Any]:
    return copy.deepcopy(content_criteria())


def test_the_owner_text_states_the_allocation_once(criteria: dict[str, Any]) -> None:
    assert cost_split_errors(criteria) == []


def test_a_per_workload_cost_hover_or_a_missing_sentence_is_refused(
    criteria: dict[str, Any],
) -> None:
    criteria["hover_templates"]["deepswe_cost_split_group"] = copy.deepcopy(
        criteria["hover_templates"]["legend_info_deep_swe_build"]
    )
    assert any("per-workload copy" in error for error in cost_split_errors(criteria))
    del criteria["hover_templates"]["deepswe_cost_split_group"]
    criteria["fact_list"]["fact_cost_split_rule"] = "Cost is split between stages."
    assert any("time-share allocation" in error for error in cost_split_errors(criteria))


@pytest.mark.parametrize("identifier", WORKLOADS)
def test_every_workload_reads_the_one_allocation_sentence(
    bundle: dict[str, Any], identifier: str
) -> None:
    sentence = bundle["content_criteria"]["fact_list"]["fact_cost_split_rule"]
    method = bundle["reader"]["methods"][identifier]
    version = max(bundle["methodologies"][method["document"]], key=version_key)
    assert sentence in reader_text(bundle, identifier, version)["notes"]["cost_how_to_read"]
