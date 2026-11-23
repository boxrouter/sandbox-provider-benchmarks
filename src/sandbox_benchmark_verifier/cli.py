# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Command line entry point for methodology checks and the generated text bundle."""

import argparse
import json
import sys
from collections import defaultdict
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from sandbox_benchmark_verifier.content import (
    content_criteria,
    reader_literal_errors,
    rendered_content_errors,
)
from sandbox_benchmark_verifier.literals import canonical_literal_terms
from sandbox_benchmark_verifier.machine_panels import panel_errors
from sandbox_benchmark_verifier.names import name_errors
from sandbox_benchmark_verifier.parameters import document_errors
from sandbox_benchmark_verifier.text import text_warnings, write_text_bundle
from sandbox_benchmark_verifier.yaml_load import load_yaml_all

ROOT = Path.cwd()


def records(path: Path) -> Iterator[tuple[Path, Any]]:
    """Load JSON or multi-document YAML records below a path."""
    if not path.exists():
        return
    paths = [path] if path.is_file() else sorted(p for p in path.rglob("*") if p.is_file())
    for item in paths:
        if item.suffix.lower() == ".json":
            yield item, json.loads(item.read_text())
        elif item.suffix.lower() in {".yaml", ".yml"}:
            for document in load_yaml_all(item.read_text()):
                if document is not None:
                    yield item, document


def schema_for(path: Path, root: Path) -> Path | None:
    """Choose the schema of a methodology document; no other folder has one."""
    if path.relative_to(root).parts[0] != "methodology":
        return None
    schema_path = root / "schemas" / "methodology.schema.json"
    return schema_path if schema_path.is_file() else None


def read_schemas(root: Path) -> tuple[dict[Path, Mapping[str, Any]], list[str]]:
    """Load and meta-validate every available schema."""
    schemas: dict[Path, Mapping[str, Any]] = {}
    errors: list[str] = []
    for schema_path in sorted((root / "schemas").glob("*.schema.json")):
        try:
            schema = json.loads(schema_path.read_text())
            Draft202012Validator.check_schema(schema)
            schemas[schema_path] = schema
        except (json.JSONDecodeError, SchemaError) as exc:
            errors.append(f"{schema_path}: invalid schema: {exc}")
    return schemas, errors


def load_documents(root: Path) -> tuple[list[tuple[Path, Any]], list[str]]:
    """Load all methodology documents."""
    folder = root / "methodology"
    try:
        return list(records(folder)), []
    except (OSError, yaml.YAMLError, json.JSONDecodeError) as exc:
        return [], [f"{folder}: cannot read document: {exc}"]


def non_string_keys(value: Any, where: str = "$") -> Iterator[str]:
    """JSON paths of mapping keys that YAML read as something other than a string."""
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                yield f"{where}.{key!r}"
            yield from non_string_keys(item, f"{where}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from non_string_keys(item, f"{where}[{index}]")


def methodology_index(
    root: Path, documents: Sequence[tuple[Path, Any]]
) -> tuple[set[str], list[str]]:
    """Index the methodology ids and report duplicates."""
    counts: dict[str, int] = defaultdict(int)
    for path, document in documents:
        if path.is_relative_to(root / "methodology") and isinstance(document, Mapping):
            if isinstance(document.get("id"), str):
                counts[document["id"]] += 1
    errors = [
        f"duplicate methodology id: {identifier}" for identifier, n in counts.items() if n > 1
    ]
    return set(counts), errors


def parameter_errors(
    root: Path,
    documents: Sequence[tuple[Path, Any]],
    method_documents: Sequence[dict[str, Any]],
) -> list[str]:
    """Check pinned parameters, shared trials and formulas."""
    owners = {str(document["id"]): document for document in method_documents}
    errors: list[str] = []
    for path, document in documents:
        if isinstance(document, Mapping):
            errors.extend(document_errors(root, path, document, owners))
    return errors


def _method_errors(root: Path, documents: Sequence[tuple[Path, Any]]) -> list[str]:
    """The checks that read every methodology document with an id."""
    method_documents = [
        dict(document)
        for path, document in documents
        if path.is_relative_to(root / "methodology")
        and isinstance(document, Mapping)
        and "id" in document
    ]
    return [
        *parameter_errors(root, documents, method_documents),
        *panel_errors(root, method_documents),
    ]


def validate(root: Path = ROOT) -> list[str]:
    """Validate every methodology document: its schema, then the checks that read them all."""
    schemas, errors = read_schemas(root)
    documents, read_errors = load_documents(root)
    errors.extend(read_errors)
    _, method_errors = methodology_index(root, documents)
    errors.extend(method_errors)
    for path, document in documents:
        errors.extend(
            f"{path}: key {where} is not a string; quote it" for where in non_string_keys(document)
        )
        schema_key = schema_for(path, root)
        schema = schemas.get(schema_key) if schema_key else None
        if schema is not None:
            for issue in Draft202012Validator(schema).iter_errors(document):
                # Do not echo a rejected value (it could contain credentials or account data).
                errors.append(f"{path}: X22 schema validation failed at {list(issue.schema_path)}")
    errors.extend(_method_errors(root, documents))
    errors.extend(name_errors(root))
    return errors


def rendered_capture_errors(payload: Mapping[str, Any]) -> list[str]:
    """Apply exported criteria to one or more consumer DOM captures."""
    failures: list[str] = []
    for check in payload.get("checks", [payload]):
        criteria = check.get("criteria", content_criteria())
        for slot in check.get("authored_slots", []):
            failures.extend(
                f"{slot['id']}: {error}"
                for error in reader_literal_errors(
                    slot["template"], criteria, slot.get("runtime_fields", [])
                )
            )
        for scope in check.get("scopes", []):
            failures.extend(
                f"{scope.get('id', 'scope')}: {error}"
                for error in rendered_content_errors(criteria, scope)
            )
    return failures


def main() -> int:
    """Run one command from the current package root."""
    parser = argparse.ArgumentParser(prog="sandbox-benchmark-verify")
    parser.add_argument("command", choices=("validate", "text", "literals", "rendered-text"))
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, help="write the generated text bundle (text only)")
    parser.add_argument(
        "--text-mode",
        choices=("report", "enforce"),
        default="enforce",
        help="enforce required text slots by default; report gaps only when set to report",
    )
    parser.add_argument("--capture", type=Path, help="neutral slot capture JSON; defaults to stdin")
    args = parser.parse_args()
    command = args.command
    if command == "rendered-text":
        payload = json.loads(args.capture.read_text() if args.capture else sys.stdin.read())
        failures = rendered_capture_errors(payload)
        print(json.dumps({"errors": failures}))
        return int(bool(failures))
    if command == "literals":
        print(json.dumps(canonical_literal_terms(), ensure_ascii=False))
        return 0
    if command == "text":
        warnings = text_warnings(args.root)
        if args.output:
            write_text_bundle(args.root, args.output)
        for warning in warnings:
            print(f"warning: {warning}", file=sys.stderr)
        mode = "report only" if args.text_mode == "report" else "enforce"
        print(f"text: {len(warnings)} warning(s); {mode}")
        return int(args.text_mode == "enforce" and bool(warnings))
    errors = validate(args.root)
    for warning in text_warnings(args.root):
        print(f"warning: {warning}", file=sys.stderr)
    for error in errors:
        print(error, file=sys.stderr)
    if not errors:
        print("validate: ok")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
