# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""A composite inherits the common of its inputs and cites inputs that exist, by id."""

import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from sandbox_benchmark_verifier import cli
from sandbox_benchmark_verifier.catalog import ordinary_documents
from sandbox_benchmark_verifier.composites import composition_input_errors, select_composite_outputs
from sandbox_benchmark_verifier.names import _methodology_documents, load_tables

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.unit
COMPOSITES = (
    "time-index",
    "cost-index",
    "time-vs-cost",
    "index.speed_score",
    "index.cost_score",
    "index.provider_score",
)


@pytest.mark.parametrize("identifier", COMPOSITES)
def test_a_composite_cites_inputs_that_exist_under_its_common(identifier: str) -> None:
    """A composite combines its inputs' results, so it stands on the common they were measured
    under (``composition_input_errors``)."""
    documents = list(_methodology_documents(ROOT))
    composite = {document["id"]: document for document in documents}[identifier]
    assert composite["composition"]["inputs"]
    assert composition_input_errors(composite, documents) == [], identifier


def test_every_input_of_every_composite_has_a_test_name_or_a_retired_name() -> None:
    """An Inputs list drew a raw id (`sandbox.image_build.size_4x8`) because a composite still
    names a retired document. Each input is a test's or an index's document, or a retired document
    with an owner-written name."""
    tables = load_tables(ROOT)
    aliases = tables.aliases.get("document", {})
    tested = {
        aliases.get(row["document"], row["document"])
        for row in tables.names
        if row["kind"] in ("test", "index") and row.get("document")
    }
    retired = tables.aliases.get("retired_names", {})
    unnamed = sorted(
        {
            item["id"]
            for document in _methodology_documents(ROOT)
            for item in document.get("composition", {}).get("inputs", [])
            if aliases.get(item["id"], item["id"]) not in tested and item["id"] not in retired
        }
    )
    assert unnamed == []


def _input(identifier: str, effective: str, inherits: str = "sbs.common") -> dict[str, Any]:
    return {"id": identifier, "effective": effective, "inherits": inherits}


def _index(
    inputs: list[dict[str, Any]], *, effective: str = "2026-01-01", inherits: str = "sbs.common"
) -> dict[str, Any]:
    composition: dict[str, Any] = {"inputs": inputs}
    return {
        "id": "synthetic.index",
        "effective": effective,
        "category": "index",
        "inherits": inherits,
        "composition": composition,
    }


FIRST = {"id": "synthetic.first", "quantity": "Value"}
SECOND = {"id": "synthetic.second", "quantity": "Value"}


@pytest.mark.parametrize(
    ("inputs", "x", "expected"),
    [
        ([FIRST, SECOND], {"id": "synthetic.first", "quantity": "X"}, None),
        (
            [FIRST, SECOND],
            {"id": "synthetic.outside", "quantity": "X"},
            "plot axis x is missing from composition inputs: synthetic.outside",
        ),
        (
            [FIRST, FIRST, SECOND],
            {"id": "synthetic.first", "quantity": "X"},
            "plot axis x is ambiguous in composition inputs: synthetic.first",
        ),
    ],
)
@pytest.mark.parametrize("axis", ["x", "y"])
def test_plot_axes_must_be_unique_members_of_the_composition_vector(
    inputs: list[dict[str, Any]], x: dict[str, str], expected: str | None, axis: str
) -> None:
    documents = [
        _input("synthetic.first", "2024-01-01"),
        _input("synthetic.second", "2024-01-01"),
        {"id": "sbs.common", "effective": "2023-01-01"},
    ]
    index = _index(deepcopy(inputs))
    other_axis = "y" if axis == "x" else "x"
    valid_other = {"id": "synthetic.second", "quantity": "Y" if axis == "x" else "X"}
    index["composition"]["plots"] = {axis: x, other_axis: valid_other}

    errors = composition_input_errors(index, documents)

    if expected is None:
        assert not [error for error in errors if error.startswith("plot axis")]
    else:
        assert expected.replace("axis x", f"axis {axis}") in errors


@pytest.mark.parametrize(
    ("index", "documents", "expected"),
    [
        (
            _index([{"id": "synthetic.leaf", "quantity": "Value"}]),
            [_input("synthetic.leaf", "2027-01-01")],
            ["input is effective after index effective date: synthetic.leaf"],
        ),
        (
            _index([{"id": "synthetic.missing", "quantity": "Value"}]),
            [_input("synthetic.leaf", "2024-01-01")],
            ["unknown input: synthetic.missing"],
        ),
        (
            _index([{"id": "synthetic.leaf", "quantity": "Value"}]),
            [_input("synthetic.leaf", "2024-01-01", inherits="other")],
            ["input does not inherit sbs.common: synthetic.leaf"],
        ),
        (
            _index([{"id": "synthetic.leaf", "quantity": "Value"}]),
            [_input("synthetic.leaf", "2024-01-01"), _input("synthetic.leaf", "2024-01-01")],
            ["duplicate input methodology: synthetic.leaf"],
        ),
    ],
)
def test_composition_input_errors_refuses_invalid_selections(
    index: dict[str, Any], documents: list[dict[str, Any]], expected: list[str]
) -> None:
    assert composition_input_errors(index, documents) == expected


def _cli_documents(*, own_procedure: bool = False) -> list[dict[str, Any]]:
    latest = {document["id"]: document for document in _methodology_documents(ROOT)}
    source_index = deepcopy(latest["time-index"])
    source_id = source_index["composition"]["inputs"][0]["id"]
    source_input = deepcopy(latest[source_id])
    first = deepcopy(source_input)
    first.update(id="synthetic.input.first", effective="2024-01-01")
    second = deepcopy(source_input)
    second.update(id="synthetic.input.second", effective="2024-01-01")
    index = source_index
    index.update(id="synthetic.index", effective="2026-01-01")
    index["composition"]["inputs"] = [
        {"id": "synthetic.input.first", "quantity": "Value"},
        {"id": "synthetic.input.second", "quantity": "Value"},
    ]
    if own_procedure:
        index["procedure"] = deepcopy(source_input["procedure"])
    return [first, second, index]


def _cli_non_index_documents() -> list[dict[str, Any]]:
    latest = {document["id"]: document for document in _methodology_documents(ROOT)}
    source_id = latest["time-index"]["composition"]["inputs"][0]["id"]
    source = deepcopy(latest[source_id])
    source.update(id="synthetic.selection.source", effective="2024-01-01")
    owner = deepcopy(source)
    owner.update(id="synthetic.selection.owner", effective="2026-01-01")
    composition: dict[str, Any] = {
        "inputs": [{"id": source["id"], "quantity": "Value"}],
        "aggregation": "geometric_mean",
        "weighting": "Equal weight per input.",
        "inclusion": "Only providers with a value in every input.",
    }
    owner["composition"] = composition
    return [source, owner]


def _run_validation_cli(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    documents: list[dict[str, Any]],
) -> tuple[int, str]:
    schema_dir = tmp_path / "schemas"
    schema_dir.mkdir()
    source_schema = ROOT / "schemas" / "methodology.schema.json"
    (schema_dir / source_schema.name).write_text(source_schema.read_text())
    methodology = tmp_path / "methodology"
    records = [(methodology / f"{document['id']}.yaml", document) for document in documents]
    monkeypatch.setattr(cli, "load_documents", lambda root: (records, []))
    for checker in (
        "parameter_errors",
        "independence_errors",
        "name_errors",
        "image_fact_errors",
        "timing_errors",
        "panel_errors",
        "text_warnings",
    ):
        monkeypatch.setattr(cli, checker, lambda *args: [])
    monkeypatch.setattr(
        sys, "argv", ["sandbox-benchmark-verify", "validate", "--root", str(tmp_path)]
    )

    exit_code = cli.main()
    output = capsys.readouterr()
    return exit_code, output.out + output.err


@pytest.mark.parametrize(
    ("documents", "expected_exit", "expected_output"),
    [
        (_cli_documents(), 0, "validate: ok"),
        (_cli_documents(own_procedure=True), 1, "schema validation failed"),
        (_cli_non_index_documents(), 0, "validate: ok"),
    ],
)
def test_validation_cli_checks_the_composite_schema(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    documents: list[dict[str, Any]],
    expected_exit: int,
    expected_output: str,
) -> None:
    exit_code, output = _run_validation_cli(monkeypatch, capsys, tmp_path, documents)

    assert exit_code == expected_exit
    assert expected_output in output


def test_output_selection_reads_only_the_requested_graph() -> None:
    common = {"id": "sbs.common", "effective": "2024-01-01"}
    leaf = _input("example", "2025-01-01")
    index = {
        "id": "example-index",
        "effective": "2025-02-01",
        "category": "index",
        "inherits": "sbs.common",
        "composition": {"inputs": [{"id": "example"}]},
    }
    unrelated = {**index, "id": "unused", "composition": {"inputs": [{"id": "missing"}]}}
    docs = [common, leaf, index, unrelated]
    assert ordinary_documents(docs)["example-index"] is index
    selected = select_composite_outputs(docs, outputs=[{"id": "example-index"}])
    assert selected["example"] is leaf
    assert "unused" not in selected
    with pytest.raises(ValueError, match="needs a reference"):
        select_composite_outputs(docs, outputs=[{"input": "example"}])
    with pytest.raises(ValueError, match="owner scope"):
        select_composite_outputs(docs, outputs=[{"id": "example-index", "scope": "invented"}])


@pytest.mark.parametrize("case", ["other-common", "future", "missing", "duplicate"])
def test_output_entrypoint_refuses_an_invalid_two_input_closure(case: str) -> None:
    common = {"id": "sbs.common", "effective": "2024-01-01"}
    a = _input("a", "2024-01-01")
    b = _input("b", "2024-01-01")
    index = _index([{"id": "a", "quantity": "A"}, {"id": "b", "quantity": "B"}])
    docs = [common, a, b, index]
    if case == "other-common":
        b["inherits"] = "elsewhere"
    elif case == "future":
        b["effective"] = "2027-01-01"
    elif case == "missing":
        index["composition"]["inputs"][1]["id"] = "c"
    else:
        docs.append(deepcopy(b))
    with pytest.raises(ValueError):
        select_composite_outputs(docs, outputs=[{"id": index["id"]}])


def test_graph_refuses_leaf_policy_mixing_inside_no_region_common() -> None:
    common = {
        "id": "sbs.common",
        "effective": "2024-01-01",
        "environment": {"region_policy": {"selection": "no_portable_region_request"}},
    }
    leaf = _input("a", "2024-01-01")
    leaf["environment"] = {"region_policy": "Existing US leaf override."}
    index = _index([{"id": "a", "quantity": "A"}])
    assert "leaf region policy conflicts with bound common selector" in composition_input_errors(
        index, [common, leaf, index]
    )
    with pytest.raises(ValueError, match="leaf region policy conflicts"):
        select_composite_outputs([common, leaf, index], outputs=[{"id": index["id"]}])
