# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""The run definition each methodology page links as its procedure: ``parameters/<id>/run.json``.

A document that pins a parameters directory holding a ``run.json`` links that file at the release
tag. A composed index links the run definitions of its inputs, once each. A document with no run
definition (a shared page, a benchmark not yet defined) links nothing.
"""

import hashlib
import tomllib
from collections.abc import Mapping
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from sandbox_benchmark_verifier.catalog import retired_ids
from sandbox_benchmark_verifier.names import (
    RetiredIdError,
    Tables,
    UnknownIdError,
    resolve,
)

PUBLIC_REPOSITORY = "https://github.com/boxrouter/sandbox-provider-benchmarks"
SOURCE_ROOT = "parameters"
RUN_FILE = "run.json"


def release_version(root: Path) -> str:
    """The package version the page's links are tagged with (the public mirror's ``v<version>``)."""
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        return str(tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"])
    try:
        return version("sandbox-benchmark-verifier")
    except PackageNotFoundError as error:
        raise ValueError("the package version is unknown") from error


def run_definition(root: Path, document: Mapping[str, Any]) -> dict[str, str] | None:
    """The run definition file of a document's pinned parameters directory, if it has one."""
    parameters = document.get("parameters")
    declared = parameters.get("path") if isinstance(parameters, Mapping) else None
    if not isinstance(declared, str):
        return None
    directory = Path(declared.strip("/"))
    if directory.is_absolute() or ".." in directory.parts or directory.parts[:1] != (SOURCE_ROOT,):
        return None
    path = root / directory / RUN_FILE
    if not path.is_file():
        return None
    relative = "/".join((*directory.parts[1:], RUN_FILE))
    return {"path": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def references(
    identifier: str,
    document: Mapping[str, Any],
    documents: Mapping[str, Mapping[str, Any]],
    entries: Mapping[str, dict[str, str]],
    *,
    tables: Tables,
) -> list[dict[str, str]]:
    """The run definitions a document's page links: its own, or its composed inputs', once each."""
    files: dict[str, dict[str, str]] = {}
    seen: set[str] = set()

    def collect(key: str, current: Mapping[str, Any]) -> None:
        canonical = resolve("document", key, tables=tables)
        if canonical in seen:
            raise ValueError("cyclic benchmark procedure composition")
        seen.add(canonical)
        if canonical in entries:
            files[entries[canonical]["path"]] = entries[canonical]
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
        return []  # Common policies and future definitions are not executable benchmarks.
    collect(identifier, document)
    return list(files.values())


def projection(root: Path, methods: list[dict[str, Any]], tables: Tables) -> dict[str, Any]:
    """Resolve every document's run definition links once for the text bundle."""
    tag = f"v{release_version(root)}"
    source = {"repository": PUBLIC_REPOSITORY, "root": SOURCE_ROOT, "ref": tag}
    aliases = tables.aliases.get("document", {})
    documents = {str(aliases.get(method["id"], method["id"])): method for method in methods}
    retired = retired_ids(root)
    entries: dict[str, dict[str, str]] = {}
    for key, method in documents.items():
        entry = run_definition(root, method)
        if entry is not None and method["id"] not in retired:
            entries[key] = entry
    projected: dict[str, Any] = {}
    for method in methods:
        if method["id"] in retired:
            continue  # a retired test runs nothing
        files = references(method["id"], method, documents, entries, tables=tables)
        if files:
            projected[method["id"]] = {
                "version": tag.removeprefix("v"),
                "source": source,
                "files": files,
            }
    return {"procedures_public": True, "references": projected}
