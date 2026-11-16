# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""A fact has one owner: bodies stated once, roots read from the conventions, names by reference."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from sandbox_benchmark_verifier import chart_bindings as bindings_module
from sandbox_benchmark_verifier.chart_bindings import chart_bindings
from sandbox_benchmark_verifier.content import CONVENTION_ROOTS, content_criteria
from sandbox_benchmark_verifier.names import load_tables
from sandbox_benchmark_verifier.yaml_load import load_yaml_all

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]


def test_a_chart_binding_is_stated_once_per_document() -> None:
    source = json.loads((ROOT / "contracts/text/chart-bindings.json").read_text(encoding="utf-8"))
    assert len({entry["document"] for entry in source["bindings"]}) == len(source["bindings"])
    for entry in chart_bindings()["bindings"]:
        assert {"document", "charts"} <= set(entry)
        assert "versions" not in entry and "version" not in entry


def test_a_document_bound_twice_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = tmp_path / "contracts/text"
    folder.mkdir(parents=True)
    body: dict[str, dict[str, str]] = {"charts": {}, "derived_metrics": {}}
    (folder / "chart-bindings.json").write_text(
        json.dumps(
            {"schema": "x", "bindings": [{"document": "d", **body}, {"document": "d", **body}]}
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(bindings_module, "_assets", lambda: tmp_path)
    with pytest.raises(ValueError, match="declared twice"):
        chart_bindings()


def test_the_criteria_roots_the_conventions_own_are_not_restated() -> None:
    legacy = json.loads((ROOT / "contracts/text/1.0.0.json").read_text(encoding="utf-8"))
    criteria = content_criteria()
    for root in CONVENTION_ROOTS:
        assert root not in legacy
        assert criteria[root] == criteria["conventions"][root]


def test_median_and_mean_labels_are_the_registry_names() -> None:
    names = {row["id"]: row["name"] for row in load_tables().names if row["kind"] == "statistic"}
    criteria = content_criteria()
    page = criteria["methodology_page"]
    for statistic in ("median", "mean"):
        assert page["field_labels"][statistic] == names[statistic]
        assert page["enum_labels"][statistic] == names[statistic]
    text = (ROOT / "contracts/text/methodology-page.json").read_text(encoding="utf-8")
    assert '"median": "Median"' not in text
    assert '"mean": "Mean"' not in text
    assert '"name": "Median"' not in text


def test_every_yaml_file_is_parsed_by_the_one_loader() -> None:
    loader = "src/sandbox_benchmark_verifier/yaml_load.py"
    bypasses = sorted(
        f"{path.relative_to(ROOT)}:{number}"
        for path in (ROOT / "src/sandbox_benchmark_verifier").glob("*.py")
        if path.relative_to(ROOT).as_posix() != loader
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if re.search(r"\byaml\.(?:safe_load|safe_load_all|load|load_all)\(", line)
    )
    assert bypasses == []


def test_a_methodology_document_has_no_version() -> None:
    """One current methodology (founder, 2026-11-09): a document is found by its id alone."""
    versioned = [
        f"{path.relative_to(ROOT)}: {document.get('id')}"
        for path in sorted((ROOT / "methodology").rglob("*.yaml"))
        for document in load_yaml_all(path.read_text(encoding="utf-8"))
        if isinstance(document, dict)
        and ("version" in document or isinstance(document.get("inherits"), dict))
    ]
    assert versioned == []
    assert not [
        p for p in (ROOT / "methodology").rglob("*") if re.search(r"-\d+\.\d+\.\d+", p.name)
    ]
    assert not [
        p
        for p in (ROOT / "parameters").glob("*/*")
        if p.is_dir() and re.fullmatch(r"\d+\.\d+\.\d+", p.name)
    ]
