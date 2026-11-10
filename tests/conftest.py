# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""`bundle` copies the real tree; `build_text_bundle` compiles fresh inputs."""

import shutil
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import yaml

from sandbox_benchmark_verifier.text import build_text_bundle as _compile_text_bundle

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    """A fresh copy of the folders the name, text and label checks read, for a test to edit."""
    root = tmp_path / "tree"
    for folder in (
        "methodology",
        "schemas",
        "names",
        "reader",
        "conventions",
        "parameters",
        "contracts",
    ):
        shutil.copytree(ROOT / folder, root / folder)
    return root


@pytest.fixture(scope="session")
def _real_tree_bundle() -> dict[str, Any]:
    return _compile_text_bundle(ROOT)


@pytest.fixture
def _bundle_snapshot_factory(_real_tree_bundle: dict[str, Any]) -> Callable[[], dict[str, Any]]:
    return lambda: deepcopy(_real_tree_bundle)


@pytest.fixture
def bundle(_bundle_snapshot_factory: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    return _bundle_snapshot_factory()


@pytest.fixture
def build_text_bundle() -> Callable[[Path], dict[str, Any]]:
    return _compile_text_bundle


def _declare_common(root: Path) -> dict[str, str]:
    """Give a stand-in tree the one explicit common catalog selection binds to (#755, O12): the
    repository's declared default (names.yaml `catalog_selection.default_common`) and that
    common document itself, copied unchanged. A stand-in document joins the selection by
    inheriting the returned reference."""
    declared = yaml.safe_load((ROOT / "names" / "names.yaml").read_text(encoding="utf-8"))
    reference = dict(declared["catalog_selection"]["default_common"])
    names = root / "names" / "names.yaml"
    names.parent.mkdir(parents=True, exist_ok=True)
    identity, version = reference["id"], reference["version"]
    head = f"catalog_selection:\n  default_common: {{id: {identity}, version: {version}}}\n"
    names.write_text(head + (names.read_text(encoding="utf-8") if names.exists() else ""))
    source = next(
        path
        for path in sorted((ROOT / "methodology").glob("00-common*.yaml"))
        if (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("version")
        == reference["version"]
    )
    (root / "methodology").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, root / "methodology" / source.name)
    return reference


@pytest.fixture
def declare_common() -> Callable[[Path], dict[str, str]]:
    """Write the declared default common into a stand-in tree; see `_declare_common`."""
    return _declare_common
