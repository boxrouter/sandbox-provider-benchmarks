# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Synthetic tests for pinned parameters, shared trials, formulas and variants."""

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from sandbox_benchmark_verifier.cli import validate
from sandbox_benchmark_verifier.parameters import (
    document_errors,
    formula_errors,
    tree_digest,
    workload_version,
)

pytestmark = pytest.mark.unit

WORKLOAD = {"id": "spotify-player", "label": "Spotify player", "data": {"trace": "a"}}
WORKLOAD["version"] = workload_version(WORKLOAD)


def make(tmp_path: Path, *, version: str | None = None, digest: str | None = None) -> Path:
    """A repository with one methodology that pins one workload."""
    directory = tmp_path / "parameters" / "workload.demo" / "1.0.0"
    directory.mkdir(parents=True)
    (directory / "workloads.json").write_text(
        json.dumps([{**WORKLOAD, **({"version": version} if version else {})}])
    )
    document = {
        "id": "workload.demo",
        "version": "1.0.0",
        "parameters": {
            "path": "parameters/workload.demo/1.0.0/",
            "sha256": digest or tree_digest(directory),
        },
    }
    (tmp_path / "methodology").mkdir()
    (tmp_path / "methodology" / "demo.yaml").write_text(yaml.safe_dump(document))
    return directory


def test_workload_version_matches_known_digest() -> None:
    assert workload_version({"data": {}, "capabilities": [], "image": None}) == (
        "sha256:3d4f685ecff31089"
    )


def test_valid_parameters_pass(tmp_path: Path) -> None:
    make(tmp_path)
    assert validate(tmp_path) == []


def test_tree_digest_mismatch_is_reported(tmp_path: Path) -> None:
    directory = make(tmp_path)
    (directory / "extra.txt").write_text("changed")
    assert any("does not match the directory tree" in e for e in validate(tmp_path))


def test_stale_workload_version_is_reported(tmp_path: Path) -> None:
    make(tmp_path, version="sha256:0000000000000000")
    assert any("version does not match its data" in e for e in validate(tmp_path))


def test_missing_parameters_directory_is_reported(tmp_path: Path) -> None:
    directory = make(tmp_path)
    (directory / "workloads.json").unlink()
    directory.rmdir()
    assert any("is missing" in e for e in validate(tmp_path))


def test_shared_trial_needs_an_owner_with_parameters(tmp_path: Path) -> None:
    make(tmp_path)
    follower = {
        "id": "workload.follower",
        "version": "1.0.0",
        "procedure": {"shared_trial": {"with": "workload.demo", "version": "1.0.0"}},
    }
    (tmp_path / "methodology" / "follower.yaml").write_text(yaml.safe_dump(follower))
    assert validate(tmp_path) == []
    follower["procedure"]["shared_trial"]["with"] = "workload.other"  # type: ignore[index]
    (tmp_path / "methodology" / "follower.yaml").write_text(yaml.safe_dump(follower))
    assert any("unknown methodology" in e for e in validate(tmp_path))


def measurement(variant: str) -> dict[str, object]:
    return {"methodology": {"id": "workload.demo", "version": "1.0.0"}, "variant": variant}


def test_measurement_variant_must_be_a_parameter_workload(tmp_path: Path) -> None:
    make(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "ok.json").write_text(json.dumps(measurement("spotify-player")))
    assert validate(tmp_path) == []
    (tmp_path / "data" / "bad.json").write_text(json.dumps(measurement("spotify_player")))
    assert any("is not a variant of workload.demo" in e for e in validate(tmp_path))


@pytest.mark.parametrize(
    "formula",
    [
        "start",
        "total - start",
        "median(latency(first-true)) + start",
        "latency(a)+end",
        "end(first-true) - start(create)",
        "median(total(true)) + median(runtime(true))",
        "sum(runtime(cmd))",
        "max(end(ready)) - min(start(create))",
        "sum(median(sum(runtime(cmd))))",
        "end(data-back) - start(resume|data-back)",
    ],
)
def test_formulas_in_the_grammar_pass(formula: str) -> None:
    assert list(formula_errors(formula, frozenset())) == []


@pytest.mark.parametrize(
    "formula",
    [
        "",
        "start +",
        "median(start",
        "bogus",
        "latency()",
        "a b",
        "end()",
        "end(a",
        "sum",
        "max(end",
    ],
)
def test_formulas_outside_the_grammar_fail(formula: str) -> None:
    assert list(formula_errors(formula, frozenset()))


def test_formula_step_names_are_checked_when_steps_are_declared() -> None:
    assert list(formula_errors("latency(x)", frozenset({"y"})))


def test_start_and_end_steps_are_checked_when_steps_are_declared() -> None:
    assert list(formula_errors("end(x) - start(y)", frozenset({"x", "y"}))) == []
    assert list(formula_errors("end(x) - start(z)", frozenset({"x", "y"})))
    assert list(formula_errors("end(x) - start(y|x)", frozenset({"x", "y"}))) == []
    assert list(formula_errors("end(x) - start(y|z)", frozenset({"x", "y"})))


def test_text_revision_reuses_only_the_same_scientific_parameter_tree(tmp_path: Path) -> None:
    make(tmp_path)
    path = tmp_path / "methodology/demo.yaml"
    original = yaml.safe_load(path.read_text())
    revision = {**original, "version": "1.0.1", "notes": {"summary": "The workload stays fixed."}}
    path.write_text(yaml.safe_dump_all([original, revision]))
    assert validate(tmp_path) == []
    revision["metrics"] = [{"name": "different", "unit": "ms"}]
    path.write_text(yaml.safe_dump_all([original, revision]))
    assert any("parameters.path must be" in error for error in validate(tmp_path))


def test_a_time_metric_of_a_procedure_needs_a_formula(tmp_path: Path) -> None:
    """Every millisecond metric says how it comes from recorded steps, so its clock is known."""
    document: dict[str, Any] = {
        "id": "demo",
        "version": "1.0.0",
        "procedure": {"steps": [{"id": "create", "record_steps": ["create"]}]},
        "metrics": [{"name": "create_time", "unit": "ms"}, {"name": "rate", "unit": "MIPS"}],
    }
    path = tmp_path / "demo.yaml"
    assert list(document_errors(tmp_path, path, document, {})) == [
        f"{path}: metric create_time: a time metric needs a formula"
    ]
    document["metrics"][0]["formula"] = "total(create)"
    assert list(document_errors(tmp_path, path, document, {})) == []
