# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Current reader revisions and authored tooltips follow the published text rules."""

import json
import re
from pathlib import Path
from typing import Any

import yaml

from sandbox_benchmark_verifier.catalog import load_methodology
from sandbox_benchmark_verifier.conventions import load_conventions
from sandbox_benchmark_verifier.versions import newest

ROOT = Path(__file__).resolve().parents[1]


def _capital_start(text: str) -> bool:
    """The capital-start rule: a capital, a digit, or a tool name its document owns in lowercase."""
    names = load_conventions(ROOT)["proper_names"]
    return (
        text[0].isupper()
        or text[0].isdigit()
        or any(re.match(rf"{re.escape(name)}(?![\w-])", text) for name in names)
    )


def _sentences(text: str) -> list[str]:
    return [part for part in re.split(r"(?<=[.!?])\s+(?=[A-Z])", text.strip()) if part]


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for child in value.values() for text in _strings(child)]
    if isinstance(value, list):
        return [text for child in value for text in _strings(child)]
    return []


def test_current_reader_slots_are_complete_and_convention_safe(bundle: dict[str, Any]) -> None:
    # load_methodology skips retired ids: a retired test's released versions are history.
    latest = newest(item.document for item in load_methodology(ROOT))
    for identifier, document in latest.items():
        version = str(document["version"])
        reader_version = version
        entry = bundle["methodologies"][identifier][version]["text"]
        notes = entry.get("notes", {})
        for field, value in notes.items():
            if isinstance(value, str) and value.strip():
                assert _capital_start(value.strip()), f"{identifier}@{reader_version}.{field}"
        for field, maximum_sentences, maximum_length in (
            ("summary", 2, 140),
            ("what_this_measures", 2, 300),
            ("how_it_is_measured", 2, 300),
            ("how_to_read", 2, 300),
        ):
            value = notes.get(field)
            if value is None or value == "":
                continue
                assert _capital_start(value), f"{identifier}@{reader_version}.{field}"
            if (identifier, field) != ("time-vs-cost", "summary"):  # the frontier template
                assert value.endswith("."), f"{identifier}@{reader_version}.{field}"
            assert len(value) <= maximum_length, f"{identifier}@{reader_version}.{field}"
            message = f"{identifier}@{reader_version}.{field}"
            assert len(_sentences(value)) <= maximum_sentences, message
            assert not re.search(r"\btypical\b", value, re.I), message
            assert "latest result" not in value.lower(), f"{identifier}@{reader_version}.{field}"
            assert not re.search(r"\{(?:name|param|unit|direction):", value), (
                f"{identifier}@{reader_version}.{field}"
            )
        limits = entry.get("interpretation", {}).get("limits", [])
        assert len(limits) <= 3, f"{identifier}@{reader_version}.limits"
        for index, limit in enumerate(limits):
            assert limit[0].isupper(), f"{identifier}@{reader_version}.limits[{index}]"
            assert limit.endswith("."), f"{identifier}@{reader_version}.limits[{index}]"
            assert len(_sentences(limit)) == 1, f"{identifier}@{reader_version}.limits[{index}]"
            assert not re.search(r"\btypical\b", limit, re.I)


def test_composite_index_descriptions_and_hero_sources_are_short_and_complete(
    bundle: dict[str, Any],
) -> None:
    expected = {
        ("time-index", "1.1.29"): (
            "A time statistic is aggregated from each workload's trace or job."
        ),
        ("cost-index", "1.1.27"): (
            "A cost statistic is aggregated from each workload's trace or job."
        ),
        ("time-vs-cost", "1.1.20"): "Composite time vs Composite cost",
    }
    for (identifier, reader_version), description in expected.items():
        text = bundle["methodologies"][identifier][reader_version]["text"]["notes"]["summary"]
        assert text == description
        if (
            identifier != "time-vs-cost"
        ):  # the frontier subtitle is the owner template, not a sentence
            assert text[0].isupper() and text.endswith(".")
            assert len(_sentences(text)) == 1
        assert len(text) <= 140

    expected_heroes = {
        "time-index": ("1.1.29", "Composite time"),
        "cost-index": ("1.1.27", "Composite cost"),
        "sequential-tti": ("1.2.18", "Create to first command"),
    }
    documents = {
        (document["id"], str(document["version"])): document
        for path in (ROOT / "methodology").rglob("*.yaml")
        for document in yaml.safe_load_all(path.read_text())
        if isinstance(document, dict) and document.get("id")
    }
    for identifier, (version, headline) in expected_heroes.items():
        document = documents[identifier, version]
        value = document["headline"]
        assert value == headline
        assert len(value) <= 60 and "\n" not in value and not value.endswith(".")
        assert {metric["direction"] for metric in document["metrics"]} == {"lower_is_better"}


def test_reader_tooltips_are_short_and_do_not_repeat_known_hover_labels(
    bundle: dict[str, Any],
) -> None:
    templates = bundle["content_criteria"]["hover_templates"]
    sample = "12"  # any runtime value: the test reads the template, not what it is filled with
    hovered_labels = {
        "popup_chip_mean": "Mean",
        "popup_chip_median": "Median",
        "popup_chip_p95": "P95",
        "popup_chip_lowest": "Lowest",
        "popup_chip_slowest": "Slowest",
        "popup_chip_spread": "Spread",
        "popup_chip_dots_for": "Dots for",
        "popup_chip_dots_total": "Dots total",
        "popup_chip_time": "Time",
        "popup_chip_cost": "Cost",
        "popup_create_info": "Create",
        "popup_delay_time_info": "Delay time",
        "popup_chip_group_disk_gib": "Disk",
        "popup_chip_group_memory_gib": "Memory",
        "popup_chip_empty_sandbox": "Empty sandbox",
        "popup_chip_memory_1_gib": "Memory 1 GiB",
        "popup_chip_disk_1_gib": "Disk 1 GiB",
        "popup_chip_disk_2_gib": "Disk 2 GiB",
        "popup_chip_disk_4_gib": "Disk 4 GiB",
        "cost_reason_trial_cost_without_total": "Trial cost without total",
        "trace_task_not_ranked": "Not ranked",
        "commands_minute_circled_i_nobody_ranked": "Nobody ranked",
        "commands_minute_circled_i_reading": "Reading",
        "commands_minute_circled_i_what_measures": "What this measures",
        "commands_minute_circled_i_method": "Method",
        "legend_info_7zip_compression": "Compression",
        "legend_info_7zip_decompression": "Decompression",
        "legend_info_7zip_total": "Total",
        "legend_info_deep_swe_build": "DeepSWE build",
        "legend_info_deep_swe_install": "DeepSWE install",
        "legend_info_deep_swe_read": "DeepSWE read",
        "legend_info_deep_swe_test": "DeepSWE test",
        "legend_info_deep_swe_write": "DeepSWE write",
        "legend_info_terminal_bench_build": "Terminal-Bench build",
        "legend_info_terminal_bench_install": "Terminal-Bench install",
        "legend_info_terminal_bench_read": "Terminal-Bench read",
        "legend_info_terminal_bench_test": "Terminal-Bench test",
        "legend_info_terminal_bench_write": "Terminal-Bench write",
        "excluded_trace_command_timed_out": "Command timed out",
        "excluded_trace_exit_code_differs": "Exit code differs",
        "excluded_trace_patch_differs": "Patch differs",
    }
    for identifier, item in templates.items():
        text = item["template"]
        for field in item.get("runtime_fields", []):
            text = text.replace("{" + field + "}", sample)
        assert not re.search(r"\{[^{}]+\}", text), identifier
        assert len(text) <= 140, identifier
        if item.get("form") == "sentence":
            assert len(_sentences(text)) == 1, identifier
            assert (text[0].isupper() or text[0].isdigit()) and text.endswith("."), identifier
        label = hovered_labels.get(identifier)
        if label:
            assert label.casefold() not in text.casefold(), identifier


def test_asked_vs_measured_tooltips_do_not_repeat_their_labels() -> None:
    data = json.loads((ROOT / "contracts/text/asked-vs-measured.json").read_text())
    for identifier, item in data.items():
        source = item["template"]
        label = " ".join(item.get("label", "").casefold().split())
        template = " ".join(source.casefold().split())
        if label and not identifier.startswith("asked_vs_measured_no_reading"):
            assert label not in template, identifier
        assert len(template) <= 140, identifier
        assert len(_sentences(source)) <= 3, identifier
        resolved = re.sub(r"\{[a-z_]+\}", "Value", source)
        assert resolved[0].isupper() and resolved.endswith("."), identifier


def test_reader_contract_slots_begin_with_a_capital(bundle: dict[str, Any]) -> None:
    index_copy = bundle["content_criteria"]["index_copy"]
    reader_copy = {key: value for key, value in index_copy.items() if key != "caption_statistic"}
    asked_vs_measured = json.loads((ROOT / "contracts/text/asked-vs-measured.json").read_text())
    for value in _strings(reader_copy):
        if value.strip():
            assert _capital_start(value.strip()), value
    for item in asked_vs_measured.values():
        for field in ("label", "template"):
            if field not in item:
                continue
            value = re.sub(r"\{[a-z_]+\}", "Value", item[field])
            assert _capital_start(value), value


def test_composite_documents_name_their_unit_statistic() -> None:
    for identifier, reader_version in (
        ("time-index", "1.1.29"),
        ("cost-index", "1.1.27"),
        ("time-vs-cost", "1.1.20"),
    ):
        files = list((ROOT / "methodology/01-indexes").glob("*.yaml"))
        documents = [
            document
            for path in files
            for document in yaml.safe_load_all(path.read_text(encoding="utf-8"))
            if isinstance(document, dict) and document.get("id") == identifier
        ]
        current = next(document for document in documents if document["version"] == reader_version)
        assert current["statistics"]["unit_statistic"] == {
            "default": "median",
            "options": ["median", "mean", "p95"],
        }
