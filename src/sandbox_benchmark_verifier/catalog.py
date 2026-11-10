# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Read the distributed methodology snapshot and schemas."""

import json
from collections.abc import Collection, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from importlib.metadata import metadata, packages_distributions
from importlib.resources import files
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, ValidationError

from sandbox_benchmark_verifier.versions import newest, version_key
from sandbox_benchmark_verifier.yaml_load import load_yaml, load_yaml_all

# Release builds stamp the source commit of their bundled assets.
METHODOLOGY_COMMIT = "fe55f37c02a2552eac59929d0222cb85d7a1dbd0"
_PACKAGED_ASSETS = Path(str(files("sandbox_benchmark_verifier").joinpath("_assets")))
SCHEMA_NAMES = ("methodology", "run", "measurement", "aggregate", "reader", "presentation")


def repository_url() -> str:
    """The repository URL the installed distribution declares."""
    distribution = packages_distributions()["sandbox_benchmark_verifier"][0]
    repository: str = next(
        value.split(",", 1)[1].strip()
        for value in metadata(distribution).get_all("Project-URL", [])
        if value.startswith("Repository,")
    )
    return repository


@dataclass(frozen=True)
class MethodologyDocument:
    """One YAML document and its declared repository source location, if known."""

    path: str
    document: dict[str, Any]
    source_commit: str | None = None

    @property
    def source_url(self) -> str | None:
        """Return this document's immutable URL using the installed repository metadata."""
        if self.source_commit is None:
            return None
        return f"{repository_url()}/blob/{self.source_commit}/{self.path}"


def _assets() -> Path:
    # Hatch includes the canonical root directories in wheels; editable installs read them in place.
    return _PACKAGED_ASSETS if _PACKAGED_ASSETS.is_dir() else Path(__file__).resolve().parents[2]


def retired_ids(root: Path | None = None) -> frozenset[str]:
    """Ids on the aliases' ``retired`` list: a retired test's released documents stay in the
    repository and the immutability lock, but nothing reads them."""
    path = (root or _assets()) / "names" / "aliases.yaml"
    if not path.is_file():
        return frozenset()
    aliases = load_yaml(path.read_text(encoding="utf-8"))
    return frozenset(str(item) for item in (aliases or {}).get("retired", []))


def load_methodology(
    root: Path | None = None, *, include_retired: bool = False
) -> list[MethodologyDocument]:
    """Return all YAML documents in repository path order, preserving multi-document files.

    A document whose id is on the aliases' ``retired`` list is skipped unless ``include_retired``:
    its released versions stay in the immutability lock and the repository, but nothing reads them.

    Paths are repository relative; each document exposes its source URL through ``source_url``.
    Each call returns fresh parsed dictionaries.
    """
    root = root or _assets()
    source_commit = METHODOLOGY_COMMIT if root.resolve() == _PACKAGED_ASSETS.resolve() else None
    retired = frozenset() if include_retired else retired_ids(root)
    documents = []
    for path in sorted((root / "methodology").rglob("*.yaml")):
        for document in load_yaml_all(path.read_text(encoding="utf-8")):
            if document is not None:
                _require_string_keys(document)
                if document.get("id") in retired:
                    continue  # a retired test's released versions stay in the lock, not in use
                documents.append(
                    MethodologyDocument(path.relative_to(root).as_posix(), document, source_commit)
                )
    return documents


def load_schema(name: str) -> dict[str, Any]:
    """Return a fresh schema by family name or filename, for example ``aggregate``."""
    family = name.removesuffix(".schema.json")
    if family not in SCHEMA_NAMES:
        raise ValueError(f"unknown methodology schema: {name}")
    document: dict[str, Any] = json.loads(
        (_assets() / "schemas" / f"{family}.schema.json").read_text(encoding="utf-8")
    )
    return document


def default_context(root: Path | None = None) -> dict[str, str]:
    """The owner's one declared default common; never inferred from version order."""
    names = load_yaml(((root or _assets()) / "names/names.yaml").read_text())
    reference = names.get("catalog_selection", {}).get("default_common")
    if (
        not isinstance(reference, dict)
        or set(reference) != {"id", "version"}
        or reference["id"] != "sbs.common"
        or not isinstance(reference["version"], str)
    ):
        raise ValueError("catalog selection needs an exact default common reference")
    version_key(reference["version"])
    return dict(reference)


def ordinary_documents(
    documents: Sequence[Mapping[str, Any]],
    *,
    common: Mapping[str, str],
    effective: str | None = None,
) -> dict[str, Mapping[str, Any]]:
    """Select ordinary versions inside an exact common context before version ordering.

    This is a selection, not a compatibility or measurement assertion. All-version catalog
    loading remains unchanged; callers validate the contributing output graphs separately.
    """
    parent = common_document(documents, common)
    found: dict[str, Mapping[str, Any]] = {}
    for document in documents:
        if effective is not None and str(document.get("effective", "")) > effective:
            continue
        if document is not parent and not ordinary_in_context(document, common):
            continue
        identity = str(document["id"])
        known = found.get(identity)
        if known is None or version_key(document["version"]) > version_key(known["version"]):
            found[identity] = document
        elif document["version"] == known["version"] and document is not known:
            raise ValueError(f"ambiguous document reference: {identity}@{document['version']}")
    for document in found.values():
        if errors := context_region_errors(document, parent):
            raise ValueError("; ".join(errors))
    return found


def ordinary_in_context(document: Mapping[str, Any], common: Mapping[str, str]) -> bool:
    """The shared ordinary candidate policy for selection and as-of input validation."""
    return document.get("inherits") == common


def common_document(
    documents: Sequence[Mapping[str, Any]], reference: Mapping[str, str]
) -> Mapping[str, Any]:
    """Validate a context without selecting or inspecting unrelated output graphs."""
    if (
        set(reference) != {"id", "version"}
        or reference["id"] != "sbs.common"
        or not isinstance(reference["version"], str)
    ):
        raise ValueError("selection needs an exact sbs.common context")
    version_key(reference["version"])
    parents = [
        d
        for d in documents
        if (d.get("id"), d.get("version")) == (reference["id"], reference["version"])
    ]
    if len(parents) != 1:
        raise ValueError("selection common is missing or ambiguous")
    return parents[0]


def selection_projection(root: Path, documents: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Generate the default context and its exact ordinary refs for artifact consumers."""
    context = default_context(root)
    current = dict(ordinary_documents(documents, common=context))
    # A document outside the default context (a planned one, such as Reliability) is still a page
    # consumers may draw, so it is listed at its newest version too.
    path = root / "names" / "aliases.yaml"
    aliases = load_yaml(path.read_text(encoding="utf-8")) if path.is_file() else {}
    canonical = (aliases or {}).get("document", {})
    covered = {canonical.get(identity, identity) for identity in current}
    current.update(
        newest(
            document
            for document in documents
            if canonical.get(str(document["id"]), str(document["id"])) not in covered
        )
    )
    return {
        "default_context": context,
        "current_documents": {
            identity: {"id": identity, "version": str(document["version"])}
            for identity, document in current.items()
        },
    }


def region_policy(document: Mapping[str, Any], documents: Sequence[Mapping[str, Any]]) -> Any:
    """The exact leaf override, otherwise its exact inherited region policy."""
    own = document.get("environment", {})
    if "region_policy" in own:
        return deepcopy(own["region_policy"])
    inherited = document.get("inherits")
    parents = [
        d for d in documents if inherited == {"id": d.get("id"), "version": d.get("version")}
    ]
    if len(parents) != 1:
        raise ValueError("region policy needs a unique exact inherited common")
    return deepcopy(parents[0].get("environment", {}).get("region_policy"))


def context_region_errors(document: Mapping[str, Any], parent: Mapping[str, Any]) -> list[str]:
    """Keep a structured location cohort homogeneous without rewriting leaf overrides."""
    bound = parent.get("environment", {}).get("region_policy")
    own = document.get("environment", {})
    if isinstance(bound, Mapping):
        if dict(bound) != {"selection": "no_portable_region_request"}:
            return ["unknown structured region selection"]
        if "region_policy" in own and own["region_policy"] != bound:
            return ["leaf region policy conflicts with bound common selector"]
    elif isinstance(own.get("region_policy"), Mapping):
        return ["structured leaf region policy requires the matching common selector"]
    return []


def region_request_errors(
    document: Mapping[str, Any],
    documents: Sequence[Mapping[str, Any]],
    resources: Mapping[str, Any],
) -> list[str]:
    """Validate the final merged portable request, without guessing native placement."""
    inherited = document.get("inherits")
    parent = common_document(documents, inherited) if isinstance(inherited, Mapping) else document
    errors = context_region_errors(document, parent)
    policy = parent.get("environment", {}).get("region_policy")
    if policy == {"selection": "no_portable_region_request"} and "region" in resources:
        errors.append("no_portable_region_request forbids region in final resources")
    return errors


def _region_country_binding(
    documents: Sequence[Mapping[str, Any]],
) -> tuple[frozenset[str], frozenset[str], dict[str, frozenset[str]]]:
    assets = _assets()
    names = load_yaml((assets / "names/names.yaml").read_text())
    selection = names.get("catalog_selection", {}) if isinstance(names, Mapping) else {}
    binding = selection.get("region_country_policy") if isinstance(selection, Mapping) else None
    if not isinstance(binding, Mapping):
        raise ValueError("region country policy binding must be an object")
    schema = json.loads((assets / "schemas/names.schema.json").read_text())
    fragment = schema["properties"]["catalog_selection"]["properties"]["region_country_policy"]
    try:
        Draft202012Validator(schema).evolve(schema=fragment).validate(binding)
    except ValidationError as error:
        raise ValueError("region country policy binding is missing or invalid") from error
    anchor = common_document(documents, binding["source"])
    policy = region_policy(anchor, documents)
    if not isinstance(policy, str) or not policy:
        raise ValueError("region country policy binding needs a legacy whole policy value")
    approved = {policy}
    for reference in binding.get("equivalents", []):
        equivalent = common_document(documents, reference)
        policy = region_policy(equivalent, documents)
        if not isinstance(policy, str) or not policy:
            raise ValueError("region country policy equivalent needs a whole policy value")
        approved.add(policy)
    exceptions = binding.get("exceptions", [])
    providers = [entry["provider"] for entry in exceptions]
    if len(set(providers)) != len(providers):
        raise ValueError("region country policy binding names a provider twice")
    return (
        frozenset(approved),
        frozenset(binding["countries"]),
        {entry["provider"]: frozenset(entry["countries"]) for entry in exceptions},
    )


def region_country_errors(
    document: Mapping[str, Any],
    documents: Sequence[Mapping[str, Any]],
    countries: Collection[str] | None,
    *,
    provider: str | None = None,
) -> list[str]:
    """Check declared requested-country evidence, separately from the raw request checker.

    `documents` is the complete owner catalogue, including the exact policy anchor. A
    `provider` the binding's `exceptions` names may also run in that entry's countries.
    Callers compose this with region_request_errors on the unchanged final resource mapping.
    Required evidence is not observed placement; None/empty never satisfies a country policy.
    """
    inherited = document.get("inherits")
    if isinstance(inherited, Mapping):
        parent = common_document(documents, inherited)
    elif document.get("id") == "sbs.common":
        version = document.get("version")
        if not isinstance(version, str):
            raise ValueError("country admission needs an exact common version")
        parent = common_document(documents, {"id": "sbs.common", "version": version})
    else:
        return ["country admission needs an exact inherited common"]
    if errors := context_region_errors(document, parent):
        return errors
    policy = region_policy(document, documents)
    if policy == {"selection": "no_portable_region_request"}:
        return []
    approved, allowed, exceptions = _region_country_binding(documents)
    if not isinstance(policy, str) or policy not in approved:
        return ["region policy has no country admission interpretation"]
    if (
        countries is None
        or not isinstance(countries, Collection)
        or isinstance(countries, (str, bytes, Mapping))
        or not countries
        or any(not isinstance(country, str) for country in countries)
    ):
        return ["requested placement countries are missing or unproved"]
    return (
        []
        if frozenset(countries) <= allowed | exceptions.get(provider or "", frozenset())
        else ["requested placement countries conflict with region policy"]
    )


def _require_string_keys(value: Any) -> None:
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("methodology YAML must contain only string mapping keys")
        for child in value.values():
            _require_string_keys(child)
    elif isinstance(value, list):
        for child in value:
            _require_string_keys(child)
