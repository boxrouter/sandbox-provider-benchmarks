# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Discover lifecycle protocols from their pinned methodology parameters."""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from sandbox_benchmark_verifier.catalog import (
    MethodologyDocument,
    common_document,
    context_region_errors,
    default_context,
    load_methodology,
    ordinary_documents,
)
from sandbox_benchmark_verifier.versions import version_key

SCHEMA = "sandbox-benchmark.lifecycle-protocol/1"


@dataclass(frozen=True)
class LifecycleProfile:
    method: MethodologyDocument
    protocol: dict[str, Any]
    directory: Path

    @property
    def identifier(self) -> str:
        """One canonical record identity, declared by the methodology itself."""
        return str(self.method.document["id"])

    def workloads(self) -> list[dict[str, Any]]:
        value: list[dict[str, Any]] = json.loads((self.directory / "workloads.json").read_text())
        return value


def _directory(root: Path, method: MethodologyDocument) -> Path:
    declared = method.document["parameters"]["path"]
    if not isinstance(declared, str):
        raise ValueError("profile needs a declared parameter path")
    directory = root / declared
    if not directory.resolve().is_relative_to(root.resolve()):
        raise ValueError("parameter directory must stay inside its source tree")
    return directory


def _load_profile(root: Path, method: MethodologyDocument) -> LifecycleProfile:
    directory = _directory(root, method)
    protocol = json.loads((directory / "protocol.json").read_text())
    if not isinstance(protocol, dict) or protocol.get("schema") != SCHEMA:
        raise ValueError("methodology declares no lifecycle protocol")
    schema = json.loads((root / "schemas/lifecycle-protocol.schema.json").read_text())
    Draft202012Validator(schema).validate(protocol)
    return LifecycleProfile(method, protocol, directory)


def profile(root: Path, reference: Mapping[str, str]) -> LifecycleProfile:
    """Read one exact archived protocol, independently of ordinary discovery defaults."""
    if (
        not isinstance(reference, Mapping)
        or set(reference) != {"id", "version"}
        or not all(isinstance(reference[key], str) for key in ("id", "version"))
    ):
        raise ValueError("profile needs an exact document reference")
    version_key(reference["version"])
    methods = load_methodology(root)
    matches = [
        method
        for method in methods
        if all(method.document[key] == reference[key] for key in ("id", "version"))
    ]
    if len(matches) != 1:
        raise ValueError("profile reference is missing or ambiguous")
    method = matches[0]
    inherited = method.document.get("inherits")
    if not isinstance(inherited, Mapping):
        raise ValueError("profile needs an exact inherited common")
    parent = common_document([item.document for item in methods], inherited)
    if str(parent["effective"]) > str(method.document["effective"]):
        raise ValueError("profile common takes effect after its definition")
    if errors := context_region_errors(method.document, parent):
        raise ValueError("; ".join(errors))
    return _load_profile(root, method)


def profiles(
    root: Path, *, common: Mapping[str, str] | None = None
) -> tuple[LifecycleProfile, ...]:
    """Discover ordinary protocols inside the explicit or sole owner default context."""
    protocols = []
    for path in (root / "parameters").glob("*/*/protocol.json"):
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("parameter directory must stay inside its source tree")
        value = json.loads(path.read_text())
        if isinstance(value, dict) and value.get("schema") == SCHEMA:
            protocols.append(path)
    if not protocols:
        return ()
    methods = load_methodology(root)
    selected = ordinary_documents(
        [method.document for method in methods],
        common=common if common is not None else default_context(root),
    )
    found = []
    for method in methods:
        if (
            selected.get(str(method.document["id"])) is not method.document
            or "parameters" not in method.document
        ):
            continue
        path = _directory(root, method) / "protocol.json"
        if not path.is_file():
            continue
        value = json.loads(path.read_text())
        if isinstance(value, dict) and value.get("schema") == SCHEMA:
            found.append(_load_profile(root, method))
    return tuple(sorted(found, key=lambda item: item.identifier))


def name_rows(root: Path, first_row: int) -> list[dict[str, Any]]:
    """Derive names and references, leaving the existing canonical tables untouched."""
    rows: list[dict[str, Any]] = []
    for item in profiles(root):
        document = item.method.document
        identity = item.identifier
        metric = {
            "document": identity,
            "version": document["version"],
            "name": document["metrics"][0]["name"],
        }
        for kind, parent in (("tab", item.protocol["section"]), ("test", identity)):
            rows.append(
                {
                    "row": first_row + len(rows),
                    "kind": kind,
                    "id": identity,
                    "name": document["name"],
                    "document": identity,
                    "parent": parent,
                    **(
                        {"metric": metric, "label": {"form": "long", "target": "test"}}
                        if kind == "test"
                        else {}
                    ),
                }
            )
        for workload in item.workloads():
            rows.append(
                {
                    "row": first_row + len(rows),
                    "kind": "variant",
                    "id": workload["id"],
                    "name": workload["label"],
                    "parent": identity,
                }
            )
    return rows
