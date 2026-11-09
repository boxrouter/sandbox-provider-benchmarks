# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""The wheel's public contract is usable without a sibling checkout."""

import json
import re
import shutil
import subprocess
from copy import deepcopy
from importlib.metadata import metadata, packages_distributions
from inspect import Parameter, signature
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from pytest_mock import MockerFixture

from sandbox_benchmark_verifier import (
    METHODOLOGY_COMMIT,
    MethodologyDocument,
    catalog,
    load_methodology,
    load_schema,
)
from sandbox_benchmark_verifier.catalog import (
    default_context,
    ordinary_documents,
    region_policy,
    region_request_errors,
)
from sandbox_benchmark_verifier.cli import close, reproduce
from sandbox_benchmark_verifier.yaml_load import load_yaml

pytestmark = pytest.mark.unit
ROOT = Path(catalog.__file__).resolve().parents[2]


def test_catalog_preserves_paths_documents_and_source_identity() -> None:
    documents = load_methodology()
    assert documents
    assert re.fullmatch(r"[0-9a-f]{40}", METHODOLOGY_COMMIT)
    assert (
        len([d for d in documents if d.path.endswith("/create.yaml")]) >= 2
    )  # grows with the first commands
    for entry in documents:
        assert entry.path.startswith("methodology/")
        assert entry.source_commit is None
        assert entry.source_url is None
        Draft202012Validator(load_schema("methodology")).validate(entry.document)
    documents[0].document["id"] = "changed"
    assert load_methodology()[0].document["id"] != "changed"


def test_document_source_url_uses_installed_repository_metadata() -> None:
    repository = next(
        value.split(",", 1)[1].strip()
        for value in metadata(packages_distributions()["sandbox_benchmark_verifier"][0]).get_all(
            "Project-URL", []
        )
        if value.startswith("Repository,")
    )
    entry = MethodologyDocument("methodology/example.yaml", {}, "a" * 40)
    assert entry.source_url == f"{repository}/blob/{entry.source_commit}/{entry.path}"


def test_source_tree_does_not_claim_uncommitted_paths(tmp_path: Path) -> None:
    declared_paths = set(
        subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", "HEAD", "--", "methodology"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
    )
    original = load_methodology(ROOT)[0]
    source = tmp_path / "methodology" / "uncommitted.yaml"
    source.parent.mkdir()
    shutil.copyfile(ROOT / original.path, source)
    documents = load_methodology(tmp_path)
    assert documents
    assert all(entry.path not in declared_paths for entry in documents)
    assert all(entry.source_commit is None and entry.source_url is None for entry in documents)


def test_changed_custom_source_has_no_immutable_identity(tmp_path: Path) -> None:
    original = load_methodology(ROOT)[0]
    source = tmp_path / original.path
    source.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / original.path, source)
    unchanged = load_methodology(tmp_path)
    assert unchanged
    assert all(entry.source_url is None for entry in unchanged)
    source.write_text("id: uncommitted-record\nversion: unpublished\n")
    changed = load_methodology(tmp_path)
    assert len(changed) == 1
    assert changed[0].document == {"id": "uncommitted-record", "version": "unpublished"}
    assert changed[0].source_commit is None
    assert changed[0].source_url is None


def test_packaged_assets_keep_stamped_identity(tmp_path: Path, mocker: MockerFixture) -> None:
    original = load_methodology()[0]
    source = tmp_path / original.path
    source.parent.mkdir(parents=True)
    source.write_bytes((ROOT / original.path).read_bytes())
    mocker.patch.object(catalog, "_PACKAGED_ASSETS", tmp_path)
    for entry in load_methodology():
        assert entry.source_commit == METHODOLOGY_COMMIT
        assert entry.source_url is not None
        assert entry.source_url.endswith(f"/blob/{METHODOLOGY_COMMIT}/{entry.path}")


def test_schema_names_are_bounded_and_values_are_fresh() -> None:
    assert load_schema("run") == load_schema("run.schema.json")
    schema = load_schema("run")
    schema["title"] = "changed"
    assert load_schema("run")["title"] != "changed"
    with pytest.raises(ValueError, match="unknown methodology schema"):
        load_schema("../private")


@pytest.mark.parametrize(
    ("precision", "found", "expected", "matches"),
    [
        ({"decimal_places": 3}, 1.234, 1.23449, True),
        ({"decimal_places": 3}, 1.234, 1.23451, False),
        ({"absolute_tolerance": 0.01}, 1.0, 1.009, True),
        ({"absolute_tolerance": 0.01}, 1.0, 1.011, False),
        ({"absolute_tolerance": 0}, 1.0, 1.000000001, False),
        ({"absolute_tolerance": 0}, 1e12, 1e12 + 1, False),
        ({"decimal_places": 3}, 2169.43, 2169.4305, True),
        ({"decimal_places": 3}, 9026.267, 9026.2675, True),
        ({"decimal_places": 3}, 64.706, 64.7055, True),
        ({"decimal_places": 3}, 64.706, 64.705499999, False),
    ],
)
def test_explicit_absolute_precision(
    precision: dict[str, float], found: float, expected: float, matches: bool
) -> None:
    assert close(found, expected, {"precision": precision}) is matches


def test_reproduce_refuses_missing_precision(tmp_path: Path) -> None:
    folder = tmp_path / "data" / "run-a" / "aggregates"
    folder.mkdir(parents=True)
    aggregate = folder / "value.json"
    aggregate.write_text('{"statistic":"median", "value":1}')
    assert reproduce(tmp_path) == [f"{aggregate}: aggregate must declare valid precision"]


@pytest.mark.parametrize(
    "precision",
    [
        None,
        {},
        {"decimal_places": -1},
        {"absolute_tolerance": -1},
        {"decimal_places": 3, "absolute_tolerance": 1},
    ],
)
def test_schema_rejects_invalid_precision(precision: dict[str, int] | None) -> None:
    schema = load_schema("aggregate")["properties"]["precision"]
    assert not Draft202012Validator(schema).is_valid(precision)


def test_bound_context_precedes_version_order_and_preserves_exact_archive() -> None:
    us: dict[str, Any] = {"id": "sbs.common", "version": "1.0.13", "effective": "2026-01-01"}
    other: dict[str, Any] = {**us, "version": "1.0.16"}
    leaf: dict[str, Any] = {
        "id": "example",
        "version": "1.0.0",
        "inherits": {"id": us["id"], "version": us["version"]},
    }
    higher: dict[str, Any] = {
        **leaf,
        "version": "2.0.0",
        "inherits": {"id": other["id"], "version": other["version"]},
    }
    index: dict[str, Any] = {
        "id": "example-index",
        "version": "1.0.0",
        "inherits": leaf["inherits"],
        "composition": {},
    }
    docs = [us, other, leaf, higher, index]
    selected = ordinary_documents(docs, common=leaf["inherits"])
    assert selected["example"] is leaf
    assert selected["example-index"] is index
    assert ordinary_documents(docs, common=higher["inherits"])["example"] is higher
    assert len(docs) == 5  # The exact archive is not filtered or relabelled.
    with pytest.raises(ValueError, match="missing or ambiguous"):
        ordinary_documents(docs, common={"id": "sbs.common", "version": "9.0.0"})


def test_structured_region_schema_and_final_merged_request() -> None:
    common = deepcopy(
        next(
            row.document
            for row in load_methodology(ROOT)
            if row.document["id"] == "sbs.common" and row.document["version"] == "1.0.18"
        )
    )
    common["environment"]["region_policy"] = {"selection": "no_portable_region_request"}
    validator = Draft202012Validator(load_schema("methodology"))
    validator.validate(common)
    leaf: dict[str, Any] = {
        "id": "example",
        "version": "1.0.0",
        "inherits": {"id": common["id"], "version": common["version"]},
    }
    assert region_policy(leaf, [common]) == common["environment"]["region_policy"]
    assert region_request_errors(leaf, [common], {"vcpu": 2}) == []
    layers: list[dict[str, Any]] = [{"region": "us"}, {"region": None}, {"region": ""}]
    for layer in layers:
        merged = {**{"vcpu": 2}, **layer}
        assert region_request_errors(leaf, [common], merged)
    override: dict[str, Any] = {
        **leaf,
        "environment": {"region_policy": "Explicit existing leaf policy."},
    }
    assert region_policy(override, [common]) == override["environment"]["region_policy"]
    assert region_request_errors(override, [common], {})
    legacy_common = deepcopy(common)
    legacy_common["environment"]["region_policy"] = "Existing US policy."
    assert region_request_errors(override, [legacy_common], {"region": "us"}) == []
    for bad in [
        {"selection": "provider_default"},
        {"selection": "no_portable_region_request", "region": "us"},
        {},
    ]:
        common["environment"]["region_policy"] = bad
        assert list(validator.iter_errors(common))


def test_no_region_context_refuses_legacy_leaf_override_and_merged_null_region() -> None:
    us: dict[str, Any] = {
        "id": "sbs.common",
        "version": "1.0.13",
        "environment": {"region_policy": "Existing US policy."},
    }
    new: dict[str, Any] = {
        "id": "sbs.common",
        "version": "1.0.999",
        "environment": {"region_policy": {"selection": "no_portable_region_request"}},
    }
    leaf: dict[str, Any] = {
        "id": "example",
        "version": "1.0.0",
        "inherits": {"id": "sbs.common", "version": us["version"]},
        "environment": {"region_policy": "Explicit existing US leaf policy."},
    }
    rogue: dict[str, Any] = {
        **leaf,
        "version": "2.0.0",
        "inherits": {"id": "sbs.common", "version": new["version"]},
    }
    docs = [us, new, leaf, rogue]
    context = rogue["inherits"]
    with pytest.raises(ValueError, match="leaf region policy conflicts"):
        ordinary_documents(docs, common=context)
    assert region_request_errors(rogue, docs, {}) == [
        "leaf region policy conflicts with bound common selector"
    ]
    assert ordinary_documents(docs, common=leaf["inherits"])["example"] is leaf
    assert region_policy(rogue, docs) == leaf["environment"]["region_policy"]
    clean = {**rogue, "environment": {}}
    assert ordinary_documents([us, new, leaf, clean], common=context)["example"] is clean
    provider: dict[str, Any] = {"vcpu": 2}
    machine: dict[str, Any] = {"memory_mib": 4096}
    local: dict[str, Any] = {"region": None}
    merged = {**provider, **machine, **local}
    assert region_request_errors(clean, [us, new, leaf, clean], merged) == [
        "no_portable_region_request forbids region in final resources"
    ]


@pytest.fixture
def country_documents() -> list[dict[str, Any]]:
    return [row.document for row in load_methodology(ROOT)]


def country_leaf(version: str = "1.0.18") -> dict[str, Any]:
    return {
        "id": "example",
        "version": "1.0.0",
        "inherits": {"id": "sbs.common", "version": version},
    }


@pytest.mark.parametrize("countries", [None, frozenset(), {"DE"}, {"US", "DE"}, "US", [None]])
def test_country_admission_refuses_unknown_or_conflicting_facts_without_changing_raw_api(
    country_documents: list[dict[str, Any]], countries: Any
) -> None:
    leaf = country_leaf()
    assert region_request_errors(leaf, country_documents, {"region": "unproved"}) == []
    assert catalog.region_country_errors(leaf, country_documents, countries)


def test_country_argument_is_required_and_has_no_enable_switch() -> None:
    parameters = signature(catalog.region_country_errors).parameters
    assert list(parameters) == ["document", "documents", "countries"]
    assert parameters["countries"].default is Parameter.empty


def test_country_admission_accepts_the_complete_real_owner_catalog_without_mutating_it(
    country_documents: list[dict[str, Any]],
) -> None:
    before = deepcopy(country_documents)
    for document in country_documents:
        assert catalog.region_country_errors(document, country_documents, frozenset({"US"})) == []
    assert country_documents == before


def test_declared_default_common_is_inherited_by_current_documents() -> None:
    documents = [row.document for row in load_methodology(ROOT)]
    context = default_context(ROOT)
    current = ordinary_documents(documents, common=context)

    assert all(
        document["inherits"] == context
        for identifier, document in current.items()
        if identifier != "sbs.common"
    )


def test_country_admission_resolves_current_common_and_exact_leaf_overrides(
    country_documents: list[dict[str, Any]],
) -> None:
    leaf = country_leaf("1.0.18")
    policy = region_policy(leaf, country_documents)
    leaf["environment"] = {"region_policy": policy}
    assert catalog.region_country_errors(leaf, country_documents, {"US"}) == []
    for different in [None, "Unknown explicit leaf policy."]:
        leaf["environment"]["region_policy"] = different
        assert catalog.region_country_errors(leaf, country_documents, {"US"})


def test_country_admission_and_raw_checker_compose_across_both_cohorts(
    country_documents: list[dict[str, Any]],
) -> None:
    context = default_context(ROOT)
    current = ordinary_documents(country_documents, common=context)
    leaf = current["exec-latency"]
    common = deepcopy(dict(catalog.common_document(country_documents, context)))
    common["version"] = "1.0.999"
    common["environment"]["region_policy"] = {"selection": "no_portable_region_request"}
    new = deepcopy(dict(leaf))
    new["version"] = "9.9.9"
    new["inherits"] = {"id": "sbs.common", "version": common["version"]}
    new.setdefault("environment", {}).pop("region_policy", None)
    documents = [*country_documents, common, new]
    assert ordinary_documents(documents, common=context) == current
    assert ordinary_documents(documents, common=new["inherits"])[new["id"]] is new
    assert catalog.region_country_errors(leaf, documents, {"US"}) == []
    assert catalog.region_country_errors(leaf, documents, None)
    assert catalog.region_country_errors(new, documents, None) == []
    assert region_request_errors(new, documents, {}) == []
    assert region_request_errors(new, documents, {"region": None})
    rogue = deepcopy(new)
    rogue["environment"]["region_policy"] = region_policy(leaf, documents)
    assert catalog.region_country_errors(rogue, documents, {"US"})
    assert region_request_errors(rogue, documents, {})
    unknown = deepcopy(common)
    unknown["environment"]["region_policy"] = {"selection": "unknown_selector"}
    assert catalog.region_country_errors(new, [*country_documents, unknown, new], None)


@pytest.mark.parametrize("duplicate", [False, True])
def test_country_admission_refuses_missing_or_ambiguous_exact_policy_anchor(
    country_documents: list[dict[str, Any]], duplicate: bool
) -> None:
    anchor = catalog.common_document(country_documents, {"id": "sbs.common", "version": "1.0.18"})
    documents = (
        [*country_documents, deepcopy(anchor)]
        if duplicate
        else [document for document in country_documents if document is not anchor]
    )
    with pytest.raises(ValueError, match="missing or ambiguous"):
        catalog.region_country_errors(country_leaf("1.0.18"), documents, {"US"})


@pytest.mark.parametrize(
    "binding",
    [
        None,
        {},
        {"source": {"id": "sbs.common", "version": "1.0.18"}, "countries": []},
        {"source": {"id": "sbs.common", "version": "1.0.18"}, "countries": ["us"]},
        {"source": {"id": "sbs.common", "version": "1.0.18"}, "countries": ["US", "US"]},
        {"source": {"id": "other", "version": "1.0.18"}, "countries": ["US"]},
        {"source": {"id": "sbs.common", "version": "latest"}, "countries": ["US"]},
        {"source": {"id": "sbs.common", "version": "1.0.18"}, "countries": ["US"], "enable": False},
    ],
)
def test_country_admission_refuses_missing_or_invalid_owned_binding(
    country_documents: list[dict[str, Any]], tmp_path: Path, mocker: MockerFixture, binding: Any
) -> None:
    (tmp_path / "names").mkdir()
    (tmp_path / "schemas").mkdir()
    (tmp_path / "names/names.yaml").write_text(
        json.dumps({"catalog_selection": {"region_country_policy": binding}})
    )
    shutil.copyfile(ROOT / "schemas/names.schema.json", tmp_path / "schemas/names.schema.json")
    mocker.patch.object(catalog, "_assets", return_value=tmp_path)
    with pytest.raises(ValueError, match="region country policy binding"):
        catalog.region_country_errors(country_leaf(), country_documents, {"US"})


def test_country_binding_schema_is_additive_to_existing_names_and_default_context() -> None:
    names = load_yaml((ROOT / "names/names.yaml").read_text())
    schema = json.loads((ROOT / "schemas/names.schema.json").read_text())
    validator = Draft202012Validator(schema)
    validator.validate(names)
    del names["catalog_selection"]["region_country_policy"]
    validator.validate(names)
    assert default_context(ROOT) == names["catalog_selection"]["default_common"]


@pytest.mark.parametrize("inherited", [None, "unbound"])
def test_country_admission_refuses_a_leaf_without_exact_common_context(
    country_documents: list[dict[str, Any]], inherited: Any
) -> None:
    leaf = country_leaf()
    leaf["environment"] = {"region_policy": region_policy(leaf, country_documents)}
    leaf["inherits"] = inherited
    assert catalog.region_country_errors(leaf, country_documents, {"US"})
