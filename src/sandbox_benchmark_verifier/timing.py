# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Resolve the executable timing contract against immutable methodology documents."""

from collections.abc import Iterator, Mapping, Sequence
from math import isfinite
from pathlib import Path
from typing import Any

import yaml

from sandbox_benchmark_verifier.catalog import MethodologyDocument, _assets, load_methodology
from sandbox_benchmark_verifier.yaml_load import load_yaml


def load_timing_contract(version: str = "1.0.0", root: Path | None = None) -> dict[str, Any]:
    """Load one explicit contract version, from source or an installed distribution."""
    assets = root or _assets()
    return _load_contract(version, assets, frozenset())


def _load_contract(version: str, assets: Path, seen: frozenset[str]) -> dict[str, Any]:
    if version in seen:
        raise ValueError("cyclic timing contract inheritance")
    path = assets / "contracts" / "timing" / f"{version}.yaml"
    document: dict[str, Any] = load_yaml(path.read_text(encoding="utf-8"))
    base_version = document.pop("extends", None)
    if base_version is None:
        return document
    base = _load_contract(str(base_version), assets, seen | {version})
    return {
        **base,
        **{
            key: {**base[key], **value}
            if isinstance(base.get(key), dict) and isinstance(value, dict)
            else value
            for key, value in document.items()
        },
    }


def resolve(
    contract: Mapping[str, Any],
    reference: Mapping[str, str],
    documents: Sequence[MethodologyDocument] | None = None,
) -> Any:
    """Resolve a methodology citation or an intra-contract pointer."""
    value: Any = contract
    if "document" in reference:
        source = contract["sources"][reference["document"]]
        matches = [
            item.document
            for item in (load_methodology() if documents is None else documents)
            if item.document["id"] == source["id"]
            and str(item.document["version"]) == str(source["version"])
        ]
        if len(matches) != 1:
            raise ValueError("timing source must identify one methodology document")
        value = matches[0]
    pointer = reference["pointer"]
    if not isinstance(pointer, str) or (pointer and not pointer.startswith("/")):
        raise ValueError("timing pointer must be an absolute JSON pointer")
    for token in pointer.split("/")[1:]:
        key = token.replace("~1", "/").replace("~0", "~")
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


def _references(value: Any) -> Iterator[Mapping[str, str]]:
    if isinstance(value, Mapping):
        if set(value) == {"document", "pointer"}:
            yield value
        else:
            for child in value.values():
                yield from _references(child)
    elif isinstance(value, list):
        for child in value:
            yield from _references(child)


def _validate_status_deadline(contract: Mapping[str, Any]) -> None:
    """Check local deadline references with the same resolver used for citations."""
    if "status_dispatch_deadline" not in contract["replay"]:
        return
    deadline = contract["replay"]["status_dispatch_deadline"]
    if not isinstance(deadline, Mapping):
        raise ValueError("status dispatch deadline must be a mapping")
    for field in ("base", "grace", "observation"):
        value = resolve(contract, {"pointer": deadline[field]})
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not isfinite(value)
            or (field == "observation" and not isinstance(value, int))
        ):
            raise ValueError("status deadline target must be a finite numeric duration")
    effective = deadline["effective"]
    if not isinstance(effective, str) or effective.split() != ["base", "+", "grace"]:
        raise ValueError("status dispatch deadline must add base and grace")


def timing_errors(root: Path) -> Iterator[str]:
    """Reject unresolved references, missing rules and unknown interval selections."""
    paths = sorted((root / "contracts" / "timing").glob("*.yaml"))
    if not paths:
        return
    documents = load_methodology(root)
    for path in paths:
        try:
            contract = load_timing_contract(path.stem, root)
            if contract["schema"] != "sandbox-benchmarks.timing/1":
                raise ValueError("unknown timing schema")
            if contract["version"] != path.stem:
                raise ValueError("timing version differs from filename")
            for reference in _references(contract):
                resolve(contract, reference, documents)
            _validate_status_deadline(contract)
            for rule in contract["operations"].values():
                if rule["interval"] not in contract["intervals"]:
                    raise ValueError("unknown operation interval")
            for section in ("readiness", "retries", "replay", "trial", "derivations"):
                if not contract[section]:
                    raise ValueError("empty timing section")
        except (KeyError, IndexError, TypeError, ValueError, yaml.YAMLError) as error:
            yield f"{path}: invalid timing contract ({type(error).__name__})"
