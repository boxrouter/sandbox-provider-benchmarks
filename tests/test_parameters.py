# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Synthetic tests for pinned parameters, shared trials, formulas and variants."""

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from sandbox_benchmark_verifier import parameters
from sandbox_benchmark_verifier.cli import validate
from sandbox_benchmark_verifier.parameters import (
    document_errors,
    formula_errors,
    tree_digest,
    workload_version,
)

pytestmark = pytest.mark.unit

WORKLOAD = {
    "id": "spotify-player",
    "label": "Spotify player",
    "data": {"trace": "a"},
    "image": "registry.example/player@sha256:" + "0" * 64,
}
WORKLOAD["version"] = workload_version(WORKLOAD)


def make(tmp_path: Path, *, version: str | None = None, digest: str | None = None) -> Path:
    """A repository with one methodology that pins one workload."""
    directory = tmp_path / "parameters" / "workload.demo"
    directory.mkdir(parents=True)
    (directory / "workloads.json").write_text(
        json.dumps([{**WORKLOAD, **({"version": version} if version else {})}])
    )
    document = {
        "id": "workload.demo",
        "parameters": {
            "path": "parameters/workload.demo/",
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


def test_workload_version_covers_the_replay_definition() -> None:
    replay = {
        "data": {},
        "repo": "https://github.com/a/b",
        "commit": "0" * 40,
        "workdir": "/app",
        "setup": ["pip install -e ."],
        "env": {},
        "replayable": True,
    }
    assert workload_version(replay) != workload_version({**replay, "commit": "1" * 40})


def repin(tmp_path: Path, directory: Path, workload: dict[str, Any]) -> None:
    """Rewrite the workload and its document's parameters digest together."""
    (directory / "workloads.json").write_text(json.dumps([workload]))
    document = {
        "id": "workload.demo",
        "parameters": {"path": "parameters/workload.demo/", "sha256": tree_digest(directory)},
    }
    (tmp_path / "methodology" / "demo.yaml").write_text(yaml.safe_dump(document))


def test_replay_workload_needs_clone_setup_and_replayable(tmp_path: Path) -> None:
    workload: dict[str, Any] = {key: value for key, value in WORKLOAD.items() if key != "image"}
    workload["repo"] = "https://github.com/a/b"
    workload["commit"] = "0" * 40
    workload["workdir"] = "/app"
    workload["setup"] = ["pip install -e ."]
    workload["env"] = {}
    workload["version"] = workload_version(workload)
    directory = make(tmp_path)
    repin(tmp_path, directory, workload)
    assert any("misses replayable" in e for e in validate(tmp_path))
    workload["replayable"] = True
    workload["version"] = workload_version(workload)
    repin(tmp_path, directory, workload)
    assert validate(tmp_path) == []


def test_not_replayable_workload_needs_a_reason(tmp_path: Path) -> None:
    workload: dict[str, Any] = {key: value for key, value in WORKLOAD.items() if key != "image"}
    workload.update(
        repo="https://github.com/a/b",
        commit="0" * 40,
        workdir="/app",
        setup=[],
        env={},
        replayable=False,
    )
    workload["version"] = workload_version(workload)
    directory = make(tmp_path)
    repin(tmp_path, directory, workload)
    assert any("gives no reason" in e for e in validate(tmp_path))


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
        "procedure": {"shared_trial": {"with": "workload.demo"}},
    }
    (tmp_path / "methodology" / "follower.yaml").write_text(yaml.safe_dump(follower))
    assert validate(tmp_path) == []
    follower["procedure"]["shared_trial"]["with"] = "workload.other"  # type: ignore[index]
    (tmp_path / "methodology" / "follower.yaml").write_text(yaml.safe_dump(follower))
    assert any("unknown methodology" in e for e in validate(tmp_path))


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


def test_a_time_metric_of_a_procedure_needs_a_formula(tmp_path: Path) -> None:
    """Every millisecond metric says how it comes from recorded steps, so its clock is known."""
    document: dict[str, Any] = {
        "id": "demo",
        "procedure": {"steps": [{"id": "create", "record_steps": ["create"]}]},
        "default_view": {"metric": "create_time"},
        "metrics": [{"name": "create_time", "unit": "ms"}, {"name": "rate", "unit": "MIPS"}],
    }
    path = tmp_path / "demo.yaml"
    assert list(document_errors(tmp_path, path, document, {})) == [
        f"{path}: metric create_time: a time metric needs a formula"
    ]
    document["metrics"][0]["formula"] = "total(create)"
    assert list(document_errors(tmp_path, path, document, {})) == []


DIGEST = "a" * 64
TARBALL_DATA = {
    "tarball": f"https://example.test/tasks/r2e-gym/demo/{DIGEST}.tar.gz",
    "sha256": DIGEST,
    "workdir": "/testbed",
    "setup": [
        "curl -fsSL -o /tmp/t.tar.gz https://example.test/t",
        f"echo '{DIGEST}  /tmp/t.tar.gz' | sha256sum -c -",
        "sudo tar -xzf /tmp/t.tar.gz -C /",
    ],
    "replayable": True,
    "replayable_reason": "the first five commands exit as recorded",
}


def tarball_problems(tmp_path: Path, **changes: Any) -> list[str]:
    """Validation problems of a tarball workload whose data has `changes` applied."""
    workload = {"id": "demo", "label": "Demo", "data": {**TARBALL_DATA, **changes}}
    workload["version"] = workload_version(workload)
    directory = tmp_path / "parameters" / "workload.demo"
    directory.mkdir(parents=True)
    (directory / "workloads.json").write_text(json.dumps([workload]))
    return list(parameters.workload_errors(directory))


def test_tarball_workload_passes(tmp_path: Path) -> None:
    assert tarball_problems(tmp_path) == []


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"sha256": "abc"}, "sha256 is not a lowercase SHA-256"),
        ({"tarball": "http://example.test/x"}, "tarball is not an https URL"),
        ({"tarball": "https://example.test/other.tar.gz"}, "does not name its sha256"),
        ({"workdir": "testbed"}, "workdir is not an absolute path"),
        ({"setup": []}, "setup must be a non-empty list"),
        ({"setup": ["sudo tar -xzf /tmp/t.tar.gz -C /"]}, "setup does not check the sha256"),
        ({"replayable": "yes"}, "replayable must be true or false"),
        ({"replayable_reason": ""}, "replayable_reason is missing"),
    ],
)
def test_tarball_fields_are_checked(tmp_path: Path, changes: dict[str, Any], expected: str) -> None:
    assert any(expected in problem for problem in tarball_problems(tmp_path, **changes))


def test_a_workload_short_must_be_text(tmp_path: Path) -> None:
    for short in ("", 3):
        workload: dict[str, Any] = {"id": "demo", "label": "Demo", "data": {}, "short": short}
        workload["version"] = workload_version(workload)
        directory = tmp_path / f"parameters-{short!r}"
        directory.mkdir()
        (directory / "workloads.json").write_text(json.dumps([workload]))
        assert any(
            "short must be a non-empty string" in e for e in parameters.workload_errors(directory)
        )


def test_a_trace_whose_label_prefixes_its_task_has_the_task_as_its_short_label() -> None:
    """Today's page names a trace by its task, so a "<agent config>: <task>" label needs a short."""
    prefixed = [
        workload
        for path in sorted((Path(__file__).parent.parent / "parameters").glob("*/workloads.json"))
        for workload in json.loads(path.read_text(encoding="utf-8"))
        if workload["label"].endswith(": " + str(workload.get("data", {}).get("task")))
    ]
    assert {w["data"]["task"] for w in prefixed} >= {"abs-stepped-slices", "batched-eval-parity"}
    assert [w["id"] for w in prefixed if w.get("short") != w["data"]["task"]] == []
