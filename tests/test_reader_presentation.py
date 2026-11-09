# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""The local reader projection contains only owner-approved presentation fields."""

import importlib
import json
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import yaml
from pytest_mock import MockerFixture

from sandbox_benchmark_verifier import text as text_module
from sandbox_benchmark_verifier.catalog import load_schema
from sandbox_benchmark_verifier.content import content_criteria
from sandbox_benchmark_verifier.conventions import load_conventions
from sandbox_benchmark_verifier.names import compiler_tables_cache, load_tables
from sandbox_benchmark_verifier.reader import (
    _presentation_rule_ids,
    compile_presentation,
    validate_presentation,
)

names_module = importlib.import_module("sandbox_benchmark_verifier.names")
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def presentation() -> dict[str, Any]:
    return compile_presentation(ROOT)


def test_presentation_is_compiled_and_its_owner_schema_is_exported_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    build_text_bundle: Callable[[Path], dict[str, Any]],
) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    bundle = build_text_bundle(ROOT)
    reader = bundle["reader"]
    assert reader["presentation"] == compile_presentation(ROOT)
    assert (
        reader["presentation"]["schema"]
        == load_schema("presentation")["properties"]["schema"]["const"]
    )
    assert reader["presentation_schema"] == load_schema("presentation")
    assert reader["presentation_schema"]["$id"].endswith("benchmark-presentation-1.json")


def test_projection_contains_only_the_settled_global_reader_roots(
    presentation: dict[str, Any],
) -> None:
    criteria = presentation["content_criteria"]
    schema = load_schema("presentation")
    criteria_schema = schema["properties"]["content_criteria"]["properties"]
    assert set(criteria) == set(criteria_schema)
    assert set(criteria["statistics_selector"]) == set(
        schema["$defs"]["statisticsSelector"]["properties"]
    )
    assert {rule["id"] for rule in criteria["conventions"]["presentation"]["rules"]} == (
        _presentation_rule_ids(schema)
    )
    section_kind = schema["$defs"]["sectionName"]["properties"]["kind"]["const"]
    name_fields = set(schema["$defs"]["sectionName"]["properties"])
    assert {row["kind"] for row in presentation["names"]} == {section_kind}
    assert all(set(row) == name_fields for row in presentation["names"])
    assert {row["id"] for row in presentation["names"]} == {
        row["id"] for row in load_tables(ROOT).names if row["kind"] == section_kind
    }
    assert set(criteria["hover_templates"]["chart_header"]) <= set(
        schema["$defs"]["hoverFields"]["properties"]
    )
    assert set(criteria["hover_slots"]["capability"]) <= set(
        schema["$defs"]["hoverFields"]["properties"]
    )


@pytest.mark.parametrize(
    ("path", "field", "value"),
    [
        (("content_criteria",), "timeout_seconds", 300),
        (("content_criteria", "statistics_selector"), "options", ["mean"]),
        (("content_criteria", "hover_templates", "chart_header"), "ranking", "P95"),
        (
            ("content_criteria", "conventions", "presentation", "rules", 0, "parameters"),
            "min_successes",
            20,
        ),
        (
            ("content_criteria", "conventions", "presentation", "rules", 1, "parameters"),
            "measurement_timer",
            "wall-clock",
        ),
    ],
)
def test_schema_rejects_unknown_or_scientific_fields(
    presentation: dict[str, Any], path: tuple[str | int, ...], field: str, value: Any
) -> None:
    changed = deepcopy(presentation)
    target: Any = changed
    for part in path:
        target = target[part]
    target[field] = value
    with pytest.raises(ValueError, match="presentation payload is invalid"):
        validate_presentation(changed)


@pytest.mark.parametrize(
    "row",
    [
        {"kind": "quantity", "id": "time", "name": "Minutes"},
        {"kind": "per-unit", "id": "second", "name": "Minutes"},
    ],
)
def test_measured_quantity_and_per_unit_renames_cannot_enter_projection(
    presentation: dict[str, Any], row: dict[str, str]
) -> None:
    changed = deepcopy(presentation)
    changed["names"].append(row)
    with pytest.raises(ValueError, match="presentation payload is invalid"):
        validate_presentation(changed)


def test_pinned_scientific_identity_cannot_be_forged_as_a_section(
    presentation: dict[str, Any],
) -> None:
    pinned_names = load_tables(ROOT).names
    changed = deepcopy(presentation)
    changed["names"].append({"kind": "section", "id": "exec-latency", "name": "Latency"})
    with pytest.raises(ValueError, match="pinned section identities"):
        validate_presentation(changed, pinned_names=pinned_names)


def test_section_display_name_can_change_without_changing_its_identity(
    presentation: dict[str, Any],
) -> None:
    pinned_names = load_tables(ROOT).names
    changed = deepcopy(presentation)
    changed["names"][0]["name"] = "A reader-facing section name"
    validate_presentation(changed, pinned_names=pinned_names)


def test_projection_does_not_retype_method_wording_or_scientific_records(
    presentation: dict[str, Any],
) -> None:
    assert (
        presentation["content_criteria"]["hover_templates"] == content_criteria()["hover_templates"]
    )
    assert "methodologies" not in presentation
    assert "methods" not in presentation


def _cache_source(root: Path, declare_common: Callable[[Path], dict[str, str]]) -> None:
    (root / "names").mkdir(parents=True)
    (root / "schemas").mkdir()
    (root / "methodology" / "sample").mkdir(parents=True)
    (root / "parameters" / "sample" / "1.0.0").mkdir(parents=True)
    (root / "names" / "names.yaml").write_text(
        "schema: test\nnames: [{row: 1, kind: section, id: overview, name: Overview}]\n",
        encoding="utf-8",
    )
    (root / "names" / "aliases.yaml").write_text("schema: test\n", encoding="utf-8")
    # Lifecycle discovery selects inside the one declared common (#755, #759).
    common = declare_common(root)
    (root / "methodology" / "sample" / "1.0.0.yaml").write_text(
        "id: sample\nversion: 1.0.0\nname: Sample\nmetrics: [{name: Latency}]\n"
        "parameters: {path: parameters/sample/1.0.0/}\n"
        f"inherits: {{id: {common['id']}, version: {common['version']}}}\n",
        encoding="utf-8",
    )
    (root / "parameters" / "sample" / "1.0.0" / "protocol.json").write_text(
        '{"schema":"sandbox-benchmark.lifecycle-protocol/1","section":"overview"}',
        encoding="utf-8",
    )
    (root / "parameters" / "sample" / "1.0.0" / "workloads.json").write_text(
        '[{"id":"small","label":"Small"}]', encoding="utf-8"
    )
    for name in (
        "methodology",
        "lifecycle-protocol",
        "names",
        "aliases",
    ):
        (root / "schemas" / f"{name}.schema.json").write_text('{"type":"object"}', encoding="utf-8")


def test_exact_source_cache_reuses_immutable_tables_across_reader_changes(
    tmp_path: Path,
    declare_common: Callable[[Path], dict[str, str]],
) -> None:
    source = tmp_path / "source"
    cache = tmp_path / "cache"
    _cache_source(source, declare_common)
    digest_algorithms = {"semantic_digest": "sha256:method/2"}
    with compiler_tables_cache(source, digest_algorithms, cache_directory=cache):
        fresh = load_tables(source)
    (source / "reader").mkdir()
    (source / "reader" / "methods.yaml").write_text("methods: updated\n", encoding="utf-8")
    (source / "conventions").mkdir()
    (source / "conventions" / "text-conventions.yaml").write_text(
        "presentation: changed\n", encoding="utf-8"
    )
    (source / "names" / "names.yaml").write_text(
        "schema: test\nnames: [{row: 1, kind: section, id: overview, name: New overview}]\n",
        encoding="utf-8",
    )
    declare_common(source)  # the rewritten names table keeps the declared common
    with compiler_tables_cache(source, digest_algorithms, cache_directory=cache):
        cached = load_tables(source)
    assert cached.names[0]["name"] == "New overview"
    assert [row for row in cached.names if row["kind"] == "variant"] == [
        row for row in fresh.names if row["kind"] == "variant"
    ]
    assert next(row for row in cached.names if row["kind"] == "test")["name"] == "Sample"
    assert len(list(cache.glob("*.json"))) == 1


def test_an_installed_package_caches_its_tables_from_its_own_compiler_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    declare_common: Callable[[Path], dict[str, str]],
) -> None:
    """A wheel has no src/ beside its assets; the compiler is the running module, so the cache
    still applies (without it every load_tables of a page build parsed the YAML again)."""
    source = tmp_path / "source"
    cache = tmp_path / "cache"
    _cache_source(source, declare_common)
    package = tmp_path / "site-packages" / "sandbox_benchmark_verifier"
    package.mkdir(parents=True)
    for name in ("names.py", "lifecycle_profile.py", "catalog.py"):
        (package / name).write_bytes(
            (Path(names_module.__file__).parent / name).read_bytes()  # type: ignore[arg-type]
        )
    monkeypatch.setattr(names_module, "__file__", str(package / "names.py"))
    with compiler_tables_cache(
        source, {"semantic_digest": "sha256:method/2"}, cache_directory=cache
    ):
        load_tables(source)
    assert len(list(cache.glob("*.json"))) == 1


def test_exact_source_cache_invalidates_science_and_digest_algorithm_changes(
    tmp_path: Path,
    declare_common: Callable[[Path], dict[str, str]],
) -> None:
    source = tmp_path / "source"
    cache = tmp_path / "cache"
    _cache_source(source, declare_common)
    algorithms = {"semantic_digest": "sha256:method/2"}
    with compiler_tables_cache(source, algorithms, cache_directory=cache):
        original = load_tables(source)
    sample = source / "methodology" / "sample" / "1.0.0.yaml"
    sample.write_text(
        sample.read_text(encoding="utf-8").replace("name: Sample", "name: Changed Science"),
        encoding="utf-8",
    )
    with compiler_tables_cache(source, algorithms, cache_directory=cache):
        changed_science = load_tables(source)
    with compiler_tables_cache(
        source, {"semantic_digest": "sha256:method/3"}, cache_directory=cache
    ):
        changed_algorithm = load_tables(source)
    assert next(row for row in changed_science.names if row["kind"] == "test")["name"] == (
        "Changed Science"
    )
    assert changed_algorithm == changed_science
    assert next(row for row in original.names if row["kind"] == "test")["name"] == "Sample"
    assert len(list(cache.glob("*.json"))) == 3


@pytest.mark.parametrize("damage", ["stale", "corrupt"])
def test_exact_source_cache_fails_closed_on_stale_or_corrupt_metadata(
    tmp_path: Path,
    damage: str,
    declare_common: Callable[[Path], dict[str, str]],
) -> None:
    source = tmp_path / "source"
    cache = tmp_path / "cache"
    _cache_source(source, declare_common)
    algorithms = {"semantic_digest": "sha256:method/2"}
    with compiler_tables_cache(source, algorithms, cache_directory=cache):
        load_tables(source)
    path = next(cache.glob("*.json"))
    record = json.loads(path.read_text(encoding="utf-8"))
    if damage == "stale":
        record["key"] = "0" * 64
    else:
        record["payload"]["rows"][0]["name"] = "Tampered"
    path.write_text(json.dumps(record), encoding="utf-8")
    with compiler_tables_cache(source, algorithms, cache_directory=cache):
        with pytest.raises(ValueError, match="compiler table cache"):
            load_tables(source)


def _minimal_names_root(root: Path) -> None:
    (root / "names").mkdir(parents=True)
    (root / "names" / "names.yaml").write_text(
        "schema: test\nnames: [{row: 1, kind: section, id: overview, name: Overview}]\n",
        encoding="utf-8",
    )
    (root / "names" / "aliases.yaml").write_text("schema: test\n", encoding="utf-8")


def test_sparse_root_disables_cache_and_still_runs_compiler_validation(
    tmp_path: Path,
    build_text_bundle: Callable[[Path], dict[str, Any]],
    declare_common: Callable[[Path], dict[str, str]],
) -> None:
    root = tmp_path / "sparse"
    cache = tmp_path / "cache"
    _minimal_names_root(root)
    declare_common(root)
    with compiler_tables_cache(root, {}, cache_directory=cache):
        tables = load_tables(root)
    assert tables.names == [{"row": 1, "kind": "section", "id": "overview", "name": "Overview"}]
    assert not cache.exists()
    bundle = build_text_bundle(root)
    assert bundle["reader"]["presentation"] is None
    assert bundle["reader"]["presentation_schema"] is None


@pytest.mark.parametrize("missing", ["schemas", "compiler"])
def test_missing_cache_schema_or_compiler_fallback_disables_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    missing: str,
    declare_common: Callable[[Path], dict[str, str]],
) -> None:
    source = tmp_path / "source"
    cache = tmp_path / "cache"
    _cache_source(source, declare_common)
    if missing == "schemas":
        (source / "schemas" / "lifecycle-protocol.schema.json").unlink()
        (source / "parameters" / "sample" / "1.0.0" / "protocol.json").unlink()
    else:
        monkeypatch.setattr(names_module, "__file__", str(tmp_path / "absent.py"))
    with compiler_tables_cache(
        source, {"semantic_digest": "sha256:method/2"}, cache_directory=cache
    ):
        tables = load_tables(source)
    if missing == "schemas":
        assert [row["kind"] for row in tables.names] == ["section"]
    else:
        assert next(row for row in tables.names if row["kind"] == "test")["name"] == "Sample"
    assert not cache.exists()


def test_nested_sparse_cache_scope_masks_and_restores_outer_scope(
    tmp_path: Path, declare_common: Callable[[Path], dict[str, str]]
) -> None:
    source = tmp_path / "source"
    sparse = tmp_path / "sparse"
    cache = tmp_path / "cache"
    _cache_source(source, declare_common)
    _minimal_names_root(sparse)
    algorithms = {"semantic_digest": "sha256:method/2"}
    outer_scope = names_module._TABLES_CACHE_SCOPE
    with compiler_tables_cache(source, algorithms, cache_directory=cache):
        outer = load_tables(source)
        outer_cache_scope = outer_scope.get()
        assert outer_cache_scope is not None
        with compiler_tables_cache(sparse, algorithms, cache_directory=cache):
            assert outer_scope.get() is None
            sparse_tables = load_tables(sparse)
            assert [row["kind"] for row in sparse_tables.names] == ["section"]
            assert len(list(cache.glob("*.json"))) == 1
        assert outer_scope.get() is outer_cache_scope
        assert load_tables(source) == outer
    assert len(list(cache.glob("*.json"))) == 1


def test_nested_scope_for_the_same_root_reuses_the_outer_scope(
    tmp_path: Path,
    mocker: MockerFixture,
    declare_common: Callable[[Path], dict[str, str]],
) -> None:
    source = tmp_path / "source"
    cache = tmp_path / "cache"
    _cache_source(source, declare_common)
    algorithms = {"semantic_digest": "sha256:method/2"}
    spy = mocker.spy(names_module, "_load_tables")
    with compiler_tables_cache(source, algorithms, cache_directory=cache):
        load_tables(source)
        outer_cache_scope = names_module._TABLES_CACHE_SCOPE.get()
        with compiler_tables_cache(source, algorithms, cache_directory=cache):
            assert names_module._TABLES_CACHE_SCOPE.get() is outer_cache_scope
            load_tables(source)
        load_tables(source)
    assert spy.call_count == 1


def test_missing_referenced_workload_parameter_still_fails(
    tmp_path: Path, declare_common: Callable[[Path], dict[str, str]]
) -> None:
    source = tmp_path / "source"
    _cache_source(source, declare_common)
    (source / "schemas" / "methodology.schema.json").unlink()
    (source / "parameters" / "sample" / "1.0.0" / "workloads.json").unlink()
    with pytest.raises(FileNotFoundError) as caught:
        with compiler_tables_cache(source, {"semantic_digest": "sha256:method/2"}):
            load_tables(source)
    assert caught.value.filename is not None
    assert caught.value.filename.endswith("workloads.json")


@pytest.mark.parametrize("cache_eligible", [True, False])
def test_cache_scope_does_not_swallow_compiler_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cache_eligible: bool,
    declare_common: Callable[[Path], dict[str, str]],
) -> None:
    source = tmp_path / "source"
    if cache_eligible:
        _cache_source(source, declare_common)
    else:
        _minimal_names_root(source)
    error = RuntimeError("compiler failed")

    def fail_compiler(*_args: Any) -> list[dict[str, Any]]:
        raise error

    monkeypatch.setattr(names_module, "name_rows", fail_compiler)
    with pytest.raises(RuntimeError, match="compiler failed") as caught:
        with compiler_tables_cache(source, {"semantic_digest": "sha256:method/2"}):
            load_tables(source)
    assert caught.value is error
    assert caught.value.__context__ is None


def test_present_but_malformed_optional_presentation_channel_fails(
    tmp_path: Path,
    build_text_bundle: Callable[[Path], dict[str, Any]],
    declare_common: Callable[[Path], dict[str, str]],
) -> None:
    root = tmp_path / "source"
    _minimal_names_root(root)
    declare_common(root)
    (root / "conventions").mkdir()
    (root / "conventions" / "text-conventions.yaml").write_text("presentation: [", encoding="utf-8")
    (root / "schemas" / "text-conventions.schema.json").parent.mkdir(exist_ok=True)
    (root / "schemas" / "text-conventions.schema.json").write_text("{}", encoding="utf-8")
    with pytest.raises(yaml.YAMLError):
        load_conventions(root)
    with pytest.raises(yaml.YAMLError):
        build_text_bundle(root)


def test_present_conventions_directory_missing_source_fails(
    tmp_path: Path,
    build_text_bundle: Callable[[Path], dict[str, Any]],
    declare_common: Callable[[Path], dict[str, str]],
) -> None:
    root = tmp_path / "source"
    _minimal_names_root(root)
    declare_common(root)
    (root / "conventions").mkdir()
    with pytest.raises(FileNotFoundError):
        load_conventions(root)
    with pytest.raises(FileNotFoundError):
        build_text_bundle(root)


def test_canonical_presentation_channel_missing_source_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    build_text_bundle: Callable[[Path], dict[str, Any]],
    declare_common: Callable[[Path], dict[str, str]],
) -> None:
    root = tmp_path / "canonical"
    _minimal_names_root(root)
    declare_common(root)
    monkeypatch.setattr(text_module, "_assets", lambda: root)
    with pytest.raises(FileNotFoundError):
        build_text_bundle(root)
