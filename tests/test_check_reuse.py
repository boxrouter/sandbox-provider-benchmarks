# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""REUSE checks inspect added and tracked files that exist in the working tree."""

import runpy
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[1]
working_tree_files = cast(
    Callable[[Path], list[Path]],
    runpy.run_path(str(ROOT / "scripts" / "check_reuse.py"))["working_tree_files"],
)


def test_working_tree_files_skips_deleted_tracked_paths_and_includes_new_files(
    tmp_path: Path,
) -> None:
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    present = tmp_path / "present.txt"
    deleted = tmp_path / "deleted.txt"
    added = tmp_path / "added.txt"
    present.write_text("still here\n", encoding="utf-8")
    deleted.write_text("removed\n", encoding="utf-8")
    subprocess.run(["git", "add", "present.txt", "deleted.txt"], cwd=tmp_path, check=True)
    deleted.unlink()
    added.write_text("new file\n", encoding="utf-8")

    assert set(working_tree_files(tmp_path)) == {present, added}
