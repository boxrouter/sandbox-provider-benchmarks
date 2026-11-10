# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Which clock a benchmark's time metrics read, derived from their formulas.

The common methodology's `clocks` define the terms: `runtime` is the sandbox's own clock; `start`,
`end`, `total` and `latency` are the job host's monotonic clock around a call, so they include
the network round trip between the job host and the provider. Nothing is tagged by hand: a
document includes network latency exactly when one of its millisecond metrics' formulas uses a
job-host term.
"""

import re
from collections.abc import Mapping
from typing import Any

JOB_HOST_TERMS = frozenset({"start", "end", "total", "latency"})
_STEP_ARGUMENT = re.compile(r"\b(start|end|total|runtime|latency)\s*\(\s*[A-Za-z0-9_|-]+\s*\)")
_WORD = re.compile(r"[A-Za-z_]+")


def formula_terms(formula: str) -> frozenset[str]:
    """The terms and folds a formula uses, without its step names."""
    return frozenset(_WORD.findall(_STEP_ARGUMENT.sub(r"\1", formula)))


def includes_network_latency(document: Mapping[str, Any]) -> bool:
    """True when a time metric of the document is read, at least in part, on the job host."""
    return any(
        formula_terms(metric["formula"]) & JOB_HOST_TERMS
        for metric in document.get("metrics") or []
        if metric.get("unit") == "ms" and isinstance(metric.get("formula"), str)
    )
