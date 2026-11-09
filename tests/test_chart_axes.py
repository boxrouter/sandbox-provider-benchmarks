# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""The prose names a chart axis the way the document's `plots` do, and types no axis word."""

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
AXIS_WORD = re.compile(r"\b(?:horizontal|vertical)\b", re.IGNORECASE)
PLACEHOLDER = re.compile(r"\{[^{}]*\}")


def _plots() -> dict[str, Any]:
    documents = yaml.safe_load_all((ROOT / "methodology/01-indexes/speed-vs-cost.yaml").read_text())
    plots: dict[str, Any] = [d for d in documents if d and d.get("id") == "time-vs-cost"][-1][
        "composition"
    ]["plots"]
    return plots


def test_reader_text_types_no_axis_word() -> None:
    reader = (ROOT / "reader/methods.yaml").read_text(encoding="utf-8")
    assert not AXIS_WORD.search(PLACEHOLDER.sub(" ", reader))


def test_the_prose_axis_names_match_the_plotted_axes(bundle: dict[str, Any]) -> None:
    plots = _plots()
    names = {row["id"]: row["name"].lower() for row in bundle["names"] if row["kind"] == "axis"}
    axis = {plots[side]["id"]: names[side] for side in ("x", "y")}
    cost_axis, time_axis = axis[plots["x"]["id"]], axis[plots["y"]["id"]]
    assert cost_axis != time_axis

    # Calculation; Time vs Cost's Metrics paragraph names each axis (1C).
    page = bundle["reader"]["methods"]["time-index"]["text"]["notes"]["calculation"]
    metric = bundle["reader"]["methods"]["time-vs-cost"]["text"]["notes"]["metric"]
    assert re.search(rf"Time Over the Costed Units \({time_axis}, ", page), page
    assert re.search(rf"Composite Cost \({cost_axis}, ", page), page
    assert f"the {cost_axis} coordinate is its" in page
    assert f"Time Over the Costed Units on the {time_axis}" in metric
    assert f"Composite Cost on the {cost_axis}" in metric
