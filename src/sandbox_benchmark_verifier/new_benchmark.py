# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Requirements for a benchmark added after the benchmark template: every step names its
operation, the document answers how its result informs a decision and how it can be wrong, and
the procedure stays inside the common limits.

``contracts/pre-template-ids.json`` lists the ids the catalog held when these requirements
started; those documents are exempt. A document with a procedure, not ``planned``, whose id is
not on that list must meet every requirement.
"""

import json
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from sandbox_benchmark_verifier.catalog import load_methodology
from sandbox_benchmark_verifier.yaml_load import load_yaml

OPERATIONS = Path("contracts/operations.yaml")
PRE_TEMPLATE_IDS = Path("contracts/pre-template-ids.json")
SIZES = ((2, 4), (4, 8))
MAX_AT_ONCE = 10
MIN_RANKED_SUCCESSES = 20
LONGEST_COMMAND_SECONDS = 60


def operations(root: Path) -> dict[str, dict[str, Any]]:
    """The operations a step can name, keyed by name, from ``contracts/operations.yaml``."""
    data: dict[str, dict[str, dict[str, Any]]] = load_yaml(
        (root / OPERATIONS).read_text(encoding="utf-8")
    )
    return data["operations"]


def pre_template_ids(root: Path) -> dict[str, Any]:
    """The ``{ids}`` list of documents that predate the requirements."""
    data: dict[str, Any] = json.loads((root / PRE_TEMPLATE_IDS).read_text(encoding="utf-8"))
    return data


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _step_errors(
    steps: list[Mapping[str, Any]], known: Mapping[str, Mapping[str, Any]]
) -> Iterator[str]:
    if any("operation" not in step for step in steps):
        yield "every step needs an operation"
    for step in steps:
        operation = step.get("operation")
        if operation is not None and operation not in known:
            yield f"step {step.get('id')} has unknown operation {operation}"
        if operation == "commands.run" and step.get("timeout_seconds", 0) > LONGEST_COMMAND_SECONDS:
            yield (
                f"step {step.get('id')} runs commands.run for more than "
                f"{LONGEST_COMMAND_SECONDS} s; use process.start"
            )


def _gate_errors(
    document: Mapping[str, Any],
    steps: list[Mapping[str, Any]],
    known: Mapping[str, Mapping[str, Any]],
) -> Iterator[str]:
    required = (document.get("capabilities") or {}).get("required") or []
    used = dict.fromkeys(step["operation"] for step in steps if step.get("operation") in known)
    for operation in used:
        gate = known[operation]["gate"]
        if gate is not None and gate not in required:
            yield f"operation {operation} needs capability {gate}"


def requirement_errors(
    document: Mapping[str, Any], known: Mapping[str, Mapping[str, Any]]
) -> list[str]:
    """What a new benchmark document lacks, one plain phrase per broken requirement."""
    procedure = document.get("procedure") or {}
    steps = procedure.get("steps") or []
    errors = list(_step_errors(steps, known))
    interpretation = document.get("interpretation") or {}
    errors.extend(
        f"interpretation.{field} is empty"
        for field in ("informs", "what_to_do")
        if not _text(interpretation.get(field))
    )
    if not document.get("validity_checks"):
        errors.append("needs at least one validity check")
    if not _text((document.get("source") or {}).get("licence")):
        errors.append("source.licence is missing")
    if (document.get("cost") or {}).get("method") != "provider_reported_or_priced":
        errors.append("cost.method must be provider_reported_or_priced")
    concurrency = procedure.get("concurrency") or {}
    parallel, at_once = concurrency.get("trials_in_parallel"), concurrency.get("sandboxes_at_once")
    if isinstance(parallel, int) and isinstance(at_once, int) and parallel * at_once > MAX_AT_ONCE:
        errors.append(f"more than {MAX_AT_ONCE} sandboxes at once")
    size = (document.get("environment") or {}).get("size") or {}
    if (size.get("vcpu"), size.get("memory_gib")) not in SIZES:
        errors.append("size must be 2 vCPU / 4 GiB or 4 vCPU / 8 GiB")
    errors.extend(_gate_errors(document, steps, known))
    ranking = (document.get("statistics") or {}).get("ranking") or {}
    minimum = ranking.get("min_successes")
    if ranking.get("ranked") and isinstance(minimum, int) and minimum < MIN_RANKED_SUCCESSES:
        errors.append(f"ranked with fewer than {MIN_RANKED_SUCCESSES} successes")
    return errors


def new_benchmark_errors(root: Path) -> list[str]:
    """Errors for every new benchmark document and for stale ids on the pre-template list.

    A tree without the pre-template list (a partial test tree) has no requirements to check.
    """
    if not (root / PRE_TEMPLATE_IDS).is_file():
        return []
    catalog = [item.document for item in load_methodology(root)]
    present = {document.get("id") for document in catalog}
    exempt = set(pre_template_ids(root)["ids"])
    errors = [
        f"{PRE_TEMPLATE_IDS}: {identifier} is no longer in the catalog; remove it"
        for identifier in sorted(exempt - present)
    ]
    known = operations(root)
    for document in catalog:
        if (
            "procedure" not in document
            or document.get("status") == "planned"
            or document.get("id") in exempt
        ):
            continue
        where = f"{document.get('id')} {document.get('version')}: new benchmark"
        errors.extend(f"{where}: {what}" for what in requirement_errors(document, known))
    return errors
