# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Registry-owned name references and retired display-name enforcement."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

from sandbox_benchmark_verifier.catalog import _assets
from sandbox_benchmark_verifier.glance_clock import glance_clock_errors
from sandbox_benchmark_verifier.lifecycle_profile import profiles
from sandbox_benchmark_verifier.lock import read_lock
from sandbox_benchmark_verifier.name_row_checks import off_documents
from sandbox_benchmark_verifier.names import Tables, _methodology_documents, load_tables
from sandbox_benchmark_verifier.placeholders import parse_placeholders, resolve_template
from sandbox_benchmark_verifier.yaml_load import load_yaml_all, read_mapping

IDENTITY_KINDS = frozenset({"section", "subsection", "tab", "test", "index", "hero", "stage"})


def canonical_name_map(tables: Tables) -> dict[str, str]:
    """Expose qualified identities, document aliases and existing index shorthand."""
    result = {f"{row['kind']}.{row['id']}": str(row["name"]) for row in tables.names}
    result.update(
        {str(row["id"]): str(row["name"]) for row in tables.names if row["kind"] == "index"}
    )
    for old_id, identifier in tables.aliases.get("document", {}).items():
        rows = [
            row for row in tables.names if row["id"] == identifier and row["kind"] in IDENTITY_KINDS
        ]
        if rows:
            result[f"document.{old_id}"] = str(rows[0]["name"])
    unit_names = {
        str(row["id"]): str(row["name"]) for row in tables.names if row["kind"] == "per-unit"
    }
    for row in tables.names:
        if row["kind"] == "test" and "unit" in row and "document" in row:
            # A workload is named by its first document id, its test id or any alias of it.
            ids = {row["document"], row["id"]} | {
                old
                for old, target in tables.aliases.get("document", {}).items()
                if target == row["id"]
            }
            for identifier in ids:
                result[f"unit.{identifier}"] = unit_names[str(row["unit"])]
                result.setdefault(f"document.{identifier}", str(row["name"]))
    # A test that is off (names status off) is also named under off.<test id> and
    # off.<document>, so a list of tests ({tests:...}) leaves it out.
    off = off_documents(tables.names)
    for row in tables.names:
        if row["kind"] == "test" and row.get("document", row["id"]) in off:
            for identifier in (row["id"], row.get("document", row["id"])):
                result[f"off.{identifier}"] = str(row["name"])
    return result


def retired_name_references(root: Path | None = None) -> dict[str, str]:
    """Read previous display names; aliases bind their current canonical identity."""
    base = root if root is not None else _assets()
    tables = load_tables(base)
    current = set(canonical_name_map(tables).values())
    return {
        old: f"{row['kind']}.{row['id']}"
        for row in read_mapping(base / "names/names.yaml").get("display_name_history", [])
        for old in row["previous_names"]
        if old not in current and row["kind"] in IDENTITY_KINDS
    }


def _pattern(names: Mapping[str, str]) -> re.Pattern[str]:
    return _compiled_pattern(tuple(sorted(names, key=len, reverse=True)))


@lru_cache(maxsize=16)
def _compiled_pattern(names: tuple[str, ...]) -> re.Pattern[str]:
    alternatives = "|".join(re.escape(name) for name in names)
    return re.compile(rf"(?<!\w)(?:{alternatives or '(?!)'})(?!\w)")


def name_reference_patterns(
    root: Path | None = None,
) -> tuple[re.Pattern[str], re.Pattern[str], frozenset[str]]:
    """Compile a registry snapshot once for a source or output sweep."""
    tables = load_tables(root)
    identities = {
        str(row["name"]): f"{row['kind']}.{row['id']}"
        for row in tables.names
        if row["kind"] in IDENTITY_KINDS
    }
    retired = retired_name_references(root)
    return (
        _pattern(
            {
                **retired,
                **{value: "" for value in canonical_name_map(tables).values()},
            }
        ),
        _pattern(identities),
        frozenset(retired),
    )


def is_identity_mention(name: str, text: str) -> bool:
    """Distinguish a display identity from ordinary words such as create or CPU."""
    return (
        text.strip() == name
        or len(name.split()) > 1
        or any(c.isdigit() for c in name)
        or (not name.isupper() and any(c.isupper() for c in name[1:]))
    )


def name_reference_errors(
    text: str,
    *,
    root: Path | None = None,
    resolved: bool = False,
    patterns: tuple[re.Pattern[str], re.Pattern[str], frozenset[str]] | None = None,
) -> list[str]:
    """Reject retired names everywhere and typed canonical identities in templates."""
    retired_pattern, identity_pattern, retired = patterns or name_reference_patterns(root)
    errors = [
        f"retired display name {match.group()!r}"
        for match in retired_pattern.finditer(text)
        if match.group() in retired
    ]
    if resolved:
        return errors
    plain = text
    for token in reversed(parse_placeholders(text, allow_literal_braces=True)):
        plain = plain[: token.start] + " " + plain[token.end :]
    # Technical identifiers and URLs are not display-name mentions.
    plain = re.sub(
        r"`[^`]*`|https?://\S+|\b[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_.]*", " ", plain
    )
    errors.extend(
        f"canonical name {match.group()!r} requires a name placeholder"
        for match in identity_pattern.finditer(plain)
        if is_identity_mention(match.group(), plain)
    )
    return list(dict.fromkeys(errors))


def reader_strings(
    value: Any, path: str = "", *, skip: frozenset[str] = frozenset()
) -> Iterator[tuple[str, str]]:
    """Every string in ``value`` with its path; values under a key in ``skip`` are not walked."""
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, Mapping):
        for key, child in value.items():
            if key not in skip:
                yield from reader_strings(child, f"{path}.{key}".lstrip("."), skip=skip)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from reader_strings(child, f"{path}[{index}]", skip=skip)


def projection_references(root: Path) -> dict[str, str]:
    """Map historical and canonical display text to its one registry reference."""
    references = retired_name_references(root)
    references.update(
        {
            str(row["name"]): f"{row['kind']}.{row['id']}"
            for row in load_tables(root).names
            if row["kind"] in IDENTITY_KINDS
        }
    )
    return references


def resolve_name_references(  # pylint: disable=too-many-arguments
    value: Any,
    *,
    documents: Mapping[str, Mapping[str, Any]],
    names: Mapping[str, str],
    names_only: bool = False,
    references: Mapping[str, str] | None = None,
    directions: Mapping[str, str] | None = None,
) -> Any:
    """Resolve a reader projection while leaving immutable measured definitions intact."""
    references = {} if references is None else references
    pattern = _pattern(references)

    def walk(item: Any) -> Any:
        if isinstance(item, str):
            # Only literal spans are migrated; references already authored stay intact.
            parts = []
            cursor = 0
            for token in parse_placeholders(item, allow_literal_braces=True):
                parts.append(
                    pattern.sub(
                        lambda match: "{name:" + references[match.group()] + "}",
                        item[cursor : token.start],
                    )
                )
                parts.append(item[token.start : token.end])
                cursor = token.end
            parts.append(
                pattern.sub(lambda match: "{name:" + references[match.group()] + "}", item[cursor:])
            )
            template = "".join(parts)
            if not names_only:
                return resolve_template(
                    template, names=names, documents=documents, directions=directions
                )
            for token in reversed(parse_placeholders(template, allow_literal_braces=True)):
                if token.kind in {"name", "workloads", "units", "quantity"}:
                    resolved = resolve_template(
                        template[token.start : token.end], names=names, documents=documents
                    )
                    template = template[: token.start] + resolved + template[token.end :]
            return template
        if isinstance(item, Mapping):
            return {key: walk(child) for key, child in item.items()}
        if isinstance(item, list):
            return [walk(child) for child in item]
        return item

    return walk(value)


def reference_composite_scopes(document: Mapping[str, Any]) -> dict[str, Any]:
    """Name each scope and each leaf rule's tests by their registry rows, not by a guessed id.

    A scope is named by its scope row alone: its members are the list drawn under the label. A
    summed leaf names its tests (``test``) and a first-test-only leaf its tabs (``tab``), so two
    tests on one page are two names ("Read 4 KiB, Write 4 KiB"), not the page's twice.
    """
    result = dict(document)
    composition = document.get("composition")
    if not isinstance(composition, Mapping):
        return result
    if composition.get("scopes"):
        scopes = [
            {**scope, "label": "{name:scope." + scope["id"] + "}"}
            for scope in composition["scopes"]
        ]
        result["composition"] = composition = {**composition, "scopes": scopes}
    leaves = composition.get("leaves")
    if isinstance(leaves, Mapping):
        named = dict(leaves)
        if "first_test_only" in leaves:
            named["first_test_only"] = [
                "{name:tab." + tab + "}" for tab in leaves["first_test_only"]
            ]
        if "summed" in leaves:
            named["summed"] = [
                {**rule, "tests": ["{name:test." + test + "}" for test in rule["tests"]]}
                for rule in leaves["summed"]
            ]
        result["composition"] = {**composition, "leaves": named}
    return result


def source_name_errors(root: Path) -> list[str]:
    """Check editable templates; locked scientific records are checked by their projection."""
    patterns = name_reference_patterns(root)
    errors: list[str] = []
    for path in sorted((root / "contracts").rglob("*")):
        if path.suffix not in {".json", ".yaml", ".yml"} or not path.is_file():
            continue
        raw = path.read_text(encoding="utf-8")
        contract = json.loads(raw) if path.suffix == ".json" else list(load_yaml_all(raw))
        for location, text in reader_strings(contract):
            reader_template = "text" in path.relative_to(
                root / "contracts"
            ).parts or location.rsplit(".", 1)[-1] in {"name", "label", "description"}
            errors.extend(
                f"{path.relative_to(root)}.{location}: {error}"
                for error in name_reference_errors(
                    text, patterns=patterns, resolved=not reader_template
                )
            )
    locked = read_lock(root)
    profile_name_owners = {
        f"{profile.identifier}@{profile.method.document['version']}" for profile in profiles(root)
    }
    for document in _methodology_documents(root):
        identifier = f"{document['id']}@{document['version']}"
        if identifier in locked:
            continue
        for location, text in reader_strings(document):
            if location == "name" and identifier in profile_name_owners:
                continue  # Lifecycle names are generated from this owning field.
            errors.extend(
                f"{identifier}.{location}: {error}"
                for error in name_reference_errors(text, patterns=patterns)
            )
    return errors


def published_name_errors(bundle: Mapping[str, Any], root: Path) -> list[str]:
    """Reject retired display names from every published methodology reader value, and a Clock
    row that is not the names table's clock labels."""
    patterns = name_reference_patterns(root)
    return [
        f"{location}: {error}"
        for location, text in reader_strings(bundle.get("methodologies", {}))
        for error in name_reference_errors(text, patterns=patterns, resolved=True)
    ] + glance_clock_errors(bundle)
