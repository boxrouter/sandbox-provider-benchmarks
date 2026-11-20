# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""The fields a results page draws from the methodology alone: each recorded step's label and
where it is shown, and the stages that open switched off."""

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, ValidationError

from sandbox_benchmark_verifier.catalog import load_methodology, load_schema
from sandbox_benchmark_verifier.parameters import document_errors
from sandbox_benchmark_verifier.yaml_load import load_yaml

ROOT = Path(__file__).resolve().parents[1]

DOCUMENTS = {item.document["id"]: item.document for item in load_methodology()}


def recording_steps(document: dict[str, Any]) -> list[dict[str, Any]]:
    return [step for step in document["procedure"]["steps"] if step.get("record_steps")]


def validate(document: dict[str, Any]) -> None:
    Draft202012Validator(load_schema("methodology")).validate(document)


@pytest.mark.parametrize("field", ["label", "show"])
def test_a_recording_step_without_its_page_field_fails(field: str) -> None:
    document = deepcopy(DOCUMENTS["pause-resume"])
    del recording_steps(document)[0][field]
    with pytest.raises(ValidationError, match=field):
        validate(document)


def test_show_is_stack_hidden_or_table() -> None:
    document = deepcopy(DOCUMENTS["pause-resume"])
    recording_steps(document)[0]["show"] = "bar"
    with pytest.raises(ValidationError):
        validate(document)


def test_a_step_that_records_nothing_needs_no_page_field() -> None:
    document = DOCUMENTS["pause-resume"]
    assert any(
        "record_steps" not in step and "show" not in step for step in document["procedure"]["steps"]
    )


def test_every_recording_step_has_its_page_fields() -> None:
    for identity, document in DOCUMENTS.items():
        for step in recording_steps(document) if document.get("procedure") else []:
            assert step["label"] and step["show"] in {"stack", "hidden", "table"}, identity


def test_only_pause_and_resume_read_opens_switched_off() -> None:
    off = {
        (identity, stage["id"])
        for identity, document in DOCUMENTS.items()
        for stage in (document.get("procedure") or {}).get("stages", [])
        if stage.get("default_off")
    }
    assert off == {("pause-resume", "read")}


def test_today_shown_steps_of_pause_and_delayed_resume() -> None:
    def shown(identity: str) -> dict[str, str]:
        return {
            step["record_steps"][0]: step["show"] for step in recording_steps(DOCUMENTS[identity])
        }

    assert shown("pause-resume") == {
        "create": "hidden",
        "pause": "stack",
        "resume": "stack",
        "data-back": "stack",
        "destroy": "hidden",
    }
    assert shown("delayed-resume") == {
        "create": "hidden",
        "control": "table",
        "pause": "hidden",
        "resume": "stack",
        "data-back": "stack",
        "destroy": "hidden",
    }


def view_errors(document: dict[str, Any]) -> list[str]:
    return list(document_errors(ROOT, Path("x.yaml"), document, DOCUMENTS))


def test_a_procedure_without_a_default_view_fails() -> None:
    document = deepcopy(DOCUMENTS["pause-resume"])
    del document["default_view"]
    assert "x.yaml: a procedure needs a default_view" in view_errors(document)


@pytest.mark.parametrize(
    ("field", "value"), [("metric", "no_such_metric"), ("variant", "no-such-variant")]
)
def test_a_default_view_names_its_own_metric_and_variant(field: str, value: str) -> None:
    document = deepcopy(DOCUMENTS["pause-resume"])
    document["default_view"][field] = value
    assert any(f"default_view {field} {value}" in error for error in view_errors(document))


def test_today_default_views_that_differ_from_the_first_listed() -> None:
    views = {identity: document.get("default_view") for identity, document in DOCUMENTS.items()}
    assert views["pause-resume"] == {"metric": "pause_to_data_back", "variant": "storage-2gib"}
    assert views["delayed-resume"] == {"metric": "resume_to_data_back", "variant": "wait-15min"}
    assert views["snapshot-and-restore"] == {
        "metric": "total_time",
        "variant": "marker",
        "sandbox": 2,
    }
    assert views["fork"] == {"metric": "total_time", "variant": "marker-4-forks", "sandbox": 2}
    assert views["package-registries"] == {"metric": "install_runtime_ms", "variant": "git-clone"}


def not_displayed() -> list[tuple[str, dict[str, Any]]]:
    return [
        (identity, entry)
        for identity, document in DOCUMENTS.items()
        for entry in (document.get("composition") or {}).get("not_displayed", [])
    ]


def test_a_not_displayed_reason_without_an_id_fails() -> None:
    document = deepcopy(DOCUMENTS["cost-index"])
    del document["composition"]["not_displayed"][0]["id"]
    with pytest.raises(ValidationError, match="'id' is a required property"):
        validate(document)


def test_one_condition_keeps_one_reason_id() -> None:
    ids: dict[str, set[str]] = {}
    for _, entry in not_displayed():
        ids.setdefault(entry["condition"], set()).add(entry["id"])
    assert all(len(found) == 1 for found in ids.values())
    conditions: dict[str, set[str]] = {}
    for _, entry in not_displayed():
        conditions.setdefault(entry["id"], set()).add(entry["condition"])
    assert all(len(found) == 1 for found in conditions.values())


def test_a_workload_without_a_provider_sourced_cost_has_its_reason_id() -> None:
    assert {
        identity for identity, entry in not_displayed() if entry["id"] == "no-provider-cost"
    } == {"cost-index", "index.cost_score"}


def test_sequential_tti_variants_have_today_short_command_ids() -> None:
    rows = load_yaml((ROOT / "names" / "names.yaml").read_text(encoding="utf-8"))["names"]
    shorts = {
        row["id"]: row.get("short")
        for row in rows
        if row["kind"] == "variant" and row.get("parent") == "sequential-tti"
    }
    assert shorts == {"true": "true", "python3": "python3", "node": "node", "git": "git"}


@pytest.mark.parametrize("document", ["7-zip-1-thread", "7-zip-all-vcpus"])
def test_a_7_zip_rating_names_its_cpu_stage_row(document: str) -> None:
    rows = load_yaml((ROOT / "names" / "names.yaml").read_text(encoding="utf-8"))["names"]
    stages = {row["id"] for row in rows if row["kind"] == "stage" and row.get("parent") == "cpu"}
    phases = {
        metric["name"]: metric["phase"]
        for metric in DOCUMENTS[document]["metrics"]
        if "phase" in metric
    }
    assert phases == {
        "compress_mips": {"kind": "stage", "id": "compression", "parent": "cpu"},
        "decompress_mips": {"kind": "stage", "id": "decompression", "parent": "cpu"},
    }
    assert {phase["id"] for phase in phases.values()} <= stages


def test_a_workload_opens_on_the_metric_its_indexes_read() -> None:
    """Today's page opens a replay suite on its workload value: each pool's geometric mean of the
    first metric, which the Time and Cost indexes read (`bench.indexes`, "its first"). A set
    total (formula over the pool's cells, such as `sum(median(sum(...)))`) has no value per cell,
    so a bar that drew it would have nothing on a single trace, job or task."""
    workloads = {
        identity: document
        for identity, document in DOCUMENTS.items()
        if document.get("category") == "workload" and document.get("default_view", {}).get("metric")
    }
    assert {"deepswe", "terminal-bench-4-0", "github-actions"} <= set(workloads)
    for identity, document in workloads.items():
        assert document["default_view"]["metric"] == document["metrics"][0]["name"], identity
