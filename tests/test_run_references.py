# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""A methodology page links its run definition, ``parameters/<id>/run.json``, at the release tag."""

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from sandbox_benchmark_verifier import run_references
from sandbox_benchmark_verifier.names import Tables
from sandbox_benchmark_verifier.text import _methodologies

ROOT = Path(__file__).parents[1]
SOURCE = {
    "repository": "https://github.com/boxrouter/sandbox-provider-benchmarks",
    "root": "parameters",
}


def _version() -> str:
    return run_references.release_version(ROOT)


def test_every_document_with_a_run_definition_links_exactly_that_file(
    bundle: dict[str, Any],
) -> None:
    linked = 0
    for document in _methodologies(ROOT):
        parameters = document.get("parameters")
        if not isinstance(parameters, dict):
            continue
        run = ROOT / parameters["path"] / "run.json"
        reference = bundle["methodologies"][document["id"]].get("procedure")
        if not run.is_file():
            assert reference is None or document.get("composition")
            continue
        linked += 1
        assert reference == {
            "version": _version(),
            "source": {**SOURCE, "ref": f"v{_version()}"},
            "files": [
                {
                    "path": f"{run.parent.name}/run.json",
                    "sha256": hashlib.sha256(run.read_bytes()).hexdigest(),
                }
            ],
        }
    assert linked


def test_the_links_are_public_and_point_at_no_procedure_source(bundle: dict[str, Any]) -> None:
    assert bundle["procedures_public"] is True
    text = json.dumps([row.get("procedure") for row in bundle["methodologies"].values()])
    assert "sandbox_benchmarks" not in text
    assert "src/" not in text


def test_a_composed_index_links_each_input_run_definition_once() -> None:
    tables = Tables(
        names=[{"kind": "index", "id": "index"}, {"kind": "test", "id": "a"}],
        aliases={},
        metric_label_grammar={},
    )
    first = {"path": "a/run.json", "sha256": "0" * 64}
    document = {"composition": {"inputs": [{"id": "a"}, "a"]}}
    found = run_references.references(
        "index",
        document,
        {"index": document, "a": {"id": "a"}},
        {"a": first},
        tables=tables,
    )
    assert found == [first]


def test_composed_procedure_cycle_is_rejected() -> None:
    tables = Tables(names=[{"kind": "index", "id": "example"}], aliases={}, metric_label_grammar={})
    document = {"composition": {"inputs": [{"id": "example"}]}}
    with pytest.raises(ValueError, match="cyclic benchmark procedure composition"):
        run_references.references("example", document, {"example": document}, {}, tables=tables)


def test_a_parameters_path_outside_the_parameters_folder_has_no_run_definition(
    tmp_path: Path,
) -> None:
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "elsewhere" / "run.json").write_text("{}")
    assert run_references.run_definition(tmp_path, {"parameters": {"path": "elsewhere/"}}) is None
    assert (
        run_references.run_definition(tmp_path, {"parameters": {"path": "parameters/../x/"}})
        is None
    )
