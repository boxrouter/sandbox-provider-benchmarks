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

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]


def test_a_chart_binding_body_is_stated_once_and_expands_per_version() -> None:
    source = json.loads((ROOT / "contracts/text/chart-bindings.json").read_text(encoding="utf-8"))
    assert len({entry["document"] for entry in source["bindings"]}) == len(source["bindings"])
    expanded = chart_bindings()["bindings"]
    listed = sum(len(entry["versions"]) for entry in source["bindings"])
    assert len(expanded) == listed
    for entry in expanded:
        assert {"document", "version", "charts"} <= set(entry)


def test_a_version_listed_twice_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = tmp_path / "contracts/text"
    folder.mkdir(parents=True)
    body: dict[str, dict[str, str]] = {"charts": {}, "derived_metrics": {}}
    (folder / "chart-bindings.json").write_text(
        json.dumps(
            {
                "schema": "x",
                "bindings": [
                    {"document": "d", "versions": ["1.0.0"], **body},
                    {"document": "d", "versions": ["1.0.0"], **body},
                ],
            }
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
    assert criteria["shared_concepts"]["median"]["name"] == names["median"]
    for path in ("methodology-page.json", "shared-concepts.json"):
        text = (ROOT / "contracts/text" / path).read_text(encoding="utf-8")
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


def test_versions_are_compared_as_numbers_not_as_text() -> None:
    """As text, 1.1.10 sorts before 1.1.4, so a check that compared the version string with "1.1.4"
    skipped the newest read-4kib version. Versions are compared through `version_key`."""
    dotted = r"""["']\d+\.\d+\.\d+["']"""
    assert _python_lines(re.compile(rf"[<>]=?\s*{dotted}|{dotted}\s*[<>]")) == []


def test_a_dotted_version_is_parsed_only_by_version_key() -> None:
    """More than forty hand-written parsers (split on dots into ints, or digits read by regex)
    had grown in src and tests; versions.version_key is the one."""
    parser = re.compile(
        r'int\(\w+\) for \w+ in .*\.split\("\."\)|map\(int, .*\.split\("\."\)|findall\(r"\\d\+"'
    )
    assert _python_lines(parser, skip="src/sandbox_benchmark_verifier/versions.py") == []


def _python_lines(pattern: re.Pattern[str], skip: str = "") -> list[str]:
    """Each line of the repository's Python that the pattern matches, as path:line."""
    return sorted(
        f"{path.relative_to(ROOT)}:{number}"
        for directory in ("src", "tests", "scripts")
        for path in (ROOT / directory).rglob("*.py")
        if path.relative_to(ROOT).as_posix() != skip
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if pattern.search(line)
    )
