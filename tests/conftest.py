# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""`bundle` copies the real tree; `build_text_bundle` compiles fresh inputs."""

import shutil
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

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


def _declare_common(root: Path) -> str:
    """Give a stand-in tree the common methodology, copied unchanged. A stand-in document joins
    the selection by inheriting the returned id."""
    (root / "methodology").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / "methodology/00-common.yaml", root / "methodology/00-common.yaml")
    return "sbs.common"


@pytest.fixture
def declare_common() -> Callable[[Path], str]:
    """Write the common into a stand-in tree; see `_declare_common`."""
    return _declare_common
