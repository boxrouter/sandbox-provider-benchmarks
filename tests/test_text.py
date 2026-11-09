# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Text-slot schema, enforcement, and release bundle tests."""

import importlib
import json
import sys
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator
from pytest_mock import MockerFixture

from sandbox_benchmark_verifier.cli import main
from sandbox_benchmark_verifier.methodology_page import omit_paths
from sandbox_benchmark_verifier.metric_labels import metric_id
from sandbox_benchmark_verifier.name_refs import projection_references, resolve_name_references
from sandbox_benchmark_verifier.names import load_tables, name_errors
from sandbox_benchmark_verifier.option_hovers import option_hovers
from sandbox_benchmark_verifier.panel_slots import panel_required_slots
from sandbox_benchmark_verifier.placeholders import PlaceholderError, resolve_template
from sandbox_benchmark_verifier.stage_hovers import stage_hovers
from sandbox_benchmark_verifier.text import (
    _methodologies,
    _warn_sentence,
    export_text_bundle,
    semantic_digest,
    text_warnings,
    write_text_bundle,
)
from sandbox_benchmark_verifier.versions import version_key

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
names_module = importlib.import_module("sandbox_benchmark_verifier.names")


def test_the_text_check_loads_the_tables_once(
    checkout: Path, tmp_path: Path, mocker: MockerFixture
) -> None:
    root = checkout
    spy = mocker.spy(names_module, "_load_tables")
    text_warnings(root)
    assert [call.args[0] for call in spy.call_args_list].count(root.resolve()) == 1


def test_old_methodology_documents_still_validate() -> None:
    schema = json.loads((ROOT / "schemas/methodology.schema.json").read_text())
    validator = Draft202012Validator(schema)
    old = {
        "id": "sbs.common",
        "version": "1.0.0",
        "effective": "2026-01-01",
        "name": "Common defaults",
        "category": "common",
        "description": "Shared benchmark defaults.",
    }
    assert validator.is_valid(old)


def test_all_additive_methodology_fields_validate() -> None:
    schema = json.loads((ROOT / "schemas/methodology.schema.json").read_text())
    validator = Draft202012Validator(schema)
    common = yaml.safe_load((ROOT / "methodology/00-common-1.0.18.yaml").read_text())
    common["notes"] = {
        "summary": "A short summary.",
        "how_it_is_measured": "Timed on a monotonic clock.",
        "what_this_measures": "The measured value.",
        "how_to_read": "Median time in milliseconds. Lower is better.",
        "cost_summary": "Cost per successful run.",
        "cost_how_to_read": "Cost in US dollars.",
        "statistics": {
            key: "Statistic tooltip."
            for key in ("median", "mean", "p95", "tail_fallback", "typical")
        },
        "stage_medians": "Stages show separate medians.",
        "time_between_stages": "Intervals can occur between stages.",
        "not_ranked": {
            key: "Reason tooltip."
            for key in (
                "not_offered",
                "in_development",
                "too_few_runs",
                "failing_often",
                "not_measured",
            )
        },
    }
    common["metrics"] = [
        {
            "name": "duration",
            "unit": "ms",
            "direction": "lower_is_better",
            "quantity": "time",
            "phase": {"kind": "stage", "id": "create"},
            "per": None,
            "statistic": "mean",
        }
    ]
    common["source"] = {"name": "synthetic", "version": "1", "venue_terms": ["Actions"]}
    assert validator.is_valid(common), list(validator.iter_errors(common))

    procedure_doc = next(
        yaml.safe_load_all((ROOT / "methodology/03-sandbox-operations/create.yaml").read_text())
    )
    procedure_doc["procedure"]["variants"][0]["description"] = "Option tooltip."
    procedure_doc["procedure"]["variants"][0]["notes"] = {
        "summary": "Variant summary.",
        "what_this_measures": "Variant measurement.",
        "how_it_is_measured": "Variant method.",
        "how_to_read": "Variant reading.",
    }
    assert validator.is_valid(procedure_doc), list(validator.iter_errors(procedure_doc))


def test_names_v2_accepts_intro_selector_hero_and_page_rows() -> None:
    schema = json.loads((ROOT / "schemas/names.schema.json").read_text())
    table: dict[str, Any] = {
        "schema": "boxrouter.bench.names/2",
        "names": [
            {
                "row": 1,
                "kind": "section",
                "id": "overview",
                "name": "Overview",
                "intro": "One sentence.",
            },
            {
                "row": 2,
                "kind": "test",
                "id": "demo",
                "name": "Demo",
                "selector": {"label": "Job", "mode": "chooses"},
            },
            {"row": 3, "kind": "hero", "id": "time-index", "name": "Time Index"},
            {
                "row": 4,
                "kind": "page",
                "id": "benchmarks",
                "name": "Sandbox Benchmarks",
                "summary": "One sentence.",
            },
        ],
    }
    assert Draft202012Validator(schema).is_valid(table)
    table["names"][1]["selector"]["mode"] = "other"
    assert not Draft202012Validator(schema).is_valid(table)


def test_report_only_text_check_reports_missing_slots_and_text_outside(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    declare_common: Callable[[Path], dict[str, str]],
) -> None:
    (tmp_path / "names").mkdir()
    (tmp_path / "methodology").mkdir()
    (tmp_path / "names/names.yaml").write_text(
        "schema: boxrouter.bench.names/2\nnames:\n"
        "  - {row: 1, kind: page, id: benchmarks, name: Sandbox Benchmarks}\n"
        "  - {row: 2, kind: section, id: workloads, name: Workloads}\n"
        "  - {row: 3, kind: test, id: demo, name: Demo, document: demo}\n"
    )
    (tmp_path / "names/aliases.yaml").write_text("schema: boxrouter.bench.aliases/1\n")
    common = declare_common(tmp_path)  # catalog selection's one explicit common (#755)
    (tmp_path / "methodology/demo.yaml").write_text(
        "id: demo\nversion: 1.0.0\neffective: '2026-01-01'\nname: '{name:test.demo}'\n"
        "category: common\ndescription: Legacy reader text.\n"
        f"inherits: {{id: {common['id']}, version: {common['version']}}}\n"
    )
    warnings = text_warnings(tmp_path)
    assert "missing or unfilled slot names row 1 benchmarks.summary" in warnings
    assert "missing or unfilled slot names row 2 workloads.intro" in warnings
    assert "missing or unfilled slot demo.notes.introduction" in warnings
    assert "text outside slots demo.description" in warnings

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sandbox_benchmark_verifier.cli.ROOT", tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        ["sandbox-benchmark-verify", "text", "--output", "text.json", "--text-mode", "report"],
    )
    assert main() == 0
    assert "report only" in capsys.readouterr().out
    assert json.loads((tmp_path / "text.json").read_text())["schema"] == "boxrouter.bench.text/3"
    monkeypatch.setattr(sys, "argv", ["sandbox-benchmark-verify", "text"])
    assert main() == 1
    assert "enforce" in capsys.readouterr().out


def test_text_enforcement_accepts_a_complete_panel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    declare_common: Callable[[Path], dict[str, str]],
) -> None:
    (tmp_path / "names").mkdir()
    (tmp_path / "methodology").mkdir()
    (tmp_path / "names/names.yaml").write_text(
        "schema: boxrouter.bench.names/2\nnames:\n"
        "  - {row: 1, kind: test, id: demo, name: Demo, document: demo}\n"
    )
    (tmp_path / "names/aliases.yaml").write_text("schema: boxrouter.bench.aliases/1\n")
    (tmp_path / "reader").mkdir()
    (tmp_path / "reader/methods.yaml").write_text(
        "schema: boxrouter.bench.reader/1\nmethods:\n  demo:\n    notes:\n"
        "      introduction: Times the call, including request and response handling.\n"
        "      setup: A monotonic clock times the call. The workload is fixed.\n"
        "      how_to_read: 'The median summarizes successful trials in the axis unit; "
        "{direction:duration}.'\n"
    )
    common = declare_common(tmp_path)  # catalog selection's one explicit common (#755)
    (tmp_path / "methodology/demo.yaml").write_text(
        "id: demo\nversion: 1.0.0\neffective: '2026-01-01'\nname: '{name:test.demo}'\n"
        f"inherits: {{id: {common['id']}, version: {common['version']}}}\n"
        "category: sandbox_operation\nnotes:\n"
        "  summary: The workload measures call completion.\n"
        "  what_this_measures: The result includes request and response handling.\n"
        "  how_it_is_measured: A monotonic clock times the call. The workload is fixed.\n"
        "  how_to_read: The median summarizes successful trials in the axis unit; "
        "{direction:duration}.\n"
        "metrics:\n"
        "  - {name: duration, unit: ms, direction: lower_is_better, quantity: time, per: call}\n"
        "interpretation:\n  limits: [One call is measured.]\n"
        "environment: {network: unknown}\n"
        "procedure: {steps: [{id: call, action: exec}]}\n"
        "statistics: {primary: median}\n"
        "validity_checks: [{id: response, threat: A response could be missing., "
        "check: The response is required.}]\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sandbox_benchmark_verifier.cli.ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["sandbox-benchmark-verify", "text", "--text-mode", "enforce"])
    assert main() == 0


def test_panel_kind_requirements_name_no_retired_summary_slot() -> None:
    required = panel_required_slots()
    assert required
    assert all("notes.summary" not in slots for slots in required.values())


def test_current_reader_references_pass_the_default_enforcing_check() -> None:
    assert text_warnings(ROOT) == []


def test_semantic_digest_excludes_text_but_tracks_method_changes() -> None:
    document: dict[str, Any] = {
        "id": "demo",
        "version": "1.0.0",
        "effective": "2026-01-01",
        "notes": {"summary": "First."},
        "metrics": [{"quantity": "time", "per": "call", "unit": "ms"}],
        "parameters": {"path": "parameters/demo/1.0.0/", "sha256": "same"},
    }
    original = semantic_digest(document)
    document["name"] = "{name:time-index}"
    document["notes"]["summary"] = "Second."
    document["metrics"][0]["quantity"] = "duration"
    assert semantic_digest(document) == original
    document["version"] = "1.0.1"
    document["effective"] = "2026-02-01"
    document["description"] = "A new display sentence."
    document["parameters"]["path"] = "parameters/demo/1.0.1/"
    assert semantic_digest(document) == original
    document["metrics"][0]["unit"] = "s"
    assert semantic_digest(document) != original


def test_text_bundle_rejects_unknown_index_name_reference(
    checkout: Path,
    tmp_path: Path,
    build_text_bundle: Callable[[Path], dict[str, Any]],
) -> None:
    root = checkout
    path = root / "methodology/01-indexes/speed.yaml"
    documents = list(yaml.safe_load_all(path.read_text()))
    latest = documents[-1]
    latest["name"] = "{name:missing-index}"
    path.write_text("\n---\n".join(yaml.safe_dump(item, sort_keys=False) for item in documents))
    with pytest.raises(PlaceholderError, match="unknown canonical name"):
        build_text_bundle(root)


def test_text_bundle_contains_all_slot_sources_and_is_stable(
    tmp_path: Path,
    build_text_bundle: Callable[[Path], dict[str, Any]],
) -> None:
    bundle = build_text_bundle(ROOT)
    assert bundle["schema"] == "boxrouter.bench.text/3"
    assert bundle["names_schema"] == "boxrouter.bench.names/2"
    methods = bundle["methodologies"]
    for index_id in ("time-index", "cost-index", "time-vs-cost"):
        newest = max(methods[index_id], key=version_key)
        url = methods[index_id][newest]["owner"]["public_method_url"]
        assert url == f"/benchmarks/methodology/{index_id}"
    source_methods = _methodologies(ROOT)
    tables = load_tables(ROOT)
    canonical_names = {
        str(row["id"]): str(row["name"]) for row in bundle["names"] if row.get("kind") == "index"
    }
    canonical_names.update(
        {
            f"{row['kind']}.{row['id']}": str(row["name"])
            for row in bundle["names"]
            if row.get("name")
        }
    )
    source_documents = {f"{item['id']}.{item['version']}": item for item in source_methods}
    directions = tables.metric_label_grammar.get("tokens", {}).get("directions", {})

    def resolve_expected(value: Any) -> Any:
        if isinstance(value, str):
            return resolve_template(
                value,
                names=canonical_names,
                documents=source_documents,
                directions=directions,
            )
        if isinstance(value, dict):
            return {key: resolve_expected(item) for key, item in value.items()}
        if isinstance(value, list):
            return [resolve_expected(item) for item in value]
        return value

    assert {
        (identifier, version) for identifier, versions in methods.items() for version in versions
    } == {(str(item["id"]), str(item["version"])) for item in source_methods}
    method_by_ref = {(str(item["id"]), str(item["version"])): item for item in source_methods}
    for document in source_methods:
        if "{param:" in json.dumps(document):
            continue  # Version-bound slots are checked against their exact sources below.
        expected: dict[str, Any] = {}
        if isinstance(document.get("name"), str) and "{name:" in document["name"]:
            name_id = document["name"].removeprefix("{name:").removesuffix("}")
            kind, _, identifier = (
                name_id.partition(".") if "." in name_id else ("index", "", name_id)
            )
            expected["name"] = next(
                row["name"]
                for row in bundle["names"]
                if row["kind"] == kind and row["id"] == identifier
            )
        if "headline" in document:
            expected["headline"] = document["headline"]
        if isinstance(document.get("notes"), dict):
            expected["notes"] = deepcopy(document["notes"])
            # The reader no longer shows the retired statistic tooltip; the record keeps it.
            omit_paths(
                expected, bundle["content_criteria"]["methodology_page"]["retired_reader_paths"]
            )
        if isinstance(document.get("metrics"), list):
            expected["metrics"] = [
                {
                    "id": metric_id(
                        tables.aliases.get("document", {}).get(document["id"], document["id"]),
                        document["version"],
                        metric["name"],
                    ),
                    "name": metric.get("name"),
                    "quantity": metric.get("quantity"),
                    "phase": metric.get("phase"),
                    "per": metric.get("per"),
                    "unit": metric.get("unit"),
                    "direction": metric.get("direction"),
                    "statistic": metric.get("statistic")
                    or document.get("statistics", {}).get("primary")
                    or method_by_ref.get(
                        (
                            str(document.get("inherits", {}).get("id", "")),
                            str(document.get("inherits", {}).get("version", "")),
                        ),
                        {},
                    )
                    .get("statistics", {})
                    .get("primary"),
                }
                for metric in document["metrics"]
                if isinstance(metric, dict)
            ]
        procedure = document.get("procedure")
        if isinstance(procedure, dict):
            for kind in ("variants", "stages"):
                if isinstance(procedure.get(kind), list):
                    expected[kind] = [
                        {
                            **({"id": item["id"]} if "id" in item else {}),
                            **{
                                key: item[key]
                                for key in ("description", "notes", "command")
                                if key in item
                            },
                        }
                        for item in procedure[kind]
                        if isinstance(item, dict)
                    ]
        if stages := stage_hovers(document):
            expected["stage_hovers"] = stages
        if options := option_hovers(document):
            expected["option_hovers"] = options
        source = document.get("source")
        if isinstance(source, dict) and "venue_terms" in source:
            expected["venue_terms"] = source["venue_terms"]
        expected = resolve_name_references(
            resolve_expected(expected),
            names=canonical_names,
            documents=source_documents,
            references=projection_references(ROOT),
        )
        assert methods[str(document["id"])][str(document["version"])]["text"] == expected
    common = methods["sbs.common"][max(methods["sbs.common"], key=version_key)]
    assert "semantic_digest" in common
    assert common["text"]
    assert all(
        versioned["semantic_digest"] and "text" in versioned
        for versions in methods.values()
        for versioned in versions.values()
    )
    output = tmp_path / "dist/text.json"
    write_text_bundle(ROOT, output)
    assert json.loads(output.read_text()) == export_text_bundle(bundle)


def test_the_published_text_holds_the_composite_sentence_source(tmp_path: Path) -> None:
    """The file consumers pin carries the path details.composite.sentence_source names."""
    output = tmp_path / "text.json"
    write_text_bundle(ROOT, output)
    published = json.loads(output.read_text())
    path = published["content_criteria"]["conventions"]["details"]["composite"]["sentence_source"]
    node: Any = published
    for key in path.split("."):
        node = node[key]
    assert isinstance(node, str)
    assert node.startswith("On the All view")


def test_metric_text_fixture_roundtrips_with_no_semantic_digest_drift(
    checkout: Path,
    tmp_path: Path,
    build_text_bundle: Callable[[Path], dict[str, Any]],
) -> None:
    root = checkout
    fixture = ROOT / "tests/fixtures/text-bundle"
    method_path = root / "methodology/03-sandbox-operations/create.yaml"
    method_documents = list(yaml.safe_load_all(method_path.read_text()))
    for method in method_documents:
        if method.get("id") == "sequential-tti" and method.get("version") == "1.2.18":
            # This fixture tests a mean binding, so its scientifically equivalent reader agrees.
            method["metrics"][0]["statistic"] = "mean"
    method_path.write_text(
        "\n---\n".join(yaml.safe_dump(item, sort_keys=False) for item in method_documents)
    )
    document = next(
        method
        for method in _methodologies(root)
        if method["id"] == "sequential-tti" and method["version"] == "1.2.18"
    )
    names_doc = yaml.safe_load((root / "names/names.yaml").read_text())
    name_row = next(
        row for row in names_doc["names"] if row["kind"] == "test" and row["id"] == "sequential-tti"
    )
    name_row.update(yaml.safe_load((fixture / "metric-binding.yaml").read_text()))
    (root / "names/names.yaml").write_text(yaml.safe_dump(names_doc, sort_keys=False))

    schema = json.loads((root / "schemas/methodology.schema.json").read_text())
    assert Draft202012Validator(schema).is_valid(document)
    assert name_errors(root) == []
    before = semantic_digest(document)
    output = tmp_path / "consumer/text.json"
    write_text_bundle(root, output)
    consumed = json.loads(output.read_text())
    entry = consumed["methodologies"]["sequential-tti"]["1.2.18"]

    assert entry["semantic_digest"] == before
    assert entry["text"]["metrics"] == [
        {
            "id": metric_id(document["id"], document["version"], document["metrics"][0]["name"]),
            "name": "time_to_interactive",
            "quantity": "time",
            "phase": None,
            "per": "sandbox",
            "unit": "ms",
            "direction": "lower_is_better",
            "statistic": "mean",
        }
    ]
    assert consumed["metric_label_grammar"] == names_doc["metric_label_grammar"]
    assert json.loads(output.read_text()) == export_text_bundle(build_text_bundle(root))


def test_semantic_digest_ignores_the_display_name() -> None:
    document = {"id": "demo", "name": "First name", "unit": "ms"}
    semantic = semantic_digest(document)
    document["name"] = "Second name"
    assert semantic_digest(document) == semantic


def test_slot_reference_never_falls_back_to_another_version(
    checkout: Path,
    tmp_path: Path,
    build_text_bundle: Callable[[Path], dict[str, Any]],
) -> None:
    root = checkout
    path = root / "methodology/01-indexes/speed.yaml"
    documents = list(yaml.safe_load_all(path.read_text()))
    documents[-1].setdefault("notes", {})["summary"] = "{param:time-index.9.9.9.notes.summary}"
    path.write_text("\n---\n".join(yaml.safe_dump(item, sort_keys=False) for item in documents))
    with pytest.raises(PlaceholderError) as error:
        build_text_bundle(root)
    assert str(error.value) == "unknown parameter path 'time-index.9.9.9.notes.summary'"


@pytest.mark.parametrize(
    "notes",
    [
        {"summary": "{name:missing}"},
        {"nested": [{"summary": "{param:demo.1.0.0.notes.nested.0.summary}"}]},
        {"a": "{param:demo.1.0.0.notes.b}", "b": "{param:demo.1.0.0.notes.a}"},
        {"summary": "{param:demo.1.0.0.parameters.value}"},
    ],
)
def test_export_rejects_invalid_nested_slot_references(
    checkout: Path,
    tmp_path: Path,
    notes: dict[str, Any],
    build_text_bundle: Callable[[Path], dict[str, Any]],
) -> None:
    root = checkout
    path = root / "methodology/demo.yaml"
    document = {
        "id": "demo",
        "version": "1.0.0",
        "notes": notes,
        "parameters": {"value": "{param:missing.1.0.0.value}"},
    }
    path.write_text(yaml.safe_dump(document))
    for check in (build_text_bundle, text_warnings):
        with pytest.raises(PlaceholderError):
            check(root)


def test_export_expands_reused_text_in_nested_slots_deterministically(
    checkout: Path,
    tmp_path: Path,
    build_text_bundle: Callable[[Path], dict[str, Any]],
) -> None:
    root = checkout
    document = {
        "id": "demo",
        "version": "1.0.0",
        "parameters": {"values": [{"text": "{name:time-index}"}]},
        "notes": {"nested": [{"text": "{param:demo.1.0.0.parameters.values.0.text}"}]},
    }
    (root / "methodology/demo.yaml").write_text(yaml.safe_dump(document))
    bundle = build_text_bundle(root)
    expected = next(
        row["name"]
        for row in bundle["names"]
        if row["id"] == "time-index" and row["kind"] == "index"
    )
    text = bundle["methodologies"]["demo"]["1.0.0"]["text"]
    assert text == {"notes": {"nested": [{"text": expected}]}}
    assert build_text_bundle(root) == bundle


def test_default_text_command_enforces_current_reader_similarity(
    checkout: Path, tmp_path: Path, build_text_bundle: Callable[[Path], dict[str, Any]]
) -> None:
    root = checkout
    path = root / "reader/methods.yaml"
    source = yaml.safe_load(path.read_text())
    setup = build_text_bundle(root)["reader"]["methods"]["reconnect"]["text"]["notes"]["setup"]
    source["methods"]["reconnect"]["notes"]["introduction"] = setup
    path.write_text(yaml.safe_dump(source, sort_keys=False))
    warnings = text_warnings(root)
    assert any("reconnect" in warning and "near-duplicate" in warning for warning in warnings)


def test_default_text_check_does_not_require_retired_caption_sentences(
    checkout: Path, tmp_path: Path
) -> None:
    root = checkout
    path = root / "names/names.yaml"
    names = yaml.safe_load(path.read_text())
    names["metric_label_grammar"]["forms"]["caption"]["template"] = "Median values."
    path.write_text(yaml.safe_dump(names, sort_keys=False))
    assert not any(".caption:" in warning for warning in text_warnings(root))


def test_composite_index_rows_carry_a_statistic_selector_of_statistic_rows() -> None:
    table = yaml.safe_load((ROOT / "names/names.yaml").read_text(encoding="utf-8"))
    rows = {row["id"]: row for row in table["names"] if row["kind"] == "index"}
    statistics = {row["id"] for row in table["names"] if row["kind"] == "statistic"}
    assert rows
    for row in rows.values():
        selector = row["selector"]
        assert set(selector) == {"mode", "options"}
        assert set(selector["options"]) <= statistics
    schema = json.loads((ROOT / "schemas/names.schema.json").read_text())
    assert Draft202012Validator(schema).is_valid(table)


def test_a_reader_sentence_may_start_with_a_lowercase_tool_name() -> None:
    """Rule reader.capital-start allows its proper names (fio, stress-ng) to start a sentence."""
    warnings: list[str] = []
    _warn_sentence(warnings, "a", "fio reads random 4 KiB blocks.", 300)
    _warn_sentence(warnings, "b", "reads random 4 KiB blocks.", 300)
    assert warnings == ["text is not a complete sentence b"]
