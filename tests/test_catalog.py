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
    ordinary_documents,
    region_policy,
    region_request_errors,
)
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
    source.write_text("id: uncommitted-record\n")
    changed = load_methodology(tmp_path)
    assert len(changed) == 1
    assert changed[0].document == {"id": "uncommitted-record"}
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
    assert load_schema("methodology") == load_schema("methodology.schema.json")
    schema = load_schema("methodology")
    schema["title"] = "changed"
    assert load_schema("methodology")["title"] != "changed"
    with pytest.raises(ValueError, match="unknown methodology schema"):
        load_schema("../private")


def test_selection_is_the_common_and_the_documents_that_inherit_it() -> None:
    common: dict[str, Any] = {"id": "sbs.common", "effective": "2026-01-01"}
    leaf: dict[str, Any] = {"id": "example", "effective": "2026-01-02", "inherits": "sbs.common"}
    other: dict[str, Any] = {"id": "elsewhere", "effective": "2026-01-02"}
    assert ordinary_documents([common, leaf, other]) == {"sbs.common": common, "example": leaf}
    assert ordinary_documents([common, leaf], effective="2026-01-01") == {"sbs.common": common}
    with pytest.raises(ValueError, match="ambiguous document reference"):
        ordinary_documents([common, leaf, deepcopy(leaf)])
    with pytest.raises(ValueError, match="missing or ambiguous"):
        ordinary_documents([leaf])


def test_structured_region_schema_and_final_merged_request() -> None:
    common = deepcopy(
        next(row.document for row in load_methodology(ROOT) if row.document["id"] == "sbs.common")
    )
    common["environment"]["region_policy"] = {"selection": "no_portable_region_request"}
    validator = Draft202012Validator(load_schema("methodology"))
    validator.validate(common)
    leaf: dict[str, Any] = {
        "id": "example",
        "inherits": common["id"],
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
    common: dict[str, Any] = {
        "id": "sbs.common",
        "environment": {"region_policy": {"selection": "no_portable_region_request"}},
    }
    leaf: dict[str, Any] = {
        "id": "example",
        "inherits": "sbs.common",
        "environment": {"region_policy": "Explicit existing US leaf policy."},
    }
    docs = [common, leaf]
    with pytest.raises(ValueError, match="leaf region policy conflicts"):
        ordinary_documents(docs)
    assert region_request_errors(leaf, docs, {}) == [
        "leaf region policy conflicts with bound common selector"
    ]
    clean = {**leaf, "environment": {}}
    docs = [common, clean]
    assert ordinary_documents(docs)["example"] is clean
    provider: dict[str, Any] = {"vcpu": 2}
    machine: dict[str, Any] = {"memory_mib": 4096}
    local: dict[str, Any] = {"region": None}
    merged = {**provider, **machine, **local}
    assert region_request_errors(clean, docs, merged) == [
        "no_portable_region_request forbids region in final resources"
    ]


@pytest.fixture
def country_documents() -> list[dict[str, Any]]:
    return [row.document for row in load_methodology(ROOT)]


def country_leaf() -> dict[str, Any]:
    return {"id": "example", "inherits": "sbs.common"}


@pytest.mark.parametrize("countries", [None, frozenset(), {"DE"}, {"US", "DE"}, "US", [None]])
def test_country_admission_refuses_unknown_or_conflicting_facts_without_changing_raw_api(
    country_documents: list[dict[str, Any]], countries: Any
) -> None:
    leaf = country_leaf()
    assert region_request_errors(leaf, country_documents, {"region": "unproved"}) == []
    assert catalog.region_country_errors(leaf, country_documents, countries)


def test_country_argument_is_required_and_has_no_enable_switch() -> None:
    parameters = signature(catalog.region_country_errors).parameters
    assert list(parameters) == ["document", "documents", "countries", "provider"]
    assert parameters["countries"].default is Parameter.empty
    assert parameters["provider"].kind is Parameter.KEYWORD_ONLY


def test_region_exceptions_are_exactly_the_named_providers_each_with_a_reason(
    country_documents: list[dict[str, Any]],
) -> None:
    """Only the providers listed run outside the US; adding one is a visible change here."""
    names = load_yaml((ROOT / "names/names.yaml").read_text())
    exceptions = names["catalog_selection"]["region_country_policy"]["exceptions"]
    assert {entry["provider"]: entry["countries"] for entry in exceptions} == {
        "boat": ["DE", "FI", "FR"]
    }
    policy = region_policy(country_leaf(), country_documents)
    for entry in exceptions:
        assert entry["reason"].strip()
        assert entry["provider"].capitalize() in policy, "the region policy text names it"


@pytest.mark.parametrize(
    ("provider", "countries", "admitted"),
    [
        ("boat", {"DE", "FI", "FR"}, True),
        ("boat", {"FI"}, True),
        ("boat", {"US"}, True),
        ("boat", {"SE"}, False),
        ("boat", None, False),
        ("other", {"DE"}, False),
        (None, {"FR"}, False),
        ("other", {"US"}, True),
    ],
)
def test_only_a_named_exception_admits_countries_outside_the_us(
    country_documents: list[dict[str, Any]],
    provider: str | None,
    countries: Any,
    admitted: bool,
) -> None:
    errors = catalog.region_country_errors(
        country_leaf(), country_documents, countries, provider=provider
    )
    assert (errors == []) is admitted


def test_country_admission_accepts_the_complete_real_owner_catalog_without_mutating_it(
    country_documents: list[dict[str, Any]],
) -> None:
    before = deepcopy(country_documents)
    for document in country_documents:
        assert catalog.region_country_errors(document, country_documents, frozenset({"US"})) == []
    assert country_documents == before


def test_current_documents_inherit_the_common() -> None:
    documents = [row.document for row in load_methodology(ROOT)]
    current = ordinary_documents(documents)

    assert all(
        document["inherits"] == "sbs.common"
        for identifier, document in current.items()
        if identifier != "sbs.common"
    )


def test_country_admission_resolves_current_common_and_exact_leaf_overrides(
    country_documents: list[dict[str, Any]],
) -> None:
    leaf = country_leaf()
    policy = region_policy(leaf, country_documents)
    leaf["environment"] = {"region_policy": policy}
    assert catalog.region_country_errors(leaf, country_documents, {"US"}) == []
    for different in [None, "Unknown explicit leaf policy."]:
        leaf["environment"]["region_policy"] = different
        assert catalog.region_country_errors(leaf, country_documents, {"US"})


def test_country_admission_and_raw_checker_compose_with_a_no_region_common(
    country_documents: list[dict[str, Any]],
) -> None:
    current = ordinary_documents(country_documents)
    leaf = current["exec-latency"]
    common = deepcopy(dict(catalog.common_document(country_documents)))
    common["environment"]["region_policy"] = {"selection": "no_portable_region_request"}
    new = deepcopy(dict(leaf))
    new.setdefault("environment", {}).pop("region_policy", None)
    documents = [common, new]
    assert catalog.region_country_errors(new, documents, None) == []
    assert region_request_errors(new, documents, {}) == []
    assert region_request_errors(new, documents, {"region": None})
    rogue = deepcopy(new)
    rogue["environment"]["region_policy"] = region_policy(leaf, country_documents)
    assert catalog.region_country_errors(rogue, documents, {"US"})
    assert region_request_errors(rogue, documents, {})
    unknown = deepcopy(common)
    unknown["environment"]["region_policy"] = {"selection": "unknown_selector"}
    assert catalog.region_country_errors(new, [unknown, new], None)


@pytest.mark.parametrize("duplicate", [False, True])
def test_country_admission_refuses_a_missing_or_ambiguous_policy_anchor(
    country_documents: list[dict[str, Any]], duplicate: bool
) -> None:
    anchor = catalog.common_document(country_documents)
    documents = (
        [*country_documents, deepcopy(anchor)]
        if duplicate
        else [document for document in country_documents if document is not anchor]
    )
    with pytest.raises(ValueError, match="missing or ambiguous"):
        catalog.region_country_errors(country_leaf(), documents, {"US"})


@pytest.mark.parametrize(
    "binding",
    [
        None,
        {},
        {"countries": []},
        {"countries": ["us"]},
        {"countries": ["US", "US"]},
        {"countries": ["US"], "enable": False},
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


def test_country_binding_schema_is_additive_to_existing_names() -> None:
    names = load_yaml((ROOT / "names/names.yaml").read_text())
    schema = json.loads((ROOT / "schemas/names.schema.json").read_text())
    validator = Draft202012Validator(schema)
    validator.validate(names)
    del names["catalog_selection"]["region_country_policy"]
    validator.validate(names)


@pytest.mark.parametrize("inherited", [None, "unbound"])
def test_country_admission_refuses_a_leaf_without_the_common_context(
    country_documents: list[dict[str, Any]], inherited: Any
) -> None:
    leaf = country_leaf()
    leaf["environment"] = {"region_policy": region_policy(leaf, country_documents)}
    leaf["inherits"] = inherited
    assert catalog.region_country_errors(leaf, country_documents, {"US"})
