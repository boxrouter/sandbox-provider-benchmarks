# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Independence rules: each benchmark answers every rule of the common methodology it inherits.

The common methodology (from ``sbs.common`` 1.0.7) lists the rules that keep one trial's result
from depending on another trial. A benchmark meets each rule or is an exception with a reason;
a missing answer fails validation, as does text that contradicts its stated concurrency.
"""

import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from sandbox_benchmark_verifier.name_refs import reader_strings
from sandbox_benchmark_verifier.names import load_tables
from sandbox_benchmark_verifier.versions import version_key

COMMON_ID = "sbs.common"
FIRST_COMMON_VERSION = (1, 0, 7)
BENCHMARK_CATEGORIES = frozenset({"workload", "sandbox_operation", "machine_performance"})
ONE_AT_A_TIME = re.compile(r"\btrials?\b[^.]{0,20}\bat a time\b", re.IGNORECASE)


def rule_ids(common: Mapping[str, Any]) -> list[str]:
    """The ids of the rules a common methodology version states."""
    independence = common.get("independence")
    rules = independence.get("rules", []) if isinstance(independence, Mapping) else []
    return [str(rule["id"]) for rule in rules if isinstance(rule, Mapping) and "id" in rule]


def answer_errors(
    document: Mapping[str, Any], commons: Mapping[tuple[int, ...], Mapping[str, Any]]
) -> list[str]:
    """Problems with one benchmark document's answers to the rules of the common it inherits."""
    name = f"{document.get('id')}@{document.get('version')}"
    inherited = document.get("inherits")
    inherited_version = (
        version_key(inherited["version"])
        if isinstance(inherited, Mapping) and inherited.get("id") == COMMON_ID
        else None
    )
    answers = document.get("independence_answers")
    if inherited_version is None or inherited_version < FIRST_COMMON_VERSION:
        return [f"{name}: independence_answers needs sbs.common 1.0.7 or later"] if answers else []
    common = commons.get(inherited_version)
    if common is None:
        return [f"{name}: inherits an sbs.common version this repository does not hold"]
    expected = rule_ids(common)
    if not isinstance(answers, Mapping):
        return [f"{name}: independence_answers is missing; answer every rule of sbs.common"]
    errors = [
        f"{name}: independence rule {rule} is unanswered"
        for rule in expected
        if rule not in answers
    ]
    errors.extend(
        f"{name}: independence answer {rule} is not a rule"
        for rule in answers
        if rule not in expected
    )
    procedure = document.get("procedure")
    concurrency = procedure.get("concurrency") if isinstance(procedure, Mapping) else None
    parallel = concurrency.get("trials_in_parallel") if isinstance(concurrency, Mapping) else None
    if isinstance(parallel, int) and parallel > 1:
        errors.extend(
            f"{name}: text says one trial runs at a time but trials_in_parallel is {parallel}"
            for _, text in reader_strings(document)
            if ONE_AT_A_TIME.search(text)
        )
    return errors


def independence_errors(root: Path, documents: Sequence[tuple[Path, Any]]) -> list[str]:
    """Check the answers of every benchmark document under ``methodology/``.

    Every document inheriting an independence-aware common methodology must answer its rules,
    and the newest version of each document the names table binds to a test must inherit one.
    """
    methodology = [
        document
        for path, document in documents
        if path.is_relative_to(root / "methodology") and isinstance(document, Mapping)
    ]
    commons = {
        version_key(document["version"]): document
        for document in methodology
        if document.get("id") == COMMON_ID and "version" in document
    }
    errors: list[str] = []
    for document in methodology:
        if document.get("category") in BENCHMARK_CATEGORIES:
            errors.extend(answer_errors(document, commons))
    try:
        tables = load_tables(root)
    except (OSError, ValueError, yaml.YAMLError):
        return errors  # name_errors reports unreadable tables
    mapped = tables.aliases.get("document", {})
    bound = {
        str(mapped.get(row["document"], row["document"]))
        for row in tables.names
        if row.get("kind") == "test" and "document" in row
    }
    newest: dict[str, Mapping[str, Any]] = {}
    for document in methodology:
        canonical = str(mapped.get(document.get("id"), document.get("id")))
        if canonical in bound and "version" in document:
            known = newest.get(canonical)
            if known is None or version_key(document["version"]) > version_key(known["version"]):
                newest[canonical] = document
    for canonical, document in sorted(newest.items()):
        inherited = document.get("inherits")
        version = version_key(inherited["version"]) if isinstance(inherited, Mapping) else ()
        if document.get("category") in BENCHMARK_CATEGORIES and version < FIRST_COMMON_VERSION:
            errors.append(
                f"{canonical}: newest version {document['version']} does not answer the "
                "independence rules; inherit sbs.common 1.0.7 or later"
            )
    return errors
