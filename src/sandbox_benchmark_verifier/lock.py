# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Immutability lock for released methodology versions.

``methodology/released.lock`` maps ``id@version`` to a sha256 over the parsed document and its
``parameters/<id>/<version>/`` tree. A locked entry must keep its hash; versions are never
edited in place. ``update`` only appends entries; it never rewrites or removes one.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from sandbox_benchmark_verifier.yaml_load import load_yaml_all

LOCK_PATH = Path("methodology/released.lock")


def tree_digest(directory: Path) -> str:
    """Digest of a directory: sorted ``relative path NUL file sha256`` lines; empty if absent."""
    if not directory.is_dir():
        return ""
    lines = [
        f"{item.relative_to(directory).as_posix()}\0{hashlib.sha256(item.read_bytes()).hexdigest()}"
        for item in sorted(directory.rglob("*"))
        if item.is_file()
    ]
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def document_hash(document: dict[str, Any], root: Path) -> str:
    """Hash one methodology document together with its parameters tree."""
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"), default=str)
    tree = tree_digest(root / "parameters" / str(document["id"]) / str(document["version"]))
    return hashlib.sha256(f"{canonical}\n{tree}".encode()).hexdigest()


def current_hashes(root: Path) -> dict[str, str]:
    """Hash every id/version document under ``methodology/``."""
    result: dict[str, str] = {}
    for path in sorted((root / "methodology").rglob("*.yaml")):
        for document in load_yaml_all(path.read_text(encoding="utf-8")):
            if isinstance(document, dict) and "id" in document and "version" in document:
                result[f"{document['id']}@{document['version']}"] = document_hash(document, root)
    return result


def read_lock(root: Path) -> dict[str, str]:
    """Parse the lock file (``id@version sha256`` per line, ``#`` comments)."""
    lock: dict[str, str] = {}
    path = root / LOCK_PATH
    if not path.is_file():
        return lock
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            key, digest = line.split()
            lock[key] = digest
    return lock


def check(root: Path, require_complete: bool = False) -> list[str]:
    """Report locked versions that changed or vanished; optionally also unlocked ones."""
    lock, now = read_lock(root), current_hashes(root)
    errors = []
    for key, digest in lock.items():
        if key not in now:
            errors.append(f"{key}: released version was removed; versions are immutable")
        elif now[key] != digest:
            errors.append(f"{key}: released version changed; publish a new version instead")
    if require_complete:
        errors.extend(f"{key}: not in {LOCK_PATH}; run update" for key in now if key not in lock)
    return errors


def update(root: Path) -> list[str]:
    """Append entries for versions not yet locked; return the keys added."""
    lock, now = read_lock(root), current_hashes(root)
    added = [key for key in now if key not in lock]
    lock.update({key: now[key] for key in added})
    header = "# Released methodology versions: id@version sha256 (document + parameters tree).\n"
    (root / LOCK_PATH).write_text(
        header + "".join(f"{key} {lock[key]}\n" for key in sorted(lock)), encoding="utf-8"
    )
    return added


def main() -> int:
    """Run ``check``, ``check --require-complete`` or ``update`` from the repository root."""
    parser = argparse.ArgumentParser(prog="python -m sandbox_benchmark_verifier.lock")
    parser.add_argument("command", choices=("check", "update"))
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()
    root = Path.cwd()
    if args.command == "update":
        print("locked: " + (", ".join(update(root)) or "nothing new"))
        return 0
    errors = check(root, args.require_complete)
    for error in errors:
        print(error, file=sys.stderr)
    if not errors:
        print("lock: ok")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
