# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""The git steps of release-on-merge (scripts/release-publish.sh) against a local origin.

A release run that prepared a version but could not push its tag (the push timed out) left main
holding "Prepare methodology v0.4.219" and no tag, so every later run selected 0.4.219 again, found
nothing to commit and failed. The steps now finish that release: commit only a change, push main
only when it lacks the commit, and retry the tag push.
"""

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "release-publish.sh"
ENVIRONMENT = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
    "RELEASE_RETRY_SLEEP": "0",
}


def git(directory: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=directory, env=ENVIRONMENT, text=True, capture_output=True, check=True
    )
    return result.stdout.strip()


def step(directory: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), *args], cwd=directory, env=ENVIRONMENT, text=True, capture_output=True
    )


def commit_version(directory: Path, version: str) -> None:
    (directory / "pyproject.toml").write_text(f'version = "{version}"\n', encoding="utf-8")
    (directory / "uv.lock").write_text(f"locked {version}\n", encoding="utf-8")
    git(directory, "add", "pyproject.toml", "uv.lock")
    git(directory, "commit", "-m", f"Prepare methodology v{version}")


def merge_content(directory: Path, name: str) -> None:
    (directory / f"{name}.yaml").write_text(name, encoding="utf-8")
    git(directory, "add", f"{name}.yaml")
    git(directory, "commit", "-m", name)


@pytest.fixture
def repository(tmp_path: Path) -> tuple[Path, Path]:
    """An origin and a clone of it, on main at v0.4.218 (tagged) with one content merge after it."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True, capture_output=True)
    git(clone, "checkout", "-q", "-b", "main")
    commit_version(clone, "0.4.218")
    git(clone, "tag", "-a", "v0.4.218", "-m", "Release v0.4.218")
    merge_content(clone, "first")
    git(clone, "push", "-q", "origin", "main", "--tags")
    return origin, clone


def test_a_new_version_is_committed_pushed_and_tagged(repository: tuple[Path, Path]) -> None:
    origin, clone = repository
    (clone / "pyproject.toml").write_text('version = "0.4.219"\n', encoding="utf-8")
    assert step(clone, "prepare", "0.4.219").returncode == 0
    assert git(clone, "log", "-1", "--format=%s") == "Prepare methodology v0.4.219"
    assert step(clone, "publish", "v0.4.219").returncode == 0
    assert git(origin, "rev-parse", "main") == git(clone, "rev-parse", "HEAD")
    assert git(origin, "rev-parse", "v0.4.219^{commit}") == git(clone, "rev-parse", "HEAD")


def test_a_version_prepared_by_an_earlier_run_is_only_tagged(repository: tuple[Path, Path]) -> None:
    """Today's sequence: the earlier run's bump is on main, its tag push timed out, a later merge
    followed, and this run selects the same version again."""
    origin, clone = repository
    commit_version(clone, "0.4.219")
    git(clone, "push", "-q", "origin", "main")
    merge_content(clone, "second")
    git(clone, "push", "-q", "origin", "main")
    main = git(origin, "rev-parse", "main")
    (clone / "pyproject.toml").write_text('version = "0.4.219"\n', encoding="utf-8")
    prepared = step(clone, "prepare", "0.4.219")
    assert prepared.returncode == 0, prepared.stderr
    assert git(clone, "rev-parse", "HEAD") == main, "nothing to commit: HEAD stays the merge"
    published = step(clone, "publish", "v0.4.219")
    assert published.returncode == 0, published.stderr
    assert git(origin, "rev-parse", "main") == main, "main is not pushed again"
    assert git(origin, "rev-parse", "v0.4.219^{commit}") == main


def test_a_tag_push_that_fails_is_retried(repository: tuple[Path, Path]) -> None:
    origin, clone = repository
    counter = origin / "tag-pushes"
    hook = origin / "hooks" / "pre-receive"
    git(origin, "config", "core.hooksPath", str(origin / "hooks"))
    hook.write_text(
        "#!/usr/bin/env bash\n"
        f'if grep -q refs/tags/ - && [ ! -e "{counter}" ]; then : > "{counter}"; exit 1; fi\n',
        encoding="utf-8",
    )
    hook.chmod(0o755)
    commit_version(clone, "0.4.219")
    assert step(clone, "publish", "v0.4.219").returncode == 0
    assert counter.exists(), "the first tag push was refused"
    assert git(origin, "rev-parse", "v0.4.219^{commit}") == git(clone, "rev-parse", "HEAD")


def test_a_tag_that_already_exists_stops_the_release(repository: tuple[Path, Path]) -> None:
    _, clone = repository
    result = step(clone, "publish", "v0.4.218")
    assert result.returncode == 1
    assert "already" in result.stdout + result.stderr or "appeared" in result.stdout + result.stderr
