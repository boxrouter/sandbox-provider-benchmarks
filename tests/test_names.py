# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""The canonical names table, the one alias table and their consistency checks."""

import copy
import json
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator

from sandbox_benchmark_verifier import (
    RetiredIdError,
    UnknownIdError,
    aliases,
    catalog,
    names,
    resolve,
    resolve_test_variant,
)
from sandbox_benchmark_verifier.catalog import retired_ids
from sandbox_benchmark_verifier.lock import read_lock
from sandbox_benchmark_verifier.name_row_checks import common_subsection_errors, off_documents
from sandbox_benchmark_verifier.names import (
    _all_variant_errors,
    _row_errors,
    label_case_errors,
    load_tables,
    name_errors,
)
from sandbox_benchmark_verifier.reader import reader_errors

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[1]


def edit(root: Path, table: str, change: object) -> None:
    path = root / "names" / f"{table}.yaml"
    document = yaml.safe_load(path.read_text())
    assert callable(change)
    change(document)
    path.write_text(yaml.safe_dump(document, sort_keys=False))


def test_current_tables_are_consistent() -> None:
    assert name_errors(ROOT) == []


def test_a_hero_row_names_the_document_of_its_test_row() -> None:
    rows = names()
    tests = {row["id"]: row for row in rows if row["kind"] == "test"}
    heroes = [row for row in rows if row["kind"] == "hero" and row["id"] in tests]
    assert heroes
    for hero in heroes:
        assert hero["document"] == tests[hero["id"]]["document"]


def test_metric_label_grammar_loads_from_the_names_owner() -> None:
    grammar = load_tables(ROOT).metric_label_grammar
    assert set(grammar["forms"]) == {"short", "long", "axis", "caption"}
    assert "selected-test-row-and-title" in grammar["contexts"]


def test_resolve_maps_old_ids_and_passes_canonical_ids() -> None:
    table = aliases()
    for kind in ("test", "task", "document", "tab"):
        for old_id, canonical in table[kind].items():
            assert resolve(kind, old_id) == canonical
            assert resolve(kind, canonical) == canonical
    for kind in ("variant", "stage"):
        for scope, scoped in table[kind].items():
            for old_id, canonical in scoped.items():
                assert resolve(kind, old_id, scope=scope) == canonical
                assert resolve(kind, canonical, scope=scope) == canonical
    for old_id, (test, variant) in table["test_variant"].items():
        assert resolve_test_variant(old_id) == (test, variant)


def test_resolve_rejects_retired_unknown_and_wrongly_scoped_ids() -> None:
    with pytest.raises(RetiredIdError):
        resolve("test", "start-time-exec")
    with pytest.raises(UnknownIdError):
        resolve("test", "nope")
    with pytest.raises(UnknownIdError):
        resolve("variant", "memory-1gib", scope="delayed-resume")
    with pytest.raises(ValueError, match="unknown alias kind"):
        resolve("hero", "x")


def test_returned_tables_are_fresh() -> None:
    old_id, canonical = next(iter(aliases()["test"].items()))
    aliases()["test"][old_id] = "changed"
    assert resolve("test", old_id) == canonical


@pytest.mark.parametrize(
    ("alias", "message"),
    [
        ({"old-thing": "no-such-test"}, "old-thing -> no-such-test"),
        ({"list": "hard-links"}, "also a canonical test id"),
    ],
)
def test_an_alias_to_a_missing_id_or_from_a_canonical_id_fails(
    checkout: Path, alias: dict[str, str], message: str
) -> None:
    edit(checkout, "aliases", lambda d: d["test"].update(alias))
    assert any(message in e for e in name_errors(checkout))


def test_duplicate_canonical_name_and_id_fail(checkout: Path, tmp_path: Path) -> None:
    root = checkout

    def duplicate(document: dict[str, list[dict[str, object]]]) -> None:
        rows = document["names"]
        row = next(r for r in rows if r["id"] == "list")
        clone = copy.deepcopy(row)
        clone["row"] = 999
        rows.append(clone)

    edit(root, "names", duplicate)
    errors = name_errors(root)
    assert any("duplicate canonical id" in e for e in errors)
    assert any("duplicate canonical name" in e for e in errors)


def test_missing_document_and_unaliased_document_fail(checkout: Path, tmp_path: Path) -> None:
    root = checkout
    edit(
        root,
        "names",
        lambda d: next(r for r in d["names"] if r["id"] == "list").update(
            {"document": "sandbox.files.nope"}
        ),
    )
    edit(root, "aliases", lambda d: d["document"].pop("index.speed_score"))
    errors = name_errors(root)
    assert any("sandbox.files.nope does not exist" in e for e in errors)
    assert any("index.speed_score: released document has no alias row" in e for e in errors)


def test_test_without_document_needs_a_recorded_gap(checkout: Path, tmp_path: Path) -> None:
    root = checkout
    edit(
        root,
        "names",
        lambda d: next(r for r in d["names"] if r["id"] == "list").pop("document"),
    )
    assert any("needs a document or a recorded gap" in e for e in name_errors(root))


def test_second_copy_of_a_table_fails(checkout: Path, tmp_path: Path) -> None:
    root = checkout
    (root / "methodology" / "aliases.yaml").write_text("schema: x\n")
    assert any("second copy" in e for e in name_errors(root))


def test_schema_violation_is_reported(checkout: Path, tmp_path: Path) -> None:
    root = checkout
    edit(root, "aliases", lambda d: d.update({"test": ["not", "a", "map"]}))
    assert any("schema validation failed" in e for e in name_errors(root))


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("phase", {"kind": "stage", "id": "unknown"}, "phase reference"),
        ("per", "unknown", "per value"),
        ("statistic", "unknown", "statistic reference"),
    ],
)
def test_metric_label_references_must_resolve(
    checkout: Path, tmp_path: Path, field: str, value: object, message: str
) -> None:
    root = checkout
    fixture = ROOT / "tests/fixtures/text-bundle"
    method_path = root / "methodology/03-sandbox-operations/create.yaml"
    method_documents = list(yaml.safe_load_all(method_path.read_text()))
    method = next(
        item
        for item in method_documents
        if item.get("id") == "sequential-tti" and item.get("version") == "1.2.18"
    )
    method["metrics"][0][field] = value
    method_path.write_text("\n---\n".join(yaml.safe_dump(item) for item in method_documents))
    names_doc = yaml.safe_load((root / "names/names.yaml").read_text())
    name_row = next(
        row for row in names_doc["names"] if row["kind"] == "test" and row["id"] == "sequential-tti"
    )
    name_row.update(yaml.safe_load((fixture / "metric-binding.yaml").read_text()))
    (root / "names/names.yaml").write_text(yaml.safe_dump(names_doc, sort_keys=False))
    assert any(message in error for error in name_errors(root))


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("document", "unknown-document", "document and version do not exist"),
        ("version", "9.9.9", "document and version do not exist"),
        ("name", "unknown_metric", "methodology metric does not resolve uniquely"),
    ],
)
def test_metric_binding_uses_exact_document_version_and_name(
    checkout: Path, tmp_path: Path, field: str, value: str, message: str
) -> None:
    root = checkout
    fixture = ROOT / "tests/fixtures/text-bundle"
    names_doc = yaml.safe_load((root / "names/names.yaml").read_text())
    row = next(
        item
        for item in names_doc["names"]
        if item["kind"] == "test" and item["id"] == "sequential-tti"
    )
    row.update(yaml.safe_load((fixture / "metric-binding.yaml").read_text()))
    row["metric"][field] = value
    (root / "names/names.yaml").write_text(yaml.safe_dump(names_doc, sort_keys=False))
    assert any(message in error for error in name_errors(root))


def test_explicit_statistic_uses_its_own_names_row(checkout: Path, tmp_path: Path) -> None:
    root = checkout
    fixture = ROOT / "tests/fixtures/text-bundle"
    method_path = root / "methodology/03-sandbox-operations/create.yaml"
    method_documents = list(yaml.safe_load_all(method_path.read_text()))
    for method in method_documents:
        if method.get("id") == "sequential-tti" and method.get("version") == "1.2.18":
            method["metrics"][0]["statistic"] = "mean"
    method_path.write_text("\n---\n".join(yaml.safe_dump(item) for item in method_documents))
    names_doc = yaml.safe_load((root / "names/names.yaml").read_text())
    row = next(
        item
        for item in names_doc["names"]
        if item["kind"] == "test" and item["id"] == "sequential-tti"
    )
    row.update(yaml.safe_load((fixture / "metric-binding.yaml").read_text()))
    (root / "names/names.yaml").write_text(yaml.safe_dump(names_doc, sort_keys=False))
    assert name_errors(root) == []


def test_the_only_page_of_a_tab_is_titled_by_the_tab_and_other_pages_by_their_test() -> None:
    """the five Machine Performance pages, Image Build, Exec and Files read the
    tab's label (the chart panel's and the rail's name); Create's two pages keep their tests'."""
    document = yaml.safe_load((ROOT / "names" / "names.yaml").read_text())
    tabs = {row["id"]: row["name"] for row in document["names"] if row["kind"] == "tab"}
    published = {
        row["id"]: row.get("page_title")
        for row in load_tables(ROOT).published_names()
        if row["kind"] == "test"
    }
    hosts = [row for row in document["names"] if row["kind"] == "test" and "page" not in row]
    for row in hosts:
        siblings = [other for other in hosts if other.get("parent") == row.get("parent")]
        single = row.get("parent") in tabs and len(siblings) == 1
        assert published[row["id"]] == (tabs[row["parent"]] if single else row["name"]), row["id"]


def test_a_subsection_of_the_common_page_is_a_hero_document_of_a_real_section() -> None:
    rows: list[dict[str, Any]] = [
        {"kind": "hero", "id": "a", "document": "d.a", "common_section": "scores"},
        {"kind": "hero", "id": "b", "document": "d.b", "common_subsection": "scores"},
    ]
    assert common_subsection_errors(rows[1], rows, "b") == []
    assert common_subsection_errors(rows[0], rows, "a") == []
    for broken, expected in (
        ({**rows[1], "common_subsection": "missing"}, "not a section of the Common page"),
        ({**rows[1], "kind": "test"}, "only a hero with a document"),
        ({**rows[1], "common_section": "scores"}, "not both"),
    ):
        assert any(expected in error for error in common_subsection_errors(broken, rows, "b"))


# Ids of `-2vcpu-8gib` tests that deliberately keep a methodology page of their own.
OWN_PAGE_2VCPU_8GIB: set[str] = set()


def test_a_2vcpu_8gib_variant_joins_its_parents_page() -> None:
    """Every `-2vcpu-8gib` test is a variant of a test and is drawn on that test's methodology
    page, so the rail has no third level for it (Hard Links had its own page until 2026-11-10)."""
    document = yaml.safe_load((ROOT / "names" / "names.yaml").read_text())
    variants = [
        row
        for row in document["names"]
        if row["kind"] == "test" and row["id"].endswith("-2vcpu-8gib")
    ]
    assert variants
    own_page = {row["id"] for row in variants if "page" not in row}
    assert own_page <= OWN_PAGE_2VCPU_8GIB, sorted(own_page - OWN_PAGE_2VCPU_8GIB)


def test_composite_statistics_selectors_only_own_their_options() -> None:
    document = yaml.safe_load((ROOT / "names" / "names.yaml").read_text())
    selectors = [row["selector"] for row in document["names"] if row["kind"] == "index"]
    assert selectors
    assert all(set(selector) == {"mode", "options"} for selector in selectors)


def test_every_composite_index_row_counts_workloads() -> None:
    """The overview composite Details draw "{Unit}" headings from the index row, so each has one."""
    document = yaml.safe_load((ROOT / "names" / "names.yaml").read_text())
    rows = [row for row in document["names"] if row["kind"] == "index"]
    assert {row["id"] for row in rows} >= {"time-index", "cost-index", "time-vs-cost"}
    assert all(row.get("unit") == "workload" for row in rows)
    assert all(row.get("unit_description") for row in rows)


@pytest.mark.parametrize(
    ("field", "value"), [("label", "Composite time on"), ("after", "statistics")]
)
def test_composite_statistics_selector_rejects_a_second_label(field: str, value: str) -> None:
    schema = json.loads((ROOT / "schemas" / "names.schema.json").read_text())
    selector: dict[str, object] = {"mode": "chooses", "options": ["median"]}
    row = {"row": 1, "kind": "index", "id": "synthetic", "name": "Synthetic", "selector": selector}
    validator = Draft202012Validator(schema)
    assert validator.is_valid({"schema": "boxrouter.bench.names/2", "names": [row]})
    selector[field] = value
    assert not validator.is_valid({"schema": "boxrouter.bench.names/2", "names": [row]})


def test_other_selector_kinds_still_require_their_label() -> None:
    schema = json.loads((ROOT / "schemas" / "names.schema.json").read_text())
    selector = {"mode": "chooses"}
    row = {"row": 1, "kind": "test", "id": "synthetic", "name": "Synthetic", "selector": selector}
    validator = Draft202012Validator(schema)
    assert not validator.is_valid({"schema": "boxrouter.bench.names/2", "names": [row]})
    selector["label"] = "Trace"
    assert validator.is_valid({"schema": "boxrouter.bench.names/2", "names": [row]})


def test_a_counted_per_unit_name_has_a_plural() -> None:
    rows = [
        {"row": 1, "kind": "per-unit", "id": "job", "name": "job"},
        {"row": 2, "kind": "test", "id": "ci", "name": "CI", "unit": "job"},
    ]
    assert any("has no plural" in error for error in _row_errors(rows, set()))
    rows[0]["plural"] = "jobs"
    assert not any("has no plural" in error for error in _row_errors(rows, set()))


def test_a_sentence_form_is_the_lower_case_word_of_a_statistic() -> None:
    rows = [{"row": 1, "kind": "statistic", "id": "median", "name": "Median", "sentence": "median"}]
    assert not any("sentence form" in error for error in _row_errors(rows, set()))
    rows[0]["sentence"] = "Median"
    assert any("sentence form" in error for error in _row_errors(rows, set()))
    rows[0].update(sentence="median", kind="test")
    assert any("sentence form" in error for error in _row_errors(rows, set()))


def test_a_selector_label_is_the_unit_word_of_its_test() -> None:
    selector = {"label": "Task"}
    rows = [
        {"row": 1, "kind": "per-unit", "id": "task", "name": "task", "plural": "tasks"},
        {
            "row": 2,
            "kind": "test",
            "id": "r2e",
            "name": "R2E",
            "unit": "task",
            "selector": selector,
        },
    ]
    assert not any("selector label" in error for error in _row_errors(rows, set()))
    selector["label"] = "Rollout"
    assert any("selector label" in error for error in _row_errors(rows, set()))


def test_an_all_variant_is_labelled_by_the_one_template() -> None:
    rows: list[dict[str, Any]] = [
        {"row": 1, "kind": "per-unit", "id": "task", "name": "task", "plural": "tasks"},
        {"row": 2, "kind": "test", "id": "r2e", "name": "R2E", "unit": "task"},
        {"row": 3, "kind": "variant", "id": "all", "name": "All Tasks", "parent": "r2e"},
    ]
    assert _all_variant_errors(rows, "All {Units}") == []
    rows[2]["name"] = "All traces"
    assert "'All Tasks'" in _all_variant_errors(rows, "All {Units}")[0]
    assert _all_variant_errors(rows[:2], "All {Units}")


def test_a_per_unit_word_may_read_as_another_unit_word() -> None:
    rows: list[dict[str, Any]] = [
        {"row": 1, "kind": "per-unit", "id": "trace", "name": "trace", "plural": "traces"},
        {"row": 2, "kind": "per-unit", "id": "session", "name": "session", "reads_as": "trace"},
    ]
    assert not any("reads_as" in error for error in _row_errors(rows, set()))
    rows[1]["reads_as"] = "missing"
    assert any("reads_as" in error for error in _row_errors(rows, set()))
    rows[1]["reads_as"] = "session"
    assert any("reads_as" in error for error in _row_errors(rows, set()))


def test_a_view_may_carry_a_sentence_form() -> None:
    rows = [{"row": 1, "kind": "view", "id": "time", "name": "Time", "sentence": "time"}]
    assert not any("sentence form" in error for error in _row_errors(rows, set()))
    rows[0]["sentence"] = "Time"
    assert any("sentence form" in error for error in _row_errors(rows, set()))


def test_exec_is_one_tab_whose_tests_are_the_view_options() -> None:
    rows = names()
    selectors = [
        row["selector"]
        for row in rows
        if row["kind"] == "test" and row.get("parent") == "exec" and row.get("selector")
    ]
    assert len(selectors) > 1
    assert all(set(selector) == {"label", "mode", "option"} for selector in selectors)
    assert len({selector["label"] for selector in selectors}) == 1
    assert len({selector["option"] for selector in selectors}) == len(selectors)
    assert all("panel_name" not in row for row in rows)


def test_a_capability_name_differs_from_its_id() -> None:
    rows: list[dict[str, Any]] = [
        {"row": 1, "kind": "capability", "id": "tcp_ports", "name": "TCP Ports"}
    ]
    assert not any("is the id" in error for error in _row_errors(rows, set()))
    rows[0]["name"] = "tcp_ports"
    assert any("is the id" in error for error in _row_errors(rows, set()))


def test_a_numbered_unit_is_its_word_and_the_number() -> None:
    rows: list[dict[str, Any]] = [
        {
            "row": 1,
            "kind": "per-unit",
            "id": "sandbox",
            "name": "sandbox",
            "numbered": "Sandbox {n}",
        }
    ]
    assert not any("numbered" in error for error in _row_errors(rows, set()))
    rows[0]["numbered"] = "Machine {n}"
    assert any("numbered" in error for error in _row_errors(rows, set()))
    rows[0].update({"kind": "stage", "numbered": "Sandbox {n}"})
    assert any("belongs to a per-unit word" in error for error in _row_errors(rows, set()))


def test_presentation_belongs_to_a_test_or_tab_and_resolves_its_references() -> None:
    rows: list[dict[str, Any]] = [
        {"row": 1, "kind": "capability", "id": "previews", "name": "Previews"},
        {"row": 2, "kind": "subsection", "id": "ci-workloads", "name": "CI Workloads"},
        {
            "row": 3,
            "kind": "test",
            "id": "previews",
            "name": "Previews",
            "presentation": {"capability": "previews", "stage_labels": "names"},
        },
        {
            "row": 4,
            "kind": "test",
            "id": "ci-jobs",
            "name": "CI Jobs",
            "parent": "ci-workloads",
            "presentation": {"own_time_total": True},
        },
    ]
    assert not any("presentation" in error for error in _row_errors(rows, set()))
    rows[2]["presentation"]["capability"] = "gone"
    rows[3]["parent"] = "elsewhere"
    errors = [
        error
        for error in _row_errors(rows, set())
        if "presentation" in error or "time total" in error
    ]
    assert any("capability 'gone'" in error for error in errors)
    assert any("own time total" in error for error in errors)
    rows[0]["presentation"] = {"panel": "pause-resume"}
    assert any("belongs to a test or a tab" in error for error in _row_errors(rows, set()))


def test_a_retired_test_has_no_row_and_its_documents_leave_every_reader() -> None:
    """Image Build at 4 vCPU / 8 GiB is retired: no names row, no alias row, no procedure, no
    document in the packaged catalog or the text bundle; retired history is absent from the lock."""
    tables = load_tables(ROOT)
    retired = {"image-build-4vcpu-8gib", "image-build-4x8", "sandbox.image_build.size_4x8"}
    assert retired <= retired_ids(ROOT)
    assert not any(row["id"] in retired for row in tables.names)
    for identifier in retired:
        with pytest.raises(RetiredIdError):
            resolve("document", identifier, tables=tables)
    assert not any(item.document.get("id") in retired for item in catalog.load_methodology(ROOT))
    assert not any(key.startswith("image-build-4vcpu-8gib@") for key in read_lock(ROOT))


@pytest.mark.parametrize(
    ("old", "new", "words"),
    [
        # The check never looked at names rows: "Time score" and "Cost score" passed (2026-11-01).
        ('name: "Time Score"', 'name: "Time score"', ("not Title Case", "time-score")),
        # The hover card's Source line used an internal clock display name (2026-11-02).
        (
            'name: "Job-host clock"',
            'name: "AWS ' + "benchmark" + '-runner clock"',
            ("clock runner",),
        ),
    ],
)
def test_a_names_row_is_title_case_and_a_clock_name_never_mentions_the_runner(
    checkout: Path, old: str, new: str, words: tuple[str, ...]
) -> None:
    path = checkout / "names" / "names.yaml"
    original = path.read_text()
    assert old in original
    path.write_text(original.replace(old, new, 1))
    assert any(all(word in error for word in words) for error in name_errors(checkout))


def test_a_metric_name_and_a_statistic_label_in_the_page_contract_are_title_case(
    checkout: Path,
    tmp_path: Path,
) -> None:
    """ "Geometric mean" and "Composite cost" sat beside "Arithmetic Mean" (2026-11-02)."""
    root = checkout
    path = root / "contracts" / "text" / "methodology-page.json"
    for old, new in (
        ('"typical_cost": "Composite Cost"', '"typical_cost": "Composite cost"'),
        ('"pull": "Pull the Image"', '"pull": "Pull the image"'),
        (
            '"geometric_mean": "{name:statistic.geometric_mean}"',
            '"geometric_mean": "Geometric mean"',
        ),
    ):
        original = path.read_text()
        assert old in original
        path.write_text(original.replace(old, new, 1))
        assert any("not Title Case" in error for error in name_errors(root)), old
        path.write_text(original)


def test_a_retired_word_is_in_no_reader_facing_string_of_the_current_sources(
    checkout: Path,
    tmp_path: Path,
) -> None:
    """The retired term is absent from all reader-facing strings."""
    root = checkout
    edits = {
        "methodology/01-indexes/speed.yaml": (
            "quantity: Time per trace",
            "quantity: Typical time per trace",
        ),
        "contracts/text/methodology-page.json": (
            '"first_command_time": "First Command Time"',
            '"first_command_time": "Typical Command Time"',
        ),
        "reader/methods.yaml": ("headline: Composite time", "headline: Typical time"),
        "names/names.yaml": ('name: "Median"', 'name: "Typical Median"'),
    }
    for relative, (old, new) in edits.items():
        path = root / relative
        original = path.read_text(encoding="utf-8")
        assert old in original, relative
        # The newest version of a document is its last one in the file.
        path.write_text(original[::-1].replace(old[::-1], new[::-1], 1)[::-1], encoding="utf-8")
        assert any("retired word" in error for error in name_errors(root)), relative
        path.write_text(original, encoding="utf-8")


def test_a_clock_name_never_adds_a_provider_label(tmp_path: Path) -> None:
    """The clock-name guard rejects a forbidden source label."""
    root = tmp_path / "tree"
    shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__"))
    assert name_errors(root) == []
    path = root / "names" / "names.yaml"
    path.write_text(path.read_text().replace('name: "Job-host clock"', 'name: "Runner clock"', 1))
    assert any("clock runner" in error and "runner" in error for error in name_errors(root))


@pytest.mark.parametrize(
    ("relative", "old", "new"),
    [
        ("names/names.yaml", 'label: "Parallel Sandboxes"', 'label: "Parallel sandboxes"'),
        ("names/names.yaml", 'label: "fio Test"', 'label: "Fio test"'),
        ("names/names.yaml", 'name: "Load Runtimes"', 'name: "Load runtimes"'),
        ("contracts/text/1.0.0.json", '"TTI for Sandbox: [', '"TTI for sandbox: ['),
    ],
)
def test_a_selector_label_a_stage_name_and_the_sandbox_picker_are_title_case(
    checkout: Path, tmp_path: Path, relative: str, old: str, new: str
) -> None:
    """The overview's selectors and legend chips mixed sentence case with Title Case (2026-11-02);
    a tool name keeps its own casing (fio, 7-Zip)."""
    root = checkout
    path = root / relative
    original = path.read_text(encoding="utf-8")
    assert old in original
    path.write_text(original.replace(old, new, 1), encoding="utf-8")
    assert any("not Title Case" in error for error in name_errors(root))


@pytest.mark.parametrize(
    ("relative", "path", "bad"),
    [
        ("contracts/text/1.0.0.json", ("ranking_statuses", "too_few_runs", "label"), "Not ranked"),
        ("contracts/text/1.0.0.json", ("ranking_statuses", "filled", "label"), "Provider failures"),
        ("contracts/text/methodology-page.json", ("field_labels", "applies_to"), "applies to"),
        ("contracts/text/methodology-page.json", ("enum_labels", "null"), "Not specified"),
        ("contracts/text/methodology-page.json", ("navigation", "all_methods"), "All methods"),
        ("contracts/text/methodology-page.json", ("actions", "source"), "View source"),
        ("contracts/text/methodology-page.json", ("pages", "back"), "Back to benchmarks"),
        ("contracts/text/methodology-page.json", ("sections", "metrics", "name"), "metrics"),
        ("contracts/text/methodology-page.json", ("metric_names", "total_time"), "Total time"),
        ("contracts/text/fact-list.json", ("fact_failed_workloads",), "Provider failures"),
    ],
)
def test_a_sentence_case_label_in_a_label_catalog_fails_the_one_label_registry(
    checkout: Path, tmp_path: Path, relative: str, path: tuple[str, ...], bad: str
) -> None:
    """Founder, 2026-11-08: "Provider Failures", "Not Ranked", not "Provider failures". Every label
    catalog in the conventions' naming.label_catalogs is Title Case; a sentence (ends with a
    period) and a listed fragment are not labels."""
    root = checkout
    assert label_case_errors(root) == []
    target = root / relative
    document = json.loads(target.read_text(encoding="utf-8"))
    node = document
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = bad
    target.write_text(json.dumps(document), encoding="utf-8")
    assert any(
        relative in error and "is not Title Case" in error for error in label_case_errors(root)
    )
    node[path[-1]] = "A sentence stays in sentence case."
    target.write_text(json.dumps(document), encoding="utf-8")
    assert label_case_errors(root) == []


def test_a_combined_view_has_its_own_subtitle() -> None:
    rows: list[dict[str, Any]] = [
        {"row": 1, "kind": "view", "id": "time", "name": "Time", "parent": "overview"},
        {
            "row": 2,
            "kind": "view",
            "id": "files-read-write",
            "name": "Read and Write",
            "parent": "x",
        },
    ]
    assert any("needs its own subtitle" in error for error in _row_errors(rows, set()))
    rows[1]["subtitle"] = "Repeated requests read and write a file."
    assert not any("subtitle" in error for error in _row_errors(rows, set()))
    rows[0]["subtitle"] = "Not here."
    assert any("overview measure view" in error for error in _row_errors(rows, set()))
    rows[0].update({"kind": "stage"})
    assert any("belongs to a combined view" in error for error in _row_errors(rows, set()))


def _page_rows() -> list[dict[str, Any]]:
    return [
        {"row": 1, "kind": "section", "id": "ops", "name": "Ops"},
        {"row": 2, "kind": "tab", "id": "one", "name": "One", "parent": "ops"},
        {"row": 3, "kind": "tab", "id": "two", "name": "Two", "parent": "ops"},
        {
            "row": 4,
            "kind": "test",
            "id": "first",
            "name": "First",
            "parent": "one",
            "document": "a",
        },
        {
            "row": 5,
            "kind": "test",
            "id": "second",
            "name": "Second",
            "parent": "one",
            "document": "b",
        },
        {
            "row": 6,
            "kind": "test",
            "id": "other",
            "name": "Other",
            "parent": "two",
            "document": "c",
        },
    ]


def _page_errors_of(rows: list[dict[str, Any]]) -> list[str]:
    return [e for e in _row_errors(rows, {"a", "b", "c"}) if "page" in e]


def test_a_test_may_have_the_page_of_another_test_of_its_tab() -> None:
    rows = _page_rows()
    rows[4]["page"] = "first"
    assert _page_errors_of(rows) == []


@pytest.mark.parametrize(
    ("target", "message"),
    [("nowhere", "is not a test"), ("other", "is not in the same tab")],
)
def test_a_page_names_an_existing_test_of_the_same_tab(target: str, message: str) -> None:
    rows = _page_rows()
    rows[4]["page"] = target
    errors = _page_errors_of(rows)
    assert any(message in error for error in errors), errors


def test_a_page_is_not_a_chain() -> None:
    rows = _page_rows()
    rows[3]["page"] = "second"
    rows[4]["page"] = "first"
    assert sum("no chains" in error for error in _page_errors_of(rows)) == 2


@pytest.mark.parametrize(
    ("row", "message"),
    [(3, "no chains"), (1, "only a test or an index has a page")],
    ids=["a test cannot be its own page", "only a test has a page"],
)
def test_a_page_belongs_to_another_test(row: int, message: str) -> None:
    rows = _page_rows()
    rows[row]["page"] = "first"
    assert any(message in error for error in _page_errors_of(rows))


def test_an_index_takes_the_page_of_an_index_of_its_section_only() -> None:
    rows = [
        {"row": 1, "kind": "section", "id": "s", "name": "S"},
        {"row": 2, "kind": "index", "id": "first", "name": "First", "parent": "s", "document": "a"},
        {
            "row": 3,
            "kind": "index",
            "id": "second",
            "name": "Second",
            "parent": "s",
            "document": "b",
        },
        {"row": 4, "kind": "tab", "id": "tab", "name": "Tab", "parent": "s"},
        {
            "row": 5,
            "kind": "test",
            "id": "third",
            "name": "Third",
            "parent": "tab",
            "document": "c",
        },
    ]
    rows[2]["page"] = "first"
    assert _page_errors_of(rows) == []
    rows[2]["page"] = "third"
    assert any("is not an index" in error for error in _page_errors_of(rows))


def test_a_test_with_a_page_keeps_only_its_own_sentences_in_the_reader(
    checkout: Path, tmp_path: Path
) -> None:
    root = checkout
    assert reader_errors(root) == []
    path = root / "reader" / "methods.yaml"
    source = yaml.safe_load(path.read_text())
    source["methods"]["exec-latency"]["notes"]["setup"] = "Times a call."
    path.write_text(yaml.safe_dump(source, sort_keys=False))
    errors = reader_errors(root)
    assert len(errors) == 1
    assert "exec-latency-per-100-calls's" in errors[0] and "setup" in errors[0]


def test_only_a_page_host_has_page_wording(
    checkout: Path, bundle: dict[str, Any], tmp_path: Path
) -> None:

    # are its own, and the Time panel keeps the host's headline and introduction.
    notes = bundle["reader"]["methods"]["time-index"]["text"]["notes"]
    assert notes["page_headline"]
    assert notes["introduction"] != notes["page_introduction"]
    root = checkout
    path = root / "reader" / "methods.yaml"
    source = yaml.safe_load(path.read_text())
    source["methods"]["deepswe"]["notes"]["page_introduction"] = "Measures a page."
    path.write_text(yaml.safe_dump(source, sort_keys=False))
    errors = reader_errors(root)
    assert len(errors) == 1
    assert "deepswe" in errors[0] and "page_introduction is a page's wording" in errors[0]


def test_every_methodology_page_has_a_subtitle_to_draw(checkout: Path) -> None:
    """A page draws its test's introduction, or its host's page_introduction when it holds tests."""
    root = checkout
    assert reader_errors(root) == []
    path = root / "reader" / "methods.yaml"
    source = yaml.safe_load(path.read_text())
    for identifier, note in (
        ("sequential-read", "page_introduction"),
        ("stream-triad", "introduction"),
    ):
        notes = source["methods"][identifier]["notes"]
        wording = notes.pop(note)
        path.write_text(yaml.safe_dump(source, sort_keys=False))
        errors = reader_errors(root)
        assert len(errors) == 1, errors
        assert identifier in errors[0] and f"notes.{note}" in errors[0]
        notes[note] = wording


def test_a_test_on_a_combined_page_explains_itself_under_metrics(checkout: Path) -> None:
    """The Metrics section draws each test of a combined page as a subsection: its name, then its
    metric paragraph. A member (folded 8 GiB variant, All vCPUs) or a host without one would leave
    a heading with nothing under it (hard-links-2vcpu-8gib on 2026-11-10)."""
    root = checkout
    assert reader_errors(root) == []
    path = root / "reader" / "methods.yaml"
    source = yaml.safe_load(path.read_text())
    for identifier in ("hard-links-2vcpu-8gib", "read-4kib", "7-zip-all-vcpus"):
        notes = source["methods"][identifier]["notes"]
        wording = notes.pop("metric")
        path.write_text(yaml.safe_dump(source, sort_keys=False))
        errors = reader_errors(root)
        assert len(errors) == 1, errors
        assert identifier in errors[0] and "notes.metric" in errors[0]
        notes["metric"] = wording


def test_the_legacy_combined_disk_document_is_retired_and_has_no_page(
    bundle: dict[str, Any],
) -> None:
    """The four fio tests have their own pages; the combined document nothing reads is retired."""
    assert "machine.disk.fio" in retired_ids()
    assert "machine.disk.fio" not in aliases()["document"]
    assert "disk" not in bundle["methodologies"]


def test_a_page_holds_every_test_of_its_tab() -> None:
    rows = _page_rows()
    rows.append(
        {"row": 7, "kind": "test", "id": "third", "name": "Third", "parent": "one", "document": "d"}
    )
    rows[4]["page"] = "first"
    errors = [e for e in _row_errors(rows, {"a", "b", "c", "d"}) if "its tab's page" in e]
    assert len(errors) == 1 and "'third'" in errors[0]
    rows[6]["page"] = "first"
    assert not [e for e in _row_errors(rows, {"a", "b", "c", "d"}) if "page" in e]


def test_the_common_methodology_name_has_one_owner() -> None:
    """The name of the Common Methodology page is the names table's `page` row `common`; the
    reader text and the text contracts read it as {name:page.common} and never type it."""
    row = next(row for row in names() if row["kind"] == "page" and row["id"] == "common")
    assert row["document"] == "sbs.common"
    typed = [
        path.relative_to(ROOT).as_posix()
        for path in [ROOT / "reader" / "methods.yaml", *sorted((ROOT / "contracts/text").glob("*"))]
        if row["name"] in path.read_text(encoding="utf-8")
    ]
    assert typed == []


def test_off_documents_are_the_tests_marked_off() -> None:
    rows = [
        {"kind": "test", "id": "a", "document": "doc-a", "status": "off"},
        {"kind": "test", "id": "b", "status": "off"},
        {"kind": "test", "id": "c", "document": "doc-c"},
        {"kind": "test", "id": "d", "document": "doc-d", "status": "live"},
    ]
    assert off_documents(rows) == {"doc-a", "b"}
    assert off_documents(load_tables().names) == {"burst-tti", "image-build-2vcpu-4gib"}


def test_status_off_belongs_on_a_test_row() -> None:
    rows = [{"row": 1, "kind": "tab", "id": "t", "name": "T", "status": "off"}]
    assert any("status off belongs on a test row" in error for error in _row_errors(rows, set()))
