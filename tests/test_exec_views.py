# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""The two Exec Latency documents come from one source and stay in step with it."""

from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
from typing import Any

import yaml

from sandbox_benchmark_verifier.parameters import tree_digest

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "build_exec_views", ROOT / "scripts" / "build_exec_views.py"
)
assert _SPEC and _SPEC.loader
build = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(build)


def source() -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load((ROOT / build.SOURCE).read_text(encoding="utf-8"))
    return loaded


def test_generated_file_is_current() -> None:
    current = yaml.safe_load((ROOT / build.SOURCE).read_text(encoding="utf-8"))
    assert (ROOT / build.OUTPUT).read_text(encoding="utf-8") == build.render(current)


def test_parameters_directories_match_the_shared_one() -> None:
    for document in build.documents(source()):
        assert (
            tree_digest(ROOT / document["parameters"]["path"]) == document["parameters"]["sha256"]
        )


def test_shared_edit_changes_both_documents() -> None:
    changed = copy.deepcopy(source())
    changed["shared"]["failure_policy"] = {"edited": "once"}
    before = build.documents(source())
    after = build.documents(changed)
    assert len(after) == 2
    for old, new in zip(before, after, strict=True):
        assert new["failure_policy"] == {"edited": "once"} != old["failure_policy"]


def test_views_differ_only_where_stated() -> None:
    first, second = build.documents(source())
    differing = {key for key in first if first[key] != second.get(key)}
    assert {"id", "version", "name", "notes", "metrics", "statistics", "parameters"} >= differing
