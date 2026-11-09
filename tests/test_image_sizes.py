# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Image Build base image facts are generated from the registries, never typed."""

import json
from pathlib import Path
from typing import Any

import pytest

from sandbox_benchmark_verifier.catalog import load_methodology
from sandbox_benchmark_verifier.content import content_criteria
from sandbox_benchmark_verifier.conventions import load_conventions
from sandbox_benchmark_verifier.image_sizes import (
    byte_size_text,
    fill_facts,
    image_fact_errors,
    standard_image_bytes,
)
from sandbox_benchmark_verifier.versions import version_key

ROOT = Path(__file__).resolve().parents[1]
STANDARD = (
    max(
        (item for item in load_methodology(ROOT) if item.document.get("id") == "sbs.common"),
        key=lambda item: version_key(str(item.document["version"])),
    )
    .document["environment"]["image"]["reference"]
    .rsplit("@", 1)[1]
)
FACTS = {"compressed_bytes": 10, "uncompressed_bytes": 30, "layers": 2, "files": 7}


def _write(root: Path, version: str, rows: list[dict[str, object]]) -> Path:
    path = root / "parameters/image-build-2vcpu-4gib" / version / "workloads.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _row(identifier: str, digest: str, facts: dict[str, int] | None = None) -> dict[str, object]:
    data: dict[str, object] = {"base_image": f"docker.io/library/{identifier}@sha256:{digest}"}
    if facts is not None:
        data["facts"] = facts
    return {"id": identifier, "data": data}


def test_the_repository_has_facts_for_every_image_build_workload() -> None:
    assert image_fact_errors(ROOT) == []


def test_a_workload_with_no_facts_fails_and_names_the_command(tmp_path: Path) -> None:
    _write(tmp_path, "1.0.0", [_row("python", "a" * 64)])
    errors = image_fact_errors(tmp_path)
    assert len(errors) == 1
    assert "sandbox-benchmark-verify image-sizes" in errors[0]


def test_the_generator_reads_a_digest_once_and_reuses_its_facts(tmp_path: Path) -> None:
    old = _write(tmp_path, "1.0.0", [_row("python", "a" * 64, FACTS)])
    new = _write(tmp_path, "1.1.0", [_row("python", "a" * 64), _row("node", "b" * 64)])
    read: list[str] = []

    def reader(reference: str) -> dict[str, int]:
        read.append(reference)
        return {**FACTS, "layers": 9}

    filled = fill_facts(tmp_path, reader)
    assert len(filled) == 2
    assert read == [f"docker.io/library/node@sha256:{'b' * 64}"]  # python's digest is already known
    rows = json.loads(new.read_text())
    assert rows[0]["data"]["facts"] == FACTS and rows[1]["data"]["facts"]["layers"] == 9
    assert (
        json.loads(old.read_text())[0]["data"]["facts"] == FACTS
    )  # released files stay as they are
    assert image_fact_errors(tmp_path) == []
    assert fill_facts(tmp_path, reader) == []


BYTE_SIZE = load_conventions()["presentation"]["byte_size"]


@pytest.mark.parametrize("vector", BYTE_SIZE["vectors"], ids=lambda row: row["text"])
def test_sizes_follow_the_published_byte_size_rule(vector: dict[str, Any]) -> None:
    # Web's bytesText runs the same vectors, so the rule has one owner and both readers follow it.
    assert byte_size_text(vector["bytes"]) == vector["text"]


def test_the_standard_image_size_is_read_from_its_facts_and_never_typed() -> None:
    page = content_criteria()["methodology_page"]
    size = standard_image_bytes(ROOT, STANDARD)
    assert size is not None and page["standard_image"]["size"] == byte_size_text(size)
    typed = json.loads((ROOT / "contracts/text/methodology-page.json").read_text())
    assert "size" not in typed["standard_image"]
