# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""An Introduction names a chart segment by the label its legend draws, read from the owner."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from sandbox_benchmark_verifier.names import _methodology_documents, load_tables
from sandbox_benchmark_verifier.versions import newest
from sandbox_benchmark_verifier.yaml_load import load_yaml

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
_NAME = re.compile(r"\{name:([\w.-]+)\}")
_STAGE = re.compile(r"\{name:(stage\.[\w-]+)\}")


def test_every_introduction_names_a_segment_as_its_legend_does() -> None:
    tables = load_tables(ROOT)
    names = {f"{row['kind']}.{row['id']}": str(row["name"]) for row in tables.names}

    def resolved(text: str) -> str:
        return _NAME.sub(lambda match: names.get(match.group(1), match.group(0)), text)

    documents = newest(_methodology_documents(ROOT))
    reader = load_yaml((ROOT / "reader" / "methods.yaml").read_text(encoding="utf-8"))
    checked = 0
    for identifier, method in reader["methods"].items():
        introduction = (method.get("notes") or {}).get("introduction")
        if not isinstance(introduction, str) or not _STAGE.search(introduction):
            continue
        document = documents[tables.aliases.get("document", {}).get(identifier, identifier)]
        legend = {resolved(str(stage["name"])) for stage in document["procedure"]["stages"]}
        for reference in _STAGE.findall(introduction):
            checked += 1
            assert names[reference] in legend, (identifier, reference, sorted(legend))
    assert checked
