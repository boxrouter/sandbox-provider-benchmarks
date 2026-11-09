# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Detect changes to source files included in the verifier wheel."""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import PurePosixPath
from typing import Any


def package_source_roots(pyproject: str) -> frozenset[str]:
    """Return the wheel's source roots from its build configuration."""
    document: dict[str, Any] = tomllib.loads(pyproject)
    try:
        wheel = document["tool"]["hatch"]["build"]["targets"]["wheel"]
        package_roots = wheel["packages"]
        forced_sources = wheel["force-include"].keys()
    except (KeyError, AttributeError, TypeError) as error:
        raise ValueError("pyproject.toml has no readable wheel source configuration") from error

    if not isinstance(package_roots, list) or not all(
        isinstance(root, str) for root in package_roots
    ):
        raise ValueError("wheel packages must be a list of source paths")

    roots = {*package_roots, *forced_sources}
    normalized: set[str] = set()
    for root in roots:
        path = PurePosixPath(root)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise ValueError(f"invalid wheel source path: {root!r}")
        normalized.add(path.as_posix().rstrip("/"))
    if not normalized:
        raise ValueError("wheel source configuration contains no source paths")
    return frozenset(normalized)


def changes_released_content(
    changed_paths: set[str], before_roots: frozenset[str], after_roots: frozenset[str]
) -> bool:
    """Report whether package roots or files included by them changed."""
    if before_roots != after_roots:
        return True
    roots = before_roots | after_roots
    return any(
        path == root or path.startswith(f"{root}/") for path in changed_paths for root in roots
    )


def git_text(*arguments: str) -> str:
    """Return text from a checked git command."""
    return subprocess.run(["git", *arguments], check=True, capture_output=True, text=True).stdout


def latest_stable_tag() -> str:
    """Return the highest existing stable vMAJOR.MINOR.PATCH tag."""
    pattern = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
    versions: list[tuple[tuple[int, int, int], str]] = []
    for tag in git_text("tag", "--list", "v*").splitlines():
        match = pattern.fullmatch(tag)
        if match:
            version = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
            versions.append((version, tag))
    if not versions:
        raise ValueError("no stable vMAJOR.MINOR.PATCH release tag exists")
    return max(versions)[1]


def release_changes(head: str) -> tuple[bool, str, set[str]]:
    """Compare packaged wheel inputs at HEAD with the latest stable release tree."""
    tag = latest_stable_tag()
    before = git_text("show", f"{tag}:pyproject.toml")
    after = git_text("show", f"{head}:pyproject.toml")
    before_roots = package_source_roots(before)
    after_roots = package_source_roots(after)
    raw_paths = subprocess.run(
        ["git", "diff", "--name-only", "-z", tag, head],
        check=True,
        capture_output=True,
    ).stdout
    changed_paths = {path.decode("utf-8") for path in raw_paths.split(b"\0") if path}
    changed = changes_released_content(changed_paths, before_roots, after_roots)
    return changed, tag, changed_paths


def main() -> int:
    """Write GitHub Actions outputs for the latest release-content comparison."""
    head = sys.argv[1] if len(sys.argv) == 2 else "HEAD"
    if len(sys.argv) > 2:
        print("usage: release_content_changes.py [git-ref]", file=sys.stderr)
        return 2
    try:
        changed, tag, paths = release_changes(head)
    except (OSError, subprocess.CalledProcessError, UnicodeError, ValueError) as error:
        print(f"could not determine release source changes: {error}", file=sys.stderr)
        return 2

    print(f"release={'true' if changed else 'false'}")
    print(f"latest_tag={tag}")
    print(f"compared_paths={len(paths)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
