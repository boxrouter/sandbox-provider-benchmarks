# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""A composite's newest version inherits its inputs' common and pins the inputs then in force."""

import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from sandbox_benchmark_verifier import cli
from sandbox_benchmark_verifier.catalog import ordinary_documents
from sandbox_benchmark_verifier.composites import composition_input_errors, select_composite_outputs
from sandbox_benchmark_verifier.names import _methodology_documents, load_tables
from sandbox_benchmark_verifier.versions import newest

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
def test_the_newest_composite_cites_the_inputs_in_force_under_their_common(
    identifier: str, methodology_root: Path
) -> None:
    """bench-planner, 2026-11-05: a composite combines its inputs' results, so it stands on the
    common they were measured under. argon-land, 2026-11-05: it pins each input's newest version
    not after its own effective date, as a test page binds its members; an input released later,
    often for text only, does not force a new composite version. Both are the validator's rules
    (``composition_input_errors``), which ``validate`` runs only for retrospective composites."""
    composite = newest(_methodology_documents(methodology_root))[identifier]
    assert composite["composition"]["inputs"]
    documents = list(_methodology_documents(methodology_root))
    assert composition_input_errors(composite, documents) == [], identifier


def test_every_input_of_every_composite_version_has_a_test_name_or_a_retired_name() -> None:
    """an Inputs list drew a raw id (`sandbox.image_build.size_4x8`) because
    an older version still names a retired document. Each input is a test's or an index's
    document, or a retired document with an owner-written name."""
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


def _input(
    identifier: str, version: str, effective: str, common_version: str = "1.0.10"
) -> dict[str, Any]:
    return {
        "id": identifier,
        "version": version,
        "effective": effective,
        "inherits": {"id": "sbs.common", "version": common_version},
    }


def _index(
    inputs: list[dict[str, Any]],
    *,
    effective: str = "2026-01-01",
    mode: str | None = None,
    common_version: str = "1.0.10",
) -> dict[str, Any]:
    composition: dict[str, Any] = {"inputs": inputs}
    if mode is not None:
        composition["input_selection"] = mode
    return {
        "id": "synthetic.index",
        "version": "1.0.0",
        "effective": effective,
        "category": "index",
        "inherits": {"id": "sbs.common", "version": common_version},
        "composition": composition,
    }


def test_omitted_selection_requires_the_latest_input_as_of_the_index_date() -> None:
    documents = [
        _input("synthetic.leaf", "1.0.0", "2024-01-01"),
        _input("synthetic.leaf", "1.1.0", "2025-01-01"),
    ]
    index = _index([{"id": "synthetic.leaf", "version": "1.0.0", "quantity": "Value"}])

    assert composition_input_errors(index, documents) == [
        "input is not latest as of index effective date: synthetic.leaf@1.0.0"
    ]


def test_retrospective_selection_accepts_an_exact_older_input_without_mutating_the_vector() -> None:
    documents = [
        _input("synthetic.leaf", "1.0.0", "2024-01-01"),
        _input("synthetic.leaf", "1.1.0", "2025-01-01"),
    ]
    inputs = [{"id": "synthetic.leaf", "version": "1.0.0", "quantity": "Value"}]
    index = _index(deepcopy(inputs), mode="retrospective_exact")

    assert composition_input_errors(index, documents) == []
    assert index["composition"]["inputs"] == inputs


@pytest.mark.parametrize(
    ("inputs", "x", "expected"),
    [
        (
            [
                {"id": "synthetic.first", "version": "1.0.0", "quantity": "Value"},
                {"id": "synthetic.second", "version": "1.0.0", "quantity": "Value"},
            ],
            {"id": "synthetic.first", "version": "1.0.0", "quantity": "X"},
            None,
        ),
        (
            [
                {"id": "synthetic.first", "version": "1.0.0", "quantity": "Value"},
                {"id": "synthetic.second", "version": "1.0.0", "quantity": "Value"},
            ],
            {"id": "synthetic.first", "version": "1.1.0", "quantity": "X"},
            "plot axis x is stale in composition inputs: synthetic.first@1.1.0",
        ),
        (
            [
                {"id": "synthetic.first", "version": "1.0.0", "quantity": "Value"},
                {"id": "synthetic.second", "version": "1.0.0", "quantity": "Value"},
            ],
            {"id": "synthetic.outside", "version": "1.0.0", "quantity": "X"},
            "plot axis x is missing from composition inputs: synthetic.outside@1.0.0",
        ),
        (
            [
                {"id": "synthetic.first", "version": "1.0.0", "quantity": "Value"},
                {"id": "synthetic.first", "version": "1.0.0", "quantity": "Value"},
                {"id": "synthetic.second", "version": "1.0.0", "quantity": "Value"},
            ],
            {"id": "synthetic.first", "version": "1.0.0", "quantity": "X"},
            "plot axis x is ambiguous in composition inputs: synthetic.first@1.0.0",
        ),
    ],
)
@pytest.mark.parametrize("axis", ["x", "y"])
def test_plot_axes_must_be_unique_exact_members_of_the_composition_vector(
    inputs: list[dict[str, Any]],
    x: dict[str, str],
    expected: str | None,
    axis: str,
) -> None:
    documents = [
        _input("synthetic.first", "1.0.0", "2024-01-01"),
        _input("synthetic.second", "1.0.0", "2024-01-01"),
    ]
    index = _index(deepcopy(inputs))
    other_axis = "y" if axis == "x" else "x"
    valid_other = {
        "id": "synthetic.second",
        "version": "1.0.0",
        "quantity": "Y" if axis == "x" else "X",
    }
    index["composition"]["plots"] = {axis: x, other_axis: valid_other}

    errors = composition_input_errors(index, documents)

    if expected is None:
        assert errors == []
    else:
        assert expected.replace("axis x", f"axis {axis}") in errors


def test_two_inputs_must_inherit_the_same_exact_common_as_the_index() -> None:
    documents = [
        _input("synthetic.first", "1.0.0", "2024-01-01", "1.0.10"),
        _input("synthetic.second", "1.0.0", "2024-01-01", "1.0.11"),
    ]
    index = _index(
        [
            {"id": "synthetic.first", "version": "1.0.0", "quantity": "Value"},
            {"id": "synthetic.second", "version": "1.0.0", "quantity": "Value"},
        ],
        mode="retrospective_exact",
        common_version="1.0.11",
    )

    assert composition_input_errors(index, documents) == [
        "input common mismatch: synthetic.first@1.0.0"
    ]


@pytest.mark.parametrize(
    ("index", "documents", "expected"),
    [
        (
            _index(
                [{"id": "synthetic.leaf", "version": "1.0.0", "quantity": "Value"}],
                mode="retrospective_exact",
            ),
            [_input("synthetic.leaf", "1.0.0", "2027-01-01")],
            "input is effective after index effective date: synthetic.leaf@1.0.0",
        ),
        (
            _index(
                [{"id": "synthetic.leaf", "version": "9.9.9", "quantity": "Value"}],
                mode="retrospective_exact",
            ),
            [_input("synthetic.leaf", "1.0.0", "2024-01-01")],
            "unknown exact input: synthetic.leaf@9.9.9",
        ),
        (
            _index([{"id": "synthetic.leaf", "quantity": "Value"}], mode="retrospective_exact"),
            [_input("synthetic.leaf", "1.0.0", "2024-01-01")],
            "input synthetic.leaf has no version",
        ),
        (
            _index(
                [{"id": "synthetic.leaf", "version": "1.0.0", "quantity": "Value"}],
                mode="unknown",
            ),
            [_input("synthetic.leaf", "1.0.0", "2024-01-01")],
            "unknown input selection",
        ),
    ],
)
def test_composition_input_errors_refuses_invalid_selections(
    index: dict[str, Any], documents: list[dict[str, Any]], expected: str
) -> None:
    assert composition_input_errors(index, documents) == [expected]


def _cli_documents(
    *,
    mode: str | None = None,
    missing_version: bool = False,
    mixed_common: bool = False,
    future_input: bool = False,
    stale_default: bool = False,
    own_procedure: bool = False,
) -> list[dict[str, Any]]:
    latest = newest(_methodology_documents(ROOT))
    source_index = deepcopy(latest["time-index"])
    source_id = source_index["composition"]["inputs"][0]["id"]
    source_input = deepcopy(latest[source_id])
    common = deepcopy(source_index["inherits"])
    first = deepcopy(source_input)
    first.update(id="synthetic.input.first", version="1.0.0", effective="2024-01-01")
    first["inherits"] = deepcopy(common)
    second = deepcopy(source_input)
    second.update(id="synthetic.input.second", version="1.0.0", effective="2024-01-01")
    second["inherits"] = deepcopy(common)
    index = source_index
    index.update(id="synthetic.index", version="1.0.0", effective="2026-01-01")
    index["inherits"] = deepcopy(common)
    index["composition"]["inputs"] = [
        {"id": "synthetic.input.first", "version": "1.0.0", "quantity": "Value"},
        {"id": "synthetic.input.second", "version": "1.0.0", "quantity": "Value"},
    ]
    if missing_version:
        index["composition"]["inputs"][0].pop("version")
    if mode is None:
        index["composition"].pop("input_selection", None)
    else:
        index["composition"]["input_selection"] = mode
    if mixed_common:
        second["inherits"]["version"] = "1.0.11"
    if future_input:
        first["effective"] = "2027-01-01"
    if own_procedure:
        index["procedure"] = deepcopy(source_input["procedure"])
    documents = [first, second, index]
    if stale_default:
        newer = deepcopy(first)
        newer.update(version="1.1.0", effective="2025-01-01")
        documents.append(newer)
    return documents


def _cli_non_index_documents(*, mode: str | None = None) -> list[dict[str, Any]]:
    latest = newest(_methodology_documents(ROOT))
    source_id = latest["time-index"]["composition"]["inputs"][0]["id"]
    source = deepcopy(latest[source_id])
    source.update(id="synthetic.selection.source", version="1.0.0", effective="2024-01-01")
    owner = deepcopy(source)
    owner.update(id="synthetic.selection.owner", effective="2026-01-01")
    composition: dict[str, Any] = {
        "inputs": [{"id": source["id"], "version": source["version"], "quantity": "Value"}],
        "aggregation": "geometric_mean",
        "weighting": "Equal weight per input.",
        "inclusion": "Only providers with a value in every input.",
    }
    if mode is not None:
        composition["input_selection"] = mode
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
        (_cli_documents(mode="retrospective_exact", stale_default=True), 0, "validate: ok"),
        (
            _cli_documents(mode="retrospective_exact", missing_version=True),
            1,
            "schema validation failed",
        ),
        (_cli_documents(mode="retrospective_exact", mixed_common=True), 1, "input common mismatch"),
        (_cli_documents(mode="unknown"), 1, "schema validation failed"),
        (_cli_documents(mode="retrospective_exact", future_input=True), 1, "effective after"),
        (_cli_documents(stale_default=True), 0, "validate: ok"),
        (_cli_documents(own_procedure=True), 1, "schema validation failed"),
        (_cli_non_index_documents(), 0, "validate: ok"),
        (_cli_non_index_documents(mode="retrospective_exact"), 1, "schema validation failed"),
    ],
)
def test_validation_cli_checks_schema_and_composite_input_selection(
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


def test_exact_retrospective_selection_does_not_shadow_ordinary_or_validate_unused_graph() -> None:
    common = {"id": "sbs.common", "version": "1.0.10", "effective": "2024-01-01"}
    old = _input("example", "1.0.0", "2024-01-01")
    new = _input("example", "1.1.0", "2025-01-01")
    # The existing fixture helpers describe schema-valid relationship fields.
    index = {
        "id": "example-index",
        "version": "1.0.0",
        "effective": "2025-02-01",
        "category": "index",
        "inherits": old["inherits"],
        "composition": {"inputs": [{"id": "example", "version": "1.1.0"}]},
    }
    retro = deepcopy(index)
    retro.update(version="2.0.0", effective="2026-01-01")
    retro["composition"] = {
        "input_selection": "retrospective_exact",
        "inputs": [{"id": "example", "version": "1.0.0"}],
    }
    unrelated = {
        **index,
        "id": "unused",
        "composition": {"inputs": [{"id": "missing", "version": "1.0.0"}]},
    }
    docs = [common, old, new, index, retro, unrelated]
    assert ordinary_documents(docs, common=old["inherits"])["example-index"] is index
    exact = select_composite_outputs(
        docs, common=old["inherits"], outputs=[{"id": "example-index", "version": "2.0.0"}]
    )
    assert exact["example"] is old
    assert "unused" not in exact
    ordinary = select_composite_outputs(
        docs, common=old["inherits"], outputs=[{"id": "example-index", "version": "1.0.0"}]
    )
    assert ordinary["example"] is new
    with pytest.raises(ValueError, match="common mismatch"):
        select_composite_outputs(
            [*docs, {"id": "sbs.common", "version": "1.0.13"}],
            common={"id": "sbs.common", "version": "1.0.13"},
            outputs=[{"id": "example-index", "version": "2.0.0"}],
        )
    with pytest.raises(ValueError, match="exact reference"):
        select_composite_outputs(docs, common=old["inherits"], outputs=[{"id": "example-index"}])
    with pytest.raises(ValueError, match="owner scope"):
        select_composite_outputs(
            docs,
            common=old["inherits"],
            outputs=[{"id": "example-index", "version": "2.0.0", "scope": "invented"}],
        )


@pytest.mark.parametrize("case", ["mixed", "future", "mode", "missing-version", "duplicate"])
def test_exact_output_entrypoint_refuses_invalid_two_input_closure(case: str) -> None:
    common = {"id": "sbs.common", "version": "1.0.10"}
    a = _input("a", "1.0.0", "2024-01-01")
    b = _input("b", "1.0.0", "2024-01-01")
    index = _index(
        [{"id": "a", "version": "1.0.0"}, {"id": "b", "version": "1.0.0"}],
        mode="retrospective_exact",
    )
    docs = [common, a, b, index]
    if case == "mixed":
        b["inherits"]["version"] = "1.0.13"
    elif case == "future":
        b["effective"] = "2027-01-01"
    elif case == "mode":
        index["composition"]["input_selection"] = "unknown"
    elif case == "missing-version":
        del index["composition"]["inputs"][1]["version"]
    else:
        docs.append(deepcopy(b))
    with pytest.raises(ValueError):
        select_composite_outputs(
            docs, common=common, outputs=[{"id": index["id"], "version": index["version"]}]
        )


def test_exact_graph_refuses_leaf_policy_mixing_inside_no_region_common() -> None:
    common = {
        "id": "sbs.common",
        "version": "1.0.10",
        "environment": {"region_policy": {"selection": "no_portable_region_request"}},
    }
    leaf = _input("a", "1.0.0", "2024-01-01")
    leaf["environment"] = {"region_policy": "Existing US leaf override."}
    index = _index([{"id": "a", "version": "1.0.0"}], mode="retrospective_exact")
    assert composition_input_errors(index, [common, leaf, index]) == [
        "leaf region policy conflicts with bound common selector"
    ]
    with pytest.raises(ValueError, match="leaf region policy conflicts"):
        select_composite_outputs(
            [common, leaf, index],
            common={"id": "sbs.common", "version": "1.0.10"},
            outputs=[{"id": index["id"], "version": index["version"]}],
        )
