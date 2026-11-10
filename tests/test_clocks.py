# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""The network-latency flag is derived from each time metric's formula, never typed by hand."""

from pathlib import Path
from typing import Any

import pytest

from sandbox_benchmark_verifier.catalog import default_context, load_methodology, ordinary_documents
from sandbox_benchmark_verifier.clocks import (
    derived_term_kinds,
    formula_terms,
    includes_network_latency,
)

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]

# Today's derived set, pinned so that a change to it shows in review. A benchmark whose time
# metrics read only the sandbox's clock, or that reports rates from its own output, is absent.
INCLUDES_NETWORK_LATENCY = {
    "burst-tti",
    "delayed-resume",
    "exec-latency",
    "exec-latency-2vcpu-8gib",
    "exec-latency-per-100-calls",
    "fork",
    "image-build-2vcpu-4gib",
    "list",
    "list-2vcpu-8gib",
    "pause-resume",
    "previews",
    "r2e-gym",
    "read-4kib",
    "read-4kib-2vcpu-8gib",
    "reconnect",
    "sequential-tti",
    "snapshot-and-restore",
    "write-4kib",
    "write-4kib-2vcpu-8gib",
}


@pytest.mark.parametrize(
    ("formula", "expected"),
    [
        ("median(total(true))", True),
        ("end(first-true) - start(create)", True),
        ("median(latency(true))", True),
        ("max(end(ready)) - min(start(create))", True),
        ("end(data-back) - start(resume|data-back)", True),
        ("sum(runtime(cmd))", False),
        ("median(runtime(true))", False),
        ("runtime(total)", False),
        ("median(total)", True),
        ("sum(end) - min(start)", True),
        ("median( total )", True),
        ("sum(runtime)", False),
    ],
)
def test_job_host_terms_include_network_latency_and_runtime_does_not(
    formula: str, expected: bool
) -> None:
    document = {"metrics": [{"unit": "ms", "formula": formula}]}
    assert includes_network_latency(document) is expected


def test_a_step_named_like_a_term_is_not_read_as_one() -> None:
    assert formula_terms("runtime(total) + sum(runtime(end))") == {"runtime", "sum"}


def test_a_derived_pipeline_time_with_job_host_calls_includes_network_latency() -> None:
    """r2e-gym's own metrics are sandbox runtimes, but the pipeline time its charts rank adds the
    create, snapshot, restore and destroy calls, which the job host times."""
    documents = [row.document for row in load_methodology(ROOT)]
    document = ordinary_documents(documents, common=default_context(ROOT))["r2e-gym"]
    assert all(
        "runtime(" in m["formula"] and "total(" not in m["formula"] for m in document["metrics"]
    )
    assert "request-elapsed" in derived_term_kinds(document)
    assert includes_network_latency(document) is True


def test_rates_and_counts_never_include_network_latency() -> None:
    document = {"metrics": [{"unit": "MIPS"}, {"unit": "count", "formula": "total(x)"}]}
    assert includes_network_latency(document) is False


def test_the_derived_set_for_todays_benchmarks_is_pinned() -> None:
    documents = [row.document for row in load_methodology(ROOT)]
    current = ordinary_documents(documents, common=default_context(ROOT))
    derived = {
        identifier
        for identifier, document in current.items()
        if document.get("procedure") and includes_network_latency(document)
    }
    assert derived == INCLUDES_NETWORK_LATENCY


def test_the_text_bundle_carries_each_flag_and_the_region_exceptions(
    bundle: dict[str, Any],
) -> None:
    """Web reads both from the bundle: which tests include network latency, and which providers
    run outside the job host's US region."""
    for identifier, reference in bundle["current_documents"].items():
        entry = bundle["methodologies"][identifier][reference["version"]]
        assert entry["includes_network_latency"] is (identifier in INCLUDES_NETWORK_LATENCY)
    assert [entry["provider"] for entry in bundle["region_exceptions"]] == ["boat"]
