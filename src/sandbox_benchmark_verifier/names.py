# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Canonical benchmark names, the one alias table, and the checks that keep them consistent.

``names/names.yaml`` maps every name the benchmark page shows to its canonical id, methodology
document and display name. ``names/aliases.yaml`` maps every old stored or published id to its
canonical id. Other repositories read old records only through :func:`resolve`.
"""

import hashlib
import json
import os
import re
import tempfile
from collections import defaultdict
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from sandbox_benchmark_verifier.catalog import _assets, load_methodology
from sandbox_benchmark_verifier.conventions import load_conventions
from sandbox_benchmark_verifier.lifecycle_profile import name_rows
from sandbox_benchmark_verifier.name_row_checks import (
    capability_name_errors,
    common_section_errors,
    common_subsection_errors,
    numbered_errors,
    page_errors,
    presentation_errors,
    reads_as_errors,
    sentence_errors,
    unit_description_errors,
    unit_label_errors,
    view_subtitle_errors,
    with_page_titles,
)
from sandbox_benchmark_verifier.name_scans import clock_name_errors, retired_word_errors
from sandbox_benchmark_verifier.new_benchmark import PRE_TEMPLATE_IDS, pre_template_ids
from sandbox_benchmark_verifier.placeholders import resolve_template
from sandbox_benchmark_verifier.text_predicates import OPERATORS
from sandbox_benchmark_verifier.unit_forms import unit_form_errors
from sandbox_benchmark_verifier.versions import version_key
from sandbox_benchmark_verifier.yaml_load import read_mapping

FLAT_KINDS = ("document", "test", "task", "tab", "view", "section", "subsection")
SCOPED_KINDS = ("variant", "stage")
# Canonical kinds an alias of each kind may point at (a task folder is a test or a tab).
TARGET_KINDS: dict[str, tuple[str, ...]] = {
    "document": ("test", "tab", "index", "section", "hero"),
    "test": ("test",),
    "task": ("test", "tab"),
    "tab": ("tab", "index"),
    "view": ("view",),
    "section": ("section",),
    "subsection": ("subsection",),
    "variant": ("variant", "job"),
    "stage": ("stage",),
}
# Canonical kinds whose ids an old id of each kind must not reuse.
OWN_KINDS: dict[str, tuple[str, ...]] = {
    "document": ("test", "tab", "index", "section"),
    **{kind: kinds for kind, kinds in TARGET_KINDS.items() if kind != "document"},
}
PARENT_KINDS: dict[str, tuple[str, ...]] = {
    "subsection": ("section",),
    "index": ("section",),
    "view": ("section",),
    "tab": ("section",),
    "test": ("tab", "subsection"),
    "variant": ("test",),
    "job": ("test",),
    "stage": ("test", "tab"),
}
SCOPE_PARENT_KINDS = {"variant": ("test",), "stage": ("test", "tab")}


class UnknownIdError(KeyError):
    """The id is neither canonical nor in the alias table."""


class RetiredIdError(KeyError):
    """The id belonged to a test that was retired; no canonical id replaces it."""


@dataclass(frozen=True)
class Tables:
    """The parsed names and alias tables."""

    names: list[dict[str, Any]]
    aliases: dict[str, Any]
    metric_label_grammar: dict[str, Any]

    def published_names(self) -> list[dict[str, Any]]:
        """The names as consumers read them: each page-hosting test carries its `page_title`."""
        return with_page_titles(self.names)


@dataclass
class _TablesCacheScope:
    root: Path
    key: str
    identity: dict[str, Any]
    directory: Path
    science_rows: list[dict[str, Any]] | None = None
    tables: Tables | None = None


_TABLES_CACHE_SCHEMA = "boxrouter.bench.compiler-tables-cache/1"


class _MissingCacheInput(Exception):
    """An optional compiler-cache identity input is absent from this root."""


_TABLES_CACHE_SCOPE: ContextVar[_TablesCacheScope | None] = ContextVar(
    "benchmark_compiler_tables_cache", default=None
)


@contextmanager
def compiler_tables_cache(
    root: Path,
    digest_algorithms: Mapping[str, str],
    *,
    cache_directory: Path | None = None,
) -> Iterator[None]:
    """Reuse exact methodology-derived rows across presentation-only source edits."""
    resolved = root.resolve()
    active = _TABLES_CACHE_SCOPE.get()
    if active is not None and active.root == resolved:
        yield
        return
    try:
        identity = _tables_cache_identity(resolved, digest_algorithms)
    except _MissingCacheInput:
        identity = None
    if identity is None:
        token = _TABLES_CACHE_SCOPE.set(None)
        try:
            yield
        finally:
            _TABLES_CACHE_SCOPE.reset(token)
        return
    key = hashlib.sha256(_canonical_json(identity)).hexdigest()
    directory = (
        cache_directory
        or Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
        / "boxrouter"
        / "sandbox-benchmark-verifier"
        / "tables"
    )
    scope = _TablesCacheScope(resolved, key, identity, directory.resolve())
    token = _TABLES_CACHE_SCOPE.set(scope)
    try:
        yield
    finally:
        _TABLES_CACHE_SCOPE.reset(token)


def _tables_cache_identity(root: Path, digest_algorithms: Mapping[str, str]) -> dict[str, Any]:
    """Hash exact science bytes and the compiler/schema identity for derived rows."""
    science = _hash_source_files(root, ("methodology", "parameters"))
    schemas = _hash_source_files(
        root,
        (),
        (
            "schemas/methodology.schema.json",
            "schemas/lifecycle-protocol.schema.json",
        ),
    )
    compiler = _hash_source_files(
        root,
        (),
        (
            "src/sandbox_benchmark_verifier/names.py",
            "src/sandbox_benchmark_verifier/lifecycle_profile.py",
            "src/sandbox_benchmark_verifier/catalog.py",
        ),
        fallback=Path(__file__).resolve().parent,
    )
    return {
        "schema": _TABLES_CACHE_SCHEMA,
        "methodology_parameters_sha256": science,
        "schemas_sha256": schemas,
        "compiler_sha256": compiler,
        "digest_algorithms": dict(digest_algorithms),
    }


def _hash_source_files(
    root: Path,
    directories: tuple[str, ...],
    files: tuple[str, ...] = (),
    *,
    fallback: Path | None = None,
) -> str:
    records: list[tuple[str, bytes]] = []
    for directory in directories:
        source_directory = root / directory
        if not source_directory.is_dir():
            raise _MissingCacheInput(
                f"compiler table cache input directory is missing: {source_directory}"
            )
        for path in sorted(source_directory.rglob("*")):
            if path.is_file():
                records.append((path.relative_to(root).as_posix(), path.read_bytes()))
    for relative in files:
        path = root / relative
        if not path.is_file() and fallback is not None:
            # The installed package: the compiler is the running module, beside this file.
            path = fallback / Path(relative).name
        if not path.is_file():
            raise _MissingCacheInput(f"compiler table cache input file is missing: {path}")
        records.append((relative, path.read_bytes()))
    digest = hashlib.sha256()
    for name, content in sorted(records):
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _science_rows_payload(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"rows": rows}


def _cached_science_rows(scope: _TablesCacheScope) -> list[dict[str, Any]] | None:
    path = scope.directory / f"{scope.key}.json"
    if not path.is_file():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"compiler table cache is corrupt: {path}") from error
    if not isinstance(record, dict) or set(record) != {
        "schema",
        "key",
        "identity",
        "payload_sha256",
        "payload",
    }:
        raise ValueError(f"compiler table cache metadata is invalid: {path}")
    if record["schema"] != _TABLES_CACHE_SCHEMA or record["key"] != scope.key:
        raise ValueError(f"compiler table cache metadata is stale: {path}")
    if record["identity"] != scope.identity:
        raise ValueError(f"compiler table cache source identity differs: {path}")
    payload = record["payload"]
    if not isinstance(payload, dict) or set(payload) != {
        "rows",
    }:
        raise ValueError(f"compiler table cache payload is invalid: {path}")
    payload_bytes = _canonical_json(payload)
    if hashlib.sha256(payload_bytes).hexdigest() != record["payload_sha256"]:
        raise ValueError(f"compiler table cache payload digest differs: {path}")
    if not isinstance(payload["rows"], list) or any(
        not isinstance(row, dict) for row in payload["rows"]
    ):
        raise ValueError(f"compiler table cache payload types are invalid: {path}")
    return payload["rows"]


def _write_cached_science_rows(scope: _TablesCacheScope, rows: list[dict[str, Any]]) -> None:
    payload = _science_rows_payload(rows)
    payload_bytes = _canonical_json(payload)
    record = {
        "schema": _TABLES_CACHE_SCHEMA,
        "key": scope.key,
        "identity": scope.identity,
        "payload_sha256": hashlib.sha256(payload_bytes).hexdigest(),
        "payload": payload,
    }
    cache_bytes = _canonical_json(record) + b"\n"
    scope.directory.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{scope.key}.", dir=scope.directory)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(cache_bytes)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, scope.directory / f"{scope.key}.json")
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _all_rows(document: Mapping[str, Any], root: Path) -> list[dict[str, Any]]:
    rows = [dict(row) for row in document["names"]]
    first_science_row = max(row["row"] for row in rows) + 1
    scope = _TABLES_CACHE_SCOPE.get()
    if scope is None or scope.root != root.resolve():
        derived = name_rows(root, first_science_row)
    else:
        if scope.science_rows is None:
            scope.science_rows = _cached_science_rows(scope)
            if scope.science_rows is None:
                scope.science_rows = name_rows(root, 0)
                _write_cached_science_rows(scope, scope.science_rows)
        derived = deepcopy(scope.science_rows)
        for row in derived:
            row["row"] += first_science_row
    return [*rows, *derived]


def load_tables(root: Path | None = None) -> Tables:
    """Read the tables from a checkout root, or from the packaged copy when ``root`` is None."""
    source_root = (root if root is not None else _assets()).resolve()
    scope = _TABLES_CACHE_SCOPE.get()
    if scope is not None and scope.root == source_root:
        if scope.tables is None:
            scope.tables = _load_tables(source_root)
        return deepcopy(scope.tables)
    return _load_tables(source_root)


def tables_view(root: Path | None = None) -> Tables:
    """The tables for reading only; callers must not change them.

    Inside a matching compiler cache scope this is the scope's own copy, without the deep copy
    ``load_tables`` makes; outside one it is a fresh parse.
    """
    source_root = (root if root is not None else _assets()).resolve()
    scope = _TABLES_CACHE_SCOPE.get()
    if scope is not None and scope.root == source_root:
        if scope.tables is None:
            scope.tables = _load_tables(source_root)
        return scope.tables
    return _load_tables(source_root)


def _load_tables(root: Path) -> Tables:
    base = root / "names"
    names_doc = read_mapping(base / "names.yaml")
    rows = _all_rows(names_doc, base.parent)
    canonical_names = {
        str(row["id"]): str(row["name"]) for row in rows if row.get("kind") == "index"
    }
    for row in rows:
        name = row.get("name")
        if isinstance(name, str) and "{name:" in name:
            row["name"] = resolve_template(name, names=canonical_names, documents={})
    return Tables(
        names=rows,
        aliases=read_mapping(base / "aliases.yaml"),
        metric_label_grammar=dict(names_doc.get("metric_label_grammar", {})),
    )


def shows_cost(test_id: str, rows: Sequence[Mapping[str, Any]] | None = None) -> bool:
    """Whether a test's cost may reach a reader based on its section's cost scope.

    A test under a section whose names row says ``shows_cost: false`` (Sandbox Operations and
    Machine Performance) shows no cost anywhere; every other test may. This is the one owner of
    "this test has cost": the reader notes, the page contract, the published ``has_cost`` and the
    web panels read it.
    """
    rows = rows if rows is not None else load_tables().names
    row = next((r for r in rows if r["kind"] in ("test", "index") and r["id"] == test_id), None)
    while row is not None:
        if row["kind"] == "section":
            return bool(row.get("shows_cost", True))
        parent = row.get("parent")
        row = next(
            (
                r
                for r in rows
                if r["id"] == parent and r["kind"] in PARENT_KINDS.get(row["kind"], ("section",))
            ),
            None,
        )
    return True


def names() -> list[dict[str, Any]]:
    """Return fresh canonical name rows."""
    return load_tables().names


def aliases() -> dict[str, Any]:
    """Return the fresh alias table."""
    return load_tables().aliases


def resolve(kind: str, old_id: str, scope: str | None = None, tables: Tables | None = None) -> str:
    """Return the canonical id for a stored id of ``kind`` (an id that is already canonical).

    ``scope`` is the canonical test or tab for the ``variant`` and ``stage`` kinds. Raises
    :class:`RetiredIdError` for retired tests and :class:`UnknownIdError` for any other id.
    """
    loaded = tables or load_tables()
    if kind not in TARGET_KINDS:
        raise ValueError(f"unknown alias kind: {kind}")
    table = loaded.aliases.get(kind, {})
    if kind in SCOPED_KINDS:
        table = table.get(scope, {})
    if old_id in table:
        return str(table[old_id])
    if old_id in loaded.aliases.get("retired", []) and kind in ("test", "task", "tab", "document"):
        raise RetiredIdError(old_id)
    for row in loaded.names:
        if row["kind"] in TARGET_KINDS[kind] and row["id"] == old_id:
            if kind not in SCOPED_KINDS or row.get("parent") in (scope, None):
                return old_id
    raise UnknownIdError(f"{kind} {old_id!r}" + (f" in {scope!r}" if scope else ""))


def resolve_test_variant(old_id: str, tables: Tables | None = None) -> tuple[str, str | None]:
    """Return ``(test id, variant id or None)`` for a page test id such as ``start-time-node``."""
    loaded = tables or load_tables()
    pair = loaded.aliases.get("test_variant", {}).get(old_id)
    if pair is not None:
        return str(pair[0]), str(pair[1])
    return resolve("test", old_id, tables=loaded), None


def _schema_errors(name: str, document: Mapping[str, Any], root: Path) -> list[str]:
    schema = json.loads((root / "schemas" / f"{name}.schema.json").read_text(encoding="utf-8"))
    return [
        f"names/{name}.yaml: schema validation failed at {list(issue.schema_path)}"
        for issue in Draft202012Validator(schema).iter_errors(document)
    ]


def _methodology_documents(root: Path) -> list[dict[str, Any]]:
    """Read every methodology document, including multiple versions in one YAML file."""
    return [
        item.document
        for item in load_methodology(root, include_retired=True)
        if isinstance(item.document, dict) and isinstance(item.document.get("id"), str)
    ]


def _command_name_errors(rows: list[dict[str, Any]], documents: list[dict[str, Any]]) -> list[str]:
    """A variant that is a shell command is named by its command word, which its document owns."""
    errors: list[str] = []
    for row in rows:
        if row.get("kind") != "variant" or "workload" not in row:
            continue
        test = next(
            (item for item in rows if item["kind"] == "test" and item["id"] == row.get("parent")),
            None,
        )
        versions = [item for item in documents if item["id"] == (test or {}).get("document")]
        if not versions:
            continue
        latest = max(versions, key=lambda item: version_key(item["version"]))
        variants = latest.get("procedure", {}).get("variants", [])
        variant: dict[str, Any] = next((v for v in variants if v.get("id") == row["workload"]), {})
        command = variant.get("command")
        if (
            isinstance(command, str)
            and str(variant.get("label", "")).endswith(" command")
            and command.split()[:1] != [row["name"]]
        ):
            errors.append(
                f"names row {row['row']} {row['id']}: a command variant is named by its command "
                "word"
            )
    return errors


def _document_ids(documents: list[dict[str, Any]]) -> tuple[set[str], set[str]]:
    """Return methodology document ids and common document ids."""
    found = {document["id"] for document in documents}
    common = {document["id"] for document in documents if document.get("category") == "common"}
    return found, common


def _all_variant_errors(rows: list[dict[str, Any]], template: str) -> list[str]:
    """Every counted workload has one all-units variant, labelled by the one template."""
    plurals = {r["id"]: r["plural"] for r in rows if r["kind"] == "per-unit" and r.get("plural")}
    errors: list[str] = []
    for test in (r for r in rows if r["kind"] == "test" and r.get("unit") in plurals):
        found = [
            r
            for r in rows
            if r.get("parent") == test["id"]
            and r["kind"] in ("variant", "job")
            and r["id"] == "all"
        ]
        expected = template.replace("{Units}", plurals[test["unit"]].title())
        if [r["name"] for r in found] != [expected]:
            errors.append(
                f"names test {test['id']}: its all variant must be one row named {expected!r}"
            )
    return errors


def _row_errors(rows: list[dict[str, Any]], documents: set[str]) -> list[str]:
    errors: list[str] = []
    seen: dict[tuple[str, str, str], int] = {}
    seen_names: dict[tuple[str, str, str], int] = {}
    ids_by_kind: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        ids_by_kind[row["kind"]].add(row["id"])
    with_plural = {row["id"] for row in rows if row["kind"] == "per-unit" and row.get("plural")}
    for row in rows:
        scope = row.get("parent", "")
        where = f"names row {row['row']} {row['kind']} {row['id']}"
        key = (row["kind"], scope, row["id"])
        name_key = (row["kind"], scope, row["name"])
        if key in seen:
            errors.append(f"{where}: duplicate canonical id")
        if name_key in seen_names:
            errors.append(f"{where}: duplicate canonical name {row['name']!r}")
        seen[key] = seen_names[name_key] = row["row"]
        if "unit" in row and row["unit"] not in ids_by_kind["per-unit"]:
            errors.append(f"{where}: unit {row['unit']!r} is not a per-unit name")
        if "unit" in row and row["unit"] not in with_plural:
            errors.append(f"{where}: per-unit name {row['unit']!r} is counted but has no plural")
        errors.extend(sentence_errors(row, where))
        errors.extend(capability_name_errors(row, where))
        errors.extend(numbered_errors(row, where))
        errors.extend(view_subtitle_errors(row, where))
        errors.extend(unit_label_errors(row, rows, where))
        errors.extend(presentation_errors(row, rows, where))
        errors.extend(unit_description_errors(row, where))
        errors.extend(page_errors(row, rows, where))
        errors.extend(common_section_errors(row, where))
        errors.extend(common_subsection_errors(row, rows, where))
        errors.extend(reads_as_errors(row, rows, where))
        if "shows_cost" in row and row["kind"] != "section":
            errors.append(f"{where}: shows_cost belongs on a section row")
        allowed = PARENT_KINDS.get(row["kind"], ())
        parent = row.get("parent")
        if parent is None:
            if allowed and row["kind"] != "stage":
                errors.append(f"{where}: needs a parent")
        elif not any(parent in ids_by_kind[kind] for kind in allowed):
            errors.append(f"{where}: parent {parent!r} is not a canonical id")
        errors.extend(_document_errors(row, documents, where))
    return errors


def _document_errors(row: dict[str, Any], documents: set[str], where: str) -> list[str]:
    """A row's document must exist; a test, index or reliability row needs one or a gap."""
    errors: list[str] = []
    if "document" in row and row["document"] not in documents:
        errors.append(f"{where}: document {row['document']} does not exist")
    needs_document = row["kind"] in ("test", "index") or row["id"] == "reliability"
    if needs_document and "document" not in row and "gap" not in row:
        errors.append(f"{where}: needs a document or a recorded gap")
    if "document" in row and "gap" in row:
        errors.append(f"{where}: has a document, so it is not a gap")
    return errors


def _flat_errors(aliases_: Mapping[str, Any], canonical: Mapping[str, set[str]]) -> list[str]:
    errors: list[str] = []
    for kind in FLAT_KINDS:
        targets = {i for k in TARGET_KINDS[kind] for i in canonical[k]}
        own = {i for k in OWN_KINDS[kind] for i in canonical[k]}
        for old, target in aliases_.get(kind, {}).items():
            if target not in targets:
                errors.append(f"alias {kind} {old} -> {target}: target is not a canonical id")
            if kind != "document" and old in own:
                errors.append(f"alias {kind} {old}: old id is also a canonical {kind} id")
    return errors


def _scoped_errors(
    aliases_: Mapping[str, Any],
    canonical: Mapping[str, set[str]],
    scoped: Mapping[tuple[str, str], set[str]],
) -> list[str]:
    errors: list[str] = []
    for kind in SCOPED_KINDS:
        parents = {i for k in SCOPE_PARENT_KINDS[kind] for i in canonical[k]}
        shared = ("",) if kind == "stage" else ()
        for scope, table in aliases_.get(kind, {}).items():
            if scope not in parents:
                errors.append(f"alias {kind} scope {scope}: not a canonical test or tab")
            valid = {
                i for k in TARGET_KINDS[kind] for s in (scope, *shared) for i in scoped[(k, s)]
            }
            for old, target in table.items():
                if target not in valid:
                    errors.append(f"alias {kind} {scope}/{old} -> {target}: no such canonical id")
                if old in valid:
                    errors.append(f"alias {kind} {scope}/{old}: old id is also canonical there")
    for old, (test, variant) in aliases_.get("test_variant", {}).items():
        if test not in canonical["test"] or variant not in scoped[("variant", test)]:
            errors.append(f"alias test_variant {old} -> {test}/{variant}: no such canonical id")
        if old in canonical["test"]:
            errors.append(f"alias test_variant {old}: old id is also a canonical test id")
    return errors


def _alias_target_errors(tables: Tables) -> list[str]:
    canonical: dict[str, set[str]] = defaultdict(set)
    scoped: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in tables.names:
        canonical[row["kind"]].add(row["id"])
        scoped[(row["kind"], row.get("parent", ""))].add(row["id"])
    errors = _flat_errors(tables.aliases, canonical)
    errors.extend(_scoped_errors(tables.aliases, canonical, scoped))
    old_tests = set(tables.aliases.get("test", {}))
    errors.extend(
        f"retired {old}: also a canonical or aliased test id"
        for old in tables.aliases.get("retired", [])
        if old in canonical["test"] or old in old_tests
    )
    errors.extend(
        f"retired_names {old}: not a retired id"
        for old in tables.aliases.get("retired_names", {})
        if old not in tables.aliases.get("retired", [])
    )
    return errors


def _duplicate_copy_errors(root: Path) -> list[str]:
    """No second copy of either table may exist anywhere in the checkout."""
    skip = {".git", ".venv", "node_modules", "__pycache__"}
    errors: list[tuple[Path, str]] = []
    for directory, directories, filenames in os.walk(root, topdown=True, followlinks=False):
        directories[:] = [name for name in directories if name not in skip]
        parent = Path(directory)
        for filename in filenames:
            if filename in skip:
                continue
            path = parent / filename
            stem = path.name.split(".")[0]
            schema = path.name.endswith(".schema.json")
            if (
                stem in ("aliases", "names")
                and path.suffix in (".yaml", ".yml", ".json")
                and not schema
                and path.is_file()
            ):
                relative = path.relative_to(root)
                if relative.parts[:1] != ("names",) or len(relative.parts) != 2:
                    errors.append((relative, f"{relative}: second copy of the name or alias table"))
    return [message for _, message in sorted(errors)]


def _reference_rows(
    rows: list[dict[str, Any]], kinds: tuple[str, ...], value: str, parent: str | None = None
) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if row["kind"] in kinds
        and (row["id"] == value or (row["kind"] == "per-unit" and row["name"] == value))
        and (parent is None or row.get("parent") == parent)
    ]


def effective_statistic_id(
    document: Mapping[str, Any],
    metric: Mapping[str, Any],
    methods: Mapping[tuple[str, str], Mapping[str, Any]],
) -> Any:
    """Return the metric statistic, falling back through document inheritance."""
    if metric.get("statistic") is not None:
        return metric["statistic"]
    return next(
        (
            source["primary"]
            for source in _statistic_sources(document, methods)
            if source.get("primary")
        ),
        None,
    )


def _statistic_sources(
    document: Mapping[str, Any], methods: Mapping[tuple[str, str], Mapping[str, Any]]
) -> list[Mapping[str, Any]]:
    sources: list[Mapping[str, Any]] = []
    local = document.get("statistics")
    if isinstance(local, Mapping):
        sources.append(local)
    inherited = document.get("inherits")
    if isinstance(inherited, Mapping):
        parent = methods.get((str(inherited.get("id", "")), str(inherited.get("version", ""))))
        parent_statistics = parent.get("statistics") if parent else None
        if isinstance(parent_statistics, Mapping):
            sources.append(parent_statistics)
    return sources


def _quantity_rule_errors(rows: list[dict[str, Any]], grammar: Mapping[str, Any]) -> list[str]:
    errors = []
    for quantity, rule in grammar.get("quantities", {}).items():
        reference = rule["name_ref"]
        matches = [
            row for row in rows if row["kind"] == reference["kind"] and row["id"] == reference["id"]
        ]
        if len(matches) != 1:
            errors.append(f"quantity {quantity}: name reference does not resolve to one names row")
    return errors


def _metric_binding_errors(
    rows: list[dict[str, Any]],
    methods: Mapping[tuple[str, str], Mapping[str, Any]],
    root: Path,
) -> tuple[list[str], set[tuple[str, str, str]]]:
    errors = []
    seen: set[tuple[str, str, str]] = set()
    bound: set[tuple[str, str, str]] = set()
    method_schema = json.loads((root / "schemas" / "methodology.schema.json").read_text())
    metadata_validators = (
        Draft202012Validator(method_schema["$defs"]["semver"]),
        Draft202012Validator(method_schema["$defs"]["snake_name"]),
    )
    for row in rows:
        reference, label = row.get("metric"), row.get("label")
        if (reference is None) != (label is None):
            errors.append(
                f"names row {row['row']} {row['id']}: metric and label must appear together"
            )
        if reference is None:
            continue
        key = (reference["document"], reference["version"], reference["name"])
        where = f"names row {row['row']} {row['id']} metric {key[0]}@{key[1]}/{key[2]}"
        if key in seen:
            errors.append(f"{where}: metric has more than one name binding")
        seen.add(key)
        bound.add(key)
        if not metadata_validators[0].is_valid(reference["version"]):
            errors.append(f"{where}: version is not semantic version syntax")
        if not metadata_validators[1].is_valid(reference["name"]):
            errors.append(f"{where}: name is not a methodology metric name")
        method = methods.get((key[0], key[1]))
        if method is None:
            errors.append(f"{where}: methodology document and version do not exist")
            continue
        matches = [metric for metric in method.get("metrics", []) if metric.get("name") == key[2]]
        if len(matches) != 1:
            errors.append(f"{where}: methodology metric does not resolve uniquely")
    return errors, bound


def _phase_reference_errors(
    rows: list[dict[str, Any]], metric: Mapping[str, Any], where: str
) -> list[str]:
    phase = metric.get("phase")
    if not isinstance(phase, Mapping):
        return []
    matches = _reference_rows(rows, (phase["kind"],), phase["id"], phase.get("parent"))
    if len(matches) != 1:
        return [f"{where}: phase reference does not resolve to one names row"]
    return []


def _statistic_reference_errors(
    rows: list[dict[str, Any]],
    document: Mapping[str, Any],
    metric: Mapping[str, Any],
    methods: Mapping[tuple[str, str], Mapping[str, Any]],
    where: str,
) -> list[str]:
    if "statistic" not in metric:
        return []
    statistic = metric["statistic"]
    errors = []
    if len(_reference_rows(rows, ("statistic",), statistic)) != 1:
        errors.append(f"{where}: statistic reference does not resolve to one names row")
    supported = set()
    for source in _statistic_sources(document, methods):
        primary = source.get("primary")
        if isinstance(primary, str):
            supported.add(primary)
        secondary = source.get("secondary", [])
        if isinstance(secondary, list):
            supported.update(value for value in secondary if isinstance(value, str))
    if statistic == "typical":
        supported.add(statistic)
    if statistic not in supported:
        errors.append(f"{where}: statistic is not declared by the methodology")
    return errors


def _bound_metric_errors(
    rows: list[dict[str, Any]],
    document: Mapping[str, Any],
    metric: Mapping[str, Any],
    methods: Mapping[tuple[str, str], Mapping[str, Any]],
    where: str,
) -> list[str]:
    errors = []
    quantity = metric.get("quantity")
    if (
        not isinstance(quantity, str)
        or len(_reference_rows(rows, ("quantity", "view"), quantity)) != 1
    ):
        errors.append(f"{where}: quantity does not resolve to one names row")
    per = metric.get("per")
    if per is not None and len(_reference_rows(rows, ("per-unit",), per)) != 1:
        errors.append(f"{where}: per value does not resolve to one names row")
    statistic = effective_statistic_id(document, metric, methods)
    if statistic is not None and len(_reference_rows(rows, ("statistic",), str(statistic))) != 1:
        errors.append(f"{where}: effective statistic does not resolve to one names row")
    return errors


def _metric_metadata_errors(
    rows: list[dict[str, Any]],
    document: Mapping[str, Any],
    metric: Mapping[str, Any],
    methods: Mapping[tuple[str, str], Mapping[str, Any]],
    bound: set[tuple[str, str, str]],
) -> list[str]:
    key = (str(document["id"]), str(document["version"]), str(metric.get("name", "")))
    where = f"{key[0]}@{key[1]}/{key[2]}"
    errors = _phase_reference_errors(rows, metric, where)
    errors.extend(_statistic_reference_errors(rows, document, metric, methods, where))
    if key in bound:
        errors.extend(_bound_metric_errors(rows, document, metric, methods, where))
    return errors


def _metric_label_errors(
    rows: list[dict[str, Any]],
    documents: list[dict[str, Any]],
    grammar: Mapping[str, Any],
    root: Path,
) -> list[str]:
    """Resolve source-bound labels and optional metric vocabulary references."""
    methods = {(str(document["id"]), str(document["version"])): document for document in documents}
    errors = _quantity_rule_errors(rows, grammar)
    binding_errors, bound = _metric_binding_errors(rows, methods, root)
    errors.extend(binding_errors)
    for document in documents:
        for metric in document.get("metrics", []):
            errors.extend(_metric_metadata_errors(rows, document, metric, methods, bound))
    return errors


_PLACEHOLDER = re.compile(r"\{[^{}]*\}|\[[^\]]*\]")


def _not_title_case(label: str, naming: Mapping[str, Any]) -> list[str]:
    """The words of a label that break Title Case; a placeholder, a bracketed slot and the
    conventions' own-casing names and words (``fio``) are not checked."""
    if label in naming.get("own_casing", []):
        return []
    text = _PLACEHOLDER.sub("X", label)
    for word in naming.get("own_casing_words", []):
        text = re.sub(rf"\b{re.escape(word)}\b", "X", text)
    bad: list[str] = OPERATORS["casing"](
        text, {"value": "title", "small_words": naming["small_words"]}
    )[1]
    return bad


def _title_case_errors(rows: list[dict[str, Any]], root: Path) -> list[str]:
    """A name or selector label of a title-cased kind (conventions ``naming``) has only small
    lower-case words."""
    if not (root / "conventions" / "text-conventions.yaml").is_file():
        return []
    naming = load_conventions(root)["naming"]
    errors = []
    for row in rows:
        labels = {
            "name": str(row.get("name", "")) if row["kind"] in naming["title_case_kinds"] else ""
        }
        selector = row.get("selector")
        if isinstance(selector, Mapping) and isinstance(selector.get("label"), str):
            labels["selector label"] = selector["label"]
        for what, label in labels.items():
            if label and (bad := _not_title_case(label, naming)):
                errors.append(
                    f"names row {row['row']} {row['kind']} {row['id']}: {what} {label!r} is not "
                    f"Title Case ({', '.join(bad)})"
                )
    return errors


def _leaves(value: Any, path: str = "") -> Iterator[tuple[str, str]]:
    """Every string of a loaded document with its dotted path."""
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield from _leaves(item, f"{path}.{key}" if path else str(key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _leaves(item, f"{path}.{index}" if path else str(index))


def label_case_errors(root: Path) -> list[str]:
    """Every short label of the catalogs in the conventions' ``naming.label_catalogs`` is Title
    Case. A catalog lists its file and the dotted path patterns of its labels; a string that ends
    with a period is a sentence and keeps sentence case, and a path in ``exceptions`` is a fragment
    that a sentence reads (it is not a label). The ranking status labels are in the table too, so a
    sentence-case label fails here, not in a review."""
    if not (root / "conventions" / "text-conventions.yaml").is_file():
        return []
    naming = load_conventions(root)["naming"]
    errors: list[str] = []
    for catalog in naming.get("label_catalogs", []):
        path = root / catalog["file"]
        if not path.is_file():
            continue
        document = json.loads(path.read_text(encoding="utf-8"))
        for where, label in _leaves(document):
            if (
                not any(fnmatchcase(where, pattern) for pattern in catalog["paths"])
                or any(fnmatchcase(where, pattern) for pattern in catalog.get("exceptions", []))
                or label.rstrip().endswith(".")
            ):
                continue
            if bad := _not_title_case(label.strip(), naming):
                errors.append(
                    f"{catalog['file']} {where}: {label!r} is not Title Case ({', '.join(bad)})"
                )
    return errors


def _unnamed_document_errors(root: Path, tables: Tables, documents: set[str]) -> list[str]:
    """Every document id has a names row, an alias row or a retired entry.

    An id absent from the pre-template list is a benchmark before graduation; graduation adds
    its names row (runner graduation lint).
    """
    named = {row["id"] for row in tables.names} | set(tables.aliases.get("retired", []))
    mapped = set(tables.aliases.get("document", {}))
    listed = root / PRE_TEMPLATE_IDS
    before_graduation = (
        documents - set(pre_template_ids(root)["ids"]) if listed.is_file() else set()
    )
    return [
        f"alias document {old}: released document has no alias row"
        for old in sorted(documents - named - mapped - before_graduation)
    ]


def name_errors(root: Path) -> list[str]:
    """Check the names and alias tables against the schemas and the methodology documents."""
    base = root / "names"
    if not base.is_dir():
        return []
    try:
        names_doc = read_mapping(base / "names.yaml")
        alias_doc = read_mapping(base / "aliases.yaml")
    except (OSError, yaml.YAMLError, ValueError) as exc:
        return [f"names: cannot read tables: {exc}"]
    errors = _schema_errors("names", names_doc, root) + _schema_errors("aliases", alias_doc, root)
    if errors:
        return errors
    tables = Tables(
        names=_all_rows(names_doc, root),
        aliases=alias_doc,
        metric_label_grammar=dict(names_doc.get("metric_label_grammar", {})),
    )
    methodology_documents = _methodology_documents(root)
    documents, common = _document_ids(methodology_documents)
    errors.extend(_row_errors(tables.names, documents))
    errors.extend(_title_case_errors(tables.names, root))
    errors.extend(label_case_errors(root))
    errors.extend(retired_word_errors(root, methodology_documents))
    errors.extend(clock_name_errors(tables.names, root))
    errors.extend(_all_variant_errors(tables.names, str(names_doc.get("all_variant_template", ""))))
    errors.extend(_alias_target_errors(tables))
    mapped = tables.aliases.get("document", {})
    errors.extend(_unnamed_document_errors(root, tables, documents - common))
    for old in sorted(set(mapped) - documents):
        errors.append(f"alias document {old}: no such methodology document")
    for row in tables.names:
        document = row.get("document")
        if document not in (None, row["id"]) and mapped.get(document) not in (
            row["id"],
            row.get("parent"),
        ):
            errors.append(f"names row {row['row']} {row['id']}: alias for {document} disagrees")
    errors.extend(
        _metric_label_errors(
            tables.names,
            methodology_documents,
            tables.metric_label_grammar,
            root,
        )
    )
    errors.extend(_duplicate_copy_errors(root))
    errors.extend(unit_form_errors(root, tables.names))
    errors.extend(_command_name_errors(tables.names, methodology_documents))
    return errors
