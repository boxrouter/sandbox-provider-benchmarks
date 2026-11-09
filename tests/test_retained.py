# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""A collapse cannot delete a version that something cites (retained-versions.yaml)."""

import shutil
from pathlib import Path

import pytest

from sandbox_benchmark_verifier.retained import RETAINED, retained_errors
from sandbox_benchmark_verifier.yaml_load import load_yaml

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]


def _copy(tmp_path: Path) -> Path:
    root = tmp_path / "tree"
    for folder in ("methodology", "parameters", "names", "schemas", "conventions", "contracts"):
        shutil.copytree(ROOT / folder, root / folder)
    shutil.copy(ROOT / RETAINED, root / RETAINED)
    return root


def test_the_retained_versions_are_all_present_and_each_has_a_holder() -> None:
    assert retained_errors(ROOT) == []
    data = load_yaml((ROOT / RETAINED).read_text(encoding="utf-8"))
    assert len(data["versions"]) > 50
    assert {key for row in data["versions"] for key in row["held_by"]} <= set(data["holders"])
    assert data["holders"]["web-c1"]["owner_decision"]


def test_a_collapse_that_deletes_a_retained_version_fails(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    lock = root / "methodology" / "released.lock"
    lock.write_text(
        "".join(
            line + "\n"
            for line in lock.read_text(encoding="utf-8").splitlines()
            if not line.startswith("burst-tti@1.2.9 ")
        ),
        encoding="utf-8",
    )
    errors = retained_errors(root)
    assert any("burst-tti@1.2.9" in e and "no row" in e for e in errors)


def test_an_entry_needs_a_known_holder_and_a_real_version(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    path = root / RETAINED
    path.write_text(
        path.read_text(encoding="utf-8")
        + '  - {id: burst-tti, version: "9.9.9", held_by: [nobody]}\n'
        + '  - {id: burst-tti, version: "1.2.9", held_by: [web-w1]}\n',
        encoding="utf-8",
    )
    errors = retained_errors(root)
    assert any("9.9.9 names unknown holder nobody" in e for e in errors)
    assert any(
        "9.9.9 is retained but the catalog has no such document version" in e for e in errors
    )
    assert any("1.2.9 is listed twice" in e for e in errors)
