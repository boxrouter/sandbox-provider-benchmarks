# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Independence rules: each benchmark answers every rule of the common methodology it inherits.

The common methodology lists the rules that keep one trial's result
from depending on another trial. A benchmark meets each rule or is an exception with a reason;
a missing answer fails validation, as does text that contradicts its stated concurrency.
"""

import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from sandbox_benchmark_verifier.catalog import COMMON_ID
from sandbox_benchmark_verifier.name_refs import reader_strings

BENCHMARK_CATEGORIES = frozenset({"workload", "sandbox_operation", "machine_performance"})
ONE_AT_A_TIME = re.compile(r"\btrials?\b[^.]{0,20}\bat a time\b", re.IGNORECASE)


def rule_ids(common: Mapping[str, Any]) -> list[str]:
    """The ids of the rules the common methodology states."""
    independence = common.get("independence")
    rules = independence.get("rules", []) if isinstance(independence, Mapping) else []
    return [str(rule["id"]) for rule in rules if isinstance(rule, Mapping) and "id" in rule]


def answer_errors(document: Mapping[str, Any], common: Mapping[str, Any]) -> list[str]:
    """Problems with one benchmark document's answers to the rules of the common."""
    name = str(document.get("id"))
    expected = rule_ids(common)
    answers = document.get("independence_answers")
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

    Every benchmark document must answer each rule of the common methodology.
    """
    methodology = [
        document
        for path, document in documents
        if path.is_relative_to(root / "methodology") and isinstance(document, Mapping)
    ]
    common = next((document for document in methodology if document.get("id") == COMMON_ID), None)
    if common is None:
        return []
    return [
        error
        for document in methodology
        if document.get("category") in BENCHMARK_CATEGORIES
        for error in answer_errors(document, common)
    ]
