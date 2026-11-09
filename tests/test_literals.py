# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
from sandbox_benchmark_verifier.literals import canonical_literal_terms


def test_literal_terms_include_canonical_names_metrics_and_parameters() -> None:
    terms = canonical_literal_terms()
    pairs = {(term["category"], term["value"]) for term in terms}

    assert ("name", "Composite Workload Time") in pairs
    assert any(category == "metric" and value == "Lower is better" for category, value in pairs)
    # A metric's quantity, per, unit and direction codes are ids a source names, not wording.
    for code in ("lower_is_better", "ms", "time_score"):
        assert ("id", code) in pairs and ("metric", code) not in pairs
    assert any(category == "size_or_count" and value == "4 GiB" for category, value in pairs)
    assert ("retired_word", "typical") in pairs
    assert all(term["source"] for term in terms)
