# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Reader text is true of the version it describes, and the gates that make it so work."""

from pathlib import Path
from typing import Any

import pytest

from sandbox_benchmark_verifier.reader import reader_errors
from sandbox_benchmark_verifier.reader_claims import reader_claim_errors
from sandbox_benchmark_verifier.reader_when import gate_errors, gated, ungated

pytestmark = pytest.mark.unit
PARALLEL = "measured.procedure.concurrency.trials_in_parallel"


def _bundle(text: str, *, parallel: int = 1, variants: int = 1, **facts: Any) -> dict[str, Any]:
    definition = {
        "procedure": {
            "concurrency": {"trials_in_parallel": parallel},
            "variants": [{"id": str(i)} for i in range(variants)],
        },
        **facts,
    }
    return {
        "reader": {
            "methods": {
                "demo": {"document": "demo", "text": {"notes": {"x": text}}, "by_version": {}}
            }
        },
        "methodologies": {"demo": {"1.0.0": {"reader_definition": definition}}},
    }


def test_the_published_reader_text_is_true_of_every_version(bundle: dict[str, Any]) -> None:
    assert reader_claim_errors(bundle) == []


@pytest.mark.parametrize(
    ("text", "fixed"),
    [
        ("Trials run in parallel per provider, up to 1 at once.", {}),
        ("Each runs up to 5 in parallel.", {"parallel": 10}),
        ("A provider is ranked once it has at least 90%: unknown across all units.", {}),
        ("All ten traces come from Claude Opus 5 (high).", {"variants": 5}),
    ],
)
def test_a_claim_its_version_contradicts_fails(text: str, fixed: dict[str, int]) -> None:
    assert reader_claim_errors(_bundle(text, **fixed))


def test_a_fact_that_draws_unknown_fails_and_a_true_claim_passes() -> None:
    assert reader_claim_errors(_bundle("Fine.", procedure_x="unknown"))
    assert (
        reader_claim_errors(_bundle("Trials run in parallel, up to 10 at once.", parallel=10)) == []
    )


@pytest.mark.parametrize(
    "text",
    [
        "A tail requires at least 20 scheduled trials for the unit.",
        "With 20 trials, the p95 is the second slowest trial.",
        "An image scheduled for too few trials for a p95 shows its Slowest trial.",
        "A provider scheduled for 20 trials that lost trials to failures retains its p95.",
    ],
)
def test_a_tail_floor_counts_successes_instead_of_scheduled_trials(text: str) -> None:
    facts: dict[str, Any] = {"statistics": {"tail": {"min_successes": 20}}}
    assert reader_claim_errors(_bundle(text, **facts)) == [
        "demo@1.0.0: notes.x bases a tail on trials without requiring successes; "
        "statistics.tail.min_successes counts successes"
    ]
    assert (
        reader_claim_errors(
            _bundle("A tail requires at least 20 successful trials for the unit.", **facts)
        )
        == []
    )


MEASURED = {
    "procedure": {"concurrency": {"trials_in_parallel": 1}, "variants": [1, 2]},
    "state": "unknown",
}


def test_a_gate_keeps_its_sentence_only_where_the_comparison_holds() -> None:
    text = f"Before. [[when {PARALLEL} > 1: In parallel.]] After."
    assert gated(text, MEASURED) == "Before. After."
    many = {"procedure": {"concurrency": {"trials_in_parallel": 10}}}
    assert gated(text, many) == "Before. In parallel. After."
    assert ungated(text) == "Before. In parallel. After."


def test_a_gate_reads_lengths_words_and_drops_empty_items() -> None:
    assert gated("A[[when measured.procedure.variants# == 2:  two]] B", MEASURED) == "A two B"
    assert gated("A[[when measured.state != unknown:  known]] B", MEASURED) == "A B"
    assert gated(["x", f"[[when {PARALLEL} > 1: y]]"], MEASURED) == ["x"]
    assert gated({"a": f"[[when {PARALLEL} > 1: y]]", "b": "z"}, MEASURED) == {"b": "z"}


def test_a_path_the_definition_lacks_is_false_but_an_error_for_the_newest_version() -> None:
    text = "[[when measured.procedure.nothing > 1: y]]"
    assert gated(f"A {text}", MEASURED) == "A"
    with pytest.raises(ValueError, match="no procedure.nothing"):
        gated(text, MEASURED, strict=True)


def test_a_misspelt_gate_is_refused() -> None:
    assert gate_errors(f"[[when {PARALLEL} => 1: y]]")
    assert gate_errors("[[when procedure.trials > 1: y]]")
    assert gate_errors(f"[[when {PARALLEL} > 1: y]]") == []


def test_the_reader_source_has_no_malformed_gate() -> None:
    assert reader_errors(Path(__file__).resolve().parents[1]) == []
