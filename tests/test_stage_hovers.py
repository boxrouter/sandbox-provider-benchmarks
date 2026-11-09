# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Stage hovers are Start, End and Source lines from the reader copy, one set for every stage."""

from __future__ import annotations

import importlib
import re
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from pytest_mock import MockerFixture

from sandbox_benchmark_verifier import stage_hovers as module
from sandbox_benchmark_verifier import text
from sandbox_benchmark_verifier.catalog import _assets, load_methodology
from sandbox_benchmark_verifier.stage_hovers import stage_hover_errors, stage_hovers
from sandbox_benchmark_verifier.versions import newest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
names_module = importlib.import_module("sandbox_benchmark_verifier.names")


def _document(identifier: str, version: str) -> dict[str, Any]:
    return next(
        row.document
        for row in load_methodology()
        if row.document["id"] == identifier and str(row.document["version"]) == version
    )


def _latest(identifier: str) -> dict[str, Any]:
    return newest(row.document for row in load_methodology())[identifier]


def test_the_text_compiler_builds_the_name_tables_once_per_root(mocker: MockerFixture) -> None:
    """Not once per document: the panel titles (#530) and the stage hovers (#645) once did."""
    documents = [row.document for row in load_methodology()]
    spy = mocker.spy(names_module, "_load_tables")
    text._methodologies(ROOT)
    assert max(Counter(call.args[0] for call in spy.call_args_list).values()) <= 1
    spy.reset_mock()
    for document in documents:
        stage_hovers(document)
    assert spy.call_count <= 1


def test_the_approved_examples_read_exactly() -> None:
    sequential = stage_hovers(_latest("sequential-tti"))
    assert sequential["create"] == {
        "start": "Create request is sent",
        "end": "Create call returns",
        "clock": "runner",
    }
    assert sequential["first-command"]["start_after"] == (
        "Create returns and the `{command}` request is sent"
    )
    assert sequential["first-command"]["end"] == "`{command}` returns its exit code"
    assert stage_hovers(_latest("pause-resume"))["resume"]["start_after"] == (
        "Pause call returns and the resume request is sent"
    )
    assert stage_hovers(_latest("snapshot-and-restore"))["load-runtimes"]["end"] == (
        "Command returns its result"
    )
    tool_calls = stage_hovers(_latest("r2e-gym"))["tool-calls"]
    assert (tool_calls["start"], tool_calls["end"], tool_calls["clock"]) == (
        "Exec each tool call",
        "Call exits",
        "sandbox",
    )
    build = stage_hovers(_latest("deepswe"))["build"]
    assert (build["start"], build["end"], build["clock"]) == (
        "Exec each build command",
        "Command completes",
        "sandbox",
    )
    burst = stage_hovers(_latest("burst-tti"))["first-command"]
    assert burst["end"] == ("First successful `true` return; retried if sandbox isn't ready")


def _newest_versions() -> list[dict[str, Any]]:
    """The newest version of each document: the reader copy describes it, while an older version
    is an immutable record that may declare other stages or time a step on another clock."""
    return list(newest(row.document for row in load_methodology()).values())


def test_the_owner_documents_pass_the_stage_checks() -> None:
    errors = [
        error
        for document in _newest_versions()
        for error in stage_hover_errors(document, limit=140)
    ]
    assert errors == []


def _with_reader(monkeypatch: pytest.MonkeyPatch, rows: dict[str, dict[str, Any]]) -> None:
    monkeypatch.setattr(module, "_reader_stages", lambda _root: {"x": rows})


def _document_x(*stages: str, timer: dict[str, str] | None = None) -> dict[str, Any]:
    steps = [
        {"id": name, "record_steps": [name], **({"timer": timer} if timer else {})}
        for name in stages
    ]
    return {
        "id": "x",
        "procedure": {
            "steps": steps,
            "stages": [{"id": name, "name": name, "record_steps": [name]} for name in stages],
        },
    }


def _row(**fields: Any) -> dict[str, Any]:
    return {"start": "Request is sent", "end": "Call returns", "clock": "runner", **fields}


def test_a_stage_the_reader_copy_leaves_out_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_reader(monkeypatch, {"a": _row()})
    errors = stage_hover_errors(_document_x("a", "b"), limit=140)
    assert any("stage b has no hover lines" in error for error in errors)


def test_the_first_stage_has_no_start_context(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_reader(monkeypatch, {"a": _row(start_after="Before returns and the request is sent")})
    assert any(
        "first in its bar" in error for error in stage_hover_errors(_document_x("a"), limit=140)
    )


@pytest.mark.parametrize(
    "line",
    ["Time from the job host", "Total summed over calls", "That call returns", "recorded call"],
)
def test_words_a_stage_line_never_carries_are_refused(
    monkeypatch: pytest.MonkeyPatch, line: str
) -> None:
    _with_reader(monkeypatch, {"a": _row(end=line)})
    assert stage_hover_errors(_document_x("a"), limit=140)


def test_a_line_starts_with_a_capital_and_fits(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_reader(monkeypatch, {"a": _row(start="request is sent", end="Call " + "returns " * 30)})
    errors = stage_hover_errors(_document_x("a"), limit=140)
    assert any("start starts with a capital" in error for error in errors)
    assert any("over 140" in error for error in errors)


def test_timed_agrees_with_the_timer_of_the_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    inside = {"start_event": "The step starts, by the sandbox's own clock."}
    _with_reader(monkeypatch, {"a": _row(clock="runner")})
    assert any(
        "sandbox clock, not runner" in e
        for e in stage_hover_errors(_document_x("a", timer=inside), limit=140)
    )
    _with_reader(monkeypatch, {"a": _row(clock="sandbox")})
    assert stage_hover_errors(_document_x("a", timer=inside), limit=140) == []
    outside = {"start_event": "The request is dispatched."}
    assert any(
        "runner clock, not sandbox" in e
        for e in stage_hover_errors(_document_x("a", timer=outside), limit=140)
    )


def test_a_command_placeholder_needs_a_variant_command_and_nothing_else_is_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _with_reader(monkeypatch, {"a": _row(end="`{command}` returns its exit code")})
    assert any(
        "names a command no variant has" in e
        for e in stage_hover_errors(_document_x("a"), limit=140)
    )
    with_command = _document_x("a")
    with_command["procedure"]["variants"] = [{"id": "v", "command": "true"}]
    assert stage_hover_errors(with_command, limit=140) == []
    _with_reader(monkeypatch, {"a": _row(end="Call returns {sandbox}")})
    assert any("holds {sandbox}" in e for e in stage_hover_errors(_document_x("a"), limit=140))


def test_a_measure_hover_names_the_clock_of_its_boundaries_as_its_source() -> None:

    assert stage_hovers(_latest("github-actions"))["boundary"] == {"clock": "sandbox"}
    assert stage_hovers(_latest("pause-resume"))["boundary"] == {"clock": "runner"}
    assert stage_hovers(_latest("r2e-gym"))["boundary"] == {
        "clock": "sandbox"
    }  # two clocks; boundary_clock picks
    assert "boundary" not in stage_hovers(_latest("time-index"))  # an index has no boundaries


def test_a_boundary_clock_is_one_of_the_methods_names_clocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = _latest("r2e-gym")
    assert module.boundary_source_errors(document) == []
    entry = module._reader_methods(_assets())["r2e-gym"]
    monkeypatch.setitem(entry, "boundary_clock", None)
    assert any("several clocks" in e for e in module.boundary_source_errors(document))
    monkeypatch.setitem(entry, "boundary_clock", "moon")
    assert any("not a names clock" in e for e in module.boundary_source_errors(document))


def _current_documents() -> dict[str, dict[str, Any]]:
    """The newest version of each current document; the dotted 1.0 ids are the retired series."""
    return newest(
        row.document
        for row in load_methodology()
        if "." not in row.document["id"] and row.document.get("timing_boundaries")
    )


CLOCK_CLAUSE = re.compile(r"(?i)\bby\s+(?:the\s+|its\s+)?[\w' -]*?\bclock\b|\b\w+-clock\b")


def test_no_current_boundary_text_carries_a_clock_clause() -> None:

    documents = _current_documents()
    assert len(documents) > 25
    for identifier, document in documents.items():
        text = " ".join(document["timing_boundaries"].values())
        assert not CLOCK_CLAUSE.search(text), f"{identifier} {document['version']}: {text}"


def test_every_current_boundary_has_a_source_row() -> None:
    for identifier, document in _current_documents().items():
        assert module.BOUNDARY in stage_hovers(document), identifier


def test_the_boundary_row_is_not_a_stage_the_text_check_expects() -> None:
    document = _latest("github-actions")
    assert module.BOUNDARY in stage_hovers(document)
    warnings: list[str] = []
    text._stage_warnings(warnings, document, "github-actions")  # noqa: SLF001
    assert warnings == []
