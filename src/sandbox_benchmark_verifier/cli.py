# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Command line entry point for methodology data checks."""

import argparse
import json
import math
import random
import sys
from collections import defaultdict
from collections.abc import Iterator, Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from sandbox_benchmark_verifier.catalog import load_schema
from sandbox_benchmark_verifier.compare import (
    CompareError,
    Selection,
    compare,
    load_summary,
    load_trials,
    render,
    summary_source,
)
from sandbox_benchmark_verifier.content import (
    content_criteria,
    reader_literal_errors,
    rendered_content_errors,
)
from sandbox_benchmark_verifier.image_sizes import fill_facts, image_fact_errors
from sandbox_benchmark_verifier.independence import independence_errors
from sandbox_benchmark_verifier.literals import canonical_literal_terms
from sandbox_benchmark_verifier.machine_panels import panel_errors
from sandbox_benchmark_verifier.names import name_errors
from sandbox_benchmark_verifier.new_benchmark import new_benchmark_errors
from sandbox_benchmark_verifier.parameters import document_errors, variant_errors
from sandbox_benchmark_verifier.safety import check_record
from sandbox_benchmark_verifier.scaffold import BenchmarkDraft, add, add_from_shape
from sandbox_benchmark_verifier.stats import bootstrap_ci, nearest_rank, numeric
from sandbox_benchmark_verifier.text import text_warnings, write_text_bundle
from sandbox_benchmark_verifier.timing import timing_errors
from sandbox_benchmark_verifier.yaml_load import load_yaml, load_yaml_all

ROOT = Path.cwd()


def records(path: Path) -> Iterator[tuple[Path, Any]]:
    """Load JSON or multi-document YAML records below a path."""
    if not path.exists():
        return
    paths = [path] if path.is_file() else sorted(p for p in path.rglob("*") if p.is_file())
    for item in paths:
        if item.suffix.lower() == ".json":
            yield item, json.loads(item.read_text())
        elif item.suffix.lower() == ".jsonl":
            for line in item.read_text().splitlines():
                if line.strip():
                    yield item, json.loads(line)
        elif item.suffix.lower() in {".yaml", ".yml"}:
            for document in load_yaml_all(item.read_text()):
                if document is not None:
                    yield item, document


def schema_for(path: Path, root: Path) -> Path | None:
    """Choose a schema by document family; data subfolders disambiguate aggregates."""
    relative = path.relative_to(root)
    if relative.parts[0] == "methodology":
        name = "methodology.schema.json"
    elif relative.parts[0] == "runs":
        name = "run.schema.json"
    elif relative.parts[0] == "data":
        name = (
            "aggregate.schema.json" if "aggregates" in relative.parts else "measurement.schema.json"
        )
    else:
        return None
    schema_path = root / "schemas" / name
    return schema_path if schema_path.is_file() else None


def method_refs(value: Any) -> Iterator[tuple[str, str]]:
    """Find explicit methodology id/version pairs in nested run and data records."""
    if isinstance(value, Mapping):
        method = value.get("methodology")
        if isinstance(method, Mapping):
            identifier, version = method.get("id"), method.get("version")
            if isinstance(identifier, str) and isinstance(version, (str, int, float)):
                yield identifier, str(version)
        for collection in ("methodologies", "methodology_versions"):
            citations = value.get(collection)
            if isinstance(citations, Mapping):
                for identifier, version in citations.items():
                    if isinstance(identifier, str) and isinstance(version, (str, int, float)):
                        yield identifier, str(version)
            else:
                yield from citation_refs(citations)
        identifier, version = value.get("methodology_id"), value.get("methodology_version")
        if isinstance(identifier, str) and isinstance(version, (str, int, float)):
            yield identifier, str(version)
        for child in value.values():
            yield from method_refs(child)
    elif isinstance(value, list):
        for child in value:
            yield from method_refs(child)


def citation_refs(value: Any) -> Iterator[tuple[str, str]]:
    """Extract id/version pairs from a methodology citation list."""
    if not isinstance(value, list):
        return
    for citation in value:
        if not isinstance(citation, Mapping):
            continue
        identifier, version = citation.get("id"), citation.get("version")
        if isinstance(identifier, str) and isinstance(version, (str, int, float)):
            yield identifier, str(version)


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
    """Load all methodology, run and data documents."""
    documents: list[tuple[Path, Any]] = []
    errors: list[str] = []
    for folder in (root / "methodology", root / "runs", root / "data"):
        try:
            documents.extend(records(folder))
        except (OSError, yaml.YAMLError, json.JSONDecodeError) as exc:
            errors.append(f"{folder}: cannot read document: {exc}")
    return documents, errors


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
) -> tuple[dict[str, set[str]], list[str]]:
    """Index methodology versions and report duplicate ID/version pairs."""
    methods: dict[str, set[str]] = defaultdict(set)
    counts: dict[tuple[str, str], int] = defaultdict(int)
    errors: list[str] = []
    for path, document in documents:
        if not path.is_relative_to(root / "methodology") or not isinstance(document, Mapping):
            continue
        identifier, version = document.get("id"), document.get("version")
        if isinstance(identifier, str) and isinstance(version, (str, int, float)):
            methods[identifier].add(str(version))
            counts[(identifier, str(version))] += 1
    for (identifier, version), count in counts.items():
        if count > 1:
            errors.append(f"duplicate methodology id/version: {identifier} {version}")
    return methods, errors


def run_identity_allowlists(
    root: Path, documents: Sequence[tuple[Path, Any]]
) -> dict[str, frozenset[str]]:
    """Resolve the generated identity registry cited by each run manifest."""
    result = {}
    for path, document in documents:
        if not path.is_relative_to(root / "runs") or not isinstance(document, Mapping):
            continue
        run_id, sources = document.get("run_id"), document.get("workload_sources")
        if isinstance(run_id, str) and isinstance(sources, list):
            result[run_id] = frozenset(
                item["id"]
                for item in sources
                if isinstance(item, Mapping) and isinstance(item.get("id"), str)
            )
    return result


def parameter_errors(
    root: Path,
    documents: Sequence[tuple[Path, Any]],
    method_documents: Sequence[dict[str, Any]],
) -> list[str]:
    """Check pinned parameters, shared trials, formulas and measurement variants."""
    owners = {
        (str(document["id"]), str(document["version"])): document
        for document in method_documents
        if "version" in document
    }
    errors: list[str] = []
    for path, document in documents:
        if path.is_relative_to(root / "methodology") and isinstance(document, Mapping):
            errors.extend(document_errors(root, path, document, owners))
    errors.extend(
        variant_errors(
            root, [(p, d) for p, d in documents if p.is_relative_to(root / "data")], owners
        )
    )
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
    """Validate every present document and cross-reference methodology versions."""
    schemas, errors = read_schemas(root)
    documents, read_errors = load_documents(root)
    errors.extend(read_errors)
    methods, method_errors = methodology_index(root, documents)
    errors.extend(method_errors)
    identity_allowlists = run_identity_allowlists(root, documents)
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
            if path.is_relative_to(root / "data") or path.is_relative_to(root / "runs"):
                errors.extend(
                    f"{path}: {issue}"
                    for issue in check_record(
                        document,
                        identity_allowlists.get(str(document.get("run_id")), frozenset())
                        if isinstance(document, Mapping)
                        else frozenset(),
                    )
                )
    for path, document in documents:
        if path.is_relative_to(root / "methodology"):
            continue
        for identifier, version in method_refs(document):
            if version not in methods.get(identifier, set()):
                errors.append(f"{path}: unknown methodology version {identifier} {version}")
    errors.extend(_method_errors(root, documents))
    errors.extend(independence_errors(root, documents))
    errors.extend(name_errors(root))
    errors.extend(new_benchmark_errors(root))
    errors.extend(image_fact_errors(root))
    errors.extend(timing_errors(root))
    return errors


def close(found: float, expected: float, aggregate: Mapping[str, Any]) -> bool:
    precision = aggregate["precision"]
    tolerance = (
        Decimal(5).scaleb(-precision["decimal_places"] - 1)
        if "decimal_places" in precision
        else Decimal(str(precision["absolute_tolerance"]))
    )
    if not math.isfinite(found) or not math.isfinite(expected) or not tolerance.is_finite():
        return False
    return abs(Decimal(str(found)) - Decimal(str(expected))) <= tolerance


def raw_measurements(root: Path) -> list[tuple[Path, Mapping[str, Any]]]:
    """Read raw records with their file locations for aggregate path and filter selection."""
    raw: list[tuple[Path, Mapping[str, Any]]] = []
    for path, document in records(root / "data"):
        if "aggregates" in path.parts or not isinstance(document, Mapping):
            continue
        raw.append((path, document))
    return raw


def input_target(root: Path, aggregate: Mapping[str, Any]) -> Path | None:
    """Resolve a repository-relative or run-relative raw input reference."""
    inputs = aggregate.get("inputs")
    if not isinstance(inputs, Mapping) or not isinstance(inputs.get("path"), str):
        return None
    base = root / "data" / str(aggregate["run_id"]) if inputs.get("relative_to") == "run" else root
    return (base / str(inputs["path"])).resolve()


def selected_measurements(
    root: Path,
    aggregate: Mapping[str, Any],
    raw: Sequence[tuple[Path, Mapping[str, Any]]],
) -> tuple[list[float], int]:
    """Select successful, included raw values and count excluded inputs."""
    inputs = aggregate.get("inputs")
    target = input_target(root, aggregate)
    filters = inputs.get("filter", {}) if isinstance(inputs, Mapping) else {}
    selected: list[float] = []
    groups: dict[str, list[float]] = defaultdict(list)
    group_by = inputs.get("group_by") if isinstance(inputs, Mapping) else None
    excluded = 0
    for path, record in raw:
        if (
            target is not None
            and path.resolve() != target
            and not path.resolve().is_relative_to(target)
        ):
            continue
        fields = (
            "provider",
            "test_id",
            "run_id",
            "methodology",
            "variant",
            "sandbox_index",
            "measurement",
            "unit",
        )
        if any(field in aggregate and record.get(field) != aggregate[field] for field in fields):
            continue
        if isinstance(filters, Mapping) and any(
            record.get(key) != value for key, value in filters.items()
        ):
            continue
        if record.get("exclusion_reason") is not None:
            excluded += 1
            continue
        value = numeric(record.get("value"))
        if record.get("success", True) is True and value is not None:
            selected.append(value)
            if group_by is not None:
                groups[str(record.get(group_by))].append(value)
    if group_by is not None:
        selected = [
            nearest_rank(values, 0.5)
            if isinstance(inputs, Mapping) and inputs.get("group_statistic") == "median"
            else values[0]
            for values in groups.values()
        ]
    return selected, excluded


def verify_ci(
    path: Path,
    aggregate: Mapping[str, Any],
    values: Sequence[float],
    options: tuple[int, int, float] | None,
    statistic: str,
) -> list[str]:
    """Check a reported percentile-bootstrap interval using its methodology settings."""
    found = aggregate.get("interval")
    if options is None or not isinstance(found, Mapping):
        return [f"{path}: interval needs a versioned methodology bootstrap configuration"]
    seed, resamples, confidence = options
    found_confidence = numeric(found.get("confidence"))
    if found_confidence is None or not math.isclose(found_confidence, confidence):
        return [f"{path}: interval confidence must match methodology ({confidence})"]
    interval = (
        bootstrap_mean_ci(values, seed, resamples, confidence)
        if statistic == "cost_per_success"
        else bootstrap_ci(values, seed, resamples, confidence)
    )
    low, high = numeric(found.get("low")), numeric(found.get("high"))
    if (
        interval is None
        or low is None
        or high is None
        or not close(low, interval[0], aggregate)
        or not close(high, interval[1], aggregate)
    ):
        return [f"{path}: expected bootstrap CI {interval}, found {found}"]
    return []


def bootstrap_mean_ci(
    values: Sequence[float], seed: int, resamples: int, confidence: float
) -> tuple[float, float] | None:
    """Percentile-bootstrap interval for mean cost per successful attempt."""
    if not values:
        return None
    rng = random.Random(seed)
    means = sorted(
        math.fsum(rng.choices(values, k=len(values))) / len(values) for _ in range(resamples)
    )
    cut = (1 - confidence) / 2
    return means[math.floor(cut * (resamples - 1))], means[math.ceil((1 - cut) * (resamples - 1))]


def bootstrap_options(root: Path, citation: Any) -> tuple[int, int, float] | None:
    """Resolve seed, resamples and confidence from the cited methodology version."""
    if not isinstance(citation, Mapping):
        return None
    identifier, version = citation.get("id"), citation.get("version")
    documents = [document for _, document in records(root / "methodology")]
    selected = [
        document
        for document in documents
        if isinstance(document, Mapping)
        and document.get("id") == identifier
        and str(document.get("version")) == str(version)
    ]
    selected.extend(
        document
        for document in documents
        if isinstance(document, Mapping) and document.get("category") == "common"
    )
    for document in selected:
        if not isinstance(document, Mapping):
            continue
        stats = document.get("statistics")
        interval = stats.get("interval") if isinstance(stats, Mapping) else None
        if not isinstance(interval, Mapping) or interval.get("type") != "bootstrap_percentile":
            continue
        seed, resamples = interval.get("seed"), interval.get("resamples")
        confidence = numeric(interval.get("confidence"))
        if isinstance(seed, int) and isinstance(resamples, int) and confidence is not None:
            return seed, resamples, confidence
    return None


def summary_value(statistic: str, values: Sequence[float]) -> float | None:
    """Calculate the aggregate statistic defined by the methodology vocabulary."""
    if statistic == "median":
        result = nearest_rank(values, 0.5)
    elif statistic in {"mean", "cost_per_success"}:
        result = math.fsum(values) / len(values)
    elif statistic == "sum":
        result = math.fsum(values)
    elif statistic == "p95":
        result = nearest_rank(values, 0.95)
    elif statistic == "p5":
        result = nearest_rank(values, 0.05)
    elif statistic == "slowest":
        result = max(values)
    elif statistic == "lowest":
        result = min(values)
    else:
        result = None
    return result


def verify_aggregate(
    root: Path,
    path: Path,
    aggregate: Mapping[str, Any],
    raw: Sequence[tuple[Path, Mapping[str, Any]]],
) -> list[str]:
    """Recompute one supported aggregate and its optional confidence interval."""
    precision = aggregate.get("precision")
    precision_schema = load_schema("aggregate")["properties"]["precision"]
    if not Draft202012Validator(precision_schema).is_valid(precision):
        return [f"{path}: aggregate must declare valid precision"]
    values, excluded = selected_measurements(root, aggregate, raw)
    error: str | None = None
    statistic = str(aggregate.get("statistic", aggregate.get("method", "median"))).lower()
    if (
        not values
        and statistic == "cost_per_success"
        and aggregate.get("value") is None
        and aggregate.get("n") == 0
        and aggregate.get("interval") is None
    ):
        if aggregate.get("excluded_n") == excluded:
            return []
        error = (
            f"{path}: expected excluded_n={excluded}, "
            f"found excluded_n={aggregate.get('excluded_n')}"
        )
    elif not values:
        error = f"{path}: no successful raw measurements match aggregate inputs"
    actual = summary_value(statistic, values) if values else None
    expected = numeric(aggregate.get("value"))
    if error is None and actual is None:
        error = f"{path}: unsupported statistic {statistic!r}"
    if error is None and expected is None:
        error = f"{path}: aggregate value must be numeric"
    interval = aggregate.get("interval")
    if error is None and interval is not None:
        interval_errors = verify_ci(
            path,
            aggregate,
            values,
            bootstrap_options(root, aggregate.get("methodology")),
            statistic,
        )
        if interval_errors:
            error = interval_errors[0]
    if error is None and isinstance(aggregate.get("n"), int) and aggregate["n"] != len(values):
        error = f"{path}: expected n={len(values)}, found n={aggregate['n']}"
    if (
        error is None
        and isinstance(aggregate.get("excluded_n"), int)
        and aggregate["excluded_n"] != excluded
    ):
        error = (
            f"{path}: expected excluded_n={excluded}, found excluded_n={aggregate['excluded_n']}"
        )
    if (
        error is None
        and expected is not None
        and actual is not None
        and not close(expected, actual, aggregate)
    ):
        error = f"{path}: expected {statistic} {actual}, found {expected}"
    return [error] if error else []


def reproduce(root: Path = ROOT) -> list[str]:
    """Recompute median, mean and seeded median intervals from raw measurements."""
    raw = raw_measurements(root)
    by_path: dict[Path, list[tuple[Path, Mapping[str, Any]]]] = defaultdict(list)
    for path, document in raw:
        by_path[path].append((path, document))
    by_path = {path.resolve(): rows for path, rows in by_path.items()}
    selections: dict[Path, list[tuple[Path, Mapping[str, Any]]]] = {}
    errors: list[str] = []
    for path, aggregate in records(root / "data"):
        if "aggregates" not in path.parts or not isinstance(aggregate, Mapping):
            continue
        target = input_target(root, aggregate)
        if target is None:
            candidates = raw
        elif target in by_path:
            candidates = by_path[target]
        else:
            if target not in selections:
                selections[target] = [
                    row
                    for source, rows in by_path.items()
                    if source.is_relative_to(target)
                    for row in rows
                ]
            candidates = selections[target]
        errors.extend(verify_aggregate(root, path, aggregate, candidates))
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


def run_compare(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    """Print how your results compare with a publication: 0 agree, 1 differ, 2 cannot compare."""
    if not (args.results and args.publication and args.provider and args.test):
        parser.error("compare requires <results>, --publication, --provider and --test")
    try:
        summary = load_summary(summary_source(args.publication, args.summary), args.publication)
        result = compare(
            load_trials(args.results), summary, Selection(args.provider, args.test, args.variant)
        )
    except CompareError as exc:
        print(f"compare: {exc}", file=sys.stderr)
        return 2
    print(render(result))
    return int(not result.agree)


def run_listing(command: str, root: Path) -> int:
    """Print the canonical literal terms, or fill the missing Image Build image facts."""
    if command == "literals":
        print(json.dumps(canonical_literal_terms(), ensure_ascii=False))
    else:
        print(f"image sizes: {len(fill_facts(root))} workload(s) filled")
    return 0


def run_scaffold(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    """Write a new benchmark: from a block's shape and a draft, or from one command line."""
    if args.shape:
        if not args.identifier or not args.draft or not args.category:
            parser.error("scaffold --shape requires --id, --draft and --category")
        path = add_from_shape(
            args.root,
            args.identifier,
            args.category,
            json.loads(args.shape.read_text()),
            load_yaml(args.draft.read_text()),
        )
        print(path.relative_to(args.root).as_posix())
        return 0
    if not args.identifier or not args.name or not args.image:
        parser.error("scaffold requires --id, --name and --image")
    path = add(
        args.root,
        BenchmarkDraft(
            args.identifier,
            args.name,
            args.command_line,
            args.image,
            measurement=args.measurement,
            sweep=json.loads(args.sweep.read_text()) if args.sweep else None,
        ),
    )
    print(path)
    return 0


def main() -> int:
    """Run one verifier command from the current repository root."""
    parser = argparse.ArgumentParser(prog="sandbox-benchmark-verify")
    parser.add_argument(
        "command",
        choices=(
            "validate",
            "reproduce",
            "compare",
            "text",
            "literals",
            "rendered-text",
            "scaffold",
            "image-sizes",
        ),
    )
    parser.add_argument(
        "results", nargs="?", type=Path, help="your per-trial results, JSON Lines (compare only)"
    )
    parser.add_argument("--publication", help="publication id to compare with (compare only)")
    parser.add_argument("--provider", help="provider or entry id in that publication (compare)")
    parser.add_argument("--test", help="test id in that publication (compare only)")
    parser.add_argument("--variant", help="variant id; read from your results when unset (compare)")
    parser.add_argument(
        "--summary",
        help="publication summary file or https URL; default is the public address (compare only)",
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--id", dest="identifier")
    parser.add_argument("--name")
    parser.add_argument("--command-line", default="true")
    parser.add_argument("--image")
    parser.add_argument("--measurement", choices=("command", "startup"), default="command")
    parser.add_argument(
        "--sweep", type=Path, help="one dimension's parameter, name, unit and image values"
    )
    parser.add_argument("--shape", type=Path, help="a runner block's shape JSON (scaffold only)")
    parser.add_argument("--draft", type=Path, help="the author's draft YAML (scaffold --shape)")
    parser.add_argument("--category", help="the methodology folder, e.g. 03-sandbox-operations")
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
    if command == "scaffold":
        return run_scaffold(args, parser)
    if command == "compare":
        return run_compare(args, parser)
    if command == "rendered-text":
        payload = json.loads(args.capture.read_text() if args.capture else sys.stdin.read())
        failures = rendered_capture_errors(payload)
        print(json.dumps({"errors": failures}))
        return int(bool(failures))
    if command in ("literals", "image-sizes"):
        return run_listing(command, args.root)
    if command == "text":
        warnings = text_warnings(args.root)
        if args.output:
            write_text_bundle(args.root, args.output)
        for warning in warnings:
            print(f"warning: {warning}", file=sys.stderr)
        mode = "report only" if args.text_mode == "report" else "enforce"
        print(f"text: {len(warnings)} warning(s); {mode}")
        return int(args.text_mode == "enforce" and bool(warnings))
    errors = validate(args.root) if command == "validate" else reproduce(args.root)
    if command == "validate":
        for warning in text_warnings(args.root):
            print(f"warning: {warning}", file=sys.stderr)
    for error in errors:
        print(error, file=sys.stderr)
    if not errors:
        print(f"{command}: ok")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
