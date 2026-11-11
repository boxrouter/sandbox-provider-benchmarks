# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Which clock a benchmark's time metrics read, derived from their formulas.

The common methodology's `clocks` define the terms: `runtime` is the sandbox's own clock; `start`,
`end`, `total` and `latency` are the job host's monotonic clock around a call, so they include
the network round trip between the job host and the provider. Nothing is tagged by hand: a
document includes network latency exactly when one of its millisecond metrics' formulas uses a
job-host term, or a time metric its chart bindings derive adds a `request-elapsed` term.
"""

import re
from collections.abc import Mapping
from typing import Any

from sandbox_benchmark_verifier.chart_bindings import chart_bindings

JOB_HOST_TERMS = frozenset({"start", "end", "total", "latency"})
JOB_HOST_KIND = "request-elapsed"
"""A chart binding's term kind for a call timed on the job host."""
_STEP_ARGUMENT = re.compile(r"\b(start|end|total|runtime|latency)\s*\(\s*[A-Za-z0-9_|-]+\s*\)")
_WORD = re.compile(r"[A-Za-z_]+")


def formula_terms(formula: str) -> frozenset[str]:
    """The terms and folds a formula uses, without its step names."""
    return frozenset(_WORD.findall(_STEP_ARGUMENT.sub(r"\1", formula)))


def derived_term_kinds(document: Mapping[str, Any]) -> list[str]:
    """The kinds of the terms that the document's derived time metrics add up, from its chart
    bindings (`request-elapsed` on the job host, `command-runtime` on the sandbox's clock)."""
    return [
        kind
        for binding in chart_bindings()["bindings"]
        if binding["document"] == document.get("id")
        for metric in (binding.get("derived_metrics") or {}).values()
        if metric.get("unit") == "ms"
        for kind in (metric.get("terms") or {}).values()
    ]


def includes_network_latency(document: Mapping[str, Any]) -> bool:
    """True when a time metric of the document, its own or one its chart bindings derive (such as
    a pipeline time that adds up its steps), is read at least in part on the job host."""
    return any(
        formula_terms(metric["formula"]) & JOB_HOST_TERMS
        for metric in document.get("metrics") or []
        if metric.get("unit") == "ms" and isinstance(metric.get("formula"), str)
    ) or JOB_HOST_KIND in derived_term_kinds(document)
