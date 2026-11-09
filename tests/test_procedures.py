# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Release pins resolve exact source files and one switch controls every link."""

import hashlib
import json
import shutil
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from pytest_mock import MockerFixture

from sandbox_benchmark_verifier import procedures
from sandbox_benchmark_verifier.names import Tables
from sandbox_benchmark_verifier.text import _methodologies

ROOT = Path(__file__).parents[1]


def test_all_benchmarks_have_release_pinned_sources_and_visibility_is_on(
    bundle: dict[str, Any],
) -> None:
    assert procedures.procedure_errors(ROOT) == []
    assert bundle["procedures_public"] is True
    reference = next(iter(bundle["methodologies"]["time-index"].values()))["procedure"]
    assert reference["version"] == procedures.configuration(ROOT)["version"]
    catalogue = procedures.catalogue(ROOT)
    document = next(doc for doc in _methodologies(ROOT) if doc["id"] == "time-index")
    expected_paths = {
        catalogue["procedures"][row["id"]]["path"] for row in document["composition"]["inputs"]
    }
    assert {row["path"] for row in reference["files"]} == expected_paths
    assert all("url" not in row for row in reference["files"])
    # One owner-edited base points directly to the public source mirror.
    assert reference["source"] == procedures.configuration(ROOT)["source"]
    assert reference["source"] == {
        "repository": "https://github.com/boxrouter/sandbox-provider-benchmarks",
        "root": "procedures",
        "ref": "main",
    }


def test_catalogue_digest_rejects_tampered_source_metadata(tmp_path: Path) -> None:
    shutil.copytree(ROOT / "contracts", tmp_path / "contracts")
    config = procedures.configuration(tmp_path)
    path = tmp_path / config["catalogue"]
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="procedure catalogue differs from its pinned digest"):
        procedures.catalogue(tmp_path)


@pytest.mark.parametrize(
    "change",
    [
        (
            'repository = "https://github.com/boxrouter/sandbox-provider-benchmarks"',
            'repository = "https://example.invalid/source"',
        ),
        ('root = "procedures"', 'root = "other"'),
        ('ref = "main"', 'ref = ""'),
    ],
)
def test_source_base_must_resolve_to_public_mirror(tmp_path: Path, change: tuple[str, str]) -> None:
    shutil.copytree(ROOT / "contracts", tmp_path / "contracts")
    path = tmp_path / procedures.CONFIG
    original, replacement = change
    path.write_text(path.read_text(encoding="utf-8").replace(original, replacement))
    with pytest.raises(ValueError, match="public.*mirror"):
        procedures.catalogue(tmp_path)


def test_release_import_checks_real_source_bytes_before_writing_pin(tmp_path: Path) -> None:
    wheel = tmp_path / "sandbox_benchmarks-1.2.3-py3-none-any.whl"
    source = b"def procedure(): pass\n"
    catalogue = {
        "version": "1.2.3",
        "procedures": {
            "example": {
                "path": "src/sandbox_benchmarks/procedures/example.py",
                "sha256": hashlib.sha256(source).hexdigest(),
            }
        },
    }
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(procedures.CATALOGUE, json.dumps(catalogue))
        archive.writestr(
            "sandbox_benchmarks-1.2.3.dist-info/METADATA",
            "Name: sandbox-benchmarks\nVersion: 1.2.3\n",
        )
        archive.writestr("sandbox_benchmarks/procedures/example.py", source + b"# tampered\n")
    with pytest.raises(ValueError, match="procedure source differs from its release digest"):
        procedures.import_release(wheel, tmp_path)
    assert not (tmp_path / procedures.CONFIG).exists()


def test_release_import_keeps_the_owner_edited_source_base(tmp_path: Path) -> None:
    wheel = tmp_path / "sandbox_benchmarks-1.2.3-py3-none-any.whl"
    source = b"def procedure(): pass\n"
    catalogue = {
        "version": "1.2.3",
        "source": {"repository": "https://mirror.example/repo", "ref": "tag", "root": "root"},
        "procedures": {
            "example": {
                "path": "src/sandbox_benchmarks/procedures/example.py",
                "sha256": hashlib.sha256(source).hexdigest(),
            }
        },
    }
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(procedures.CATALOGUE, json.dumps(catalogue))
        archive.writestr(
            "sandbox_benchmarks-1.2.3.dist-info/METADATA",
            "Name: sandbox-benchmarks\nVersion: 1.2.3\n",
        )
        archive.writestr("sandbox_benchmarks/procedures/example.py", source)
    shutil.copytree(ROOT / "contracts", tmp_path / "contracts")
    base = procedures.configuration(tmp_path)["source"]
    procedures.import_release(wheel, tmp_path)
    config = procedures.configuration(tmp_path)
    assert config["procedures_public"] is True
    assert config["source"] == base
    assert procedures.catalogue(tmp_path)["source"] == base
    assert (
        tmp_path / "procedures/src/sandbox_benchmarks/procedures/example.py"
    ).read_bytes() == source


def test_public_procedure_copy_digest_is_enforced(tmp_path: Path) -> None:
    source = b"def procedure(): pass\n"
    row = {
        "path": "src/sandbox_benchmarks/procedures/example.py",
        "sha256": hashlib.sha256(source).hexdigest(),
    }
    mirror = tmp_path / "procedures" / row["path"]
    mirror.parent.mkdir(parents=True)
    mirror.write_bytes(source)
    data = {
        "source": {
            "repository": procedures.PUBLIC_REPOSITORY,
            "root": procedures.PUBLIC_SOURCE_ROOT,
            "ref": "main",
        },
        "procedures": {"example": row},
    }

    assert procedures._procedure_copy_errors(tmp_path, data) == []
    mirror.write_bytes(source + b"# edited\n")
    assert procedures._procedure_copy_errors(tmp_path, data) == [
        "procedure source copy differs from catalogue digest: " + row["path"]
    ]
    mirror.write_bytes(source)
    extra = mirror.parent / "unlisted.py"
    extra.write_text("def unlisted(): pass\n")
    assert procedures._procedure_copy_errors(tmp_path, data) == [
        "procedure source mirror contains an unpinned file: "
        "src/sandbox_benchmarks/procedures/unlisted.py"
    ]


def test_public_procedure_copy_root_cannot_escape_owner(tmp_path: Path) -> None:
    data = {
        "source": {
            "repository": procedures.PUBLIC_REPOSITORY,
            "root": "../outside",
            "ref": "main",
        },
        "procedures": {},
    }
    with pytest.raises(ValueError, match="procedure source mirror root must stay within its owner"):
        procedures._procedure_copy_errors(tmp_path, data)


def test_composed_procedure_cycle_is_rejected() -> None:
    tables = Tables(names=[{"kind": "index", "id": "example"}], aliases={}, metric_label_grammar={})
    document = {"composition": {"inputs": [{"id": "example"}]}}
    release = {"version": "1", "procedures": {}}
    with pytest.raises(ValueError, match="cyclic benchmark procedure composition"):
        procedures.references(
            "example", document, {"example": document}, release=release, tables=tables
        )


def test_one_owner_flag_changes_visibility_without_changing_any_reference(
    mocker: MockerFixture,
    bundle: dict[str, Any],
    build_text_bundle: Callable[[Path], dict[str, Any]],
) -> None:
    original = bundle
    config = procedures.configuration(ROOT)
    mocker.patch.object(
        procedures, "configuration", return_value={**config, "procedures_public": True}
    )
    visible = build_text_bundle(ROOT)
    original["procedures_public"] = True
    assert original == visible


def test_missing_contract_refuses_in_a_canonical_tree(tmp_path: Path) -> None:
    assert procedures.procedure_errors(tmp_path) == [
        f"{procedures.CONFIG}: procedures contract missing from a canonical tree"
    ]
