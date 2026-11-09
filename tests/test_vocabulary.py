# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""A retired word reaches no reader: the scan covers every source a consumer draws from."""

import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from sandbox_benchmark_verifier.conventions import load_conventions
from sandbox_benchmark_verifier.names import load_tables
from sandbox_benchmark_verifier.versions import version_key
from sandbox_benchmark_verifier.vocabulary import retired_word_errors, retired_words

ROOT = Path(__file__).resolve().parents[1]
WORD = "typical"


def _errors(bundle: dict[str, Any]) -> list[str]:
    aliases = load_tables(ROOT).aliases["document"]
    return retired_word_errors(bundle, load_conventions(ROOT), aliases)


def _newest(versions: dict[str, Any]) -> str:
    return max(versions, key=version_key)


def test_the_vocabulary_rule_allows_the_retired_word_in_no_context() -> None:
    assert retired_words(load_conventions(ROOT)) == [WORD]


def test_no_source_a_consumer_draws_holds_the_retired_word(bundle: dict[str, Any]) -> None:
    assert _errors(bundle) == []


def test_the_scan_covers_contract_text_reader_copy_and_current_definitions(
    bundle: dict[str, Any],
) -> None:
    """Each place the sweep found the word (2026-11-01) is read by the scan."""
    broken = deepcopy(bundle)
    broken["content_criteria"]["index_copy"]["descriptions"]["time"] = "A Typical time."
    broken["content_criteria"]["methodology_page"]["metric_names"]["typical_cost"] = "Typical Cost"
    broken["reader"]["methods"]["common"]["text"]["notes"]["statistics_rules"] = "labeled typical"
    newest = _newest(broken["methodologies"]["time-index"])
    row = broken["methodologies"]["time-index"][newest]
    row["reader_definition"]["metrics"][0]["description"] = "each workload's typical time"
    row["text"]["notes"]["summary"] = "typical trace or job"
    found = "\n".join(_errors(broken))
    for path in (
        "content_criteria.index_copy.descriptions.time",
        "content_criteria.methodology_page.metric_names.typical_cost",
        "reader.methods.common.text.notes.statistics_rules",
        f"time-index@{newest}.reader_definition.metrics[0].description",
        f"time-index@{newest}.text.notes.summary",
    ):
        assert re.search(rf"{re.escape(path)}: ", found), path
