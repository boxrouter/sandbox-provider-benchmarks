#!/usr/bin/env python3
# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Scan tracked text, archives, and Git history using the configured blocklist."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path


def configured_blocklist() -> str:
    """The blocklist from the environment (CI), else the repository variable through gh."""
    value = os.environ.get("PUBLIC_TEXT_BLOCKLIST", "")
    if value or os.environ.get("CI"):
        return value
    public_name = "boxrouter/sandbox-provider-benchmarks"
    for repository in (public_name, f"{public_name}-private"):
        result = subprocess.run(
            [
                "gh",
                "variable",
                "get",
                "PUBLIC_TEXT_BLOCKLIST",
                "-R",
                repository,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout
    return ""


def patterns() -> tuple[re.Pattern[str], ...]:
    values = [line.strip() for line in configured_blocklist().splitlines()]
    if not values or any(not value for value in values):
        raise SystemExit("PUBLIC_TEXT_BLOCKLIST is required and must not be empty")
    configured = tuple(
        re.compile(value[3:], re.IGNORECASE)
        if value.startswith("re:")
        else re.compile(re.escape(value), re.I)
        for value in values
    )
    retired_image_name = "standard-image" + "-private"
    return configured + (re.compile(re.escape(retired_image_name), re.IGNORECASE),)


def find(label: str, text: str, blocked: tuple[re.Pattern[str], ...]) -> list[str]:
    joined = f"{label}\n{text}"
    return [
        f"{label}: blocked rule {index}"
        for index, pattern in enumerate(blocked, start=1)
        if pattern.search(joined)
    ]


def read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None


def scan_archive(path: Path, blocked: tuple[re.Pattern[str], ...]) -> list[str]:
    failures: list[str] = []
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                try:
                    content = archive.read(name).decode("utf-8")
                except UnicodeError:
                    continue
                failures.extend(find(f"{path.name}:{name}", content, blocked))
    elif tarfile.is_tarfile(path):
        with tarfile.open(path) as archive:
            for member in archive.getmembers():
                stream = archive.extractfile(member)
                if stream is not None:
                    try:
                        content = stream.read().decode("utf-8")
                    except UnicodeError:
                        continue
                    failures.extend(find(f"{path.name}:{member.name}", content, blocked))
    else:
        text = read(path)
        if text is not None:
            failures.extend(find(path.as_posix(), text, blocked))
    return failures


ACCEPTED_COMMITS = "public-text-accepted-commits.json"


def accepted_commits(root: Path) -> frozenset[str]:
    """Full commit ids whose history is accepted, each with a date and reason; matched exactly."""
    path = root / ACCEPTED_COMMITS
    if not path.exists():
        return frozenset()
    try:
        entries = json.loads(path.read_text(encoding="utf-8")).get("accepted")
    except (OSError, UnicodeError, json.JSONDecodeError, AttributeError) as error:
        raise SystemExit(f"could not read accepted commits: {path}") from error
    if not isinstance(entries, list):
        raise SystemExit("accepted commits requires an accepted list")
    commits: set[str] = set()
    for entry in entries:
        commit = entry.get("commit") if isinstance(entry, dict) else None
        if (
            not isinstance(commit, str)
            or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit)
            or not all(isinstance(entry.get(key), str) and entry[key] for key in ("date", "reason"))
        ):
            raise SystemExit("each accepted commit requires a full commit id, a date and a reason")
        commits.add(commit)
    return frozenset(commits)


def history(
    root: Path, blocked: tuple[re.Pattern[str], ...], *, baseline: str | None = None
) -> list[str]:
    revisions = [f"{baseline}..HEAD"] if baseline else ["--all"]
    commits = subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "log",
            *revisions,
            "--no-ext-diff",
            "--format=__HISTORY_COMMIT__%H%n%an%n%ae%n%cn%n%ce%n%B",
            "-p",
        ],
        check=True,
        capture_output=True,
        text=True,
        errors="replace",
    ).stdout
    failures: list[str] = []
    accepted = accepted_commits(root)
    for block in commits.split("__HISTORY_COMMIT__"):
        if not block.strip():
            continue
        commit = next(
            (
                line.strip()
                for line in block.splitlines()
                if re.fullmatch(r"[0-9a-f]{40,64}", line.strip())
            ),
            "unknown",
        )
        if commit in accepted:
            continue
        matches = [
            str(index) for index, pattern in enumerate(blocked, start=1) if pattern.search(block)
        ]
        if matches:
            paths = re.findall(r"(?m)^diff --git a/\S+ b/(\S+)", block)
            location = ", ".join(sorted(set(paths))) if paths else "metadata"
            failures.append(f"commit {commit} ({location}): blocked rule {', '.join(matches)}")
    if baseline:
        return failures
    tags = subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "for-each-ref",
            "--format=%(refname)%00%(taggername)%00%(taggeremail)%00%(contents)",
            "refs/tags",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    for row in tags.splitlines():
        if not row:
            continue
        ref, _, rest = row.partition("\0")
        matches = [
            str(index) for index, pattern in enumerate(blocked, start=1) if pattern.search(rest)
        ]
        if matches:
            failures.append(f"tag {ref}: blocked rule {', '.join(matches)}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    history_mode = parser.add_mutually_exclusive_group()
    history_mode.add_argument("--history", action="store_true")
    history_mode.add_argument("--history-after-baseline", type=Path)
    parser.add_argument("--artifacts-only", action="store_true")
    parser.add_argument("artifacts", nargs="*")
    args = parser.parse_args()
    root = args.root.resolve()
    blocked = patterns()
    failures: list[str] = []
    if not args.artifacts_only:
        tracked = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"], check=True, capture_output=True
        ).stdout
        for raw in tracked.split(b"\0"):
            if not raw:
                continue
            path = root / os.fsdecode(raw)
            content = read(path)
            if content is not None:
                failures.extend(find(path.relative_to(root).as_posix(), content, blocked))
    if args.history and not args.artifacts_only:
        failures.extend(history(root, blocked))
    if args.history_after_baseline and not args.artifacts_only:
        baseline_path = args.history_after_baseline
        if not baseline_path.is_absolute():
            baseline_path = root / baseline_path
        try:
            baseline_data = json.loads(baseline_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise SystemExit(f"could not read history baseline: {baseline_path}") from error
        if not isinstance(baseline_data, dict):
            raise SystemExit("history baseline must be a JSON object")
        baseline = baseline_data.get("baseline_commit")
        baseline_date = baseline_data.get("date")
        reason = baseline_data.get("reason")
        if (
            not isinstance(baseline, str)
            or not re.fullmatch(r"[0-9a-f]{40,64}", baseline)
            or not isinstance(baseline_date, str)
            or not baseline_date
            or not isinstance(reason, str)
            or not reason
        ):
            raise SystemExit("history baseline requires baseline_commit, date, and reason")
        exists = subprocess.run(
            ["git", "-C", str(root), "cat-file", "-e", f"{baseline}^{{commit}}"],
            check=False,
            capture_output=True,
        )
        ancestor = subprocess.run(
            ["git", "-C", str(root), "merge-base", "--is-ancestor", baseline, "HEAD"],
            check=False,
            capture_output=True,
        )
        if exists.returncode or ancestor.returncode:
            raise SystemExit("history baseline must be an ancestor commit of HEAD")
        failures.extend(history(root, blocked, baseline=baseline))
    for arg in args.artifacts:
        artifact = Path(arg)
        if not artifact.is_absolute():
            artifact = Path.cwd() / artifact
        failures.extend(scan_archive(artifact, blocked))
    if failures:
        print("Blocked public text found:\n" + "\n".join(dict.fromkeys(failures)), file=sys.stderr)
        return 1
    print("No blocked text in tracked files, supplied artifacts, or requested history.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
