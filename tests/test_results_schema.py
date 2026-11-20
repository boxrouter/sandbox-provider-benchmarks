# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""The results schema's own rules (page-data plan, section 3 and unit P0.1).

The schema must be meta-valid, at most 25.5 KiB and 15 definitions, and every property's description
must end with `Read by:` and reader ids that the schema's `readers` map defines.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

import sandbox_benchmark_verifier

SCHEMA_PATH = Path(sandbox_benchmark_verifier.__file__).parent / "results.schema.json"
EXAMPLE_PATH = Path(__file__).parent / "fixtures" / "results-example.json"

# 1.9.0: +1 KiB timing_excluded; 1.11.0: +0.5 KiB unsupported reason; 1.12.0: +0.5 KiB larger_size
MAX_BYTES = 26 * 1024
MAX_DEFINITIONS = 15
READ_BY = re.compile(r"Read by: ([a-z-]+(?:, [a-z-]+)*)\.$")


def _schema() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(SCHEMA_PATH.read_text())
    return loaded


def _example() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(EXAMPLE_PATH.read_text())
    return loaded


def _validator() -> Draft202012Validator:
    return Draft202012Validator(_schema(), format_checker=FormatChecker())


def _described(node: Any, path: str) -> Iterator[tuple[str, Any]]:
    """Yield (path, schema) for the root, every definition and every property, at any depth."""
    if isinstance(node, dict):
        if path == "" or re.search(r"/(properties|\$defs)/[^/]+$", path):
            yield path or "/", node
        for key, child in node.items():
            if key in {"readers", "enum", "const", "required"}:
                continue
            yield from _described(child, f"{path}/{key}")
    elif isinstance(node, list):
        for index, child in enumerate(node):
            yield from _described(child, f"{path}/{index}")


def test_schema_is_meta_valid() -> None:
    Draft202012Validator.check_schema(_schema())


def test_schema_stays_within_its_size_and_definition_budget() -> None:
    size = SCHEMA_PATH.stat().st_size
    assert size <= MAX_BYTES, f"results.schema.json is {size} bytes; the budget is {MAX_BYTES}"
    count = len(_schema()["$defs"])
    assert count <= MAX_DEFINITIONS, f"{count} definitions; the budget is {MAX_DEFINITIONS}"


def test_every_property_names_the_readers_that_draw_it() -> None:
    schema = _schema()
    readers = set(schema["readers"])
    used: set[str] = set()
    problems = []
    for path, node in _described(schema, ""):
        match = READ_BY.search(node.get("description", ""))
        if match is None:
            problems.append(f"{path}: description must end with 'Read by: <reader ids>.'")
            continue
        named = set(match.group(1).split(", "))
        used |= named
        if unknown := named - readers:
            problems.append(f"{path}: unknown readers {sorted(unknown)}")
    assert not problems, "\n".join(problems)
    assert used == readers, f"readers no property names: {sorted(readers - used)}"


def test_version_is_semver_and_matches_the_format_major() -> None:
    schema = _schema()
    version = schema["schema_version"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", version), version
    major = schema["properties"]["schema"]["const"].rsplit("/", 1)[1]
    assert version.split(".")[0] == major


def test_example_results_validate() -> None:
    _validator().validate(_example())


def test_example_covers_every_cell_state_and_a_pool() -> None:
    example = _example()
    assert {cell["state"] for cell in example["cells"]} == {
        "complete",
        "partial",
        "missing",
        "unsupported",
    }
    assert len({cell["provider"] for cell in example["cells"]}) == 2
    assert example["pools"]


@pytest.mark.parametrize(
    ("provider", "valid"),
    [("modal#vm", True), ("daytona#container", True), ("modal#", False), ("a#b#c", False)],
)
def test_a_provider_id_may_name_its_class(provider: str, valid: bool) -> None:
    example = copy.deepcopy(_example())
    example["cells"][0]["provider"] = provider
    assert _validator().is_valid(example) is valid


def _with(change: str) -> dict[str, Any]:
    example = copy.deepcopy(_example())
    complete = next(cell for cell in example["cells"] if cell["state"] == "complete")
    if change == "series-extra-key":
        complete["metrics"]["tool_time"]["label"] = "Tool time"
    elif change == "commands-outside-a-stage":
        complete["metrics"]["tool_time"]["commands"] = []
    elif change == "unknown-state":
        complete["state"] = "approved"
    elif change == "sentence-as-id":
        complete["document"] = "Sequential TTI"
    elif change == "filled-variant-id-only":
        example["pools"][0]["metrics"]["tool_time"]["filled"] = ["aiomonitor-2"]
    elif change == "other-major":
        example["schema"] = "boxrouter.bench.results/2"
    return example


@pytest.mark.parametrize(
    "change",
    [
        "series-extra-key",
        "commands-outside-a-stage",
        "unknown-state",
        "sentence-as-id",
        "filled-variant-id-only",
        "other-major",
    ],
)
def test_schema_refuses(change: str) -> None:
    with pytest.raises(ValidationError):
        _validator().validate(_with(change))
