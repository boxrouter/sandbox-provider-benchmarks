# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Load the canonical benchmark strings that source repositories must not repeat."""

import json
import re
from typing import Any

from sandbox_benchmark_verifier.catalog import _assets, load_methodology
from sandbox_benchmark_verifier.conventions import load_conventions
from sandbox_benchmark_verifier.name_refs import reader_strings
from sandbox_benchmark_verifier.vocabulary import retired_words
from sandbox_benchmark_verifier.yaml_load import load_yaml

_COUNT_OR_SIZE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:vCPU|GiB|MiB|GB|MB|threads?|trials?|passes?|jobs?|steps?|files?|items?|workers?)\b",
    re.IGNORECASE,
)


def _name_terms(root: Any) -> list[dict[str, str]]:
    terms = []
    names = load_yaml((root / "names" / "names.yaml").read_text(encoding="utf-8"))
    for row in names.get("names", []):
        for field, category in (("name", "name"), ("id", "id"), ("label", "name")):
            value = row.get(field)
            if isinstance(value, str) and len(value.strip()) > 1:
                terms.append(
                    {"category": category, "value": value.strip(), "source": "names/names.yaml"}
                )
    return terms


def _method_terms() -> list[dict[str, str]]:
    terms = []
    for record in load_methodology():
        document = record.document
        procedure = document.get("procedure", {})
        trials = procedure.get("trials") if isinstance(procedure, dict) else None
        if isinstance(trials, int):
            terms.append(
                {"category": "size_or_count", "value": f"{trials} trials", "source": record.path}
            )
        environment = document.get("environment", {})
        size = environment.get("size", {}) if isinstance(environment, dict) else {}
        if isinstance(size, dict):
            vcpu = size.get("vcpu")
            memory = size.get("memory_gib")
            if isinstance(vcpu, (int, float)) and isinstance(memory, (int, float)):
                terms.append(
                    {
                        "category": "size_or_count",
                        "value": f"{vcpu:g} vCPU / {memory:g} GiB",
                        "source": record.path,
                    }
                )
        for metric in document.get("metrics", []):
            if not isinstance(metric, dict):
                continue
            for field in ("quantity", "per", "unit", "direction"):
                value = metric.get(field)
                # The field's own value is a code (`time_score`, `ms`, `lower_is_better`) that a
                # source names by value, so it is an id; only its wording below is reader text.
                if isinstance(value, str) and len(value.strip()) > 1:
                    terms.append({"category": "id", "value": value.strip(), "source": record.path})
                if field == "direction" and isinstance(value, str):
                    terms.append(
                        {
                            "category": "metric",
                            "value": value.replace("_", " ").capitalize(),
                            "source": record.path,
                        }
                    )
    return terms


def _parameter_terms(root: Any) -> list[dict[str, str]]:
    terms = []
    for path in sorted((root / "parameters").rglob("*.json")):
        try:
            content = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        source = path.relative_to(root).as_posix()
        for _, value in reader_strings(content):
            for match in _COUNT_OR_SIZE.finditer(value):
                terms.append(
                    {"category": "size_or_count", "value": match.group(0), "source": source}
                )
    return terms


def _retired_terms(root: Any) -> list[dict[str, str]]:
    """The words reader text never contains (rule ``reader.vocabulary``), for a consumer to scan."""
    return [
        {
            "category": "retired_word",
            "value": word,
            "source": "conventions/text-conventions.yaml",
        }
        for word in retired_words(load_conventions(root))
    ]


def canonical_literal_terms() -> list[dict[str, str]]:
    """Return sourced labels, metric wording, sizes, counted parameters and retired words."""
    root = _assets()
    unique = {
        (term["category"], term["value"], term["source"]): term
        for term in (
            *_name_terms(root),
            *_method_terms(),
            *_parameter_terms(root),
            *_retired_terms(root),
        )
    }
    return sorted(
        unique.values(), key=lambda row: (row["value"].casefold(), row["category"], row["source"])
    )
