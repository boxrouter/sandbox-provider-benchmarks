# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Ordinary words that #234 made name placeholders are literal again; the reader text is unchanged.

#234 (2026-10-29) replaced words with {name:...} references wherever they matched a registry name,
including where the word is used in its ordinary sense: the verb in "How to Read It", the CPU and
memory resources, the adjective "Total". Such a text would change if the stage or tab sharing its
word were renamed. Each value below is now the literal text its reference rendered; the test
resolves the earlier template and requires the current value to read the same.
"""

import json
from pathlib import Path

import pytest

from sandbox_benchmark_verifier.name_refs import canonical_name_map
from sandbox_benchmark_verifier.names import load_tables
from sandbox_benchmark_verifier.placeholders import parse_placeholders, resolve_template

ROOT = Path(__file__).resolve().parents[1]

# (file, JSON pointer, the value before this change)
LITERALS = [
    (
        "contracts/text/1.0.0.json",
        "/method_blocks/how-to-read-it/heading",
        "How to {name:stage.read} It",
    ),
    (
        "contracts/text/methodology-page.json",
        "/index_lead",
        "{name:stage.read} what each benchmark measures, how it is calculated,"
        " and how to interpret its results.",
    ),
    (
        "contracts/text/methodology-page.json",
        "/field_labels/cost_how_to_read",
        "How to {name:stage.read} Cost",
    ),
    (
        "contracts/text/methodology-page.json",
        "/field_labels/how_to_read",
        "How to {name:stage.read} It",
    ),
    (
        "contracts/text/methodology-page.json",
        "/pages/workload_title",
        "{name:stage.test} Your Workload",
    ),
    (
        "contracts/text/asked-vs-measured.json",
        "/asked_vs_measured_asked/template",
        "The benchmark requested this {name:tab.cpu} and memory size through BoxRouter.",
    ),
    (
        "contracts/text/hover-templates.json",
        "/templates/legend_info_7zip_decompression/template",
        "Performance mostly reflects {name:tab.cpu} integer speed.",
    ),
    ("contracts/text/methodology-page.json", "/field_labels/vcpu", "{name:tab.cpu} Cores"),
    (
        "contracts/text/methodology-page.json",
        "/field_labels/7-zip-all-vcpus",
        "7-Zip With All {name:tab.cpu} Cores",
    ),
    (
        "contracts/text/methodology-page.json",
        "/enum_labels/image-build-2vcpu-4gib",
        "Two {name:tab.cpu} Cores and Four GiB of {name:tab.memory}",
    ),
    ("contracts/text/methodology-page.json", "/field_labels/memory_gib", "{name:tab.memory} (GiB)"),
    (
        "contracts/text/methodology-page.json",
        "/field_labels/four_fio_rows",
        "Four {name:tab.disk} {name:section.workloads}",
    ),
    ("contracts/text/methodology-page.json", "/metric_names/total_time", "{name:stage.total} Time"),
    (
        "contracts/text/methodology-page.json",
        "/metric_names/command_total",
        "{name:stage.total} Command Time",
    ),
    (
        "contracts/text/methodology-page.json",
        "/field_labels/write_marker",
        "{name:stage.write} Marker",
    ),
    (
        "contracts/text/methodology-page.json",
        "/field_labels/snapshot_deletable",
        "{name:stage.snapshot} Can Be Deleted",
    ),
    (
        "contracts/text/methodology-page.json",
        "/field_labels/delete_snapshot",
        "Delete {name:stage.snapshot}",
    ),
    (
        "contracts/text/methodology-page.json",
        "/field_labels/network_lockdown",
        "{name:tab.network} Restrictions",
    ),
]


def _at(document: object, pointer: str) -> object:
    for part in pointer.strip("/").split("/"):
        document = document[int(part)] if isinstance(document, list) else document[part]  # type: ignore[index]
    return document


@pytest.mark.parametrize(("path", "pointer", "before"), LITERALS)
def test_a_literal_reads_as_its_earlier_name_reference_did(
    path: str, pointer: str, before: str
) -> None:
    names = canonical_name_map(load_tables(ROOT))
    current = _at(json.loads((ROOT / path).read_text(encoding="utf-8")), pointer)
    assert isinstance(current, str)
    assert not [
        token
        for token in parse_placeholders(current, allow_literal_braces=True)
        if token.kind == "name"
    ]
    assert current == resolve_template(before, names=names, documents={})
