# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""A machine panel's title is generated from fields and names the standard test."""

import copy
from pathlib import Path
from typing import Any

import yaml

from sandbox_benchmark_verifier.conventions import load_conventions
from sandbox_benchmark_verifier.machine_panels import (
    _tabs,
    apply_panel_text,
    panel_errors,
    title_parts,
)
from sandbox_benchmark_verifier.names import _load_tables, compiler_tables_cache, load_tables
from sandbox_benchmark_verifier.panel_titles import title_errors
from sandbox_benchmark_verifier.text import DIGEST_ALGORITHMS
from sandbox_benchmark_verifier.text_predicates import check_program
from sandbox_benchmark_verifier.versions import newest

ROOT = Path(__file__).resolve().parents[1]


def _documents() -> list[dict[str, Any]]:
    return [
        document
        for path in sorted((ROOT / "methodology").rglob("*.yaml"))
        for document in yaml.safe_load_all(path.read_text(encoding="utf-8"))
        if isinstance(document, dict) and isinstance(document.get("id"), str)
    ]


def _specified_tests() -> set[str]:
    rows = load_tables(ROOT).names
    tabs = {row["id"] for row in rows if row.get("kind") == "tab" and "measure" in row}
    return {
        str(row["document"])
        for row in rows
        if row.get("kind") == "test" and row.get("parent") in tabs
    }


def _latest(documents: list[dict[str, Any]], identifier: str) -> dict[str, Any]:
    return newest(documents)[identifier]


def test_every_machine_test_has_a_generated_title_naming_its_standard(
    bundle: dict[str, Any],
) -> None:
    documents = _documents()
    assert panel_errors(ROOT, documents) == []
    tests = _specified_tests()
    assert tests >= {"7-zip-1-thread", "stream-triad", "sequential-read", "hard-links"}
    generated = apply_panel_text(ROOT, documents)
    for identifier in tests:
        document = _latest(generated, identifier)
        standard = document["source"]["name"]
        assert standard in document["panel"]["title"]
        entry = bundle["methodologies"][identifier][document["version"]]
        assert entry["text"]["panel"] == {
            "title": document["panel"]["title"],
            "command": document["panel"]["command"],
        }
        assert "summary" not in entry["text"]["notes"]  # the subtitle is the Introduction


def test_the_title_readers_leave_the_cached_tables_unchanged(tmp_path: Path) -> None:
    """``tables_view`` hands these callers the cache scope's own tables, not a copy."""
    with compiler_tables_cache(ROOT, DIGEST_ALGORITHMS, cache_directory=tmp_path):
        assert apply_panel_text(ROOT, []) == []
        assert panel_errors(ROOT, []) == []
        for identifier in _tabs(ROOT):
            parts = title_parts(ROOT, {"id": identifier, "source": {"name": "tool"}})
            if parts is not None:
                title_errors(ROOT, "", **parts)
        assert load_tables(ROOT) == _load_tables(ROOT)


def test_the_title_is_the_one_template_filled_with_the_owner_parts() -> None:
    generated = apply_panel_text(ROOT, _documents())
    titles = {
        identifier: _latest(generated, identifier)["panel"]["title"]
        for identifier in (
            "7-zip-1-thread",
            "7-zip-all-vcpus",
            "stream-triad",
            "sequential-read",
            "sequential-write",
            "random-read",
            "random-write",
            "hard-links",
        )
    }
    assert titles == {
        "7-zip-1-thread": "CPU Speed (7-Zip)",
        "7-zip-all-vcpus": "CPU Speed (7-Zip)",
        "stream-triad": "Memory Bandwidth (STREAM)",
        "sequential-read": "Disk Speed (fio) · Sequential Read",
        "sequential-write": "Disk Speed (fio) · Sequential Write",
        "random-read": "Disk Speed (fio) · Random Read",
        "random-write": "Disk Speed (fio) · Random Write",
        "hard-links": "Filesystem Hard-Link Speed (stress-ng)",
    }


def test_a_command_the_pinned_workload_does_not_run_is_an_error() -> None:
    documents = copy.deepcopy(_documents())
    _latest(documents, "sequential-read")["panel"]["command"] = "fio --rw=randread --bs=1m"
    assert any("not in the pinned workload" in error for error in panel_errors(ROOT, documents))


def test_a_machine_test_without_a_panel_block_is_an_error() -> None:
    documents = copy.deepcopy(_documents())
    del _latest(documents, "stream-triad")["panel"]
    assert any("no panel block" in error for error in panel_errors(ROOT, documents))


def test_a_sentence_may_start_with_a_tool_name_exactly_as_its_document_owns_it() -> None:
    conventions = load_conventions(ROOT)
    owned = {
        str(document["source"]["name"])
        for document in _documents()
        if isinstance(document.get("source"), dict)
        and str(document["source"].get("name", ""))[:1].islower()
        and len(str(document["source"]["name"]).split()) == 1
    }
    assert conventions["proper_names"] == sorted(owned)
    assert {"fio", "stress-ng"} <= owned

    def capital_start_findings(text: str) -> list[str]:
        found = check_program(
            {"slots": [{"slot": "description", "text": text}], "context": "panel"}, conventions
        )
        return [item["rule_id"] for item in found if item["rule_id"] == "reader.capital-start"]

    assert capital_start_findings("fio's `fio --rw=read` benchmarks read throughput.") == []
    assert capital_start_findings("stress-ng's `stress-ng --link 1` benchmarks links.") == []
    assert capital_start_findings("fiox benchmarks read throughput.") == ["reader.capital-start"]
    assert capital_start_findings("the disk benchmarks read throughput.") == [
        "reader.capital-start"
    ]
