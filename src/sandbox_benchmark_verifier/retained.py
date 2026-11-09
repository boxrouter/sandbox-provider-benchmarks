# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""The methodology versions a collapse must keep: ``retained-versions.yaml``.

Something served or frozen can cite a version that is no longer the latest: web's pinned
datasets or a frozen task tree. A collapse to the latest versions that deletes
one makes every method page of that data say "No Such Methodology" (web#4889). The file lists
each such version with its holder and when the holder retires. ``validate`` fails when a listed
version has no document in the catalog or no lock row, so a collapse cannot delete one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sandbox_benchmark_verifier.catalog import load_methodology
from sandbox_benchmark_verifier.lock import LOCK_PATH
from sandbox_benchmark_verifier.yaml_load import load_yaml

RETAINED = Path("retained-versions.yaml")


def _methodology_versions(root: Path) -> set[tuple[str, str]]:
    return {
        (str(item.document["id"]), str(item.document["version"])) for item in load_methodology(root)
    }


def retained_versions(root: Path) -> set[tuple[str, str]]:
    """The (id, version) pairs ``retained-versions.yaml`` keeps; none when the file is absent."""
    path = root / RETAINED
    if not path.is_file():
        return set()
    data: Any = load_yaml(path.read_text(encoding="utf-8"))
    return {(str(row["id"]), str(row["version"])) for row in data.get("versions", [])}


def _holder_errors(holders: dict[str, Any]) -> list[str]:
    return [
        f"{RETAINED}: holder {key} needs {field}"
        for key, holder in holders.items()
        for field in ("what", "retire_when")
        if not isinstance(holder, dict) or not holder.get(field)
    ]


def _row_errors(
    row: dict[str, Any],
    holders: dict[str, Any],
    documents: set[tuple[str, str]],
    locked: set[str],
) -> list[str]:
    identity = (str(row.get("id")), str(row.get("version")))
    where = f"{RETAINED}: {identity[0]}@{identity[1]}"
    held_by = row.get("held_by") or []
    errors = [] if held_by else [f"{where} names no holder"]
    errors.extend(f"{where} names unknown holder {key}" for key in held_by if key not in holders)
    if identity not in documents:
        errors.append(f"{where} is retained but the catalog has no such document version")
    if f"{identity[0]}@{identity[1]}" not in locked:
        errors.append(f"{where} is retained but has no row in {LOCK_PATH}")
    return errors


def retained_errors(root: Path) -> list[str]:
    """Every retained version is in the catalog and has a lock row."""
    path = root / RETAINED
    if not path.is_file():
        return []
    data: Any = load_yaml(path.read_text(encoding="utf-8"))
    holders = data.get("holders", {})
    lock_path = root / LOCK_PATH
    lock = lock_path.read_text(encoding="utf-8").splitlines() if lock_path.is_file() else []
    locked = {line.split()[0] for line in lock if line and not line.startswith("#")}
    documents = _methodology_versions(root)
    errors = _holder_errors(holders)
    seen: set[tuple[str, str]] = set()
    for row in data.get("versions", []):
        identity = (str(row.get("id")), str(row.get("version")))
        if identity in seen:
            errors.append(f"{RETAINED}: {identity[0]}@{identity[1]} is listed twice")
        seen.add(identity)
        errors.extend(_row_errors(row, holders, documents, locked))
    return errors
