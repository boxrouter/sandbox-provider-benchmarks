# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""A benchmark added after the template must meet every new-benchmark requirement."""

import json
import shutil
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator

from sandbox_benchmark_verifier.new_benchmark import (
    OPERATIONS,
    PRE_TEMPLATE_IDS,
    new_benchmark_errors,
    operations,
    pre_template_ids,
)

ROOT = Path(__file__).resolve().parents[1]

VALID: dict[str, Any] = {
    "id": "fixture-new",
    "version": "1.0.0",
    "procedure": {
        "concurrency": {"trials_in_parallel": 10, "sandboxes_at_once": 1},
        "steps": [
            {"id": "create", "operation": "create", "timeout_seconds": 300},
            {"id": "snapshot", "operation": "snapshots.create", "timeout_seconds": 600},
            {"id": "exec", "operation": "commands.run", "timeout_seconds": 60},
            {"id": "destroy", "operation": "destroy", "timeout_seconds": 300},
        ],
    },
    "interpretation": {"informs": "Which provider forks fastest.", "what_to_do": "Pick it."},
    "validity_checks": [{"id": "marker", "threat": "A stale fork.", "check": "Read the marker."}],
    "source": {"name": "Fixture", "version": "1", "licence": "Apache-2.0"},
    "cost": {"method": "provider_reported_or_priced"},
    "environment": {"size": {"vcpu": 2, "memory_gib": 4}},
    "capabilities": {"required": ["snapshots"]},
    "statistics": {"ranking": {"ranked": True, "min_successes": 20}},
}


def _step(document: dict[str, Any], index: int) -> dict[str, Any]:
    step: dict[str, Any] = document["procedure"]["steps"][index]
    return step


def _set(path: str, value: Any) -> Callable[[dict[str, Any]], None]:
    *parents, last = path.split(".")

    def change(document: dict[str, Any]) -> None:
        for key in parents:
            document = document[key]
        document[last] = value

    return change


def _drop_operation(document: dict[str, Any]) -> None:
    del _step(document, 2)["operation"]


def _unknown_operation(document: dict[str, Any]) -> None:
    _step(document, 2)["operation"] = "commands.exec"


def _long_command(document: dict[str, Any]) -> None:
    _step(document, 2)["timeout_seconds"] = 61


def _drop_licence(document: dict[str, Any]) -> None:
    del document["source"]["licence"]


CASES: list[tuple[str, Callable[[dict[str, Any]], None], str | None]] = [
    ("valid", lambda _: None, None),
    ("larger-size", _set("environment.size", {"vcpu": 4, "memory_gib": 8}), None),
    ("step-without-operation", _drop_operation, "every step needs an operation"),
    ("unknown-operation", _unknown_operation, "step exec has unknown operation commands.exec"),
    ("no-informs", _set("interpretation.informs", " "), "interpretation.informs is empty"),
    ("no-what-to-do", _set("interpretation.what_to_do", ""), "interpretation.what_to_do is empty"),
    ("no-validity-check", _set("validity_checks", []), "needs at least one validity check"),
    ("no-licence", _drop_licence, "source.licence is missing"),
    (
        "unpriced-cost",
        _set("cost.method", "not_published"),
        "cost.method must be provider_reported_or_priced",
    ),
    (
        "long-command",
        _long_command,
        "step exec runs commands.run for more than 60 s; use process.start",
    ),
    (
        "too-many-at-once",
        _set("procedure.concurrency", {"trials_in_parallel": 6, "sandboxes_at_once": 2}),
        "more than 10 sandboxes at once",
    ),
    (
        "other-size",
        _set("environment.size", {"vcpu": 8, "memory_gib": 16}),
        "size must be 2 vCPU / 4 GiB or 4 vCPU / 8 GiB",
    ),
    (
        "missing-gate",
        _set("capabilities.required", []),
        "operation snapshots.create needs capability snapshots",
    ),
    (
        "ranked-too-early",
        _set("statistics.ranking.min_successes", 19),
        "ranked with fewer than 20 successes",
    ),
]


def _tree(tmp_path: Path, document: dict[str, Any], exempt: list[str]) -> Path:
    (tmp_path / "methodology").mkdir(parents=True)
    (tmp_path / "methodology/fixture.yaml").write_text(yaml.safe_dump(document))
    (tmp_path / "contracts").mkdir()
    shutil.copy(ROOT / OPERATIONS, tmp_path / OPERATIONS)
    (tmp_path / PRE_TEMPLATE_IDS).write_text(json.dumps({"ids": exempt}))
    return tmp_path


@pytest.mark.parametrize(
    ("change", "message"), [case[1:] for case in CASES], ids=[c[0] for c in CASES]
)
def test_each_new_benchmark_requirement_fails_with_its_own_message(
    tmp_path: Path, change: Callable[[dict[str, Any]], None], message: str | None
) -> None:
    document = deepcopy(VALID)
    change(document)
    expected = [] if message is None else [f"fixture-new 1.0.0: new benchmark: {message}"]
    assert new_benchmark_errors(_tree(tmp_path, document, [])) == expected


def test_pre_template_and_planned_documents_are_exempt(tmp_path: Path) -> None:
    document = {"id": "fixture-new", "version": "1.0.0", "procedure": {"steps": [{"id": "a"}]}}
    assert new_benchmark_errors(_tree(tmp_path / "listed", document, ["fixture-new"])) == []
    planned = {**document, "status": "planned"}
    assert new_benchmark_errors(_tree(tmp_path / "planned", planned, [])) == []


def test_a_listed_id_no_longer_in_the_catalog_fails(tmp_path: Path) -> None:
    root = _tree(tmp_path, deepcopy(VALID), ["fixture-new", "fixture-gone"])
    assert new_benchmark_errors(root) == [
        f"{PRE_TEMPLATE_IDS}: fixture-gone is no longer in the catalog; remove it"
    ]


# Never add an id; when ids are removed, lower this number.
PRE_TEMPLATE_COUNT = 40


def test_the_pre_template_list_cannot_grow() -> None:
    listed = pre_template_ids(ROOT)["ids"]
    assert listed == sorted(listed)
    assert len(listed) <= PRE_TEMPLATE_COUNT


def test_operations_give_a_timeout_action_timer_and_gate() -> None:
    known = operations(ROOT)
    assert len(known) == 18
    for entry in known.values():
        assert set(entry) == {"timeout_seconds", "action", "timer", "gate"}
        assert isinstance(entry["timeout_seconds"], int) and entry["timeout_seconds"] > 0
        assert set(entry["timer"]) == {"from", "until"}
    gates = {name: entry["gate"] for name, entry in known.items() if entry["gate"]}
    assert gates == {
        "pause": "pause_resume",
        "resume": "pause_resume",
        "snapshots.create": "snapshots",
        "restore": "snapshots",
        "previews.open": "previews",
    }


def test_the_shape_contract_example_is_a_valid_shape() -> None:
    schema = json.loads((ROOT / "schemas/shape.schema.json").read_text())
    shape: dict[str, Any] = {
        "schema": "sandbox-benchmarks.shape/1",
        "block": "snapshot",
        "launch": "sequential",
        "reads": ["command", "marker", "restores", "snapshots"],
        "steps": [
            {"id": "create", "operation": "create", "sandbox": "first", "timed": True},
            {"id": "marker", "operation": "commands.run", "sandbox": "first", "timed": False},
        ],
    }
    validator = Draft202012Validator(schema)
    assert [error.message for error in validator.iter_errors(shape)] == [
        "'owner' is a required property"
    ]
    shape["steps"][1]["owner"] = "provider"
    assert validator.is_valid(shape)
    assert {step["operation"] for step in shape["steps"]} <= set(operations(ROOT))
