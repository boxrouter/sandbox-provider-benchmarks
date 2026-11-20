# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Synthetic-only verifier behavior tests."""

from pathlib import Path

import pytest

from sandbox_benchmark_verifier.cli import validate

pytestmark = pytest.mark.unit


def test_empty_repository_folders_are_valid(tmp_path: Path) -> None:
    assert validate(tmp_path) == []


def test_current_methodology_and_schemas_validate() -> None:
    repository = Path(__file__).parents[1]
    assert validate(repository) == []


def test_validate_handles_multi_document_methodology(tmp_path: Path) -> None:
    (tmp_path / "methodology").mkdir()
    (tmp_path / "methodology" / "cpu.yaml").write_text("id: cpu\n---\nid: memory\n")
    assert validate(tmp_path) == []


def test_non_string_keys_are_rejected(tmp_path: Path) -> None:
    (tmp_path / "methodology").mkdir()
    (tmp_path / "methodology" / "exec.yaml").write_text(
        "id: exec\nfailure_policy:\n  timeout_seconds:\n    true: 30\n"
    )
    assert validate(tmp_path) == [
        f"{tmp_path / 'methodology' / 'exec.yaml'}: key $.failure_policy.timeout_seconds.True "
        "is not a string; quote it"
    ]


def test_ready_check_skip_lists_name_existing_methodology_ids(tmp_path: Path) -> None:
    (tmp_path / "methodology").mkdir()
    common = tmp_path / "methodology" / "common.yaml"
    common.write_text(
        "id: sbs.common\nready_check:\n"
        "  skipped_by_benchmarks: [sbs.common]\n  skipped_on_restore_by_benchmarks: [sbs.common]\n"
    )
    assert validate(tmp_path) == []
    common.write_text(
        "id: sbs.common\nready_check:\n"
        "  skipped_by_benchmarks: [sandbox.create.sequential_tti]\n"
        "  skipped_on_restore_by_benchmarks: [fork]\n"
    )
    assert validate(tmp_path) == [
        f"{common}: ready_check.skipped_by_benchmarks names sandbox.create.sequential_tti, "
        "which is no methodology id",
        f"{common}: ready_check.skipped_on_restore_by_benchmarks names fork, "
        "which is no methodology id",
    ]
