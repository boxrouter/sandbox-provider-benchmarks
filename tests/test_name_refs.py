# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Registry references survive renames and membership changes across reader surfaces."""

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from sandbox_benchmark_verifier.name_refs import (
    canonical_name_map,
    name_reference_errors,
    name_reference_patterns,
    published_name_errors,
    retired_name_references,
    source_name_errors,
)
from sandbox_benchmark_verifier.names import _methodology_documents, load_tables
from sandbox_benchmark_verifier.placeholders import PlaceholderError, resolve_template
from sandbox_benchmark_verifier.versions import version_key

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.unit


def test_registry_names_require_references_and_retired_names_fail_after_resolution() -> None:
    names = canonical_name_map(load_tables(ROOT))
    for key in ("test.deepswe", "test.r2e-gym", "test.ci-jobs", "index.time-index"):
        assert name_reference_errors(names[key], root=ROOT)
        assert name_reference_errors("{name:" + key + "}", root=ROOT) == []
        assert name_reference_errors(names[key], root=ROOT, resolved=True) == []
    patterns = name_reference_patterns(ROOT)
    for previous in retired_name_references(ROOT):
        assert name_reference_errors(previous, patterns=patterns, resolved=True) == [
            f"retired display name {previous!r}"
        ]


def test_workload_list_tracks_both_names_and_own_scope_membership() -> None:
    documents: dict[str, Any] = {
        "composite": {
            "composition": {
                "inputs": [{"id": "a"}, {"id": "b"}],
                "scopes": [{"id": "selected", "inputs": ["b"]}],
            }
        }
    }
    names = {"document.a": "First", "document.b": "Second"}
    assert (
        resolve_template("{workloads:composite}", names=names, documents=documents)
        == "First and Second"
    )
    names["document.b"] = "Renamed"
    documents["composite"]["composition"]["inputs"].reverse()
    assert (
        resolve_template("{workloads:composite}", names=names, documents=documents)
        == "Renamed and First"
    )
    assert (
        resolve_template("{workloads:composite.selected}", names=names, documents=documents)
        == "Renamed"
    )
    for target in ("missing", "composite.missing"):
        with pytest.raises(PlaceholderError):
            resolve_template("{workloads:" + target + "}", names=names, documents=documents)
    documents["composite"]["composition"]["inputs"] = []
    with pytest.raises(PlaceholderError, match="empty composite scope"):
        resolve_template("{workloads:composite}", names=names, documents=documents)


def test_owner_sources_and_current_reader_projection_pass_name_checks(
    bundle: dict[str, Any],
) -> None:
    assert source_name_errors(ROOT) == []
    assert published_name_errors(bundle, ROOT) == []
    text = bundle["content_criteria"]["index_copy"]["details"]["time"]["what_this_measures_concise"]
    assert "Terminal-Bench 4.0 and CI Actions" in text
    scope = bundle["methodologies"]["cost-index"]["1.1.27"]["reader_definition"]["composition"][
        "scopes"
    ][1]
    assert scope["label"] == "Coding"


def test_no_scope_label_repeats_the_names_of_the_list_under_it(bundle: dict[str, Any]) -> None:
    """ "Execution (DeepSWE, Terminal-Bench 4.0 and CI Actions)" sat above the same three names."""
    members = {row["name"] for row in bundle["names"] if row["kind"] == "test"}
    seen = 0
    for versions in bundle["methodologies"].values():
        for entry in versions.values():
            for scope in entry["reader_definition"].get("composition", {}).get("scopes", []):
                seen += 1
                assert not [name for name in members if name in scope["label"]], scope["label"]
    assert seen


def test_contract_literal_is_rejected_without_a_hardcoded_blocklist(tmp_path: Path) -> None:
    shutil.copytree(ROOT / "names", tmp_path / "names")
    shutil.copytree(ROOT / "contracts", tmp_path / "contracts")
    shutil.copytree(ROOT / "schemas", tmp_path / "schemas")
    path = tmp_path / "contracts/text/index-copy.json"
    names = canonical_name_map(load_tables(ROOT))
    path.write_text(json.dumps({"description": names["test.ci-jobs"]}), encoding="utf-8")
    assert any("canonical name" in error for error in source_name_errors(tmp_path))
    previous = next(iter(retired_name_references(ROOT)))
    path.write_text(json.dumps({"description": previous}), encoding="utf-8")
    assert any("retired display name" in error for error in source_name_errors(tmp_path))


def test_retired_names_fail_in_contract_metadata_outside_reader_copy(tmp_path: Path) -> None:
    shutil.copytree(ROOT / "names", tmp_path / "names")
    shutil.copytree(ROOT / "schemas", tmp_path / "schemas")
    path = tmp_path / "contracts/nested/policy.yaml"
    path.parent.mkdir(parents=True)
    previous = next(iter(retired_name_references(ROOT)))
    path.write_text(json.dumps({"metadata": {"value": previous}}))
    assert any("retired display name" in error for error in source_name_errors(tmp_path))


def test_the_names_table_gives_every_composite_member_a_unit() -> None:

    names = canonical_name_map(load_tables(ROOT))
    assert [
        names[f"unit.{document}"]
        for document in ("deepswe", "terminal-bench-4-0", "r2e-gym", "github-actions")
    ] == ["trace", "trace", "task", "job"]


def test_a_composite_input_is_named_by_its_current_document_id() -> None:
    """The newest composites pin `deepswe`, `terminal-bench-4-0` and `github-actions`, which name
    the same workloads and units as their retired ids."""
    names = canonical_name_map(load_tables(ROOT))
    assert [
        names[f"unit.{current}"] for current in ("deepswe", "terminal-bench-4-0", "github-actions")
    ] == [
        "trace",
        "trace",
        "job",
    ]


def test_composite_subtitles_are_the_template_with_the_members_units(
    bundle: dict[str, Any],
) -> None:

    names = canonical_name_map(load_tables(ROOT))
    index = max(
        (document for document in _methodology_documents(ROOT) if document["id"] == "time-index"),
        key=lambda document: version_key(document["version"]),
    )
    units = [names[f"unit.{member['id']}"] for member in index["composition"]["inputs"]]
    units = list(dict.fromkeys(units))
    joined = ", ".join(units[:-1]) + " or " + units[-1]
    descriptions = bundle["content_criteria"]["index_copy"]["descriptions"]
    assert descriptions["time"] == f"A time statistic is aggregated from each workload's {joined}."
    assert descriptions["cost"] == f"A cost statistic is aggregated from each workload's {joined}."
    # Only the locked time-vs-cost versions read this one; no chart draws it.
    assert descriptions["time_vs_cost"] == "Composite time vs Composite cost"
