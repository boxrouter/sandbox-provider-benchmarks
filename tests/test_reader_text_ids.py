# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Reader prose names a direction or a statistic in words; no id reaches a reader."""

from __future__ import annotations

from typing import Any

import pytest

from sandbox_benchmark_verifier.text_ids import index_metric_id_warnings, raw_token_warnings

pytestmark = pytest.mark.unit


def _how_to_read(bundle: dict[str, Any], identifier: str, version: str) -> str:
    text: str = bundle["methodologies"][identifier][version]["reader_definition"]["notes"][
        "how_to_read"
    ]
    return text


def test_a_direction_placeholder_reads_as_the_owner_words(bundle: dict[str, Any]) -> None:
    assert _how_to_read(bundle, "deepswe", "1.1.22").endswith("in the axis unit; lower is better.")
    assert _how_to_read(bundle, "sequential-read", "1.1.18").endswith(
        "in the axis unit; higher is better."
    )


def test_a_statistic_placeholder_reads_as_its_name(bundle: dict[str, Any]) -> None:
    assert _how_to_read(bundle, "deepswe", "1.1.22").startswith("The median summarizes")


def test_the_release_text_shows_no_raw_id(bundle: dict[str, Any]) -> None:
    assert raw_token_warnings(bundle) == []


def test_a_raw_direction_id_or_snake_case_token_in_reader_prose_is_refused() -> None:
    leaked = {
        "methodologies": {
            "x": {
                "1.0.0": {
                    "reader_definition": {
                        "notes": {"how_to_read": "Read it; lower_is_better, `kept_in_code`."},
                        "metrics": [{"name": "tool_time"}],
                    }
                }
            }
        }
    }
    assert raw_token_warnings(leaked) == [
        "raw id in reader text x@1.0.0.reader_definition.notes.how_to_read: lower_is_better"
    ]


def test_a_composite_metric_id_never_reaches_the_page_even_as_code(bundle: dict[str, Any]) -> None:
    """The Composite About text printed `typical_time` and `typical_cost` as code (2026-11-01)."""
    assert index_metric_id_warnings(bundle) == []
    leaked = {
        "content_criteria": {
            "index_copy": {"details": {"time": "A figure (`typical_time` in ms)."}}
        },
        "reader": {},
        "methodologies": {
            "time-index": {
                "1.0.0": {
                    "reader_definition": {
                        "category": "index",
                        "metrics": [{"name": "typical_time"}],
                    }
                }
            },
            "x": {
                "1.0.0": {
                    "reader_definition": {
                        "category": "benchmark",
                        "notes": {
                            "how_to_read": "Recorded as `typical_time`, not as `burst_time`."
                        },
                        "metrics": [{"name": "burst_time"}],
                    }
                }
            },
        },
    }
    assert index_metric_id_warnings(leaked) == [
        "index metric id in reader text content.index_copy.details.time: typical_time",
        "index metric id in reader text x@1.0.0.reader_definition.notes.how_to_read: typical_time",
    ]
