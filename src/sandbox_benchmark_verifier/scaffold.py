# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""One command-workload scaffold, using the existing schemas and parameter digest."""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from sandbox_benchmark_verifier.catalog import common_document, default_context, load_methodology
from sandbox_benchmark_verifier.lifecycle_profile import SCHEMA
from sandbox_benchmark_verifier.lock import update as update_lock
from sandbox_benchmark_verifier.name_refs import canonical_name_map, name_reference_errors
from sandbox_benchmark_verifier.names import load_tables
from sandbox_benchmark_verifier.new_benchmark import operations
from sandbox_benchmark_verifier.parameters import tree_digest, workload_version


def independence_answers(trials_in_parallel: int = 1) -> dict[str, dict[str, str]]:
    """Answers to the common independence rules for a one-sandbox draft that runs
    ``trials_in_parallel`` trials of a provider at once."""
    meets = {
        "fresh_sandbox": "One fresh sandbox per trial; no command is repeated.",
        "equal_concurrency": "One trial at a time for every provider, the same number for each.",
        "shuffled_order": "The draft's position in each provider's chain is shuffled per run, "
        "with the seed recorded, and its trials run in the run's rounds.",
        "caches": "A fresh sandbox per trial from the stated image; the provider's image caches "
        "stay on and the workload states any cache it relies on.",
        "job_host": "Operation times come from the job host's monotonic clock; the job host "
        "serves one provider only.",
        "shared_provider_hosts": "One trial runs at a time per provider, so no other trial of "
        "the batch is in flight.",
        "empty_account": "The account is checked empty before the provider's chain, and the "
        "count of other live sandboxes is recorded at each batch's start and end.",
        "placement_facts": "Each trial records where it ran, with the job host's load over its "
        "window.",
        "run_check": "The run's independence check covers the pair on its main metric with the "
        "common thresholds.",
        "outliers": "Values are excluded only by the common outlier and physical-limit rule, "
        "with the reason recorded.",
    }
    if trials_in_parallel > 1:
        meets["equal_concurrency"] = (
            f"Up to {trials_in_parallel} trials at once for every provider, the same number "
            "for each."
        )
        meets["shared_provider_hosts"] = (
            f"Up to {trials_in_parallel - 1} other trials of the batch may be in flight on the "
            "provider's hosts; the number in flight is recorded for each trial."
        )
    return {rule: {"status": "meets", "text": text} for rule, text in meets.items()}


def reuse(root: Path, identifier: str, name: str) -> tuple[str, ...]:
    """Find an existing identity or canonical name before creating any files."""
    return tuple(
        sorted(
            {
                str(item.document["id"])
                for item in load_methodology(root)
                if item.document["id"] == identifier
                or str(item.document["name"]).casefold() == name.casefold()
            }
        )
    )


@dataclass(frozen=True)
class BenchmarkDraft:  # pylint: disable=too-many-instance-attributes
    """Typed scientific inputs; fields mirror the schema and command options."""

    identifier: str
    name: str
    command: str
    image: str
    version: str = "1.0.0"
    trials: int = 5
    timeout_s: int = 60
    measurement: str = "command"
    sweep: dict[str, Any] | None = None


def _workloads(request: BenchmarkDraft) -> list[dict[str, Any]]:
    workloads = []
    for item in (
        request.sweep["values"] if request.sweep else [{"value": None, "image": request.image}]
    ):
        value = format(Decimal(str(item["value"])).normalize(), "f") if request.sweep else "command"
        identity = "value-" + value.replace(".", "-") if request.sweep else "command"
        data: dict[str, Any] = {"command": request.command}
        if request.sweep:
            data["sweep"] = {
                "parameter": request.sweep["parameter"],
                "value": item["value"],
                "unit": request.sweep["unit"],
            }
        workload: dict[str, Any] = {
            "id": identity,
            "label": f"{value} {request.sweep['unit']}" if request.sweep else request.name,
            "data": data,
            "image": item["image"],
        }
        workload["version"] = workload_version(workload)
        workloads.append(workload)
    return workloads


def add(root: Path, request: BenchmarkDraft) -> Path:
    """Create a complete, independently expressible timed-command methodology.

    Only new files are written; an existing identity, name or file is refused.
    Publication permission is intentionally outside this scientific document.
    """
    if not re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", request.identifier):
        raise ValueError("new command identity must be a lowercase record id")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", request.version):
        raise ValueError("version must be semantic major.minor.patch")
    if (
        not request.name.strip()
        or not request.command.strip()
        or request.trials < 1
        or request.timeout_s < 1
    ):
        raise ValueError("name, command, positive trials and timeout are required")
    if not re.fullmatch(r"[^\s]+@sha256:[0-9a-f]{64}", request.image):
        raise ValueError("image must be pinned by sha256 digest")
    if request.measurement not in ("command", "startup"):
        raise ValueError("measurement must be command or startup")
    if matches := reuse(root, request.identifier, request.name):
        raise ValueError("reuse an existing methodology: " + ", ".join(matches))
    path = root / "methodology" / request.identifier / f"{request.version}.yaml"
    directory = root / "parameters" / request.identifier / request.version
    if path.exists() or directory.exists():
        raise ValueError("scaffold destination exists; edit the existing document in place")
    schema = json.loads((root / "schemas/methodology.schema.json").read_text())
    if request.sweep is not None:
        Draft202012Validator(
            {
                "$ref": "#/$defs/sweep",
                "$defs": {
                    **schema["$defs"],
                    "sweep": schema["properties"]["procedure"]["properties"]["sweep"],
                },
            }
        ).validate(request.sweep)
        values = request.sweep["values"]
        if len({Decimal(str(item["value"])) for item in values}) != len(values):
            raise ValueError("a sweep must not repeat a parameter value")
        if len({item["image"] for item in values}) != len(values):
            raise ValueError("an image sweep must pin a different image for each value")
    workloads = _workloads(request)
    boundary = (
        "The sandbox create request is dispatched."
        if request.measurement == "startup"
        else "The command request is dispatched."
    )
    finish = "The command exit status returns to the caller."
    document = {
        "id": request.identifier,
        "version": request.version,
        "effective": datetime.now(UTC).date().isoformat(),
        "name": request.name,
        "category": "workload",
        "notes": {
            "summary": "Time to the first command result in a fresh sandbox."
            if request.measurement == "startup"
            else "Elapsed time for the configured command.",
            "what_this_measures": "Time from sandbox create dispatch to the first command result."
            if request.measurement == "startup"
            else "Time from dispatching a command until its exit status returns.",
            "how_it_is_measured": (
                "One monotonic clock measures each fresh sandbox and command, without warm-up."
            ),
            "how_to_read": "Median time is shown in milliseconds; lower is better.",
            "cost_summary": "Provider charges for the complete sandbox lifetime.",
            "cost_how_to_read": "Median cost per run is shown in US dollars; lower is better.",
        },
        "metrics": [
            {
                "name": f"{request.measurement}_time",
                "unit": "ms",
                "direction": "lower_is_better",
                "quantity": "time",
                "per": None,
                "formula": "end(command) - start(create)"
                if request.measurement == "startup"
                else "total(command)",
            }
        ],
        "environment": {
            "size": {"vcpu": 2, "memory_gib": 4},
            "image": {"reference": request.image},
            "lifetime_seconds": request.timeout_s + 600,
        },
        "clocks": {"operation": "Monotonic clock around the command call."},
        "procedure": {
            "mode": "single",
            "trials": request.trials,
            "sandboxes_per_trial": 1,
            "concurrency": {"trials_in_parallel": 1, "sandboxes_at_once": 1},
            "warm_cold_policy": {"policy": "One fresh sandbox per trial; no command is repeated."},
            **({"sweep": request.sweep} if request.sweep else {}),
            "steps": [
                {
                    "id": "create",
                    "name": "{name:stage.create}",
                    "action": "Create one fresh sandbox.",
                    "timer": None,
                    "timeout_seconds": 300,
                },
                {
                    "id": "command",
                    "name": "Command",
                    "action": "Run the pinned command workload.",
                    "timer": {"start_event": boundary, "stop_event": finish},
                    "timeout_seconds": request.timeout_s,
                },
                {
                    "id": "destroy",
                    "name": "{name:stage.destroy}",
                    "action": "Destroy the sandbox and confirm cleanup.",
                    "timer": None,
                    "timeout_seconds": 300,
                },
            ],
        },
        "timing_boundaries": {"start": boundary, "stop": finish},
        "statistics": {
            "primary": "median",
            "ranking": {"ranked": True, "min_successes": request.trials},
        },
        "cost": {"method": "provider_reported_or_priced"},
        "interpretation": {
            "limits": [
                "The result includes command transport overhead and the declared workload only."
            ]
        },
        "execution": {"mode": "script", "inference": False},
        "inherits": default_context(root),
        "independence_answers": independence_answers(),
        "failure_policy": {
            "timeout_seconds": {"command": request.timeout_s},
            "retries": "No timed command is retried.",
            "success": "The command returns exit code zero.",
            "provider_errors": ["A sandbox or command API failure."],
            "benchmark_errors": ["Malformed workload or timing data."],
            "exclusions": [
                "Proven failures outside the provider remain recorded with their reason."
            ],
        },
    }
    protocol = {
        "schema": SCHEMA,
        "section": "workloads",
        "step": "command",
        "measurement": request.measurement,
    }
    _write(
        root, path, directory, document, {"workloads.json": workloads, "protocol.json": protocol}
    )
    return path


def _write(
    root: Path,
    path: Path,
    directory: Path,
    document: dict[str, Any],
    files: Mapping[str, Any],
) -> None:
    """Bind the parameter files to the document by digest, validate it, then write both."""
    schema = json.loads((root / "schemas/methodology.schema.json").read_text())
    with TemporaryDirectory(prefix="methodology-scaffold-") as temporary:
        staged = Path(temporary)
        for name, value in files.items():
            (staged / name).write_text(json.dumps(value, indent=2) + "\n")
        document["parameters"] = {
            "path": directory.relative_to(root).as_posix() + "/",
            "sha256": tree_digest(staged),
        }
        Draft202012Validator(schema).validate(document)
        directory.parent.mkdir(parents=True, exist_ok=True)
        directory.mkdir()
        for child in staged.iterdir():
            (directory / child.name).write_bytes(child.read_bytes())
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x") as output:
            output.write(
                "# SPDX-FileCopyrightText: BoxRouter, Inc.\n# SPDX-License-Identifier: CC-BY-4.0\n"
                + yaml.safe_dump(document, sort_keys=False, width=100)
            )


TRIALS = 40
TRIALS_IN_PARALLEL = 10
MODES = {"single": "single", "sequential": "sequential", "at_once": "concurrent"}
UNSTAGED = ("create", "destroy")


def shape_errors(root: Path, shape: Mapping[str, Any]) -> list[str]:
    """Why a shape is not one ``schemas/shape.schema.json`` and ``operations.yaml`` accept."""
    schema = json.loads((root / "schemas/shape.schema.json").read_text())
    errors = [
        f"shape {'/'.join(map(str, issue.absolute_path)) or '$'}: {issue.message}"
        for issue in Draft202012Validator(schema).iter_errors(shape)
    ]
    if errors:
        return errors
    known = operations(root)
    ids = [step["id"] for step in shape["steps"]]
    errors.extend(
        f"shape step {i} is repeated" for i in sorted({i for i in ids if ids.count(i) > 1})
    )
    for step in shape["steps"]:
        if step["operation"] not in known:
            errors.append(f"shape step {step['id']} has unknown operation {step['operation']}")
        if "ready_check" in step and step["operation"] not in ("create", "restore"):
            errors.append(f"shape step {step['id']}: ready_check is only for create and restore")
        if shape["launch"] == "single" and step["sandbox"] == "rest":
            errors.append(f"shape step {step['id']}: launch single has no sandbox after the first")
    return errors


def _sandboxes(shape: Mapping[str, Any], workloads: list[dict[str, Any]]) -> int:
    """Sandboxes per trial: one, 1 + the workload's restores, two, or the workload's sandboxes."""
    launch = shape["launch"]
    key = "sandboxes" if launch == "at_once" else "restores"
    if launch == "single":
        return 1
    if key not in shape["reads"]:
        if launch == "at_once":
            raise ValueError("an at_once shape must read the workload's sandboxes")
        return 2
    counts = {workload["data"][key] for workload in workloads}
    if len(counts) != 1 or not all(isinstance(count, int) and count >= 1 for count in counts):
        raise ValueError(f"every workload must give the same positive whole number of {key}")
    count: int = counts.pop()
    return count if launch == "at_once" else 1 + count


def _step_name(root: Path, identifier: str) -> str:
    """The placeholder of the canonical name the record id spells, a stage's first; else the
    record id in sentence case."""
    literal = identifier.replace("-", " ").capitalize()
    matches = sorted(
        (not key.startswith("stage."), key)
        for key, name in canonical_name_map(load_tables(root)).items()
        if name.casefold() == literal.casefold()
    )
    if matches and (
        matches[0][1].startswith("stage.") or name_reference_errors(literal, root=root)
    ):
        return f"{{name:{matches[0][1]}}}"
    if name_reference_errors(literal, root=root):
        raise ValueError(f"name shape step {identifier} in the draft's steps")
    return literal


def _in_sandboxes(where: str, sandboxes: int) -> dict[str, list[int]]:
    if where == "every":
        return {}
    return {"in_sandboxes": [1] if where == "first" else list(range(2, sandboxes + 1))}


def _lifetime(shape: Mapping[str, Any], steps: list[dict[str, Any]]) -> int:
    """The longest one sandbox can live: the timeouts of every step it runs, summed."""
    timeouts = [
        (s["sandbox"], m["timeout_seconds"]) for s, m in zip(shape["steps"], steps, strict=True)
    ]
    return max(
        sum(int(timeout) for where, timeout in timeouts if where in (sandbox, "every"))
        for sandbox in ("first", "rest")
    )


def _workloads_from_draft(
    shape: Mapping[str, Any], draft: Mapping[str, Any]
) -> list[dict[str, Any]]:
    workloads = []
    for item in draft["workloads"]:
        if sorted(item["data"]) != sorted(shape["reads"]):
            raise ValueError(f"workload {item['id']} data keys must equal the shape's reads")
        workload = {key: item[key] for key in ("id", "label", "data", "image") if key in item}
        workload["version"] = workload_version(workload)
        workloads.append(workload)
    return workloads


def _category(root: Path, folder: str) -> str:
    """The one category the documents in a catalog folder share."""
    found = {
        str(item.document.get("category"))
        for item in load_methodology(root)
        if item.path.startswith(f"methodology/{folder}/")
    }
    if len(found) != 1:
        raise ValueError(f"methodology/{folder} must hold documents of exactly one category")
    [category] = found
    return category


def _steps(
    root: Path, shape: Mapping[str, Any], draft: Mapping[str, Any], sandboxes: int
) -> list[dict[str, Any]]:
    known = operations(root)
    overrides = draft.get("steps", {})
    unknown = sorted(set(overrides) - {step["id"] for step in shape["steps"]})
    if unknown:
        raise ValueError("draft overrides unknown shape steps: " + ", ".join(unknown))
    steps = []
    for step in shape["steps"]:
        operation = known[step["operation"]]
        override = overrides.get(step["id"], {})
        steps.append(
            {
                "id": step["id"].replace("-", "_"),
                "name": override.get("name", _step_name(root, step["id"])),
                "operation": step["operation"],
                "action": override.get("action", operation["action"]),
                "timer": dict(operation["timer"]) if step["timed"] else None,
                "timeout_seconds": operation["timeout_seconds"],
                **({"repeats": True} if step.get("repeats") else {}),
                "record_steps": [step["id"]],
                **_in_sandboxes(step["sandbox"], sandboxes),
            }
        )
    return steps


def _shape_answers(trials_in_parallel: int, sandboxes: int) -> dict[str, dict[str, str]]:
    answers = independence_answers(trials_in_parallel)
    if sandboxes > 1:
        answers["fresh_sandbox"] = {
            "status": "exception",
            "text": f"Each trial uses {sandboxes} fresh sandboxes. The trial's sandboxes form one "
            "sample, and no sandbox or snapshot is shared across trials.",
        }
    return answers


def add_from_shape(  # pylint: disable=too-many-locals
    root: Path,
    identifier: str,
    folder: str,
    shape: Mapping[str, Any],
    draft: Mapping[str, Any],
) -> Path:
    """Write a new benchmark at version 1.0.0 from a runner block's shape and the author's draft.

    Only new files are written: ``methodology/<folder>/<id>.yaml`` and its parameter tree. Then
    the lock update rewrites ``methodology/released.lock`` so the new document has its row.
    """
    if not re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", identifier):
        raise ValueError("new benchmark identity must be a lowercase record id")
    if errors := shape_errors(root, shape):
        raise ValueError("; ".join(errors))
    draft_schema = json.loads((root / "schemas/scaffold-draft.schema.json").read_text())
    Draft202012Validator(draft_schema).validate(draft)
    if matches := reuse(root, identifier, str(draft["name"])):
        raise ValueError("reuse an existing methodology: " + ", ".join(matches))
    path = root / "methodology" / folder / f"{identifier}.yaml"
    directory = root / "parameters" / identifier / "1.0.0"
    if path.exists() or directory.exists():
        raise ValueError("scaffold destination exists; edit the existing document in place")
    category = _category(root, folder)
    workloads = _workloads_from_draft(shape, draft)
    sandboxes = _sandboxes(shape, workloads)
    at_once = shape["launch"] == "at_once"
    concurrency = {
        "trials_in_parallel": 1 if at_once else TRIALS_IN_PARALLEL,
        "sandboxes_at_once": sandboxes if at_once else 1,
    }
    steps = _steps(root, shape, draft, sandboxes)
    context = default_context(root)
    common = common_document([item.document for item in load_methodology(root)], context)
    gates = [operations(root)[step["operation"]]["gate"] for step in steps]
    document: dict[str, Any] = {
        "id": identifier,
        "version": "1.0.0",
        "effective": datetime.now(UTC).date().isoformat(),
        "name": draft["name"],
        "category": category,
        "notes": draft["notes"],
        "inherits": context,
        "metrics": draft["metrics"],
        "environment": {
            "size": {"vcpu": 2, "memory_gib": 4},
            "image": dict(common["environment"]["image"]),
            "lifetime_seconds": _lifetime(shape, steps),
        },
        "procedure": {
            "mode": MODES[shape["launch"]],
            "trials": TRIALS,
            "sandboxes_per_trial": sandboxes,
            "concurrency": concurrency,
            "warm_cold_policy": {
                "policy": "Every trial creates its own fresh sandboxes; nothing is reused across "
                "trials."
            },
            "steps": steps,
            "stages": [
                {"id": record, "name": step["name"], "record_steps": [record]}
                for step, shaped in zip(steps, shape["steps"], strict=True)
                if shaped["timed"] and shaped["operation"] not in UNSTAGED
                for record in step["record_steps"]
            ],
        },
        "timing_boundaries": {
            "start": "Each timed step's timer starts at the request its timer names.",
            "stop": "Each timed step's timer stops at the event its timer names.",
        },
        "statistics": {"primary": "median", "ranking": {"ranked": True, "min_successes": 20}},
        "cost": {"method": "provider_reported_or_priced"},
        "capabilities": {"required": list(dict.fromkeys(gate for gate in gates if gate))},
        "interpretation": draft["interpretation"],
        "validity_checks": draft["validity_checks"],
        "source": draft["source"],
        "execution": {"mode": "script", "inference": False},
        "independence_answers": _shape_answers(concurrency["trials_in_parallel"], sandboxes),
        "failure_policy": {
            "timeout_seconds": {step["id"]: step["timeout_seconds"] for step in steps},
            "retries": "No measured operation is retried; the first failed step ends the trial.",
            **draft["failure_policy"],
            "exclusions": [
                "A failure shown not to be the provider's; retain the trial with its reason."
            ],
        },
    }
    _write(root, path, directory, document, {"workloads.json": workloads})
    update_lock(root)
    return path
