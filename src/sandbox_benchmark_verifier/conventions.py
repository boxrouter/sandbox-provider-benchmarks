# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Load, validate and document the single shared reader-text convention source."""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Mapping
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from sandbox_benchmark_verifier.catalog import _assets, load_methodology
from sandbox_benchmark_verifier.text_predicates import (
    all_rules,
    check_program,
    resolve,
)
from sandbox_benchmark_verifier.yaml_load import load_yaml

SOURCE = "conventions/text-conventions.yaml"
REFERENCE = "docs/reference/text-conventions.md"


def load_conventions(root: Path | None = None) -> dict[str, Any]:
    """Return the packaged convention data; editable installs read its YAML source."""
    root = root or _assets()
    source = root / SOURCE
    schema_source = root / "schemas/text-conventions.schema.json"
    stamp = (source.stat().st_mtime_ns, schema_source.stat().st_mtime_ns)
    result: dict[str, Any] = json.loads(_validated_source(source, schema_source, stamp))
    result["proper_names"] = _proper_names(root)
    return result


def conventions_view(root: Path | None = None) -> dict[str, Any]:
    """The convention data for reading only; callers must not change what it holds.

    Equal to ``load_conventions`` without its fresh parse: a compile reads the panel-title rules
    hundreds of times.
    """
    root = root or _assets()
    source = root / SOURCE
    schema_source = root / "schemas/text-conventions.schema.json"
    stamp = (source.stat().st_mtime_ns, schema_source.stat().st_mtime_ns)
    return {**_parsed_source(source, schema_source, stamp), "proper_names": _proper_names(root)}


@lru_cache(maxsize=8)
def _parsed_source(source: Path, schema_source: Path, stamp: tuple[int, int]) -> dict[str, Any]:
    result: dict[str, Any] = json.loads(_validated_source(source, schema_source, stamp))
    return result


@lru_cache(maxsize=8)
def _proper_names(root: Path) -> list[str]:
    """The tool names a documented standard writes in lowercase (fio, stress-ng).

    They are the documents' own one-word ``source.name`` values, exactly as owned (a workload
    description is not a name): a sentence may start with one although the rule asks for a capital.
    """
    names = {
        str(item.document["source"]["name"])
        for item in load_methodology(root)
        if isinstance(item.document, dict)
        and isinstance(item.document.get("source"), dict)
        and str(item.document["source"].get("name", ""))[:1].islower()
        and len(str(item.document["source"]["name"]).split()) == 1
    }
    return sorted(names)


@lru_cache(maxsize=8)
def _validated_source(source: Path, schema_source: Path, _stamp: tuple[int, int]) -> str:
    with source.open(encoding="utf-8") as stream:
        result: dict[str, Any] = load_yaml(stream)
    schema = json.loads(schema_source.read_text(encoding="utf-8"))
    errors = list(Draft202012Validator(schema).iter_errors(result))
    if errors:
        raise ValueError("; ".join(error.message for error in errors))
    for slot_id, rule in result["slots"].items():
        if rule["min_length"] > rule["max_length"]:
            raise ValueError(f"{slot_id}: minimum length exceeds maximum")
        if not rule["enabled"] and (rule["required"] or rule["form"] != "forbidden"):
            raise ValueError(f"{slot_id}: retired slots must be optional and forbidden")
    declared = set(result["slots"])
    referenced = set(result["panel"]["slot_order"]) | set(result["details"]["part_order"])
    if not referenced <= declared:
        raise ValueError("slot order references an undeclared slot")
    rules = all_rules(result)
    for rule in rules:
        _resolve_parameters(rule)
    ids = [rule["id"] for rule in rules]
    if len(ids) != len(set(ids)):
        raise ValueError("convention rule IDs must be unique across text and presentation")
    _validate_required_slots(compatibility_exports(result))
    return json.dumps(result)


_PARAMETER_TOKEN = re.compile(r"\{parameters\.([a-z][a-z0-9_]*)\}")


def _resolve_parameters(rule: dict[str, Any]) -> None:
    """Write a rule's prose from its parameters, so a number or template lives in one place."""
    parameters: dict[str, Any] = rule.get("parameters", {})
    used: set[str] = set()
    text = rule.get("text")
    if isinstance(text, str):
        for name in _PARAMETER_TOKEN.findall(text):
            if name not in parameters:
                raise ValueError(f"{rule['id']}.text: undeclared parameter {name}")
            used.add(name)
        rule["text"] = _PARAMETER_TOKEN.sub(lambda match: str(parameters[match.group(1)]), text)
    unused = set(parameters) - used
    if unused:
        raise ValueError(f"{rule['id']}: parameters never named in its text: {sorted(unused)}")


def _validate_required_slots(exports: Mapping[str, Any]) -> None:
    for kind, panel in exports["panels"].items():
        for slot in panel.get("required_slots", []):
            if slot not in exports["slots"] or not exports["slots"][slot].get("enabled", True):
                raise ValueError(f"{kind}: requires unknown or forbidden slot {slot}")


def resolved_slot_rule(conventions: Mapping[str, Any], slot_id: str) -> dict[str, Any]:
    """Derive the executable slot rule from the same serialized conventions."""
    rule = dict(conventions["slots"][slot_id])
    if rule["form"] == "sentence":
        rule.update(conventions["sentence_rule"])
    return rule


def compatibility_exports(conventions: Mapping[str, Any]) -> dict[str, Any]:
    """Generate consumer views from conventions without sharing mutable rule objects."""
    slots = {key: deepcopy(resolved_slot_rule(conventions, key)) for key in conventions["slots"]}
    panels = deepcopy(resolve(conventions["panels"], {"conventions": conventions}))
    for panel in panels.values():
        if "extends" in panel:
            inherited = deepcopy(panels[panel["extends"]])
            inherited.update(panel)
            inherited.pop("abstract", None)
            panel.update(inherited)
    pages = deepcopy(conventions["method_pages"])
    defaults = conventions["method_page"]
    for kind, page in pages.items():
        blocks = [defaults["slot_prefix"] + block for block in page["required_blocks"]]
        order = [*defaults["header_slots"], *blocks]
        panels[defaults["slot_prefix"] + kind] = {
            "required_slots": list(order),
            "slot_order": list(order),
        }
        for block in blocks:
            slots[block] = deepcopy(defaults["block_rule"])
    headings = {
        key: rule["heading"]
        for key, rule in slots.items()
        if rule.get("enabled", True) and "heading" in rule
    }
    return {
        "slots": slots,
        "panels": panels,
        "method_pages": pages,
        "detail_headings": headings,
    }


def compatibility_errors(criteria: Mapping[str, Any]) -> list[str]:
    """Reject a serialized compatibility view that drifts from its convention program."""
    expected = compatibility_exports(criteria["conventions"])
    errors = []
    for slot, rule in expected["slots"].items():
        actual = criteria.get("slots", {}).get(slot, {})
        if any(actual.get(key) != value for key, value in rule.items()):
            errors.append(f"{slot}: compatibility slot differs from conventions")
    for kind, panel in expected["panels"].items():
        actual = criteria.get("panels", {}).get(kind, {})
        for key in ("required_slots", "optional_slots", "slot_order", "details_order"):
            if actual.get(key, []) != panel.get(key, []):
                errors.append(f"{kind}.{key}: compatibility panel differs from conventions")
    if criteria.get("detail_headings") != expected["detail_headings"]:
        errors.append("detail_headings: compatibility headings differ from conventions")
    return errors


def check_conventions(
    scope: Mapping[str, Any], *, conventions: Mapping[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Run the one predicate interpreter for source, pricing and rendered captures."""
    return check_program(scope, conventions if conventions is not None else load_conventions())


def reference_text(conventions: Mapping[str, Any]) -> str:
    """Generate the reader reference: the slot table and a link to the source for every rule."""
    lines = [
        "<!-- SPDX-FileCopyrightText: BoxRouter, Inc. -->",
        "".join(("<!-- SPDX-License-", "Identifier: CC-BY-4.0 -->")),
        (
            f"<!-- Generated by python -m {__package__}.conventions "
            "--write-reference; do not edit. -->"
        ),
        "# Text Conventions",
        "",
        f"Edit [`{SOURCE}`](../../{SOURCE}) in a PR; "
        "validators, templates and this reference read it.",
        "",
        "| Slot | Form | Casing | Characters | Required | Enabled |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for slot_id, rule in conventions["slots"].items():
        lines.append(
            f"| {rule['name']} (`{slot_id}`) | {rule['form']} | {rule['casing']} | "
            f"{rule['min_length']}–{rule['max_length']} | {rule['required']} | {rule['enabled']} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    """Regenerate the checked-in reference, or fail on a stale generated reference."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-reference", action="store_true")
    args = parser.parse_args()
    root = _assets()
    reference = root / REFERENCE
    expected = reference_text(load_conventions(root))
    if args.write_reference:
        reference.parent.mkdir(parents=True, exist_ok=True)
        reference.write_text(expected, encoding="utf-8")
    elif not reference.is_file() or reference.read_text(encoding="utf-8") != expected:
        raise SystemExit(
            "Generated text conventions reference is stale; run with --write-reference"
        )


if __name__ == "__main__":
    main()
