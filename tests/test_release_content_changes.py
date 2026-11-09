# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Tests for automatic release source detection."""

import runpy
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

MODULE: dict[str, Any] = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "scripts" / "release_content_changes.py")
)
package_source_roots = cast(Callable[[str], frozenset[str]], MODULE["package_source_roots"])
changes_released_content = cast(
    Callable[[set[str], frozenset[str], frozenset[str]], bool],
    MODULE["changes_released_content"],
)
release_changes = cast(Callable[[str], tuple[bool, str, set[str]]], MODULE["release_changes"])


PYPROJECT = """
[tool.hatch.build.targets.wheel]
packages = ["src/sandbox_benchmark_verifier"]

[tool.hatch.build.targets.wheel.force-include]
methodology = "sandbox_benchmark_verifier/_assets/methodology"
reader = "sandbox_benchmark_verifier/_assets/reader"
"""


def test_package_roots_follow_wheel_configuration() -> None:
    assert package_source_roots(PYPROJECT) == frozenset(
        {"src/sandbox_benchmark_verifier", "methodology", "reader"}
    )


def test_documentation_ci_and_test_changes_do_not_release() -> None:
    roots = package_source_roots(PYPROJECT)
    assert not changes_released_content(
        {
            "README.md",
            "docs/RUNBOOK.md",
            ".github/workflows/release-on-merge.yml",
            "tests/test_x.py",
        },
        roots,
        roots,
    )


def test_changes_to_packaged_methodology_reader_or_code_release() -> None:
    roots = package_source_roots(PYPROJECT)
    for changed_path in (
        "methodology/released.lock",
        "reader/methods.yaml",
        "src/sandbox_benchmark_verifier/catalog.py",
    ):
        assert changes_released_content({changed_path}, roots, roots)


def test_package_source_configuration_change_releases() -> None:
    before = frozenset({"src/sandbox_benchmark_verifier"})
    after = before | {"reader"}
    assert changes_released_content(set(), before, after)


def test_directory_prefix_does_not_match_a_sibling_path() -> None:
    roots = frozenset({"src/sandbox_benchmark_verifier"})
    assert not changes_released_content(
        {"src/sandbox_benchmark_verifier_old/catalog.py"}, roots, roots
    )


def test_prepare_tag_need_not_be_ancestor_of_squash_merged_main(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def git(*arguments: str) -> None:
        subprocess.run(
            ["git", *arguments], cwd=tmp_path, check=True, capture_output=True, text=True
        )

    git("init", "--quiet", "--initial-branch=main")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.com")
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nversion = "0.4.119"\n' + PYPROJECT)
    reader = tmp_path / "reader" / "methods.yaml"
    reader.parent.mkdir()
    reader.write_text("methods: []\n")
    git("add", "pyproject.toml", "reader/methods.yaml")
    git("commit", "--quiet", "-m", "release content")

    git("switch", "--quiet", "-c", "prepare")
    pyproject.write_text('[project]\nversion = "0.4.120"\n' + PYPROJECT)
    git("add", "pyproject.toml")
    git("commit", "--quiet", "-m", "Prepare methodology v0.4.120")
    git("tag", "v0.4.120")

    git("switch", "--quiet", "main")
    reader.write_text("methods: [] # packaged source change\n")
    git("add", "reader/methods.yaml")
    git("commit", "--quiet", "-m", "squash merged source change")

    # release_changes runs git in the current directory: point it at this repository, not ours.
    monkeypatch.chdir(tmp_path)
    changed, tag, paths = release_changes("main")

    assert changed
    assert tag == "v0.4.120"
    assert "reader/methods.yaml" in paths
