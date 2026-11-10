# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Generate real scaffolds against the current schema and conventions in CI."""

import json
import shutil
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import ValidationError

from sandbox_benchmark_verifier import lifecycle_profile
from sandbox_benchmark_verifier.catalog import default_context
from sandbox_benchmark_verifier.cli import main, validate
from sandbox_benchmark_verifier.lifecycle_profile import profiles
from sandbox_benchmark_verifier.lock import check as lock_errors
from sandbox_benchmark_verifier.name_refs import source_name_errors
from sandbox_benchmark_verifier.names import load_tables
from sandbox_benchmark_verifier.scaffold import BenchmarkDraft, add, add_from_shape
from sandbox_benchmark_verifier.text import text_warnings

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "example.invalid/fixture@sha256:" + "0" * 64


def author_gaps(identifier: str) -> list[str]:
    """The new-benchmark requirements a command-mode draft leaves for its author to answer."""
    return [
        f"{identifier} 1.0.0: new benchmark: {what}"
        for what in (
            "every step needs an operation",
            "interpretation.informs is empty",
            "interpretation.what_to_do is empty",
            "needs at least one validity check",
            "source.licence is missing",
            "ranked with fewer than 20 successes",
        )
    ]


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    for folder in (
        "methodology",
        "names",
        "parameters",
        "schemas",
        "contracts",
        "conventions",
        "reader",
    ):
        shutil.copytree(ROOT / folder, tmp_path / folder)
    return tmp_path


def sweep() -> dict[str, object]:
    return {
        "parameter": "image_size_mib",
        "name": "Image Size",
        "unit": "MiB",
        "values": [
            {"value": size, "image": "example.invalid/fixture@sha256:" + f"{size:064x}"}
            for size in (64, 256, 1024)
        ],
    }


@pytest.mark.parametrize("measurement", ["command", "startup"])
def test_generated_definition_passes_current_schema_and_conventions(
    tree: Path, measurement: str
) -> None:
    before = load_tables(tree).names
    name_files = {p.name: p.read_bytes() for p in (tree / "names").iterdir() if p.is_file()}
    previous = set((tree / "methodology").rglob("*.yaml"))
    path = add(
        tree,
        BenchmarkDraft(
            "fixture-image-startup",
            "Fixture Image Startup",
            "true",
            IMAGE,
            measurement=measurement,
            sweep=sweep() if measurement == "startup" else None,
        ),
    )
    assert set((tree / "methodology").rglob("*.yaml")) - previous == {path}
    assert validate(tree) == author_gaps("fixture-image-startup")
    assert text_warnings(tree) == []
    assert load_tables(tree).names[: len(before)] == before
    assert {p.name: p.read_bytes() for p in (tree / "names").iterdir() if p.is_file()} == name_files
    [profile] = profiles(tree)
    assert len(profile.workloads()) == (3 if measurement == "startup" else 1)
    if measurement == "startup":
        document = yaml.safe_load(path.read_text())
        assert document["procedure"]["sweep"] == sweep()
        assert document["metrics"][0]["formula"] == "end(command) - start(create)"
        assert [w["data"]["sweep"]["value"] for w in profile.workloads()] == [64, 256, 1024]


def test_reuse_is_checked_before_any_write(tree: Path) -> None:
    request = BenchmarkDraft("fixture-new", "Fixture New", "true", IMAGE)
    path = add(tree, request)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="reuse an existing methodology: fixture-new"):
        add(tree, request)
    assert path.read_bytes() == before


def test_future_schema_requirement_cannot_silently_make_the_template_stale(tree: Path) -> None:
    path = tree / "schemas/methodology.schema.json"
    schema = json.loads(path.read_text())
    schema["required"].append("future-required-fact")
    path.write_text(json.dumps(schema))
    with pytest.raises(ValidationError, match="future-required-fact"):
        add(tree, BenchmarkDraft("fixture-new", "Fixture New", "true", IMAGE))
    assert not (tree / "methodology/fixture-new").exists()
    assert not (tree / "parameters/fixture-new/1.0.0").exists()


def test_cli_generates_one_image_sweep_definition(
    tree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dimensions = tmp_path / "sweep.json"
    dimensions.write_text(json.dumps(sweep()))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "sandbox-benchmark-verify",
            "scaffold",
            "--root",
            str(tree),
            "--id",
            "fixture-startup",
            "--name",
            "Fixture Startup",
            "--image",
            IMAGE,
            "--measurement",
            "startup",
            "--sweep",
            str(dimensions),
        ],
    )
    assert main() == 0
    assert len(list((tree / "methodology/fixture-startup").glob("*.yaml"))) == 1
    assert validate(tree) == author_gaps("fixture-startup")
    assert text_warnings(tree) == []


def test_profile_name_is_owned_once_and_reader_mentions_still_require_references(
    tree: Path,
) -> None:
    request = BenchmarkDraft("fixture-owned-name", "Fixture Owned Name", "true", IMAGE)
    path = add(tree, request)
    document = yaml.safe_load(path.read_text())
    assert document["name"] == request.name
    assert document["procedure"]["steps"][0]["name"] == "{name:stage.create}"
    assert document["procedure"]["steps"][-1]["name"] == "{name:stage.destroy}"
    assert source_name_errors(tree) == []
    document["notes"]["summary"] = request.name
    path.write_text(yaml.safe_dump(document))
    assert source_name_errors(tree) == [
        "fixture-owned-name@1.0.0.notes.summary: canonical name 'Fixture Owned Name' "
        "requires a name placeholder"
    ]


def _profile_successor(tree: Path, source: Path, version: str, common: dict[str, str]) -> Path:
    document = yaml.safe_load(source.read_text())
    old = tree / document["parameters"]["path"]
    directory = tree / "parameters" / document["id"] / version
    shutil.copytree(old, directory)
    document["version"] = version
    document["inherits"] = common
    document["parameters"]["path"] = str(directory.relative_to(tree))
    target = source.with_name(f"{version}.yaml")
    target.write_text(yaml.safe_dump(document))
    return target


def test_lifecycle_discovery_binds_context_before_version_and_exact_profile_keeps_old_version(
    tree: Path,
) -> None:
    source = add(tree, BenchmarkDraft("fixture-context", "Fixture Context", "true", IMAGE))
    declared = default_context(tree)
    assert yaml.safe_load(source.read_text())["inherits"] == declared
    # 00-common.yaml holds several documents, so every common file is read as a stream.
    future = deepcopy(
        next(
            document
            for path in sorted((tree / "methodology").glob("00-common*.yaml"))
            for document in yaml.safe_load_all(path.read_text())
            if document and document["version"] == declared["version"]
        )
    )
    future["version"] = "1.0.999"
    future["environment"]["region_policy"] = {"selection": "no_portable_region_request"}
    (tree / "methodology/00-common-1.0.999.yaml").write_text(yaml.safe_dump(future))
    other = {"id": "sbs.common", "version": "1.0.999"}
    _profile_successor(tree, source, "9.0.0", other)
    _profile_successor(tree, source, "1.0.1", declared)
    assert [p.method.document["version"] for p in profiles(tree)] == ["1.0.1"]
    assert [p.method.document["version"] for p in profiles(tree, common=other)] == ["9.0.0"]
    exact = lifecycle_profile.profile(tree, {"id": "fixture-context", "version": "1.0.0"})
    assert exact.method.document == yaml.safe_load(source.read_text())
    assert exact.workloads()


def test_lifecycle_exact_profile_refuses_invalid_missing_or_escaping_reference(tree: Path) -> None:
    source = add(tree, BenchmarkDraft("fixture-exact", "Fixture Exact", "true", IMAGE))
    with pytest.raises(ValueError, match="exact"):
        lifecycle_profile.profile(tree, {"id": "fixture-exact"})
    with pytest.raises(ValueError, match="missing or ambiguous"):
        lifecycle_profile.profile(tree, {"id": "fixture-exact", "version": "9.0.0"})
    document = yaml.safe_load(source.read_text())
    document["parameters"]["path"] = "../outside"
    source.write_text(yaml.safe_dump(document))
    with pytest.raises(ValueError, match="inside"):
        lifecycle_profile.profile(tree, {"id": "fixture-exact", "version": "1.0.0"})


@pytest.mark.parametrize("defect", ["ambiguous", "missing-common", "future-common", "policy"])
def test_exact_lifecycle_profile_refuses_invalid_bound_context(tree: Path, defect: str) -> None:
    source = add(tree, BenchmarkDraft("fixture-bound", "Fixture Bound", "true", IMAGE))
    document = yaml.safe_load(source.read_text())
    if defect == "ambiguous":
        source.with_name("duplicate.yaml").write_text(source.read_text())
        message = "missing or ambiguous"
    elif defect == "missing-common":
        document["inherits"]["version"] = "9.0.0"
        source.write_text(yaml.safe_dump(document))
        message = "common is missing or ambiguous"
    else:
        common_ref = document["inherits"]
        # The file that holds that version alone (00-common.yaml is a stream of several).
        common_path = next(
            path
            for path in sorted((tree / "methodology").glob("00-common*.yaml"))
            if [d["version"] for d in yaml.safe_load_all(path.read_text()) if d]
            == [common_ref["version"]]
        )
        common = yaml.safe_load(common_path.read_text())
        if defect == "future-common":
            common["effective"] = "9999-01-01"
            message = "common takes effect after"
        else:
            common["environment"]["region_policy"] = {"selection": "no_portable_region_request"}
            document["environment"]["region_policy"] = "legacy US request"
            source.write_text(yaml.safe_dump(document))
            message = "leaf region policy conflicts"
        common_path.write_text(yaml.safe_dump(common))
    with pytest.raises(ValueError, match=message):
        lifecycle_profile.profile(tree, {"id": "fixture-bound", "version": "1.0.0"})


def test_lifecycle_discovery_requires_declared_default_when_a_protocol_exists(tree: Path) -> None:
    add(tree, BenchmarkDraft("fixture-default", "Fixture Default", "true", IMAGE))
    path = tree / "names/names.yaml"
    names = yaml.safe_load(path.read_text())
    names.pop("catalog_selection")
    path.write_text(yaml.safe_dump(names))
    with pytest.raises(ValueError, match="exact default common"):
        profiles(tree)


SHAPES = ROOT / "tests/fixtures/shapes"


def shape_and_draft(block: str) -> tuple[dict[str, Any], dict[str, Any]]:
    shape: dict[str, Any] = json.loads((SHAPES / f"{block}.json").read_text())
    draft: dict[str, Any] = yaml.safe_load((SHAPES / f"{block}-draft.yaml").read_text())
    return shape, draft


@pytest.mark.parametrize(
    ("block", "expected"),
    [
        (
            "snapshot",
            {
                "mode": "sequential",
                "sandboxes_per_trial": 2,
                "capabilities": ["snapshots"],
                "stages": ["snapshot", "create-from-snapshot", "exec"],
                "fresh_sandbox": "exception",
            },
        ),
        (
            "pause_resume",
            {
                "mode": "single",
                "sandboxes_per_trial": 1,
                "capabilities": ["pause_resume"],
                "stages": ["pause", "resume", "exec"],
                "fresh_sandbox": "meets",
            },
        ),
        (
            "reconnect",
            {
                "mode": "single",
                "sandboxes_per_trial": 1,
                "capabilities": [],
                "stages": ["attach", "true"],
                "fresh_sandbox": "meets",
            },
        ),
    ],
)
def test_a_shape_and_draft_generate_a_valid_new_benchmark(
    tree: Path, block: str, expected: dict[str, Any]
) -> None:
    name_files = {p.name: p.read_bytes() for p in (tree / "names").iterdir() if p.is_file()}
    shape, draft = shape_and_draft(block)
    identifier = "fixture-" + block.replace("_", "-")
    path = add_from_shape(tree, identifier, "03-sandbox-operations", shape, draft)
    assert path == tree / f"methodology/03-sandbox-operations/{identifier}.yaml"
    assert validate(tree) == []
    assert text_warnings(tree) == []
    assert {p.name: p.read_bytes() for p in (tree / "names").iterdir() if p.is_file()} == name_files
    document = yaml.safe_load(path.read_text())
    procedure = document["procedure"]
    assert {
        "mode": procedure["mode"],
        "sandboxes_per_trial": procedure["sandboxes_per_trial"],
        "capabilities": document["capabilities"]["required"],
        "stages": [stage["id"] for stage in procedure["stages"]],
        "fresh_sandbox": document["independence_answers"]["fresh_sandbox"]["status"],
    } == expected
    assert [step["operation"] for step in procedure["steps"]] == [
        step["operation"] for step in shape["steps"]
    ]
    assert [step["record_steps"] for step in procedure["steps"]] == [
        [step["id"]] for step in shape["steps"]
    ]
    assert procedure["concurrency"] == {"trials_in_parallel": 10, "sandboxes_at_once": 1}
    assert sorted((tree / f"parameters/{identifier}/1.0.0").iterdir()) == [
        tree / f"parameters/{identifier}/1.0.0/workloads.json"
    ]


def test_a_repeated_shape_step_is_a_repeated_methodology_step(tree: Path) -> None:
    shape, draft = shape_and_draft("reconnect")
    path = add_from_shape(tree, "fixture-reconnect", "03-sandbox-operations", shape, draft)
    steps = yaml.safe_load(path.read_text())["procedure"]["steps"]
    assert {step["id"]: step.get("repeats", False) for step in steps} == {
        "create": False,
        "attach": True,
        "true": True,
        "destroy": False,
    }


def test_snapshot_steps_take_their_sandboxes_timers_and_overrides(tree: Path) -> None:
    shape, draft = shape_and_draft("snapshot")
    path = add_from_shape(tree, "fixture-snapshot", "03-sandbox-operations", shape, draft)
    steps = {step["id"]: step for step in yaml.safe_load(path.read_text())["procedure"]["steps"]}
    assert steps["snapshot"]["timer"] == {
        "from": "the snapshot request",
        "until": "the snapshot call returns",
    }
    assert steps["snapshot"]["timeout_seconds"] == 600
    assert steps["snapshot"]["in_sandboxes"] == [1]
    assert steps["create_from_snapshot"]["in_sandboxes"] == [2]
    assert "in_sandboxes" not in steps["destroy"]
    assert steps["marker"]["timer"] is None
    assert steps["marker"]["action"] == draft["steps"]["marker"]["action"]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda shape, draft: shape["steps"][1].update(operation="commands.exec"), "unknown"),
        (lambda shape, draft: shape["steps"][1].pop("owner"), "'owner' is a required property"),
        (lambda shape, draft: draft["workloads"][0]["data"].pop("restores"), "must equal"),
        (lambda shape, draft: draft.pop("validity_checks"), "'validity_checks' is a required"),
    ],
)
def test_a_bad_shape_or_draft_writes_nothing(tree: Path, change: Any, message: str) -> None:
    shape, draft = shape_and_draft("snapshot")
    change(shape, draft)
    with pytest.raises((ValueError, ValidationError), match=message):
        add_from_shape(tree, "fixture-snapshot", "03-sandbox-operations", shape, draft)
    assert not (tree / "methodology/03-sandbox-operations/fixture-snapshot.yaml").exists()
    assert not (tree / "parameters/fixture-snapshot").exists()


def test_an_existing_id_or_file_is_refused(tree: Path) -> None:
    shape, draft = shape_and_draft("snapshot")
    with pytest.raises(ValueError, match="reuse an existing methodology: fork"):
        add_from_shape(tree, "fork", "03-sandbox-operations", shape, draft)
    add_from_shape(tree, "fixture-snapshot", "03-sandbox-operations", shape, draft)
    with pytest.raises(ValueError, match="reuse an existing methodology: fixture-snapshot"):
        add_from_shape(tree, "fixture-snapshot", "03-sandbox-operations", shape, draft)


def test_a_shape_benchmark_lands_with_its_lock_row(tree: Path) -> None:
    shape, draft = shape_and_draft("snapshot")
    add_from_shape(tree, "fixture-snapshot", "03-sandbox-operations", shape, draft)
    assert "fixture-snapshot@1.0.0 " in (tree / "methodology/released.lock").read_text()
    assert lock_errors(tree, require_complete=True) == []


def test_cli_writes_a_shape_benchmark_and_prints_its_path(
    tree: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "sandbox-benchmark-verify",
            "scaffold",
            "--root",
            str(tree),
            "--id",
            "fixture-snapshot",
            "--shape",
            str(SHAPES / "snapshot.json"),
            "--draft",
            str(SHAPES / "snapshot-draft.yaml"),
            "--category",
            "03-sandbox-operations",
        ],
    )
    assert main() == 0
    assert capsys.readouterr().out == "methodology/03-sandbox-operations/fixture-snapshot.yaml\n"


def test_only_a_benchmark_before_graduation_may_lack_a_names_row(tree: Path) -> None:
    shape, draft = shape_and_draft("snapshot")
    add_from_shape(tree, "fixture-snapshot", "03-sandbox-operations", shape, draft)
    listed_path = tree / "contracts/pre-template-ids.json"
    listed = json.loads(listed_path.read_text())
    listed["ids"] = sorted([*listed["ids"], "fixture-snapshot"])
    listed_path.write_text(json.dumps(listed))
    assert "alias document fixture-snapshot: released document has no alias row" in validate(tree)
