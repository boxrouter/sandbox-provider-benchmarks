# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Synthetic-only verifier behavior tests."""

import json
from pathlib import Path

import pytest

from sandbox_benchmark_verifier.cli import reproduce, validate
from sandbox_benchmark_verifier.stats import bootstrap_ci, nearest_rank

pytestmark = pytest.mark.unit


def test_empty_repository_folders_are_valid(tmp_path: Path) -> None:
    assert validate(tmp_path) == []


def test_current_methodology_and_schemas_validate() -> None:
    repository = Path(__file__).parents[1]
    assert validate(repository) == []


def test_validate_handles_multi_document_methodology(tmp_path: Path) -> None:
    (tmp_path / "methodology").mkdir()
    (tmp_path / "methodology" / "cpu.yaml").write_text(
        "id: cpu\nversion: 1.0.0\n---\nid: memory\nversion: 1.0.0\n"
    )
    (tmp_path / "runs").mkdir()
    (tmp_path / "runs" / "sample.yaml").write_text("methodologies:\n  - methodology_id: cpu\n")
    assert validate(tmp_path) == []


def test_non_string_keys_are_rejected(tmp_path: Path) -> None:
    (tmp_path / "methodology").mkdir()
    (tmp_path / "methodology" / "exec.yaml").write_text(
        "id: exec\nversion: 1.0.0\nfailure_policy:\n  timeout_seconds:\n    true: 30\n"
    )
    assert validate(tmp_path) == [
        f"{tmp_path / 'methodology' / 'exec.yaml'}: key $.failure_policy.timeout_seconds.True "
        "is not a string; quote it"
    ]


def test_run_methodology_citations_are_checked_by_id(tmp_path: Path) -> None:
    (tmp_path / "methodology").mkdir()
    (tmp_path / "methodology" / "cpu.yaml").write_text("id: cpu\n")
    (tmp_path / "runs").mkdir()
    manifest = tmp_path / "runs" / "sample.yaml"
    for cited in ("methodologies:\n  - cpu\n", "methodologies:\n  cpu: '1.0.0'\n"):
        manifest.write_text(cited)
        assert validate(tmp_path) == []
    manifest.write_text("methodologies:\n  - memory\n")
    assert validate(tmp_path) == [f"{manifest}: unknown methodology memory"]


def test_ready_check_skip_lists_name_existing_methodology_ids(tmp_path: Path) -> None:
    (tmp_path / "methodology").mkdir()
    common = tmp_path / "methodology" / "common.yaml"
    common.write_text(
        "id: sbs.common\nversion: 1.0.0\nready_check:\n"
        "  skipped_by_benchmarks: [sbs.common]\n  skipped_on_restore_by_benchmarks: [sbs.common]\n"
    )
    assert validate(tmp_path) == []
    common.write_text(
        "id: sbs.common\nversion: 1.0.0\nready_check:\n"
        "  skipped_by_benchmarks: [sandbox.create.sequential_tti]\n"
        "  skipped_on_restore_by_benchmarks: [fork]\n"
    )
    assert validate(tmp_path) == [
        f"{common}: ready_check.skipped_by_benchmarks names sandbox.create.sequential_tti, "
        "which is no methodology id",
        f"{common}: ready_check.skipped_on_restore_by_benchmarks names fork, "
        "which is no methodology id",
    ]


def test_reproduce_detects_wrong_median(tmp_path: Path) -> None:
    (tmp_path / "data" / "run-a" / "raw").mkdir(parents=True)
    (tmp_path / "data" / "run-a" / "aggregates").mkdir()
    raw = tmp_path / "data" / "run-a" / "raw" / "measurements.yaml"
    raw.write_text(
        """provider: provider-a
measurement: compute
value: 10
success: true
exclusion_reason: null
---
provider: provider-a
measurement: compute
value: 20
success: true
exclusion_reason: null
---
provider: provider-a
measurement: compute
value: 30
success: true
exclusion_reason: null
---
provider: provider-b
measurement: compute
value: 3
success: true
exclusion_reason: null
"""
    )
    aggregate = tmp_path / "data" / "run-a" / "aggregates" / "cpu.yaml"
    aggregate.write_text(
        "provider: provider-a\nmeasurement: compute\n"
        "precision: {decimal_places: 9}\nstatistic: median\nvalue: 999\n"
    )
    mean = tmp_path / "data" / "run-a" / "aggregates" / "mean.yaml"
    mean.write_text(
        "provider: provider-a\nmeasurement: compute\n"
        "precision: {decimal_places: 9}\nstatistic: mean\nvalue: 20\n"
    )
    assert reproduce(tmp_path) == [f"{aggregate}: expected median 20.0, found 999.0"]


def test_reproduce_checks_seeded_percentile_interval(tmp_path: Path) -> None:
    (tmp_path / "methodology").mkdir()
    (tmp_path / "methodology" / "cpu.yaml").write_text(
        "id: sbs.common\nversion: 1.0.0\ncategory: common\nstatistics:\n  interval:\n"
        "    type: bootstrap_percentile\n    confidence: 0.95\n    resamples: 100\n    seed: 7\n"
        "---\nid: cpu\nversion: 1.0.0\n"
    )
    raw_dir = tmp_path / "data" / "run-a" / "raw"
    aggregate_dir = tmp_path / "data" / "run-a" / "aggregates"
    raw_dir.mkdir(parents=True)
    aggregate_dir.mkdir()
    values = [12.0, 20.0, 24.0]
    rows = []
    for index, value in enumerate(values, start=1):
        rows.append(
            f"provider: provider-a\nrun_id: run-a\nmethodology: {{id: cpu, version: '1.0.0'}}\n"
            f"measurement: compute\nunit: MIPS\ntrial: {index}\nvalue: {value}\n"
            "success: true\nexclusion_reason: null\n"
        )
    (raw_dir / "measurements.yaml").write_text("---\n".join(rows))
    low, high = bootstrap_ci(values, seed=7, resamples=100) or (0.0, 0.0)
    aggregate = aggregate_dir / "cpu.yaml"
    aggregate.write_text(
        "provider: provider-a\nrun_id: run-a\nmethodology: {id: cpu, version: '1.0.0'}\n"
        "measurement: compute\n"
        "precision: {decimal_places: 9}\nstatistic: median\nvalue: "
        f"{nearest_rank(values, 0.5)}\nunit: MIPS\n"
        f"interval: {{low: {low}, high: {high}, confidence: 0.95}}\n"
        "n: 3\nexcluded_n: 0\ninputs: {path: data/run-a/raw/}\n"
    )
    assert reproduce(tmp_path) == []


def test_unknown_cost_is_reproducible_when_there_are_no_values(tmp_path: Path) -> None:
    raw_dir = tmp_path / "data" / "run-a" / "raw"
    aggregate_dir = tmp_path / "data" / "run-a" / "aggregates"
    raw_dir.mkdir(parents=True)
    aggregate_dir.mkdir()
    (raw_dir / "cost.yaml").write_text(
        "provider: provider-a\nrun_id: run-a\nmeasurement: cost\nvalue: null\n"
        "success: true\nexclusion_reason: null\n"
    )
    (aggregate_dir / "cost.yaml").write_text(
        "provider: provider-a\nrun_id: run-a\nmeasurement: cost\n"
        "precision: {decimal_places: 9}\n"
        "statistic: cost_per_success\nvalue: null\nunit: USD\ninterval: null\n"
        "n: 0\nexcluded_n: 0\ninputs: {path: data/run-a/raw/}\n"
    )
    assert reproduce(tmp_path) == []


def test_schema_is_applied_when_present(tmp_path: Path) -> None:
    (tmp_path / "schemas").mkdir()
    (tmp_path / "schemas" / "run.schema.json").write_text(
        json.dumps({"type": "object", "required": ["id"]})
    )
    (tmp_path / "runs").mkdir()
    (tmp_path / "runs" / "invalid.yaml").write_text("title: no id\n")
    assert "X22 schema validation failed" in validate(tmp_path)[0]


def test_workload_grouping_keeps_raw_replays_reproducible(tmp_path: Path) -> None:
    raw = tmp_path / "data" / "r" / "raw"
    aggregates = tmp_path / "data" / "r" / "aggregates"
    raw.mkdir(parents=True)
    aggregates.mkdir()
    (raw / "values.jsonl").write_text(
        "\n".join(
            json.dumps(row)
            for row in [
                {"workload": "a", "value": 10},
                {"workload": "a", "value": 30},
                {"workload": "b", "value": 20},
                {"workload": "b", "value": 40},
            ]
        )
    )
    aggregate = {
        "statistic": "sum",
        "value": 30,
        "n": 2,
        "excluded_n": 0,
        "precision": {"absolute_tolerance": 0},
        "inputs": {"path": "data/r/raw/", "group_by": "workload", "group_statistic": "median"},
    }
    (aggregates / "value.json").write_text(json.dumps(aggregate))
    assert reproduce(tmp_path) == []
    aggregate["statistic"] = "mean"
    aggregate["value"] = 15
    (aggregates / "value.json").write_text(json.dumps(aggregate))
    assert reproduce(tmp_path) == []


def test_run_relative_reference_selects_only_its_named_run(tmp_path: Path) -> None:
    for run, number in (("a", 10), ("b", 20)):
        raw = tmp_path / "data" / run / "raw"
        raw.mkdir(parents=True)
        (raw / "value.json").write_text(json.dumps({"value": number, "run_id": run}))
    folder = tmp_path / "data" / "a" / "aggregates"
    folder.mkdir()
    (folder / "value.json").write_text(
        json.dumps(
            {
                "run_id": "a",
                "statistic": "median",
                "value": 10,
                "precision": {"absolute_tolerance": 0},
                "inputs": {"relative_to": "run", "path": "raw/value.json"},
            }
        )
    )
    assert reproduce(tmp_path) == []
