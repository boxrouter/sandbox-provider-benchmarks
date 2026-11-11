# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Reader text must be true of the version it describes: no "unknown", no contradicted claim.

The reader copy is written once and read against every measured version. A sentence that holds
only for some versions carries a ``[[when ...]]`` gate (``reader_when``); this check reads the
compiled text of every version and fails what the gates missed:

* the word "unknown" anywhere in a method's text, and any fact of its reader definition whose value
  is "unknown" (a parameter the measured version does not state is left out, not drawn);
* "Trials run in parallel" or "up to N in parallel" where the version's ``trials_in_parallel`` is
  not above 1, or is not N;
* a trace set "recorded by" or "coming from" one named model where the version lists several
  variants.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from sandbox_benchmark_verifier.name_refs import reader_strings

_UNKNOWN = re.compile(r"\bunknown\b", re.IGNORECASE)
_PARALLEL = re.compile(r"\btrials (?:run|running) (?:in parallel|concurrently)\b", re.IGNORECASE)
_UP_TO = re.compile(r"\bup to (\d+) (?:in parallel|at once)\b", re.IGNORECASE)
_ONE_MODEL = re.compile(r"\b(?:recorded by|come from) Claude\b")
_NON_SUCCESS_TAIL = re.compile(
    r"\b(?:tail|p95|p5)\b[^.!?\n]*\bscheduled\b"
    r"|\bscheduled\b[^.!?\n]*\b(?:tail|p95|p5)\b"
    r"|\bwith \d+ trials\b[^.!?\n]*\b(?:tail|p95|p5)\b",
    re.IGNORECASE,
)
# Prose fields of a reader definition; they may say "unknown" in a sentence.
_NOT_FACTS = frozenset({"notes", "headline"})


def reader_claim_errors(bundle: Mapping[str, Any]) -> list[str]:
    """Where a method's compiled reader text, for some measured version, says what that version's
    own parameters contradict."""
    errors: list[str] = []
    for method, entry in bundle["reader"]["methods"].items():
        rows = bundle["methodologies"][entry["document"]]
        for version, row in rows.items():
            text = entry["by_version"].get(version, entry["text"])
            procedure = row["reader_definition"].get("procedure", {})
            parallel = procedure.get("concurrency", {}).get("trials_in_parallel")
            variants = len(procedure.get("variants", []))
            tail = row["reader_definition"].get("statistics", {}).get("tail", {})
            where = f"{method}@{version}"
            errors.extend(
                f"{where}: {fact} draws the value unknown"
                for fact, value in reader_strings(row["reader_definition"], skip=_NOT_FACTS)
                if value.strip().lower() == "unknown"
            )
            for path, sentence in reader_strings(text):
                if isinstance(tail.get("min_successes"), int) and _NON_SUCCESS_TAIL.search(
                    sentence
                ):
                    errors.append(
                        f"{where}: {path} bases a tail on trials without requiring successes; "
                        "statistics.tail.min_successes counts successes"
                    )
                # A shared methodology (Common, Pricing) states rules, which name an unknown
                # outcome and a cost without a provider record.
                if row["reader_definition"].get("category") != "common" and _UNKNOWN.search(
                    sentence
                ):
                    errors.append(f"{where}: {path} says unknown")
                if _PARALLEL.search(sentence) and not (isinstance(parallel, int) and parallel > 1):
                    errors.append(f"{where}: {path} says trials run in parallel; it is {parallel}")
                errors.extend(
                    f"{where}: {path} says up to {found[1]} at once; it is {parallel}"
                    for found in _UP_TO.finditer(sentence)
                    if int(found[1]) != parallel or int(found[1]) < 2
                )
                if _ONE_MODEL.search(sentence) and variants > 1:
                    errors.append(f"{where}: {path} names one model; the version lists {variants}")
    return errors
