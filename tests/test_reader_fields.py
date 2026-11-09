# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""The reader's Details fields, their slots, the shared block and Common's cost.

the Details dropdown shows Introduction, Setup, Trial steps and Limitations, each
from its own reader field; the method page opens with `opening`; a tab block is written once.
"""

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from sandbox_benchmark_verifier import reader
from sandbox_benchmark_verifier import text as text_module
from sandbox_benchmark_verifier.catalog import load_schema
from sandbox_benchmark_verifier.content import content_criteria
from sandbox_benchmark_verifier.methodology_page import reader_definition
from sandbox_benchmark_verifier.pinned_workload import pinned_workload
from sandbox_benchmark_verifier.reader_steps import split_trial_steps
from sandbox_benchmark_verifier.text import export_text_bundle, placeholder_leftovers
from sandbox_benchmark_verifier.versions import version_key

pytestmark = pytest.mark.unit


ROOT = Path(__file__).resolve().parents[1]
SENTENCE = "A trial runs one command."


def _notes(**fields: Any) -> dict[str, Any]:
    return {"methods": {"demo": {"text": {"notes": fields}}}}


def _errors(**fields: Any) -> list[str]:
    return reader.reader_slot_errors(_notes(**fields), content_criteria())


def _source() -> dict[str, Any]:
    loaded = reader.load_reader(ROOT)
    assert loaded is not None
    return copy.deepcopy(loaded)


def _valid(source: dict[str, Any]) -> bool:
    return Draft202012Validator(load_schema("reader")).is_valid(source)


def _clauses(method: str) -> list[tuple[list[str] | None, str]]:
    real = reader.load_reader(ROOT)["methods"][method]["notes"]["trial_steps"]  # type: ignore[index]
    return split_trial_steps(real)[1]


def _marked(method: str, text: str) -> str:
    """The method's real note with its first clause's wording replaced by `text`, so the
    placeholder under test is the only thing that varies and the markers stay valid."""
    marked = []
    for index, (ids, wording) in enumerate(_clauses(method)):
        marker = "[[untimed]]" if ids is None else f"[[steps: {', '.join(ids)}]]"
        marked.append(f"{marker}{text if index == 0 else wording}")
    return "".join(marked)


def test_trial_steps_is_bounded_to_400_characters_and_three_sentences() -> None:
    assert _errors(trial_steps=" ".join([SENTENCE] * 3)) == []
    assert _errors(trial_steps=" ".join([SENTENCE] * 4)) == [
        "demo.notes.trial_steps[0]: at most 3 sentences are allowed"
    ]
    assert any("length must be 8..400" in error for error in _errors(trial_steps="Short."))
    assert any("length must be 8..400" in error for error in _errors(trial_steps="A" * 401 + "."))


@pytest.mark.parametrize(
    ("field", "limit"),
    [
        ("workload", 4500),
        ("procedure", 4500),
        ("metrics", 3500),
        ("calculation", 3500),
        ("failure_policy", 3000),
        ("statistics_rules", 4500),
        ("independence", 4500),
    ],
)
def test_a_method_page_note_is_bounded_by_its_slot_and_may_run_several_paragraphs(
    field: str, limit: int
) -> None:
    paragraphs = f"{SENTENCE} {SENTENCE}\n\n{SENTENCE}"
    assert _errors(**{field: paragraphs}) == []
    assert any(
        f"length must be 8..{limit}" in error for error in _errors(**{field: "A" * (limit + 1)})
    )
    assert _valid_note(field)


def _valid_note(field: str) -> bool:
    source = _source()
    source["methods"]["sequential-read"]["notes"][field] = SENTENCE
    return _valid(source)


def test_apinned_workload_exposes_the_whole_parameter_file(tmp_path: Path) -> None:
    directory = tmp_path / "pinned"
    directory.mkdir()
    data = {"command": "./stress-ng --link 1 -t 30", "free_bytes": 100, "rows": {"a": {"bs": "1m"}}}
    (directory / "workloads.json").write_text(json.dumps([{"data": data}]), encoding="utf-8")
    measured = {"parameters": {"path": str(directory)}}
    pinned = pinned_workload(tmp_path, measured, {})
    assert pinned["free_bytes"] == 100
    assert pinned["rows"] == {"a": {"bs": "1m"}}
    assert pinned["options"] == {"link": "1"}


def _versions(tmp_path: Path, data_by_version: dict[str, dict[str, Any]]) -> dict[Any, Any]:
    """Documents `demo` at each version, each with a parameter file holding the given data."""
    documents = {}
    for version, data in data_by_version.items():
        directory = tmp_path / "parameters" / "demo" / version
        directory.mkdir(parents=True)
        workload = [{"data": data, "id": "w", "label": "L", "version": "x"}]
        (directory / "workloads.json").write_text(json.dumps(workload), encoding="utf-8")
        documents[("demo", version)] = {
            "id": "demo",
            "version": version,
            "parameters": {"path": f"parameters/demo/{version}/"},
        }
    return documents


def test_a_parameter_an_older_version_lacks_comes_from_the_oldest_later_version_with_it(
    tmp_path: Path,
) -> None:
    documents = _versions(
        tmp_path,
        {
            "1.0.0": {"free_bytes": 100, "job_file_sha256": "a"},
            "1.0.1": {"free_bytes": 100, "job_file_sha256": "a"},
            "1.0.2": {"free_bytes": 100, "job_file_sha256": "b", "file_bytes": 7},
            "1.0.3": {"free_bytes": 100, "job_file_sha256": "b", "file_bytes": 7, "extra": 1},
        },
    )
    old = pinned_workload(tmp_path, documents[("demo", "1.0.0")], documents)
    assert (old["file_bytes"], old["job_file_sha256"]) == (7, "a")
    assert "extra" in pinned_workload(tmp_path, documents[("demo", "1.0.1")], documents)
    assert pinned_workload(tmp_path, documents[("demo", "1.0.3")], documents)["extra"] == 1


def test_an_older_version_whose_own_parameter_differs_from_the_later_one_is_an_error(
    tmp_path: Path,
) -> None:
    documents = _versions(
        tmp_path,
        {"1.0.0": {"free_bytes": 100}, "1.0.1": {"free_bytes": 200, "file_bytes": 7}},
    )
    with pytest.raises(ValueError, match=r"demo@1\.0\.0 and demo@1\.0\.1 differ"):
        pinned_workload(tmp_path, documents[("demo", "1.0.0")], documents)


def test_a_later_version_that_differs_in_anything_but_added_keys_supplies_nothing(
    tmp_path: Path,
) -> None:
    documents = _versions(tmp_path, {"1.0.0": {"free_bytes": 100}, "1.0.1": {"file_bytes": 7}})
    with pytest.raises(ValueError, match="differ in more than added parameters"):
        pinned_workload(tmp_path, documents[("demo", "1.0.0")], documents)


def test_the_style_rules_read_the_pinned_workload_of_a_document(
    tmp_path: Path, declare_common: Callable[[Path], dict[str, str]]
) -> None:
    common = declare_common(tmp_path)
    directory = tmp_path / "parameters" / "demo" / "1.0.0"
    directory.mkdir(parents=True)
    data = {"rows": {"a": {"bs": "4k"}}}
    (directory / "workloads.json").write_text(json.dumps([{"data": data}]), encoding="utf-8")
    (tmp_path / "methodology").mkdir(exist_ok=True)
    (tmp_path / "methodology" / "demo.yaml").write_text(
        "id: demo\nversion: 1.0.0\nparameters:\n  path: parameters/demo/1.0.0/\n"
        f"inherits: {{id: {common['id']}, version: {common['version']}}}\n",
        encoding="utf-8",
    )
    documents = text_module._style_documents(tmp_path)  # pylint: disable=protected-access
    assert documents["demo"]["parameters"]["workload"]["rows"] == {"a": {"bs": "4k"}}


def test_limitations_items_are_one_sentence_of_at_most_240_characters() -> None:
    assert _errors(limitations=[SENTENCE, "A" * 239 + "."]) == []
    assert _errors(limitations=[SENTENCE, f"{SENTENCE} {SENTENCE}"]) == [
        "demo.notes.limitations[1]: at most 1 sentences are allowed"
    ]
    assert any("length must be 8..240" in error for error in _errors(limitations=["A" * 240 + "."]))


def test_limitations_hold_at_most_eight_items_and_details_shows_the_first_three() -> None:
    """Limitations list all caveats; Details shows the first 3."""
    assert _errors(limitations=[SENTENCE] * 8) == []
    assert _errors(limitations=[SENTENCE] * 9) == [
        "demo.notes.limitations: at most 8 items are allowed, got 9"
    ]
    slot = content_criteria()["slots"]["details.limitations"]
    assert slot["shown_items"] == 3
    assert slot["max_items"] == 8 and slot["shown_items"] == 3


def test_opening_is_bounded_to_1200_characters_and_six_sentences() -> None:
    assert _errors(opening=" ".join([SENTENCE] * 6)) == []
    assert _errors(opening=" ".join([SENTENCE] * 7)) == [
        "demo.notes.opening[0]: at most 6 sentences are allowed"
    ]
    assert any("length must be 8..1200" in error for error in _errors(opening="A" * 1200 + "."))
    assert content_criteria()["slots"]["method-opening"]["max_length"] == 1200


def test_the_schema_takes_the_new_fields_and_refuses_the_old_names() -> None:
    source = _source()
    source["methods"]["sequential-read"]["notes"].update(
        trial_steps=SENTENCE, limitations=[SENTENCE], opening=SENTENCE
    )
    assert _valid(source)
    for old in ("what_this_measures", "how_it_is_measured", "keep_in_mind"):
        refused = _source()
        refused["methods"]["sequential-read"]["notes"][old] = SENTENCE
        assert not _valid(refused)
    empty = _source()
    empty["methods"]["sequential-read"]["notes"]["limitations"] = []
    assert not _valid(empty)


def test_a_tab_block_is_written_once_and_a_test_field_reads_it(
    monkeypatch: pytest.MonkeyPatch, build_text_bundle: Callable[[Path], dict[str, Any]]
) -> None:
    source = _source()
    block = "The job host starts a fresh sandbox for each trial."
    source["common"]["blocks"]["create"]["fresh_sandbox"] = block
    for method in ("sequential-tti", "burst-tti"):
        source["methods"][method]["notes"]["trial_steps"] = _marked(
            method, "{param:reader.common.blocks.create.fresh_sandbox} The trial then stops."
        )
    monkeypatch.setattr(reader, "load_reader", lambda _root: source)
    assert reader.reader_errors(ROOT) == []
    methods = build_text_bundle(ROOT)["reader"]["methods"]
    for method in ("sequential-tti", "burst-tti"):
        assert methods[method]["text"]["notes"]["trial_steps"].startswith(
            f"{block} The trial then stops."
        )


def test_a_reference_to_a_missing_tab_block_fails_naming_the_method(
    monkeypatch: pytest.MonkeyPatch, build_text_bundle: Callable[[Path], dict[str, Any]]
) -> None:
    source = _source()
    source["methods"]["sequential-tti"]["notes"]["trial_steps"] = (
        "{param:reader.common.blocks.create.no_such_block}"
    )
    monkeypatch.setattr(reader, "load_reader", lambda _root: source)
    with pytest.raises(ValueError, match=r"sequential-tti@.*no_such_block"):
        build_text_bundle(ROOT)


def test_a_block_names_a_tab_a_section_or_common(monkeypatch: pytest.MonkeyPatch) -> None:
    source = _source()
    source["common"]["blocks"]["no-such-scope"] = {"step": SENTENCE}
    monkeypatch.setattr(reader, "load_reader", lambda _root: source)
    errors = reader.reader_errors(ROOT)
    assert any(
        "common.blocks.no-such-scope: names no tab, section or common" in error for error in errors
    )


def test_a_tab_block_reads_the_measured_definition_of_the_test_that_references_it(
    monkeypatch: pytest.MonkeyPatch, build_text_bundle: Callable[[Path], dict[str, Any]]
) -> None:
    source = _source()
    source["common"]["blocks"]["create"]["size"] = (
        "A {param:measured.environment.size.vcpu} vCPU sandbox starts."
    )
    for method in ("sequential-tti", "burst-tti"):
        source["methods"][method]["notes"]["trial_steps"] = _marked(
            method, "{param:reader.common.blocks.create.size}"
        )
    monkeypatch.setattr(reader, "load_reader", lambda _root: source)
    assert reader.reader_errors(ROOT) == []
    methods = build_text_bundle(ROOT)["reader"]["methods"]
    for method in ("sequential-tti", "burst-tti"):
        assert methods[method]["text"]["notes"]["trial_steps"].startswith(
            "A 2 vCPU sandbox starts."
        )


def test_a_note_reads_apinned_workload_row(
    monkeypatch: pytest.MonkeyPatch, build_text_bundle: Callable[[Path], dict[str, Any]]
) -> None:
    source = _source()
    row = "{param:measured.parameters.workload.rows.disk-seq-read-1m.bs}"
    source["methods"]["sequential-read"]["notes"]["trial_steps"] = _marked(
        "sequential-read", f"Reads blocks of {row}."
    )
    monkeypatch.setattr(reader, "load_reader", lambda _root: source)
    methods = build_text_bundle(ROOT)["reader"]["methods"]
    assert methods["sequential-read"]["text"]["notes"]["trial_steps"].startswith(
        "Reads blocks of 1m."
    )


def test_a_common_definition_without_independence_facts_draws_no_independence_note() -> None:
    common = max(
        (
            document
            for document in text_module._methodologies(ROOT)
            if document["id"] == "sbs.common"
        ),
        key=lambda document: version_key(str(document["version"])),
    )
    common = copy.deepcopy(common)
    common.pop("independence", None)
    definition = reader_definition(
        common,
        {("sbs.common", str(common["version"])): common},
        content_criteria()["methodology_page"],
    )
    notes = definition["notes"]
    assert "independence" not in notes
    assert "what_this_measures" in notes


def test_common_has_a_reader_entry_for_the_common_methodology(bundle: dict[str, Any]) -> None:
    common = bundle["reader"]["methods"]["common"]
    assert common["document"] == "sbs.common"
    assert common["text"]["notes"]["introduction"]
    assert common["unresolved_versions"] == []


def test_common_owns_cost_for_a_definition_that_states_none() -> None:
    policy = content_criteria()["methodology_page"]
    assert "cost" in policy["inherited_fields"]
    common = {
        "id": "sbs.common",
        "version": "1.0.10",
        "cost": {"method": "provider_reported_or_priced"},
    }
    child = {
        "id": "demo",
        "version": "1.0.0",
        "inherits": {"id": "sbs.common", "version": "1.0.10"},
    }
    documents = {("sbs.common", "1.0.10"): common, ("demo", "1.0.0"): child}
    definition = reader_definition(child, documents, policy)
    assert definition["cost"] == common["cost"]
    assert definition["reader_sources"]["cost"] == {"id": "sbs.common", "version": "1.0.10"}


def test_a_note_reads_counts_durations_and_products_of_its_own_definition(
    monkeypatch: pytest.MonkeyPatch, build_text_bundle: Callable[[Path], dict[str, Any]]
) -> None:
    source = _source()
    source["methods"]["sequential-read"]["notes"]["trial_steps"] = _marked(
        "sequential-read",
        "{product:measured.procedure.trials*measured.procedure.concurrency.trials_in_parallel} "
        "runs, {count:measured.metrics} metric, {duration:measured.environment.lifetime_seconds}.",
    )
    monkeypatch.setattr(reader, "load_reader", lambda _root: source)
    methods = build_text_bundle(ROOT)["reader"]["methods"]
    assert methods["sequential-read"]["text"]["notes"]["trial_steps"].startswith(
        "400 runs, 1 metric, 30 min."
    )


def test_every_measured_version_has_reader_text(bundle: dict[str, Any]) -> None:
    """A version the copy cannot describe throws when a page draws it; none may exist today.

    A pinned version's variants read its own `notes.*` (the locked definition's), which the reader's
    notes replace; those references must still resolve (sequential-tti@1.2.2 once did not).
    """
    unresolved = {
        identifier: method["unresolved_versions"]
        for identifier, method in bundle["reader"]["methods"].items()
        if method["unresolved_versions"]
    }
    assert unresolved == {}


def test_a_test_reads_only_the_blocks_of_its_tab_its_section_and_common(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source()
    blocks = source["common"]["blocks"]
    blocks["common"] = {"rule": SENTENCE}
    blocks["sandbox-operations"] = {"rule": SENTENCE}
    notes = source["methods"]["sequential-tti"]["notes"]
    for scope in ("common", "sandbox-operations", "create"):
        notes["trial_steps"] = f"{{param:reader.common.blocks.{scope}.rule}}"
        monkeypatch.setattr(reader, "load_reader", lambda _root: source)
        if scope == "create":
            blocks["create"]["rule"] = SENTENCE
        assert reader.reader_errors(ROOT) == []
    notes["trial_steps"] = "{param:reader.common.blocks.machine-performance.trial_start}"
    errors = reader.reader_errors(ROOT)
    assert any("sequential-tti.notes.trial_steps: reads block machine-perf" in e for e in errors)
    # A tab that shares its id with a test: the test still reads its own tab's blocks.
    source["common"]["blocks"]["previews"] = {"rule": SENTENCE}
    source["methods"]["previews"]["notes"]["trial_steps"] = (
        "{param:reader.common.blocks.previews.rule}"
    )
    notes["trial_steps"] = SENTENCE
    assert reader.reader_errors(ROOT) == []


def test_the_exported_bundle_holds_resolved_text_only(bundle: dict[str, Any]) -> None:
    """No `{kind:...}` reference reaches a consumer: raw blocks and authored forms stay home."""
    authored = bundle["content_criteria"]["hover_templates"]["legend_info_deep_swe_build"]
    assert "{param:" in authored["authored_template"]  # the checks read this form
    exported = export_text_bundle(bundle)
    assert placeholder_leftovers(exported) == []
    # Only the one block details.composite.sentence_source names; environment wording is bound
    # to measured definitions and stays out of the shared common.
    assert set(exported["reader"]["common"]) == {"blocks"}
    assert list(exported["reader"]["common"]["blocks"]) == ["common"]
    assert list(exported["reader"]["common"]["blocks"]["common"]) == ["all_view_aggregation"]
    floor = exported["content_criteria"]["hover_templates"]["legend_info_deep_swe_build"]
    assert floor["authored_template"] == floor["template"]


def test_a_reference_left_in_the_bundle_fails_the_export(bundle: dict[str, Any]) -> None:
    broken = copy.deepcopy(bundle)
    broken["methodologies"]["sequential-tti"]["1.2.18"]["text"]["notes"]["setup"] = (
        "Takes {count:measured.procedure.trials} trials."
    )
    with pytest.raises(ValueError, match=r"methodologies\.sequential-tti"):
        export_text_bundle(broken)
    assert placeholder_leftovers({"a": "A {command} stays for the page to fill."}) == []
