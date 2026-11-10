# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Report text-slot gaps, build the release text asset. At pylint's 1000-line limit: split first."""

import copy
import hashlib
import json
import re
from collections.abc import Callable, Iterable, Mapping
from functools import cache, partial
from pathlib import Path
from typing import Any
from urllib.parse import quote

from sandbox_benchmark_verifier import catalog, procedures, reader_style
from sandbox_benchmark_verifier.catalog import _assets
from sandbox_benchmark_verifier.clocks import includes_network_latency
from sandbox_benchmark_verifier.content import (
    content_criteria,
    criteria_errors,
    document_content_errors,
    is_literal_template,
    slot_errors,
)
from sandbox_benchmark_verifier.conventions import load_conventions
from sandbox_benchmark_verifier.machine_panels import apply_panel_text
from sandbox_benchmark_verifier.methodology_page import (
    inherited_cost,
    omit_paths,
    reader_definition,
)
from sandbox_benchmark_verifier.metric_labels import compile_metric_labels, expand_metric_labels
from sandbox_benchmark_verifier.name_refs import (
    canonical_name_map,
    projection_references,
    published_name_errors,
    reference_composite_scopes,
    resolve_name_references,
    retired_name_references,
    source_name_errors,
)
from sandbox_benchmark_verifier.names import (
    Tables,
    compiler_tables_cache,
    effective_statistic_id,
    load_tables,
    shows_cost,
)
from sandbox_benchmark_verifier.option_hovers import option_hovers
from sandbox_benchmark_verifier.panel_slots import panel_required_slots
from sandbox_benchmark_verifier.pinned_workload import pinned_workload
from sandbox_benchmark_verifier.placeholders import PLACEHOLDER_OPENERS, resolve_template
from sandbox_benchmark_verifier.reader import (
    PAGE_MEMBER_NOTES,
    apply_reader_views,
    compile_presentation,
    compile_reader,
    exported_common,
    presentation_schema,
    reader_common,
    reader_errors,
    reader_similarity_errors,
    reader_slot_errors,
    reader_source,
)
from sandbox_benchmark_verifier.rejected_labels import rejected_label_errors
from sandbox_benchmark_verifier.scope_kinds import panel_scope_kind
from sandbox_benchmark_verifier.stage_hovers import BOUNDARY, stage_hovers
from sandbox_benchmark_verifier.status_footer import (
    concurrency_cap_status_errors,
    status_label_owner_errors,
)
from sandbox_benchmark_verifier.text_ids import index_metric_id_warnings, raw_token_warnings
from sandbox_benchmark_verifier.versions import version_key
from sandbox_benchmark_verifier.vocabulary import retired_word_errors
from sandbox_benchmark_verifier.yaml_load import load_yaml

DIGEST_ALGORITHMS = {"semantic_digest": "sha256:method/2"}


def semantic_digest(document: Mapping[str, Any], names: Mapping[str, str] | None = None) -> str:
    """Hash measured semantics independently of display names and reader-facing text.

    A canonical-name placeholder hashes as the name it resolves to, so a reader revision may
    reference a name instead of repeating it. ``names`` defaults to the checkout's registry.
    """
    return _method_digest(document, names=names or _registry_names())


@cache
def _registry_names() -> dict[str, str]:
    return canonical_name_map(load_tables())


def _method_digest(document: Mapping[str, Any], *, names: Mapping[str, str] | None = None) -> str:
    method = _measured_definition(document)
    if names:
        method = _named(method, names)
    canonical = json.dumps(method, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _measured_definition(document: Mapping[str, Any]) -> dict[str, Any]:
    """Canonical scientific fields shared by fingerprints and reader compatibility."""
    method: dict[str, Any] = json.loads(json.dumps(document))
    for key in (
        "version",
        "effective",
        "description",
        "headline",
        "notes",
        "interpretation",
        "panel",
        "name",
    ):
        method.pop(key, None)
    parameters = method.get("parameters")
    if isinstance(parameters, dict):
        parameters.pop("path", None)
    procedure = method.get("procedure")
    if isinstance(procedure, dict):
        for kind in ("variants", "stages"):
            for item in procedure.get(kind, []):
                if isinstance(item, dict):
                    for key in ("description", "notes"):
                        item.pop(key, None)
                    if kind == "variants":
                        item.pop("label", None)
    for metric in method.get("metrics", []):
        if isinstance(metric, dict):
            metric.pop("quantity", None)
            metric.pop("per", None)
            metric.pop("phase", None)
            metric.pop("statistic", None)
    source = method.get("source")
    if isinstance(source, dict):
        source.pop("venue_terms", None)
    return method


def _methodologies(root: Path) -> list[dict[str, Any]]:
    documents = [
        item.document
        for item in catalog.load_methodology(root)
        if isinstance(item.document, dict) and isinstance(item.document.get("id"), str)
    ]
    with compiler_tables_cache(root, DIGEST_ALGORITHMS):
        return apply_panel_text(root, documents)


def _name_slot(document: Mapping[str, Any], names: Mapping[str, str]) -> dict[str, str]:
    """Resolve an explicitly referenced canonical name for the text bundle."""
    name = document.get("name")
    if isinstance(name, str) and "{name:" in name:
        return {"name": resolve_template(name, names=names, documents={})}
    return {}


def _slot_text(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    document: Mapping[str, Any],
    documents: Mapping[tuple[str, str], Mapping[str, Any]],
    names: Mapping[str, str],
    directions: Mapping[str, str],
    sources: Mapping[str, Mapping[str, Any]],
    labels: bool = False,
) -> dict[str, Any]:
    """Project fields that supply visible text, excluding method-only prose and facts.

    ``labels`` marks the names of timed parts in the notes (conventions ``inline_label``).
    """
    result: dict[str, Any] = _name_slot(document, names)
    if "headline" in document:
        result["headline"] = document["headline"]
    if "title" in document.get("panel", {}):
        result["panel"] = {key: document["panel"][key] for key in ("title", "command")}
    notes = document.get("notes")
    if isinstance(notes, Mapping):
        result["notes"] = dict(notes)
    result.update(
        {
            key: hovers
            for key, hovers in (
                ("stage_hovers", stage_hovers(document)),
                ("option_hovers", option_hovers(document)),
            )
            if hovers
        }
    )
    metrics = document.get("metrics")
    if isinstance(metrics, list):
        result["metrics"] = [
            {
                "name": metric.get("name"),
                "quantity": metric.get("quantity"),
                "phase": metric.get("phase"),
                "per": metric.get("per"),
                "unit": metric.get("unit"),
                "direction": metric.get("direction"),
                "statistic": effective_statistic_id(document, metric, documents),
            }
            for metric in metrics
            if isinstance(metric, Mapping)
        ]
    procedure = document.get("procedure")
    if isinstance(procedure, Mapping):
        for kind in ("variants", "stages"):
            values = procedure.get(kind)
            if not isinstance(values, list):
                continue
            rows = []
            for value in values:
                if not isinstance(value, Mapping):
                    continue
                rows.append(
                    {
                        key: value[key]
                        for key in ("id", "description", "notes", "command")
                        if key in value
                    }
                )
            result[kind] = rows
    if isinstance(document.get("source"), Mapping) and "venue_terms" in document["source"]:
        result["venue_terms"] = document["source"]["venue_terms"]
    return {
        key: _resolve_slot_references(
            value,
            partial(
                resolve_template,
                names=names,
                documents=sources,
                directions=directions,
                stage_marks=labels and key == "notes",
            ),
        )
        for key, value in result.items()
    }


def _resolve_slot_references(value: Any, resolve: Callable[[str], str]) -> Any:
    """Resolve explicit document/version references with the shared placeholder resolver."""
    if isinstance(value, str):
        return resolve(value)
    if isinstance(value, dict):
        return {key: _resolve_slot_references(item, resolve) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_slot_references(item, resolve) for item in value]
    return value


def _reader_versions(
    aliases: Mapping[str, Any],
    documents: Mapping[tuple[str, str], Mapping[str, Any]],
    names: Mapping[str, str],
) -> dict[tuple[str, str], str]:
    """Validate explicit one-hop reader revisions without selecting compatible candidates."""
    bindings: dict[tuple[str, str], str] = {}
    for reference in aliases.get("reader_versions", []):
        measured = (reference["id"], reference["version"])
        reader_version = reference["reader_version"]
        if measured in bindings:
            raise ValueError(f"duplicate or conflicting reader revision for {measured!r}")
        bindings[measured] = reader_version
    for measured, reader_version in bindings.items():
        reader = (measured[0], reader_version)
        if measured not in documents or reader not in documents:
            raise ValueError(
                f"unknown reader revision document/version: {measured!r} -> {reader!r}"
            )
        if reader in bindings:
            raise ValueError(f"reader revisions must be direct: {measured!r} -> {reader!r}")
        original, revised = documents[measured], documents[reader]
        if _named(_measured_definition(original), names) != _named(
            _measured_definition(revised), names
        ) or (
            _metric_statistics(original, documents) != _metric_statistics(revised, documents)
            or original.get("parameters") != revised.get("parameters")
        ):
            raise ValueError(
                f"reader revision changes measured semantics: {measured!r} -> {reader!r}"
            )
    return bindings


def _named(value: Any, names: Mapping[str, str]) -> Any:
    """A definition with its canonical-name placeholders written out, so a reader revision may
    reference a name instead of repeating it."""
    return _resolve_slot_references(
        value, partial(resolve_template, names=names, documents={}, directions={})
    )


def _metric_statistics(
    document: Mapping[str, Any],
    documents: Mapping[tuple[str, str], Mapping[str, Any]],
) -> list[str | None]:
    return [
        effective_statistic_id(document, metric, documents)
        for metric in document.get("metrics", [])
    ]


def _public_method_url(identifier: str, tables: Any) -> str:
    """The page address of a document: its page row's id in the names table when it has one
    (``common`` for ``sbs.common``), else the document id."""
    canonical_id = str(tables.aliases.get("document", {}).get(identifier, identifier))
    page_ids = {
        str(row["document"]): str(row["id"])
        for row in tables.names
        if row["kind"] == "page" and "document" in row
    }
    return f"/benchmarks/methodology/{quote(page_ids.get(canonical_id, canonical_id), safe='')}"


def _direction_labels(tables: Any) -> Mapping[str, str]:
    """The reader's words for a metric's direction id."""
    labels: Mapping[str, str] = tables.metric_label_grammar.get("tokens", {}).get("directions", {})
    return labels


def _reader_bundle(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    root: Path,
    tables: Tables,
    documents: Mapping[tuple[str, str], Mapping[str, Any]],
    names: Mapping[str, str],
    replacements: Mapping[str, str],
    parameters: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """The current reader copy compiled against each measured version of its method."""
    directions = tables.metric_label_grammar.get("tokens", {}).get("directions", {})
    known_documents = {identifier for identifier, _ in documents}
    common_documents = {
        identifier
        for (identifier, _), document in documents.items()
        if document.get("category") == "common"
    }
    document_ids = {
        str(row["id"]): next(
            candidate
            for candidate in (
                str(tables.aliases.get("document", {}).get(row["document"], row["document"])),
                str(row["document"]),
            )
            if candidate in known_documents
        )
        for row in tables.names
        if row["kind"] in ("test", "index") and "document" in row
    }
    # The common methodology has no names row: its reader entry is keyed "common".
    for identifier in sorted(common_documents):
        document_ids["common"] = identifier

    # A typed stage name stays typed (reader_style rule stage-labels); only a placeholder is marked.
    typed = {k: v for k, v in replacements.items() if k not in names.values() or "stage." != v[:6]}

    def project(carrier: Mapping[str, Any], *, labels: bool = True) -> dict[str, Any]:
        """The reader slots of one carrier: headline, notes, panel (no labels in a table cell)."""
        # The carrier holds its pinned workload, so notes read the same rows the panel text does.
        # References to the version's own notes (a variant's notes read them) still resolve.
        own = documents[carrier["id"], str(carrier["version"])].get("notes", {})
        pinned = {**carrier, "notes": {**own, **carrier.get("notes", {})}}
        sources = {**parameters, f"{carrier['id']}.{carrier['version']}": pinned}
        text = _slot_text(carrier, documents, names, directions, sources, labels=labels)
        projected: dict[str, Any] = resolve_name_references(
            {key: text[key] for key in ("headline", "notes", "panel") if key in text},
            documents=parameters,
            names=names,
            references=typed,
            directions=directions,
        )
        return projected

    methods = compile_reader(root, documents=documents, document_ids=document_ids, project=project)
    presentation = _optional_presentation(root)
    return {
        "source": reader_source(root) if methods is not None else None,
        "common": reader_common(root),
        "methods": methods or {},
        "presentation": presentation,
        "presentation_schema": presentation_schema() if presentation is not None else None,
    }


def _optional_presentation(root: Path) -> dict[str, Any] | None:
    """Compile the optional global presentation channel when the source tree supplies it."""
    if (root / "conventions").exists() or root.resolve() == _assets().resolve():
        return compile_presentation(root)
    return None


def _region_exceptions(names_doc: Any) -> list[Any]:
    """The providers the region policy lets run outside the US, as names.yaml lists them."""
    selection = names_doc.get("catalog_selection", {}) if isinstance(names_doc, Mapping) else {}
    return list((selection.get("region_country_policy") or {}).get("exceptions", []))


def build_text_bundle(root: Path) -> dict[str, Any]:
    """Return deterministic slot text for every methodology version and name row."""
    with compiler_tables_cache(root, DIGEST_ALGORITHMS):
        return _build_text_bundle(root)


def _build_text_bundle(root: Path) -> dict[str, Any]:
    """Build the owned text bundle within an exact-source table-cache scope."""
    names_path = root / "names" / "names.yaml"
    names_doc = load_yaml(names_path.read_text(encoding="utf-8")) if names_path.is_file() else {}
    tables = load_tables(root)
    canonical_names = canonical_name_map(tables)
    name_replacements = projection_references(root)
    methods = _methodologies(root)
    methods.sort(key=lambda item: (str(item.get("id", "")), str(item.get("version", ""))))
    indexed_methods: dict[str, dict[str, Any]] = {}
    documents = {(str(method["id"]), str(method["version"])): method for method in methods}
    reader_versions = _reader_versions(tables.aliases, documents, canonical_names)
    parameters: dict[str, Mapping[str, Any]] = {
        f"{identifier}.{version}": doc for (identifier, version), doc in documents.items()
    }
    # Fresh copies, as a second _methodologies(root) call returns them (one parse serves both).
    parameters.update(
        copy.deepcopy(_latest_documents(methods, common=catalog.default_context(root)))
    )
    parameters["content"] = content_criteria()
    parameters["reader"] = {"common": reader_common(root)}
    parameters["procedure_references"] = procedures.projection(root, methods, tables)
    for method in methods:
        identifier = str(method["id"])
        version = str(method["version"])
        indexed_methods.setdefault(identifier, {})[version] = {
            "owner": {"public_method_url": _public_method_url(identifier, tables)},
            "semantic_digest": semantic_digest(method, canonical_names),
            "cost": inherited_cost(method, documents) if "cost" in method else {},
            "reader_definition": resolve_name_references(
                reference_composite_scopes(
                    reader_definition(
                        method,
                        documents,
                        parameters["content"]["methodology_page"],
                        str(tables.aliases.get("document", {}).get(identifier, identifier)),
                        tables.names,
                        tables.aliases.get("document", {}),
                    )
                ),
                documents=parameters,
                names=canonical_names,
                references=name_replacements,
                directions=_direction_labels(tables),
            ),
            "includes_network_latency": includes_network_latency(method),
            "timed_steps": sorted(
                {
                    record
                    for step in method.get("procedure", {}).get("steps", [])
                    if step.get("timer") is not None
                    for record in step.get("record_steps", [])
                }
            ),
            "text": _slot_text(
                method,
                documents,
                canonical_names,
                _direction_labels(tables),
                parameters,
            ),
        }
        indexed_methods[identifier][version] = resolve_name_references(
            indexed_methods[identifier][version],
            documents=parameters,
            names=canonical_names,
            references=name_replacements,
            directions=_direction_labels(tables),
        )
        omit_paths(
            indexed_methods[identifier][version]["text"],
            parameters["content"]["methodology_page"].get("retired_reader_paths", []),
        )
        if (identifier, version) in parameters["procedure_references"]["references"]:
            indexed_methods[identifier][version]["procedure"] = parameters["procedure_references"][
                "references"
            ][identifier, version]
        if (identifier, version) in reader_versions:
            indexed_methods[identifier][version]["reader_version"] = reader_versions[
                identifier, version
            ]
    bundle = {
        "schema": "boxrouter.bench.text/3",
        "procedures_public": parameters["procedure_references"]["procedures_public"],
        "digest_algorithms": dict(DIGEST_ALGORITHMS),
        "content_criteria": parameters["content"],
        "names_schema": names_doc.get("schema") if isinstance(names_doc, Mapping) else None,
        "names": tables.published_names(),
        "name_references": {"retired_display_names": sorted(retired_name_references(root))},
        "metric_label_grammar": names_doc.get("metric_label_grammar")
        if isinstance(names_doc, Mapping)
        else None,
        **catalog.selection_projection(root, methods),
        "region_exceptions": _region_exceptions(names_doc),
        "methodologies": indexed_methods,
        "reader": _reader_bundle(
            root, tables, documents, canonical_names, name_replacements, parameters
        ),
    }

    apply_reader_views(root, indexed_methods, bundle["reader"])
    bundle["metric_labels"] = compile_metric_labels(bundle, tables.aliases)
    return bundle


def export_text_bundle(bundle: Mapping[str, Any]) -> dict[str, Any]:
    """The bundle as consumers read it: resolved text only, no source form left to resolve.

    The checks read the authored forms in the in-memory bundle; the file keeps the resolved text,
    and only what `exported_common` keeps of ``reader.common`` (its other tab blocks are sources).
    A ``{kind:...}`` reference left anywhere fails the build, so no consumer needs a resolver.
    """
    exported: dict[str, Any] = copy.deepcopy(dict(bundle))
    criteria = exported["content_criteria"]
    for entry in criteria.get("hover_templates", {}).values():
        if "authored_template" in entry:
            entry["authored_template"] = entry["template"]
    for entry in criteria.get("literal_templates", {}).values():
        entry["reference"] = resolve_template(
            entry["reference"], names={}, documents={"content": criteria}
        )
    reader = exported["reader"]
    reader["common"] = exported_common(reader["common"], criteria)
    for entry in (
        (reader.get("presentation") or {})
        .get("content_criteria", {})
        .get("hover_templates", {})
        .values()
    ):
        entry["authored_template"] = entry["template"]
    if leftovers := placeholder_leftovers(exported):
        raise ValueError(
            "the text bundle must hold resolved text only; unresolved references at "
            + ", ".join(leftovers[:10])
            + (f" and {len(leftovers) - 10} more" if len(leftovers) > 10 else "")
        )
    return exported


def placeholder_leftovers(value: Any, path: str = "") -> list[str]:
    """Where a string still holds a ``{kind:...}`` reference."""
    if isinstance(value, str):
        return [path] if any(opener in value for opener in PLACEHOLDER_OPENERS) else []
    if isinstance(value, Mapping):
        return [
            found
            for key, item in value.items()
            for found in placeholder_leftovers(item, f"{path}.{key}")
        ]
    if isinstance(value, list):
        return [
            found
            for index, item in enumerate(value)
            for found in placeholder_leftovers(item, f"{path}[{index}]")
        ]
    return []


def write_text_bundle(root: Path, output: Path) -> None:
    """Write the generated text bundle as stable UTF-8 JSON."""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(export_text_bundle(build_text_bundle(root)), indent=2, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )


def _nonempty(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    return value is not None


def _warn_slot(warnings: list[str], path: str, value: Any) -> None:
    if not _nonempty(value):
        warnings.append(f"missing or unfilled slot {path}")


def _warn_length(warnings: list[str], path: str, value: Any, limit: int) -> None:
    if isinstance(value, str) and len(value) > limit:
        warnings.append(f"text exceeds {limit} characters {path}")


@cache
def _proper_name_starts() -> frozenset[str]:
    """The lowercase tool names a sentence may start with (reader.capital-start allowed_starts)."""
    return frozenset(load_conventions()["proper_names"])


def _warn_sentence(warnings: list[str], path: str, value: Any, limit: int) -> None:
    _warn_length(warnings, path, value, limit)
    if not isinstance(value, str) or not value.strip():
        return
    text = value.strip()
    starts = text[:1].isupper() or text[:1].isdigit() or text.split()[0] in _proper_name_starts()
    if not starts or not text.endswith("."):
        warnings.append(f"text is not a complete sentence {path}")


def _latest_documents(
    documents: Iterable[Mapping[str, Any]], *, common: Mapping[str, str] | None = None
) -> dict[str, Mapping[str, Any]]:
    """Ordinary defaults in the owner's bound context; exact-version records remain separate."""
    return catalog.ordinary_documents(
        list(documents), common=catalog.default_context() if common is None else common
    )


def _style_documents(root: Path) -> dict[str, Mapping[str, Any]]:
    """Selected defaults with their exact pinned workloads for reader checks."""
    every = {(str(doc["id"]), str(doc.get("version"))): doc for doc in _methodologies(root)}
    return {
        name: (
            {
                **doc,
                "parameters": {**doc["parameters"], "workload": pinned_workload(root, doc, every)},
            }
            if isinstance(doc.get("parameters"), Mapping) and "path" in doc["parameters"]
            else doc
        )
        for name, doc in _latest_documents(
            _methodologies(root), common=catalog.default_context(root)
        ).items()
    }


def _document_rows(root: Path, rows: list[Any]) -> dict[str, Mapping[str, Any]]:
    aliases_path = root / "names" / "aliases.yaml"
    aliases_doc = (
        load_yaml(aliases_path.read_text(encoding="utf-8")) if aliases_path.is_file() else {}
    )
    document_aliases = aliases_doc.get("document", {}) if isinstance(aliases_doc, Mapping) else {}
    latest = _latest_documents(_methodologies(root), common=catalog.default_context(root))
    mapped: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if isinstance(row, Mapping) and isinstance(row.get("document"), str):
            document_id = str(document_aliases.get(row["document"], row["document"]))
            candidates = [
                document
                for source_id, document in latest.items()
                if document_aliases.get(source_id, source_id) == document_id
            ]
            if candidates:
                # This audits the current owner definition; it grants no reader compatibility.
                mapped[f"{row.get('kind')}:{row.get('id')}"] = max(
                    candidates,
                    key=lambda document: version_key(document["version"]),
                )
    return mapped


def _field_warnings(
    warnings: list[str],
    identifier: str,
    document: Mapping[str, Any],
    field: str,
    *,
    criteria: Mapping[str, Any] | None = None,
) -> None:
    if field.startswith("notes."):
        name = field.removeprefix("notes.")
        notes = document.get("notes", {})
        value = notes.get(name) if isinstance(notes, Mapping) else None
        _warn_slot(warnings, f"{identifier}.{field}", value)
        if not (
            name in {"introduction", "cost_summary"}
            and is_literal_template(value, document.get("category"), criteria=criteria)
        ):
            _warn_sentence(
                warnings,
                f"{identifier}.{field}",
                value,
                140 if name == "cost_summary" and document.get("category") != "workload" else 300,
            )
    elif field.startswith("metrics[]."):
        metric_field = field.removeprefix("metrics[].")
        metrics = document.get("metrics", [])
        for index, metric in enumerate(metrics if isinstance(metrics, list) else []):
            if isinstance(metric, Mapping):
                _warn_slot(
                    warnings,
                    f"{identifier}.metrics[{index}].{metric_field}",
                    metric.get(metric_field),
                )


def _panel_slots(panel_kind: str, *, cost_shown: bool) -> list[str]:
    """The slots a panel of this kind must fill; a test that shows cost adds the cost view's."""
    required = panel_required_slots()
    slots = list(required[panel_kind])
    if panel_kind != "workload_cost_view" and cost_shown:
        slots.extend(required["workload_cost_view"])
    return slots


def _base_warnings(
    warnings: list[str],
    identifier: str,
    document: Mapping[str, Any],
    required_fields: list[str],
    *,
    criteria: Mapping[str, Any] | None = None,
) -> None:
    for field in required_fields:
        _field_warnings(warnings, identifier, document, field, criteria=criteria)
    if "description" in document:
        warnings.append(f"text outside slots {identifier}.description")


def _variant_warnings(
    warnings: list[str],
    rows: list[Any],
    row: Mapping[str, Any],
    document: Mapping[str, Any],
    identifier: str,
) -> None:
    selector = row.get("selector")
    procedure = document.get("procedure", {})
    variants = procedure.get("variants", []) if isinstance(procedure, Mapping) else []
    name_rows = {
        str(item.get("workload")): item
        for item in rows
        if isinstance(item, Mapping)
        and item.get("kind") in ("variant", "job")
        and item.get("parent") == row.get("id")
    }
    if any(
        isinstance(item, Mapping)
        and item.get("parent") == row.get("id")
        and item.get("kind") in ("variant", "job")
        for item in rows
    ) and not isinstance(selector, Mapping):
        warnings.append(
            f"missing selector slot names row {row.get('row')} {row.get('id')}.selector"
        )
    # An option names the test itself as one choice of a selector shared by several tests
    # (Disk's fio tests, Exec's views); the test's variants are not that selector's options.
    if not isinstance(selector, Mapping) or "option" in selector:
        return
    for variant in variants if isinstance(variants, list) else []:
        if not isinstance(variant, Mapping):
            continue
        variant_id = str(variant.get("id"))
        _warn_slot(
            warnings, f"{identifier}.variants.{variant_id}.description", variant.get("description")
        )
        _warn_sentence(
            warnings,
            f"{identifier}.variants.{variant_id}.description",
            variant.get("description"),
            160,
        )
        notes = variant.get("notes", {})
        if selector.get("mode") == "chooses":
            for field in ("summary", "what_this_measures"):
                value = notes.get(field) if isinstance(notes, Mapping) else None
                _warn_slot(warnings, f"{identifier}.variants.{variant_id}.notes.{field}", value)
        name_row = name_rows.get(variant_id)
        if name_row and variant.get("label") != name_row.get("name"):
            warnings.append(
                f"text does not match names slot {identifier}.variants.{variant_id}.label"
            )


def _stage_warnings(warnings: list[str], document: Mapping[str, Any], identifier: str) -> None:
    procedure = document.get("procedure", {})
    stages = procedure.get("stages", []) if isinstance(procedure, Mapping) else []
    rows = {key: row for key, row in stage_hovers(document).items() if key != BOUNDARY}
    for stage in stages if isinstance(stages, list) else []:
        if isinstance(stage, Mapping) and rows:
            for line in ("start", "end"):
                _warn_slot(
                    warnings,
                    f"{identifier}.stages.{stage.get('id')}.{line}",
                    rows.get(str(stage.get("id")), {}).get(line),
                )


def _common_warnings(warnings: list[str], document: Mapping[str, Any]) -> None:
    identifier = "sbs.common"
    raw_notes = document.get("notes")
    notes = raw_notes if isinstance(raw_notes, Mapping) else {}
    _warn_slot(warnings, f"{identifier}.notes.summary", notes.get("summary"))
    _warn_sentence(warnings, f"{identifier}.notes.summary", notes.get("summary"), 140)
    _warn_sentence(
        warnings, f"{identifier}.notes.what_this_measures", notes.get("what_this_measures"), 300
    )
    _warn_sentence(warnings, f"{identifier}.notes.how_to_read", notes.get("how_to_read"), 300)
    if "description" in document:
        warnings.append(f"text outside slots {identifier}.description")
    statistics = notes.get("statistics", {})
    for field in ("median", "mean", "p95", "tail_fallback", "typical"):
        value = statistics.get(field) if isinstance(statistics, Mapping) else None
        _warn_slot(warnings, f"{identifier}.notes.statistics.{field}", value)
    for field in ("stage_medians", "time_between_stages"):
        _warn_slot(warnings, f"{identifier}.notes.{field}", notes.get(field))


def _venue_warnings(
    warnings: list[str],
    row: Mapping[str, Any],
    document: Mapping[str, Any],
    rows: list[Any],
    identifier: str,
) -> None:
    source = document.get("source", {})
    terms = source.get("venue_terms", []) if isinstance(source, Mapping) else []
    notes = document.get("notes", {})
    visible_text = [notes.get("summary", "") if isinstance(notes, Mapping) else ""]
    parent = next(
        (
            item
            for item in rows
            if isinstance(item, Mapping) and item.get("id") == row.get("parent")
        ),
        None,
    )
    if isinstance(parent, Mapping):
        visible_text.append(parent.get("intro", ""))
    for term in terms if isinstance(terms, list) else []:
        if not isinstance(term, str) or not term:
            continue
        for value in visible_text:
            if isinstance(value, str) and re.search(
                rf"\b{re.escape(term)}\b\s+(?:on|in|runs?|ran)\b", value, re.IGNORECASE
            ):
                warnings.append(
                    f"wrong venue wording {identifier}: source term {term!r} is used as a venue"
                )


def _name_row_warnings(warnings: list[str], row: Mapping[str, Any]) -> None:
    label = f"names row {row.get('row')} {row.get('id')}"
    if row.get("kind") != "capability":
        _warn_slot(warnings, f"{label}.name", row.get("name"))
        _warn_length(warnings, f"{label}.name", row.get("name"), 40)
    summary = None if "document" in row else "summary"  # a document page's summary is its own
    field = {"section": "intro", "subsection": "intro", "page": summary}.get(str(row.get("kind")))
    if row.get("kind") == "view" and "subtitle" in row:
        field = "subtitle"
    if field:
        value = row.get(field)
        _warn_slot(warnings, f"{label}.{field}", value)
        _warn_sentence(warnings, f"{label}.{field}", value, 160)


def _document_row_warnings(
    warnings: list[str],
    row: Mapping[str, Any],
    document: Mapping[str, Any],
    rows: list[Any],
    *,
    criteria: Mapping[str, Any] | None = None,
) -> None:
    identifier = str(document.get("id"))
    if row.get("kind") == "hero":
        _warn_slot(warnings, f"{identifier}.headline", document.get("headline"))
    if row.get("kind") not in ("index", "test"):
        return
    panel_kind = panel_scope_kind(row, rows)
    if panel_kind:
        slots = _panel_slots(
            panel_kind,
            cost_shown=shows_cost(str(row["id"]), rows)
            and isinstance(document.get("cost"), Mapping),
        )
        if "page" in row:
            # Its page text is the host test's; only its own sentences are its own.
            slots = [slot for slot in slots if slot.removeprefix("notes.") in PAGE_MEMBER_NOTES]
        _base_warnings(warnings, identifier, document, slots, criteria=criteria)
    _variant_warnings(warnings, rows, row, document, identifier)
    _stage_warnings(warnings, document, identifier)
    _venue_warnings(warnings, row, document, rows, identifier)


def content_warnings(
    root: Path, rows: list[Any], *, criteria: Mapping[str, Any] | None = None
) -> list[str]:
    """Audit current source text using the exported content criteria."""
    warnings: list[str] = []
    source_documents: dict[str, Mapping[str, Any]] = {
        f"{doc['id']}.{doc['version']}": doc for doc in _methodologies(root)
    }
    source_criteria = content_criteria() if criteria is None else criteria
    source_documents["content"] = source_criteria
    tables = load_tables(root)
    canonical_names = {
        str(row["id"]): str(row["name"]) for row in tables.names if row.get("kind") == "index"
    }
    canonical_names.update(
        {f"{row['kind']}.{row['id']}": str(row["name"]) for row in tables.names if row.get("name")}
    )
    for reader_document in {
        str(doc["id"]): doc for doc in _document_rows(root, rows).values()
    }.values():
        warnings.extend(
            document_content_errors(
                reader_document,
                documents=source_documents,
                names=canonical_names,
                criteria=source_criteria,
            )
        )
    return warnings


def _row_text(
    bundle: Mapping[str, Any], row: Mapping[str, Any], document: Mapping[str, Any]
) -> dict[str, Any]:
    """A document with its measured text, and the current reader copy over it (what pages draw)."""
    measured = bundle["methodologies"][document["id"]][str(document["version"])]["text"]
    current = bundle["reader"]["methods"].get(str(row["id"]), {}).get("text", {})
    return {**document, **measured, **current}


def _retired_words(bundle: Mapping[str, Any], root: Path) -> list[str]:
    conventions = bundle["content_criteria"]["conventions"]
    return retired_word_errors(bundle, conventions, load_tables(root).aliases.get("document", {}))


def text_warnings(root: Path) -> list[str]:
    """Return report-only warnings for missing slots, legacy prose and inconsistent names."""
    with compiler_tables_cache(root, DIGEST_ALGORITHMS):
        return _text_warnings(root)


def _text_warnings(root: Path) -> list[str]:
    """Collect the warnings within one table-cache scope, so the tables load once."""
    bundle = build_text_bundle(root)
    names_path = root / "names" / "names.yaml"
    if not names_path.is_file():
        return []
    names_doc = load_yaml(names_path.read_text(encoding="utf-8"))
    if not isinstance(names_doc, Mapping) or not isinstance(names_doc.get("names"), list):
        return ["names/names.yaml: cannot inspect text slots"]
    rows = names_doc["names"]
    criteria = bundle["content_criteria"]
    warnings: list[str] = []
    for row in rows:
        if isinstance(row, Mapping):
            _name_row_warnings(warnings, row)
    documents = _document_rows(root, rows)
    for row in rows:
        if isinstance(row, Mapping):
            document = documents.get(f"{row.get('kind')}:{row.get('id')}")
            if document is not None:
                _document_row_warnings(
                    warnings, row, _row_text(bundle, row, document), rows, criteria=criteria
                )
    for document in _latest_documents(
        _methodologies(root), common=catalog.default_context(root)
    ).values():
        if document.get("category") == "common" and document.get("id") == "sbs.common":
            _common_warnings(warnings, document)
    for identity, metric in expand_metric_labels(bundle["metric_labels"]).items():
        if not criteria["slots"]["caption"]["enabled"]:
            continue
        for target, caption in metric["labels"].get("caption", {}).items():
            resolved = re.sub(r"\{[^{}]+\}", "Value", caption)
            warnings.extend(
                f"{identity}.{target}.caption: {error}"
                for error in slot_errors(resolved, criteria["slots"]["caption"], generated=True)
            )
    warnings.extend(criteria_errors(criteria))
    warnings.extend(raw_token_warnings(bundle))
    warnings.extend(index_metric_id_warnings(bundle))
    warnings.extend(_retired_words(bundle, root))
    warnings.extend(rejected_label_errors(bundle["names"]))
    warnings.extend(reader_errors(root))
    warnings.extend(reader_slot_errors(bundle["reader"], criteria))
    warnings.extend(reader_similarity_errors(bundle["reader"], criteria))
    warnings.extend(
        reader_style.reader_style_errors(
            root,
            {name: method["document"] for name, method in bundle["reader"]["methods"].items()},
            _style_documents(root),
            {
                name: method["text"]["notes"]["introduction"]
                for name, method in bundle["reader"]["methods"].items()
                if "introduction" in method["text"].get("notes", {})
            },
            {
                name: method["text"].get("notes", {})
                for name, method in bundle["reader"]["methods"].items()
            },
        )
    )
    warnings.extend(content_warnings(root, rows, criteria=criteria))
    warnings.extend(source_name_errors(root) + status_label_owner_errors(root))
    warnings.extend(concurrency_cap_status_errors(root))
    warnings.extend(published_name_errors(bundle, root))
    return warnings
