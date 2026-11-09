# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Executable policy resolves from one source and invalid references fail closed."""

from pathlib import Path
from typing import Any

import pytest
import yaml

from sandbox_benchmark_verifier.catalog import load_methodology
from sandbox_benchmark_verifier.timing import load_timing_contract, resolve, timing_errors

ROOT = Path(__file__).resolve().parents[1]


def _write_contract(root: Path, contract: dict[str, Any], version: str = "1.0.0") -> Path:
    target = root / "contracts" / "timing" / f"{version}.yaml"
    target.parent.mkdir(parents=True)
    target.write_text(yaml.safe_dump(contract))
    (root / "methodology").symlink_to(ROOT / "methodology", target_is_directory=True)
    return target


def test_timing_rules_and_referenced_values_resolve() -> None:
    assert list(timing_errors(ROOT)) == []
    contract = load_timing_contract()
    documents = load_methodology()
    assert resolve(contract, contract["readiness"]["policy"], documents) == next(
        item.document["ready_check"]
        for item in documents
        if item.document["id"] == contract["sources"]["common"]["id"]
        and item.document["version"] == contract["sources"]["common"]["version"]
    )


@pytest.mark.parametrize("defect", ["citation", "interval", "version", "missing-section"])
def test_invalid_timing_contract_is_rejected(tmp_path: Path, defect: str) -> None:
    contract: dict[str, Any] = load_timing_contract()
    if defect == "citation":
        contract["readiness"]["citation"]["pointer"] = "/does-not-exist"
    elif defect == "interval":
        contract["operations"]["create"]["interval"] = "does-not-exist"
    elif defect == "version":
        contract["version"] = "does-not-exist"
    else:
        del contract["trial"]
    target = _write_contract(tmp_path, contract)
    errors = list(timing_errors(tmp_path))
    assert len(errors) == 1
    assert errors[0].startswith(f"{target}: invalid timing contract")


def test_additive_status_deadline_contract_preserves_the_released_base() -> None:
    base = load_timing_contract("1.0.0")
    assert load_timing_contract() == base
    revised = load_timing_contract("1.0.1")
    deadline = revised["replay"]["status_dispatch_deadline"]

    values = {
        field: resolve(revised, {"pointer": deadline[field]})
        for field in ("base", "grace", "observation")
    }
    assert deadline["effective"] == "base + grace"
    assert values["base"] == base["replay"]["status_dispatch_timeout_seconds"]
    assert values["grace"] == base["readiness"]["deadline_grace_seconds"]
    assert values["observation"] == base["replay"]["status_observation_timeout_ms"]
    assert values["base"] + values["grace"] > values["observation"] / 1000
    assert revised["replay"].pop("status_dispatch_deadline") == deadline
    revised["version"] = base["version"]
    assert revised == base
    assert "status_dispatch_deadline" not in load_timing_contract("1.0.0")["replay"]


def test_additive_contract_rejects_an_inheritance_cycle(tmp_path: Path) -> None:
    target = tmp_path / "contracts" / "timing"
    target.mkdir(parents=True)
    (target / "base.yaml").write_text("extends: revision\n")
    (target / "revision.yaml").write_text("extends: base\n")
    with pytest.raises(ValueError, match="cyclic timing contract inheritance"):
        load_timing_contract("revision", tmp_path)


def test_portable_binding_revision_preserves_replay_rules_and_historical_owners() -> None:
    base = load_timing_contract("1.0.1")
    revised = load_timing_contract("1.0.2")
    assert revised["trial"]["excluded_owners"] == [
        "binding",
        "account",
        "script",
        "external",
        "trace",
    ]
    revised["trial"]["excluded_owners"] = base["trial"]["excluded_owners"]
    revised["version"] = base["version"]
    assert revised == base
    assert load_timing_contract("1.0.0")["trial"]["excluded_owners"][0] == "core"
    assert load_timing_contract("1.0.1")["trial"]["excluded_owners"][0] == "core"


@pytest.mark.parametrize("field", ["base", "grace", "observation"])
@pytest.mark.parametrize(
    ("pointer", "error"),
    [
        ("/replay/does_not_exist", "KeyError"),
        ("replay", "ValueError"),
        (None, "ValueError"),
        (False, "ValueError"),
        ([], "ValueError"),
    ],
)
def test_invalid_local_deadline_reference_is_rejected(
    tmp_path: Path, field: str, pointer: Any, error: str
) -> None:
    contract = load_timing_contract("1.0.1")
    contract["replay"]["status_dispatch_deadline"][field] = pointer
    target = _write_contract(tmp_path, contract, "1.0.1")
    assert list(timing_errors(tmp_path)) == [f"{target}: invalid timing contract ({error})"]


@pytest.mark.parametrize("field", ["base", "grace", "observation"])
@pytest.mark.parametrize("value", [None, True, "invalid", [], {}, float("nan"), float("inf")])
def test_invalid_deadline_target_type_is_rejected(tmp_path: Path, field: str, value: Any) -> None:
    contract = load_timing_contract("1.0.1")
    contract["replay"]["status_dispatch_deadline"][field] = "/replay/test_target"
    contract["replay"]["test_target"] = value
    target = _write_contract(tmp_path, contract, "1.0.1")
    assert list(timing_errors(tmp_path)) == [f"{target}: invalid timing contract (ValueError)"]


@pytest.mark.parametrize("expression", ["base - grace", "base + missing", "base", None, []])
def test_invalid_deadline_expression_is_rejected(tmp_path: Path, expression: Any) -> None:
    contract = load_timing_contract("1.0.1")
    contract["replay"]["status_dispatch_deadline"]["effective"] = expression
    target = _write_contract(tmp_path, contract, "1.0.1")
    assert list(timing_errors(tmp_path)) == [f"{target}: invalid timing contract (ValueError)"]


def test_observation_timeout_requires_integer_milliseconds(tmp_path: Path) -> None:
    contract = load_timing_contract("1.0.1")
    contract["replay"]["status_dispatch_deadline"]["observation"] = "/replay/test_target"
    contract["replay"]["test_target"] = 1.5
    target = _write_contract(tmp_path, contract, "1.0.1")
    assert list(timing_errors(tmp_path)) == [f"{target}: invalid timing contract (ValueError)"]


@pytest.mark.parametrize("deadline", [None, [], "invalid"])
def test_invalid_deadline_structure_is_rejected(tmp_path: Path, deadline: Any) -> None:
    contract = load_timing_contract("1.0.1")
    contract["replay"]["status_dispatch_deadline"] = deadline
    target = _write_contract(tmp_path, contract, "1.0.1")
    assert list(timing_errors(tmp_path)) == [f"{target}: invalid timing contract (ValueError)"]


@pytest.mark.parametrize("field", ["base", "grace", "observation", "effective"])
def test_missing_deadline_field_is_rejected(tmp_path: Path, field: str) -> None:
    contract = load_timing_contract("1.0.1")
    del contract["replay"]["status_dispatch_deadline"][field]
    target = _write_contract(tmp_path, contract, "1.0.1")
    assert list(timing_errors(tmp_path)) == [f"{target}: invalid timing contract (KeyError)"]
