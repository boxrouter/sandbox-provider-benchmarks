# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Resolve short shared definitions and illustrative examples from owned data."""

import json
from collections.abc import Mapping
from functools import cache
from pathlib import Path
from typing import Any

from sandbox_benchmark_verifier.catalog import _assets, load_methodology
from sandbox_benchmark_verifier.name_refs import canonical_name_map
from sandbox_benchmark_verifier.names import load_tables
from sandbox_benchmark_verifier.placeholders import resolve_template


def shared_concepts(facts: Mapping[str, Any], index_copy: Mapping[str, Any]) -> dict[str, Any]:
    """Produce reader cards; formal ids remain metadata rather than reader prose."""
    path = _assets() / "contracts/text/shared-concepts.json"
    if not path.is_file():
        return {}
    source = json.loads(path.read_text(encoding="utf-8"))
    result = {}
    names = canonical_name_map(load_tables())
    for code, entry in source["concepts"].items():
        context = {"facts": facts, "concept": entry, "content": {"index_copy": index_copy}}
        result[code] = {
            **entry,
            "name": resolve_template(entry["name"], names=names, documents=context),
            **(
                {"meaning": resolve_template(entry["meaning"], names=names, documents=context)}
                if "meaning" in entry
                else {}
            ),
            **(
                {
                    "example": resolve_template(
                        entry["example"]["template"], names=names, documents=context
                    )
                }
                if "example" in entry
                else {}
            ),
        }
    return result


@cache
def _template_documents(root: Path) -> dict[str, Mapping[str, Any]]:
    """Reuse immutable source records while compiling the release's reader templates."""
    return {
        f"{item.document['id']}.{item.document['version']}": item.document
        for item in load_methodology(root)
    }


def hover_templates(content: Mapping[str, Any]) -> dict[str, Any]:
    """Compile owned parameter references; runtime facts remain declared substitutions."""
    return _compiled_entries(content, "templates")


def reasons(content: Mapping[str, Any]) -> dict[str, Any]:
    """Each publication text code's reader sentence, or `show: []` for a code that says nothing."""
    return _compiled_entries(content, "reasons")


def _compiled_entries(content: Mapping[str, Any], table: str) -> dict[str, Any]:
    root = _assets()
    path = root / "contracts/text/hover-templates.json"
    if not path.is_file():
        return {}
    source = json.loads(path.read_text(encoding="utf-8"))
    context = {**_template_documents(root), "content": content}
    names = canonical_name_map(load_tables(root))
    return {
        code: {
            **entry,
            **(
                {
                    "authored_template": entry["template"],
                    "template": resolve_template(entry["template"], names=names, documents=context),
                }
                if "template" in entry
                else {}
            ),
        }
        for code, entry in _expanded(source.get(table, {})).items()
    }


def _expanded(entries: Mapping[str, Any]) -> dict[str, Any]:
    """An entry `{"template_ref": key}` is another entry's whole body; a reference never chains."""
    return {
        code: entries[entry["template_ref"]] if "template_ref" in entry else entry
        for code, entry in entries.items()
    }


def hover_source_errors(root: Path | None = None) -> list[str]:
    """Reject a dangling or chained reference and two entries that spell out one whole body."""
    path = (root or _assets()) / "contracts/text/hover-templates.json"
    if not path.is_file():
        return []
    entries = json.loads(path.read_text(encoding="utf-8")).get("templates", {})
    errors: list[str] = []
    bodies: dict[str, str] = {}
    for code, entry in entries.items():
        if "template_ref" in entry:
            target = entry["template_ref"]
            if set(entry) != {"template_ref"} or target not in entries or target == code:
                errors.append(f"{code}: a reference names one other entry and nothing else")
            elif "template_ref" in entries[target]:
                errors.append(f"{code}: a reference to another reference")
            continue
        body = json.dumps(entry, sort_keys=True)
        if body in bodies:
            errors.append(f"{code}: same body as {bodies[body]}; make it a template_ref")
        bodies.setdefault(body, code)
    return errors
