# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Test reference-test parsers against captured upstream tool output."""

import json
import runpy
from pathlib import Path

import jsonschema

from sandbox_benchmark_verifier.catalog import load_methodology
from sandbox_benchmark_verifier.compare import interval_settings

ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "tests/fixtures/reference-tests"
MEASUREMENT_SCHEMA = json.loads((ROOT / "schemas/measurement.schema.json").read_text())


def assert_records_valid(records: list[dict[str, object]]) -> None:
    """Each record fits the schema and cites a methodology metric in the metric's own unit."""
    documents = load_methodology()
    headlines: dict[object, tuple[str, set[object]]] = {}
    for record in records:
        jsonschema.validate(record, MEASUREMENT_SCHEMA)
        citation = record["methodology"]
        assert isinstance(citation, dict)
        document = next(
            item.document
            for item in documents
            if (item.document.get("id"), item.document.get("version"))
            == (citation["id"], citation["version"])
        )
        metrics = {metric["name"]: metric["unit"] for metric in document["metrics"]}
        assert metrics[str(record["measurement"])] == record["unit"]
        assert interval_settings(document, documents) == (0, 10000, 0.95)
        _, measured = headlines.setdefault(
            record["test_id"], (document["metrics"][0]["name"], set())
        )
        measured.add(record["measurement"])
    # compare reads each test's first metric, so every test must emit it
    for headline, measured in headlines.values():
        assert headline in measured


def test_cpu_parser_uses_captured_7zip_output() -> None:
    parser = runpy.run_path(str(ROOT / "reference-tests/cpu/parse.py"))
    output = (FIXTURES / "cpu.txt").read_text()
    records = list(parser["measurements"](output))
    assert {record["measurement"] for record in records} == {
        "compress_mips",
        "decompress_mips",
        "total_mips",
    }
    assert_records_valid(records)
    assert {(record["test_id"], record["variant"]) for record in records} == {
        ("cpu-7zip-1t", "standard")
    }


def test_memory_parser_uses_captured_stream_output() -> None:
    parser = runpy.run_path(str(ROOT / "reference-tests/memory/parse.py"))
    records = list(parser["measurements"]((FIXTURES / "memory.txt").read_text()))
    assert records[0]["measurement"] == "triad_gigabytes_per_second"
    assert (records[0]["test_id"], records[0]["variant"]) == ("stream-triad", "standard")
    assert_records_valid(records)


def test_disk_parser_uses_captured_fio_json() -> None:
    parser = runpy.run_path(str(ROOT / "reference-tests/disk/parse.py"))
    records = list(parser["measurements"](sorted((FIXTURES / "disk").glob("*.json"))))
    assert {record["measurement"] for record in records} == {
        "sequential_read_mib_per_second",
        "sequential_write_mib_per_second",
        "random_read_iops",
        "random_write_iops",
    }
    assert {record["test_id"] for record in records} == {
        "disk-seq-read-1m",
        "disk-seq-write-1m",
        "disk-rand-read-4k",
        "disk-rand-write-4k",
    }
    assert {record["variant"] for record in records} == {"fio-1g"}
    assert_records_valid(records)


def test_filesystem_parser_uses_captured_stress_ng_yaml() -> None:
    parser = runpy.run_path(str(ROOT / "reference-tests/filesystem/parse.py"))
    record = parser["measurements"](FIXTURES / "filesystem.yaml")
    assert record["measurement"] == "links_per_second"
    assert (record["test_id"], record["variant"]) == ("hardlink", "link-30s")
    assert_records_valid([record])


def test_network_parser_uses_captured_install_results() -> None:
    parser = runpy.run_path(str(ROOT / "reference-tests/network/parse.py"))
    records = [
        parser["measurement"]((FIXTURES / "network" / f"{variant}.json").read_text())
        for variant in ("git_clone", "npm_ci", "pip_install")
    ]
    assert {record["variant"] for record in records} == {"git-clone", "npm-ci", "pip-install"}
    assert {record["test_id"] for record in records} == {"install"}
    assert_records_valid(records)
