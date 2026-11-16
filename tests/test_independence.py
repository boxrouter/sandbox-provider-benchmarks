# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Every benchmark answers each independence rule of the common methodology."""

import copy
from pathlib import Path
from typing import Any

import pytest

from sandbox_benchmark_verifier.cli import load_documents
from sandbox_benchmark_verifier.independence import (
    answer_errors,
    independence_errors,
    rule_ids,
)

ROOT = Path(__file__).resolve().parents[1]
RULES = [
    "fresh_sandbox",
    "equal_concurrency",
    "shuffled_order",
    "caches",
    "job_host",
    "shared_provider_hosts",
    "empty_account",
    "placement_facts",
    "run_check",
    "outliers",
]


def _documents() -> list[dict[str, Any]]:
    loaded, errors = load_documents(ROOT)
    assert errors == []
    return [
        dict(document)
        for path, document in loaded
        if path.is_relative_to(ROOT / "methodology") and isinstance(document, dict)
    ]


def _common() -> dict[str, Any]:
    return next(document for document in _documents() if document["id"] == "sbs.common")


def _benchmark() -> dict[str, Any]:
    return next(document for document in _documents() if document["id"] == "sequential-read")


@pytest.mark.unit
def test_repository_answers_every_rule() -> None:
    loaded, _ = load_documents(ROOT)
    assert independence_errors(ROOT, loaded) == []


@pytest.mark.unit
def test_common_states_the_ten_rules_and_the_thresholds() -> None:
    common = _common()
    assert rule_ids(common) == RULES
    independence = common["independence"]
    assert independence["rounds"] == 4
    check = independence["run_check"]
    assert check["min_trials"] == 15
    assert check["significance"]["correction"] == "benjamini_hochberg"
    assert check["significance"]["q"] == 0.05
    assert [item["id"] for item in check["checks"]] == [
        "trend",
        "first_round",
        "round_start",
        "lag1",
        "overlap",
        "host_saturated",
        "host_type",
        "tier_split",
        "outliers",
        "cache_flags",
    ]
    tier = next(item for item in check["checks"] if item["id"] == "tier_split")
    assert tier["min_ashman_d_by_n"][0] == [8, 6.3]
    assert "cluster_p" not in tier


@pytest.mark.unit
def test_a_missing_answer_fails() -> None:
    common = _common()
    document = copy.deepcopy(_benchmark())
    assert answer_errors(document, common) == []
    del document["independence_answers"]["caches"]
    assert answer_errors(document, common) == [
        "sequential-read: independence rule caches is unanswered"
    ]
    del document["independence_answers"]
    assert "independence_answers is missing" in answer_errors(document, common)[0]


@pytest.mark.unit
def test_an_unknown_answer_fails() -> None:
    common = _common()
    document = copy.deepcopy(_benchmark())
    document["independence_answers"]["made_up"] = {"status": "meets", "text": "x"}
    assert "made_up is not a rule" in answer_errors(document, common)[0]


@pytest.mark.unit
def test_text_that_says_one_at_a_time_contradicts_parallel_trials() -> None:
    common = _common()
    document = copy.deepcopy(_benchmark())
    parallel = document["procedure"]["concurrency"]["trials_in_parallel"]
    assert parallel > 1
    document["interpretation"]["caveats"] = ["One trial runs at a time per provider."]
    errors = answer_errors(document, common)
    assert errors and f"trials_in_parallel is {parallel}" in errors[0]


@pytest.mark.unit
def test_a_published_test_must_answer() -> None:
    loaded, _ = load_documents(ROOT)
    stripped = [
        (
            path,
            {key: value for key, value in document.items() if key != "independence_answers"}
            if isinstance(document, dict) and document.get("id") == "stream-triad"
            else document,
        )
        for path, document in loaded
    ]
    errors = independence_errors(ROOT, stripped)
    assert any(error.startswith("stream-triad:") for error in errors)


@pytest.mark.unit
def test_only_burst_and_image_build_run_one_trial_at_a_time() -> None:
    documents = {
        document["id"]: document for document in _documents() if "independence_answers" in document
    }
    serial = {
        identity
        for identity, document in documents.items()
        if document["procedure"]["concurrency"]["trials_in_parallel"] == 1
    }
    assert serial == {"burst-tti", "image-build-2vcpu-4gib"}
    for identity in serial:
        assert (
            documents[identity]["independence_answers"]["equal_concurrency"]["status"]
            == "exception"
        )
