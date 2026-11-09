# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Check REUSE on tracked and added files from one repository-root project."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import tomllib
from pathlib import Path
from tempfile import TemporaryDirectory


def working_tree_files(root: Path) -> list[Path]:
    tracked = subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
        ],
        check=True,
        capture_output=True,
    ).stdout.split(b"\0")
    files = [
        path
        for raw in tracked
        if raw
        if (path := root / raw.decode()).exists() or path.is_symlink()
    ]
    return files


def component_files(root: Path) -> dict[Path, list[Path]]:
    files = working_tree_files(root)
    scopes = {root}
    for path in files:
        if path.name == "pyproject.toml" and tomllib.loads(path.read_text()).get("project"):
            scopes.add(path.parent)
    groups: dict[Path, list[Path]] = {scope: [] for scope in scopes}
    for path in files:
        scope = max(
            (scope for scope in scopes if path.is_relative_to(scope)),
            key=lambda scope: len(scope.parts),
        )
        groups[scope].append(path)
    return groups


def check_reuse(root: Path) -> None:
    with TemporaryDirectory(prefix="reuse-project-") as temporary:
        candidate = Path(temporary)
        for path in working_tree_files(root):
            target = candidate / path.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            if path.is_symlink():
                target.symlink_to(os.readlink(path))
            else:
                shutil.copy2(path, target)
        print("REUSE project: .", flush=True)
        result = subprocess.run(["reuse", "--root", str(candidate), "lint"], check=False)
        if result.returncode:
            raise SystemExit(result.returncode)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    check_reuse(args.root.resolve())
