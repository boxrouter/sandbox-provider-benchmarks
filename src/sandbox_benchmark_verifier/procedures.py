# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Resolve benchmark source files from one verified, release-pinned procedure catalogue."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tomllib
import zipfile
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from sandbox_benchmark_verifier.catalog import _assets, load_methodology, retired_ids
from sandbox_benchmark_verifier.names import (
    RetiredIdError,
    Tables,
    UnknownIdError,
    load_tables,
    resolve,
)
from sandbox_benchmark_verifier.versions import version_key

CONFIG = "contracts/procedures.toml"
CATALOGUE = "sandbox_benchmarks/_assets/procedures.json"
PUBLIC_REPOSITORY = "https://github.com/boxrouter/sandbox-provider-benchmarks"
PUBLIC_SOURCE_ROOT = "procedures"


def configuration(root: Path | None = None) -> dict[str, Any]:
    """Read the single visibility flag and exact release identity."""
    return tomllib.loads(((root or _assets()) / CONFIG).read_text())


def _public_source_root(root: Path, data: Mapping[str, Any]) -> Path:
    """Resolve the owner-edited mirror directory without allowing it to escape the tree."""
    source = data.get("source")
    if (
        not isinstance(source, Mapping)
        or source.get("repository") != PUBLIC_REPOSITORY
        or not isinstance(source.get("ref"), str)
        or not source["ref"].strip()
    ):
        raise ValueError("procedure source must resolve through the public Benchmarks mirror")
    value = source.get("root")
    if not isinstance(value, str):
        raise ValueError("procedure source mirror root is invalid")
    path = PurePosixPath(value)
    if not path.parts or path.is_absolute() or any(part in {".", ".."} for part in path.parts):
        raise ValueError("procedure source mirror root must stay within its owner")
    if path.as_posix() != PUBLIC_SOURCE_ROOT:
        raise ValueError("procedure source must use the public mirror directory")
    return root.joinpath(*path.parts)


def catalogue(root: Path | None = None) -> dict[str, Any]:
    """Reject edited or mismatched release metadata before exposing any source link."""
    root = root or _assets()
    config = configuration(root)
    path = PurePosixPath(config["catalogue"])
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("procedure catalogue must stay within its owner")
    content = (root / path).read_bytes()
    if hashlib.sha256(content).hexdigest() != config["catalogue_sha256"]:
        raise ValueError("procedure catalogue differs from its pinned digest")
    data: dict[str, Any] = json.loads(content)
    if data["version"] != config["version"]:
        raise ValueError("procedure catalogue differs from its pinned version")
    if "source" in config:
        data["source"] = config["source"]  # the one owner-edited public source base
    for row in data["procedures"].values():
        source = PurePosixPath(row["path"])
        if (
            source.is_absolute()
            or ".." in source.parts
            or not source.is_relative_to("src/sandbox_benchmarks/procedures")
            or source.suffix != ".py"
            or re.fullmatch(r"[0-9a-f]{64}", row["sha256"]) is None
        ):
            raise ValueError("procedure catalogue has an invalid source reference")
    _public_source_root(root, data)
    return data


def import_release(wheel: Path, root: Path) -> None:
    """Generate a pin only after each exact file resolves inside the release wheel."""
    payloads: dict[str, bytes] = {}
    with zipfile.ZipFile(wheel) as archive:
        content = archive.read(CATALOGUE)
        data = json.loads(content)
        metadata = archive.read(f"sandbox_benchmarks-{data['version']}.dist-info/METADATA")
        if (
            b"Name: sandbox-benchmarks\n" not in metadata
            or f"Version: {data['version']}\n".encode() not in metadata
        ):
            raise ValueError("procedure catalogue and distribution versions differ")
        for row in data["procedures"].values():
            source = PurePosixPath(row["path"])
            if source.is_absolute() or ".." in source.parts or source.parts[0] != "src":
                raise ValueError("procedure source must identify a package file")
            payload = archive.read(str(source.relative_to("src")))
            if hashlib.sha256(payload).hexdigest() != row["sha256"]:
                raise ValueError("procedure source differs from its release digest")
            payloads[source.as_posix()] = payload
    _write_release_mirror(wheel, root, data, content, payloads)


def _write_release_mirror(
    wheel: Path,
    root: Path,
    data: Mapping[str, Any],
    content: bytes,
    payloads: Mapping[str, bytes],
) -> None:
    """Write only verified release files and their matching public-source contract."""
    source_root, public, base, previous_catalogue = _source_settings(root, data)
    _prune_stale_procedures(source_root, previous_catalogue, payloads)
    for relative, payload in payloads.items():
        procedure_path = source_root.joinpath(*PurePosixPath(relative).parts)
        procedure_path.parent.mkdir(parents=True, exist_ok=True)
        procedure_path.write_bytes(payload)
    catalogue_path = f"contracts/procedures/{data['version']}.json"
    (root / catalogue_path).parent.mkdir(parents=True, exist_ok=True)
    (root / catalogue_path).write_bytes(content)
    (root / CONFIG).write_text(
        "# SPDX-FileCopyrightText: BoxRouter, Inc.\n"
        "# SPDX-License-"
        "Identifier: Apache-2.0\n"
        "# Generated from the verified distribution; only visibility and [source] are "
        "owner-edited.\n"
        f"procedures_public = {str(public).lower()}\n"
        f"version = {json.dumps(data['version'])}\n"
        f"distribution = {json.dumps(wheel.name)}\n"
        f"distribution_sha256 = {json.dumps(hashlib.sha256(wheel.read_bytes()).hexdigest())}\n"
        f"catalogue = {json.dumps(catalogue_path)}\n"
        f"catalogue_sha256 = {json.dumps(hashlib.sha256(content).hexdigest())}\n"
        + (f"\n[source]\n{base}" if base else "")
    )
    catalogue(root)


def _source_settings(
    root: Path, data: Mapping[str, Any]
) -> tuple[Path, bool, str, Mapping[str, Any]]:
    """Retain the configured public source root when refreshing release pins."""
    previous = configuration(root) if (root / CONFIG).is_file() else {}
    previous_catalogue = catalogue(root) if previous else {}
    source_config = previous.get("source", data.get("source", {}))
    source_root = _public_source_root(root, {"source": source_config})
    public = previous.get("procedures_public", False)
    base = "".join(f"{key} = {json.dumps(value)}\n" for key, value in source_config.items())
    return source_root, public, base, previous_catalogue


def _prune_stale_procedures(
    source_root: Path,
    previous_catalogue: Mapping[str, Any],
    payloads: Mapping[str, bytes],
) -> None:
    """Delete only previously pinned mirror files absent from the new release."""
    old_paths = {row["path"] for row in previous_catalogue.get("procedures", {}).values()}
    for stale in old_paths - payloads.keys():
        stale_path = source_root.joinpath(*PurePosixPath(stale).parts)
        if stale_path.is_file() and not stale_path.is_symlink():
            stale_path.unlink()


def live_procedures(data: Mapping[str, Any], tables: Tables) -> dict[str, Any]:
    """The release's procedures without those of a retired test: nothing runs them."""
    retired = set(tables.aliases.get("retired", []))
    return {key: row for key, row in data["procedures"].items() if key not in retired}


def references(
    identifier: str,
    document: Mapping[str, Any],
    documents: Mapping[str, Mapping[str, Any]],
    *,
    release: dict[str, Any] | None = None,
    tables: Tables | None = None,
) -> dict[str, Any] | None:
    """Direct procedures or the deduplicated input procedures of a composed benchmark."""
    data = release if release is not None else catalogue()
    tables = tables if tables is not None else load_tables()
    entries = {
        resolve("document", key, tables=tables): row
        for key, row in live_procedures(data, tables).items()
    }
    files: dict[str, dict[str, str]] = {}
    seen: set[str] = set()

    def collect(key: str, current: Mapping[str, Any]) -> None:
        canonical = resolve("document", key, tables=tables)
        if canonical in seen:
            raise ValueError("cyclic benchmark procedure composition")
        seen.add(canonical)
        if canonical in entries:
            row = entries[canonical]
            files[row["path"]] = {"path": row["path"], "sha256": row["sha256"]}
        else:
            for item in current.get("composition", {}).get("inputs", []):
                child = item if isinstance(item, str) else item["id"]
                try:
                    target = resolve("document", child, tables=tables)
                except RetiredIdError:
                    continue  # an older composition's input that has since been retired
                found = documents.get(target)
                if found is None:
                    raise ValueError("composed benchmark has an unresolved procedure input")
                collect(child, found)
        seen.remove(canonical)

    try:
        resolve("document", identifier, tables=tables)
    except UnknownIdError:
        return None  # Common policies and future definitions are not executable benchmarks.
    collect(identifier, document)
    if not files:
        return None
    return {"version": data["version"], "source": data["source"], "files": list(files.values())}


def projection(root: Path, methods: list[dict[str, Any]], tables: Tables) -> dict[str, Any]:
    """Resolve one pinned release once for every immutable methodology version."""
    if not (root / CONFIG).is_file():
        return {"procedures_public": False, "references": {}}
    release = catalogue(root)
    documents = {
        str(tables.aliases.get("document", {}).get(method["id"], method["id"])): method
        for method in sorted(methods, key=lambda item: version_key(item["version"]))
    }
    projected = {}
    retired = retired_ids(root)
    for method in methods:
        if method["id"] in retired:
            continue  # a retired test runs no procedure
        reference = references(method["id"], method, documents, release=release, tables=tables)
        if reference is not None:
            projected[method["id"], str(method["version"])] = reference
    return {"procedures_public": configuration(root)["procedures_public"], "references": projected}


def procedure_errors(root: Path | None = None) -> list[str]:
    """Every named executable benchmark and composed index must resolve to package source."""
    root = root or _assets()
    if not (root / CONFIG).is_file():
        return [f"{CONFIG}: procedures contract missing from a canonical tree"]
    try:
        data = catalogue(root)
        errors = _procedure_copy_errors(root, data)
        tables = load_tables(root)
        available = {
            resolve("document", key, tables=tables) for key in live_procedures(data, tables)
        }
        expected = {
            resolve("document", row["document"], tables=tables)
            for row in tables.names
            if row["kind"] == "test" and row.get("document")
        }
        missing = expected - available
        errors.extend(f"benchmark has no released procedure: {key}" for key in sorted(missing))
        documents = {}
        for record in sorted(
            load_methodology(root),
            key=lambda item: version_key(item.document["version"]),
        ):
            try:
                canonical = resolve("document", record.document["id"], tables=tables)
            except UnknownIdError:
                continue
            documents[canonical] = record.document
        for row in tables.names:
            if row["kind"] not in {"test", "index"} or not row.get("document"):
                continue
            key = resolve("document", row["document"], tables=tables)
            if (
                key not in documents
                or references(key, documents[key], documents, release=data, tables=tables) is None
            ):
                errors.append(f"benchmark has no resolvable procedure: {key}")
        return errors
    except (OSError, KeyError, ValueError) as error:
        return [str(error)]


def _procedure_copy_errors(root: Path, data: Mapping[str, Any]) -> list[str]:
    """The public mirror contains exactly the source files pinned by its release catalogue."""
    mirror = _public_source_root(root, data)
    if mirror.is_symlink() or not mirror.is_dir():
        return [f"procedure source mirror is missing: {mirror.relative_to(root)}"]
    expected = {row["path"]: row["sha256"] for row in data["procedures"].values()}
    errors: list[str] = []
    actual: set[str] = set()
    for path in mirror.rglob("*.py"):
        relative = path.relative_to(mirror).as_posix()
        actual.add(relative)
        if path.is_symlink() or not path.is_file():
            errors.append(f"procedure source mirror contains a non-file: {relative}")
        elif relative not in expected:
            errors.append(f"procedure source mirror contains an unpinned file: {relative}")
        elif hashlib.sha256(path.read_bytes()).hexdigest() != expected[relative]:
            errors.append(f"procedure source copy differs from catalogue digest: {relative}")
    errors.extend(
        f"procedure source copy is missing: {relative}"
        for relative in sorted(expected.keys() - actual)
    )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=_assets())
    parser.add_argument("--wheel", type=Path)
    args = parser.parse_args()
    if args.wheel:
        import_release(args.wheel, args.root)
    errors = procedure_errors(args.root)
    for error in errors:
        print(error)
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
