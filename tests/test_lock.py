# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
import importlib.util
import shutil
from pathlib import Path
from types import ModuleType

import pytest

from sandbox_benchmark_verifier import lock

ROOT = Path(__file__).resolve().parents[1]
SHA = "0123456789abcdef0123456789abcdef01234567"


def stamper() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "stamp", ROOT / "scripts" / "stamp-methodology-commit.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    shutil.copytree(ROOT / "methodology", tmp_path / "methodology")
    (tmp_path / lock.LOCK_PATH).unlink()
    return tmp_path


@pytest.mark.unit
def test_committed_lock_covers_every_version() -> None:
    assert lock.check(ROOT, require_complete=True) == []


@pytest.mark.unit
def test_edit_removal_and_parameters_are_caught(repo: Path) -> None:
    lock.update(repo)
    assert lock.check(repo, require_complete=True) == []
    target = repo / "methodology" / "03-sandbox-operations" / "exec.yaml"
    original = target.read_text()
    target.write_text(original + "\nextra_field: 1\n")
    assert any("changed" in error for error in lock.check(repo))
    target.unlink()
    assert any("removed" in error for error in lock.check(repo))
    target.write_text(original)
    assert lock.check(repo) == []


@pytest.mark.unit
def test_parameters_tree_is_part_of_the_hash(repo: Path) -> None:
    before = lock.current_hashes(repo)
    key = next(iter(before))
    identifier, version = key.split("@")
    folder = repo / "parameters" / identifier / version
    folder.mkdir(parents=True)
    (folder / "workloads.json").write_text("[]")
    assert lock.current_hashes(repo)[key] != before[key]


@pytest.mark.unit
def test_update_only_appends(repo: Path) -> None:
    assert lock.update(repo)
    first = (repo / lock.LOCK_PATH).read_text()
    assert lock.update(repo) == []
    assert (repo / lock.LOCK_PATH).read_text() == first


@pytest.mark.unit
def test_unlocked_version_fails_only_when_complete_required(repo: Path) -> None:
    lock.update(repo)
    lines = (repo / lock.LOCK_PATH).read_text().splitlines(keepends=True)
    (repo / lock.LOCK_PATH).write_text("".join(lines[:-1]))
    assert lock.check(repo) == []
    assert lock.check(repo, require_complete=True)


@pytest.mark.unit
def test_stamp_rewrites_one_assignment() -> None:
    text = (ROOT / "src/sandbox_benchmark_verifier/catalog.py").read_text()
    stamped = stamper().stamp(text, SHA)
    assert f'METHODOLOGY_COMMIT = "{SHA}"' in stamped
    with pytest.raises(SystemExit):
        stamper().stamp(text, "nothex")
    with pytest.raises(SystemExit):
        stamper().stamp("x = 1\n", SHA)
