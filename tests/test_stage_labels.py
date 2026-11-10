# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""A timed part's name in reader prose is compiled as a label (``*Name*``) and nowhere else."""

import re
from typing import Any

import pytest

from sandbox_benchmark_verifier.name_refs import reader_strings
from sandbox_benchmark_verifier.placeholders import resolve_template

pytestmark = pytest.mark.unit

NAMES = {"stage.create": "Create", "test.demo": "Demo"}
DOCUMENTS = {
    "demo": {
        "procedure": {
            "stages": [
                {"id": "create", "name": "{name:stage.create}"},
                {"id": "first-command", "name": "First command"},
            ]
        }
    }
}
LABEL = re.compile(r"(?<![\w`*])\*[^*\s][^*]*\*(?![\w*])")


def _resolve(template: str, *, marks: bool) -> str:
    return resolve_template(template, names=NAMES, documents=DOCUMENTS, stage_marks=marks)


def test_a_stage_name_placeholder_is_marked_once_only_when_asked() -> None:
    template = "{name:stage.create} and {param:demo.procedure.stages.1.name} add up."
    assert _resolve(template, marks=True) == "*Create* and *First command* add up."
    assert _resolve(template, marks=False) == "Create and First command add up."
    # A stage name that is itself a placeholder is marked once, not twice.
    assert _resolve("{param:demo.procedure.stages.0.name}", marks=True) == "*Create*"
    # Other names are plain words.
    assert _resolve("{name:test.demo}", marks=True) == "Demo"


def test_the_burst_tti_step_note_reads_as_andre_asked(bundle: dict[str, Any]) -> None:
    items = bundle["reader"]["methods"]["burst-tti"]["text"]["notes"]["step_items"]
    note = next(
        item["note"] for item in items if item.get("note", "").startswith("*First command*")
    )
    assert note.startswith(
        "*First command* is measured as the time from the last `create` call returned to the "
        "last sandbox's first successful `true`. *Create* and *First command* add up to the "
        "burst time. As each individual `create` call returns, "
    )


def test_labels_are_drawn_in_notes_only_and_each_is_a_stage_name(bundle: dict[str, Any]) -> None:
    stages = {row["name"] for row in bundle["names"] if row.get("kind") == "stage"} | {
        "First command"
    }
    seen: set[str] = set()
    for method, entry in bundle["reader"]["methods"].items():
        text = entry["text"]
        for key in ("headline", "panel"):
            assert not LABEL.search(str(text.get(key, ""))), f"{method}.{key}"
        notes = text.get("notes", {})
        # A table of timed parts and the limits name their steps in cells, not prose.
        for key in ("timed_parts", "time_limits"):
            assert not LABEL.search(str(notes.get(key, ""))), f"{method}.notes.{key}"
        for key, value in reader_strings(notes, f"{method}.notes"):
            found = {span[1:-1] for span in LABEL.findall(re.sub(r"`[^`]*`", " ", value))}
            assert found <= stages, f"{key}: {found - stages}"
            seen |= found
    assert {"Create", "First command", "Install", "Read"} <= seen
