# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Focused traversal and compatibility checks for the duplicate-table scan."""

import glob
import os
from pathlib import Path
from typing import Any

import pytest

from sandbox_benchmark_verifier.names import _duplicate_copy_errors

pytestmark = pytest.mark.unit


def test_nested_duplicates_keep_path_order_and_exact_messages(tmp_path: Path) -> None:
    (tmp_path / "z").mkdir()
    (tmp_path / "a").mkdir()
    for relative in (
        "z/names.yaml",
        "a/aliases.yaml",
        "z/aliases.json",
        "a/names.yml",
    ):
        (tmp_path / relative).write_text("table\n", encoding="utf-8")

    assert _duplicate_copy_errors(tmp_path) == [
        "a/aliases.yaml: second copy of the name or alias table",
        "a/names.yml: second copy of the name or alias table",
        "z/aliases.json: second copy of the name or alias table",
        "z/names.yaml: second copy of the name or alias table",
    ]


def test_direct_owner_tables_and_schema_files_are_accepted(tmp_path: Path) -> None:
    owner = tmp_path / "names"
    owner.mkdir()
    (owner / "names.yaml").write_text("owner\n", encoding="utf-8")
    (owner / "aliases.yaml").write_text("owner\n", encoding="utf-8")
    (tmp_path / "names.schema.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / "nested.schema.json").write_text("{}\n", encoding="utf-8")

    assert _duplicate_copy_errors(tmp_path) == []


def test_ignored_directories_are_pruned_before_scandir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ignored_names = {".git", ".venv", "node_modules", "__pycache__"}
    ignored_roots = [tmp_path / name for name in ignored_names]
    for ignored_root in ignored_roots:
        nested = ignored_root / "nested"
        nested.mkdir(parents=True)
        (nested / "names.yaml").write_text("ignored\n", encoding="utf-8")

    real_scandir = os.scandir

    def guarded_scandir(path: str | os.PathLike[str] = ".") -> Any:
        scanned = Path(path)
        if any(scanned == ignored or ignored in scanned.parents for ignored in ignored_roots):
            raise AssertionError(f"scanned inside ignored directory: {scanned}")
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", guarded_scandir)
    globber = getattr(glob, "_StringGlobber", None)
    if globber is not None:
        monkeypatch.setattr(globber, "scandir", staticmethod(guarded_scandir))

    assert _duplicate_copy_errors(tmp_path) == []


def test_directory_symlinks_are_not_followed(tmp_path: Path) -> None:
    root = tmp_path / "checkout"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "names.yaml").write_text("outside\n", encoding="utf-8")
    link = root / "linked"
    link.symlink_to(outside, target_is_directory=True)

    assert _duplicate_copy_errors(root) == []
