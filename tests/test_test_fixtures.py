# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Keep compiler access centralized and bundle snapshots isolated."""

import ast
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

TESTS = Path(__file__).parent


def _attribute_chain(node: ast.expr) -> list[str]:
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Attribute):
        return [*_attribute_chain(node.value), node.attr]
    return []


def _uses_text_bundle_builder(source: str) -> bool:
    tree = ast.parse(source)
    text_modules: set[str] = set()
    package_modules: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == "sandbox_benchmark_verifier.text" and any(
                alias.name == "build_text_bundle" for alias in node.names
            ):
                return True
            if node.module == "sandbox_benchmark_verifier":
                text_modules.update(
                    alias.asname or alias.name for alias in node.names if alias.name == "text"
                )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "sandbox_benchmark_verifier":
                    package_modules.add(alias.asname or alias.name)
                elif alias.name == "sandbox_benchmark_verifier.text":
                    if alias.asname:
                        text_modules.add(alias.asname)
                    else:
                        package_modules.add("sandbox_benchmark_verifier")

    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or node.attr != "build_text_bundle":
            continue
        chain = _attribute_chain(node)
        if len(chain) == 2 and chain[0] in text_modules:
            return True
        if len(chain) == 3 and chain[0] in package_modules and chain[1] == "text":
            return True
    return False


def test_test_modules_use_the_shared_compiler_fixture() -> None:
    offenders = [
        path.name
        for path in sorted(TESTS.rglob("*.py"))
        if path.name != "conftest.py"
        if _uses_text_bundle_builder(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


@pytest.mark.parametrize(
    "source",
    [
        "from sandbox_benchmark_verifier.text import build_text_bundle",
        "from sandbox_benchmark_verifier.text import build_text_bundle as compile_bundle",
        "import sandbox_benchmark_verifier.text as text\ntext.build_text_bundle",
        "from sandbox_benchmark_verifier import text as compiler_text\n"
        "compiler_text.build_text_bundle",
        "import sandbox_benchmark_verifier as verifier\nverifier.text.build_text_bundle",
    ],
)
def test_compiler_access_guard_detects_direct_and_aliased_imports(source: str) -> None:
    assert _uses_text_bundle_builder(source)


def test_bundle_snapshots_copy_nested_mutable_objects(
    bundle: dict[str, Any],
    _real_tree_bundle: dict[str, Any],
    _bundle_snapshot_factory: Callable[[], dict[str, Any]],
) -> None:
    other_snapshot = _bundle_snapshot_factory()
    snapshot_row = next(row for row in bundle["names"] if row["kind"] == "section")
    session_row = next(row for row in _real_tree_bundle["names"] if row["kind"] == "section")
    other_row = next(row for row in other_snapshot["names"] if row["kind"] == "section")
    original_name = snapshot_row["name"]

    assert bundle is not _real_tree_bundle
    assert bundle["names"] is not _real_tree_bundle["names"]
    assert snapshot_row is not session_row
    assert snapshot_row is not other_row
    snapshot_row["name"] = "Snapshot mutation"
    assert session_row["name"] == original_name
    assert other_row["name"] == original_name
