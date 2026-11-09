# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCANNER = ROOT / "scripts/scan-public-text-blocklist.py"


def test_empty_blocklist_fails() -> None:
    env = {key: value for key, value in os.environ.items() if key != "PUBLIC_TEXT_BLOCKLIST"}
    env["CI"] = "1"
    result = subprocess.run(
        [sys.executable, str(SCANNER)], env=env, capture_output=True, text=True, check=False
    )
    assert result.returncode != 0
    assert "PUBLIC_TEXT_BLOCKLIST" in result.stderr


def test_blocked_wheel_content_fails(tmp_path: Path) -> None:
    wheel = tmp_path / "fixture.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("fixture/data.txt", "blocked-demo-term")
    result = subprocess.run(
        [sys.executable, str(SCANNER), str(wheel)],
        env={**os.environ, "PUBLIC_TEXT_BLOCKLIST": "blocked-demo-term"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "fixture.whl:fixture/data.txt" in result.stderr


def test_history_mode_scans_commit_content(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    author = ["-c", "user.name=Example Maintainer", "-c", "user.email=example@example.invalid"]
    subprocess.run(
        ["git", *author, "commit", "--allow-empty", "-m", "initial"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    (tmp_path / "fixture.txt").write_text("blocked-demo-term\n", encoding="utf-8")
    subprocess.run(["git", "add", "fixture.txt"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", *author, "commit", "-m", "sample"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    result = subprocess.run(
        [sys.executable, str(SCANNER), "--history"],
        cwd=tmp_path,
        env={**os.environ, "PUBLIC_TEXT_BLOCKLIST": "blocked-demo-term"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "fixture.txt" in result.stderr


def _history_repo(tmp_path: Path) -> tuple[Path, list[str]]:
    author = ["-c", "user.name=Example Maintainer", "-c", "user.email=example@example.invalid"]

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *author, *args], cwd=tmp_path, check=True, capture_output=True, text=True
        ).stdout.strip()

    git("init", "--quiet")
    commits = []
    for name in ("one", "two"):
        (tmp_path / f"{name}.txt").write_text("blocked-demo-term\n", encoding="utf-8")
        git("add", f"{name}.txt")
        git("commit", "-m", name, "--quiet")
        commits.append(git("rev-parse", "HEAD"))
    return tmp_path, commits


def _scan_history(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCANNER), "--root", str(root), "--history"],
        env={**os.environ, "PUBLIC_TEXT_BLOCKLIST": "blocked-demo-term"},
        capture_output=True,
        text=True,
        check=False,
    )


def test_missing_excluded_accepted_commits_file_scans_all_history(
    tmp_path: Path,
) -> None:
    root, commits = _history_repo(tmp_path)

    assert not (root / "public-text-accepted-commits.json").exists()
    result = _scan_history(root)

    assert result.returncode == 1
    assert all(commit in result.stderr for commit in commits)


def test_an_accepted_commit_is_skipped_and_any_other_commit_is_still_caught(
    tmp_path: Path,
) -> None:
    root, (first, second) = _history_repo(tmp_path)

    (root / "public-text-accepted-commits.json").write_text(
        json.dumps({"accepted": [{"commit": first, "date": "2026-10-30", "reason": "fixture"}]}),
        encoding="utf-8",
    )
    result = _scan_history(root)
    assert result.returncode == 1
    assert second in result.stderr
    assert first not in result.stderr


def test_an_accepted_commit_must_be_a_full_id_with_a_date_and_a_reason(tmp_path: Path) -> None:

    root, (first, _second) = _history_repo(tmp_path)
    for entry in (
        {"commit": first[:12], "date": "2026-10-30", "reason": "short id"},
        {"commit": first, "date": "2026-10-30"},
        {"commit": "*", "date": "2026-10-30", "reason": "glob"},
    ):
        (root / "public-text-accepted-commits.json").write_text(
            json.dumps({"accepted": [entry]}), encoding="utf-8"
        )
        result = _scan_history(root)
        assert result.returncode != 0
        assert "requires a full commit id" in result.stderr


def test_history_accepts_three_full_fixture_commit_ids(tmp_path: Path) -> None:
    root, commits = _history_repo(tmp_path)
    author = ["-c", "user.name=Example Maintainer", "-c", "user.email=example@example.invalid"]
    (root / "three.txt").write_text("blocked-demo-term\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "three.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(root), *author, "commit", "-m", "three", "--quiet"],
        check=True,
        capture_output=True,
    )
    commits.append(
        subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    accepted = [
        {"commit": commit, "date": "2026-01-01", "reason": "synthetic scanner fixture"}
        for commit in commits
    ]
    (root / "public-text-accepted-commits.json").write_text(
        json.dumps({"accepted": accepted}), encoding="utf-8"
    )

    result = _scan_history(root)

    assert len(accepted) == 3
    assert result.returncode == 1
    assert all(commit not in result.stderr for commit in commits)


def _no_blocklist_env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key != "PUBLIC_TEXT_BLOCKLIST"}
    env["CI"] = "1"
    return env


def test_unmarked_root_still_fails_closed(tmp_path: Path) -> None:
    """The gate refuses to run without a configured blocklist."""
    result = subprocess.run(
        [sys.executable, str(SCANNER), "--root", str(tmp_path)],
        env=_no_blocklist_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "PUBLIC_TEXT_BLOCKLIST" in result.stderr


def test_retired_image_name_fails_in_built_artifact(tmp_path: Path) -> None:
    artifact = tmp_path / "candidate.whl"
    retired_image = "ghcr.io/boxrouter/standard-image" + "-private"
    with zipfile.ZipFile(artifact, "w") as archive:
        archive.writestr("package/METADATA", retired_image)
    result = subprocess.run(
        [sys.executable, str(SCANNER), "--artifacts-only", str(artifact)],
        env={**os.environ, "PUBLIC_TEXT_BLOCKLIST": "unrelated-test-sentinel"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "candidate.whl:package/METADATA" in result.stderr
