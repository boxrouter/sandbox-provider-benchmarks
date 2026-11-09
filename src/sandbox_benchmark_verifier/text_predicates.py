# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Generic data-predicate interpreter; no convention rule IDs or slot names live here."""

from __future__ import annotations

import itertools
import json
import os
import re
from collections.abc import Callable, Mapping
from difflib import SequenceMatcher
from fnmatch import fnmatchcase
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

Result = tuple[bool, Any]
Operator = Callable[[Any, Mapping[str, Any]], Result]


def lookup(value: Any, path: str) -> Any:
    """Read a neutral binding, including arrays addressed with a wildcard."""
    if not path:
        return value
    key, _, tail = path.partition(".")
    if key == "*" and isinstance(value, (list, Mapping)):
        values = value.values() if isinstance(value, Mapping) else value
        found = [lookup(item, tail) for item in values]
        return [
            part
            for item in found
            if item is not None
            for part in (item if isinstance(item, list) else [item])
        ]
    return lookup(value.get(key), tail) if isinstance(value, Mapping) and key in value else None


def resolve(value: Any, bindings: Mapping[str, Any]) -> Any:
    if isinstance(value, Mapping):
        if set(value) == {"from"}:
            return lookup(bindings, value["from"])
        return {key: resolve(item, bindings) for key, item in value.items()}
    return [resolve(item, bindings) for item in value] if isinstance(value, list) else value


def condition(test: Mapping[str, Any], bindings: Mapping[str, Any]) -> bool:
    actual = lookup(bindings, test["field"])
    expected = resolve(test.get("value"), bindings)
    operations = {
        "eq": actual == expected,
        "ne": actual != expected,
        "exists": actual is not None,
        "in": actual in expected
        if isinstance(expected, list) or (isinstance(actual, str) and isinstance(expected, str))
        else False,
    }
    return bool(operations.get(test["op"], False))


def all_rules(value: Any) -> list[dict[str, Any]]:
    """Collect text and presentation rules through the same structural interface."""
    if isinstance(value, dict):
        if "id" in value and "predicate" in value:
            return [value]
        return [rule for item in value.values() for rule in all_rules(item)]
    return [rule for item in value for rule in all_rules(item)] if isinstance(value, list) else []


def compile_shape(shape: Mapping[str, Any]) -> dict[str, Any]:
    """Generate a JSON Schema for optional, ordered, typed parts from their data shape."""
    parts = shape["parts"]
    choices = []
    for count in range(shape.get("min_parts", 0), len(parts) + 1):
        for selected in itertools.combinations(parts, count):
            items = [
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [shape["tag"], *part["required"]],
                    "properties": {shape["tag"]: {"const": part["kind"]}, **part["properties"]},
                }
                for part in selected
            ]
            choices.append(
                {
                    "type": "array",
                    "minItems": count,
                    "maxItems": count,
                    "prefixItems": items,
                    "items": False,
                }
            )
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "oneOf": choices}


def readability_grade(text: str) -> float:
    """Approximate English Flesch–Kincaid grade; a writing-review signal, never a verdict."""
    words = re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", text)
    if not words:
        return 0.0
    syllables = 0
    for word in words:
        lower = word.lower()
        count = len(re.findall(r"[aeiouy]+", lower))
        if lower.endswith("e") and not lower.endswith(("le", "ye")) and count > 1:
            count -= 1
        syllables += max(1, count)
    sentences = max(1, len(re.findall(r"[.!?](?:\s|$)", text)))
    return round(max(0.0, 0.39 * len(words) / sentences + 11.8 * syllables / len(words) - 15.59), 2)


def _whole_token(literal: str) -> str:
    """A listed literal, matched only as a whole token: not inside a longer identifier or path."""
    return rf"(?<![\w./-]){re.escape(literal)}(?![\w/-]|\.\w)"


def _patterns(actual: Any, predicate: Mapping[str, Any]) -> Result:
    values = predicate.get("value")
    values = list(values.values()) if isinstance(values, Mapping) else values
    values = values if isinstance(values, list) else [values] if values else []
    flags = re.IGNORECASE if predicate.get("flags") == "i" else 0
    matches = []
    text = str(actual)
    for ignored in predicate.get("ignore_patterns", []):
        text = re.sub(ignored, "", text)
    for literal in predicate.get("ignore_literals", []):
        text = re.sub(_whole_token(literal), "", text)
    for value in values:
        pattern = (
            rf"(?<!\w){re.escape(str(value))}(?!\w)" if predicate.get("literal") else str(value)
        )
        if re.search(pattern, text, flags):
            matches.append(value)
    return not matches, matches


def _casing(actual: Any, predicate: Mapping[str, Any]) -> Result:
    text = str(actual)
    if predicate["value"] == "title":
        words = re.findall(r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9]*", re.sub(r"\([^)]*\)", "", text))
        bad = [
            word
            for index, word in enumerate(words)
            if word.islower() and (index == 0 or word not in predicate.get("small_words", []))
        ]
        return not bad, bad
    if predicate["value"] == "sentence":
        verb = re.search(predicate.get("predicate_pattern", r"(?!)"), text, re.I)
        return not (verb and verb.start() > 0 and verb.group()[0].isupper()), text
    return True, text


def _required(actual: Any, predicate: Mapping[str, Any]) -> Result:
    required = predicate.get("value") or []
    actual = actual if actual is not None else {}
    present = set(actual) if isinstance(actual, Mapping) else {item["slot"] for item in actual}
    missing = [
        key
        for key in required
        if key not in present or (isinstance(actual, Mapping) and not actual[key])
    ]
    return not missing, missing


def _order(actual: Any, predicate: Mapping[str, Any]) -> Result:
    order = predicate.get("value") or []
    present = list(dict.fromkeys(item["slot"] for item in actual if item["slot"] in order))
    return present == [key for key in order if key in present], present


def _unique(actual: Any, predicate: Mapping[str, Any]) -> Result:
    found = []
    for left, right in itertools.combinations(actual, 2):
        groups = predicate.get("compare_between")
        if groups and not any(
            any(fnmatchcase(first["slot"], pattern) for pattern in groups[0])
            and any(fnmatchcase(second["slot"], pattern) for pattern in groups[1])
            for first, second in ((left, right), (right, left))
        ):
            continue
        a, b = (" ".join(str(item.get("text", "")).casefold().split()) for item in (left, right))
        ratio = SequenceMatcher(None, a, b).ratio() if a and b else 0
        if ratio >= predicate["value"]:
            found.append({"slots": [left["slot"], right["slot"]], "similarity": ratio})
    return not found, found


def _vocabulary(actual: Any, predicate: Mapping[str, Any]) -> Result:
    bad = [
        word
        for word, allowed in predicate["value"].items()
        if predicate.get("context") not in allowed
        and re.search(rf"(?i)\b{re.escape(word)}\b", str(actual))
    ]
    return not bad, bad


def _occurrences(actual: Any, predicate: Mapping[str, Any]) -> Result:
    texts = (
        [
            str(row.get("text", ""))
            for row in actual
            if row["slot"] not in predicate.get("ignore_slots", [])
        ]
        if isinstance(actual, list)
        else [str(actual)]
    )
    values = predicate.get("literals") or []
    literals = (
        list(values.values())
        if isinstance(values, Mapping)
        else values
        if isinstance(values, list)
        else [values]
    )
    counts = {
        value: len(re.findall(rf"(?i)(?<!\w){re.escape(value)}(?!\w)", " ".join(texts)))
        for value in literals
        if value
    }
    return all(count <= predicate["value"] for count in counts.values()), counts


def _schema(actual: Any, predicate: Mapping[str, Any]) -> Result:
    errors = list(
        Draft202012Validator(predicate["schema"], format_checker=FormatChecker()).iter_errors(
            actual
        )
    )
    return not errors, [error.message for error in errors]


def _template(actual: Any, predicate: Mapping[str, Any]) -> Result:
    try:
        expected = predicate["template"].format(**predicate["value"])
    except (KeyError, TypeError):
        return False, "missing template values"
    return actual == expected, expected


def _capital(actual: Any, predicate: Mapping[str, Any]) -> Result:
    text = str(actual)
    pieces = (
        re.split(predicate["boundary_pattern"], text) if predicate.get("each_sentence") else [text]
    )
    pattern = r"[A-Z0-9]" if predicate.get("allow_number") else r"[A-Z]"
    # A sentence may start with a tool name its owner writes in lowercase (fio), exactly as owned.
    names = [rf"{re.escape(name)}(?![\w-])" for name in predicate.get("allowed_starts") or []]
    bad = [
        piece
        for piece in pieces
        if not re.match(pattern, piece) and not any(re.match(name, piece) for name in names)
    ]
    return not bad, bad


OPERATORS: dict[str, Operator] = {
    "min_chars": lambda text, p: (len(str(text)) >= p["value"], len(str(text))),
    "max_chars": lambda text, p: (len(str(text)) <= p["value"], len(str(text))),
    "max_sentences": lambda text, p: (
        len(re.split(p["boundary_pattern"], str(text))) <= p["value"],
        len(re.split(p["boundary_pattern"], str(text))),
    ),
    "starts_with_capital": _capital,
    "ends_with_period": lambda text, _p: (
        bool(re.search(r"(?<!\.)\.$", str(text))),
        str(text)[-2:],
    ),
    "required_pattern": lambda text, p: (
        bool(re.search(p["value"], str(text), re.I if p.get("flags") == "i" else 0)),
        str(text),
    ),
    "forbidden_pattern": _patterns,
    "casing": _casing,
    "equals": lambda actual, p: (actual == p["value"], actual),
    "required": _required,
    "order": _order,
    "unique_across_slots": _unique,
    "allowed_vocabulary": _vocabulary,
    "max_occurrences": _occurrences,
    "schema": _schema,
    "template": _template,
    "separator": lambda text, p: (
        len(str(text).split(p["value"])) == p["parts"]
        and all(str(part).strip() for part in str(text).split(p["value"])),
        str(text),
    ),
    "allowed_values": lambda actual, p: (actual in p["value"], actual),
    "mapped_value": lambda actual, p: (
        actual is not None and p["mapping"].get(actual) == p["value"],
        actual,
    ),
    "readability_grade": lambda text, p: (
        readability_grade(str(text)) <= p["value"],
        readability_grade(str(text)),
    ),
}


def safe_text(value: Any, policy: Mapping[str, Any]) -> str:
    """Keep diagnostics useful without leaking boundary-blocklisted source text."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    reporting = policy["reporting"]
    configured = os.environ.get(reporting["blocklist_environment"], "")
    if not configured and reporting["redact_without_blocklist"]:
        return "<text hidden: boundary blocklist unavailable>"
    for line in configured.splitlines():
        if line.strip():
            pattern = line[3:] if line.startswith("re:") else re.escape(line)
            text = re.sub(pattern, "[redacted]", text, flags=re.I)
    return " ".join(text.split())[: reporting["offending_text_max_chars"]]


def _applicable(rule: Mapping[str, Any], bindings: Mapping[str, Any]) -> bool:
    record, slot = bindings["record"], bindings["slot"]
    selector = resolve(rule.get("applies_to", {}), bindings)
    patterns = selector.get("slots", ["*"])
    if rule.get("target") == "scope":
        if patterns != ["*"] and not any(
            fnmatchcase(row["slot"], pattern)
            for row in bindings["scope"].get("slots", [])
            for pattern in patterns
        ):
            return False
    elif not any(fnmatchcase(record["slot"], pattern) for pattern in patterns):
        return False
    if any(fnmatchcase(record["slot"], pattern) for pattern in selector.get("exclude_slots", [])):
        return False
    if selector.get("forms") and slot.get("form") not in selector["forms"]:
        return False
    return not rule.get("when") or condition(rule["when"], bindings)


def _operands(
    rule: Mapping[str, Any], bindings: Mapping[str, Any]
) -> tuple[dict[str, Any], Any, Operator | None]:
    predicate = resolve(rule["predicate"], bindings)
    selector = resolve(rule.get("applies_to", {}), bindings)
    actual = bindings["record"].get("text", "")
    if isinstance(actual, str):
        actual = " ".join(actual.split())
    if rule.get("target") == "scope":
        actual = [
            row
            for row in bindings["scope"].get("slots", [])
            if any(fnmatchcase(row["slot"], pattern) for pattern in selector.get("slots", ["*"]))
        ]
    if predicate["kind"] == "allowed_vocabulary":
        predicate["context"] = bindings["scope"].get("context", "panel")
    if predicate["kind"] == "schema" and "shape" in predicate:
        shape = bindings["conventions"]["shapes"][predicate["shape"]]
        predicate["schema"] = compile_shape(resolve(shape, bindings))
    actual = predicate.get(
        "subject", predicate.get("value") if predicate["kind"] == "schema" else actual
    )
    operation = OPERATORS.get(predicate["kind"])
    if predicate["kind"] == "schema" and "schema" not in predicate:
        operation = None
    if predicate.get("evidence_required"):
        evidence = bindings["scope"].get("evidence", {})
        operation = operation if rule["id"] in evidence else None
        actual = evidence.get(rule["id"], actual)
    return predicate, actual, operation


def check_program(scope: Mapping[str, Any], policy: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Execute all applicable data predicates and return severity-tagged diagnostics."""
    scope = {"context": "panel", "slot_rules": policy["slots"], **scope}
    violations = []
    records = list(scope.get("slots", []))
    for rule in all_rules(policy):
        subjects = (
            [{"slot": "@scope", "text": records}] if rule.get("target") == "scope" else records
        )
        for record in subjects:
            bindings = {
                "scope": scope,
                "record": record,
                "slot": scope["slot_rules"].get(record["slot"], {}),
                "conventions": policy,
            }
            if not _applicable(rule, bindings):
                continue
            predicate, actual, operation = _operands(rule, bindings)
            passed, observed = (
                operation(actual, predicate) if operation else (False, "NO CHECK YET")
            )
            if not passed:
                violations.append(
                    _diagnostic(rule, record, (predicate, actual, observed, operation), policy)
                )
    return violations


def _diagnostic(
    rule: Mapping[str, Any],
    record: Mapping[str, Any],
    evaluation: tuple[Any, Any, Any, Any],
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    predicate, actual, observed, operation = evaluation
    hint = rule["text"]
    for key, value in {**predicate, "observed": observed}.items():
        hint = hint.replace("{" + key + "}", str(value))
    return {
        "rule_id": rule["id"],
        "slot": record["slot"],
        "offending_text": safe_text(actual, policy),
        "message": safe_text(hint, policy)
        if os.environ.get(policy["reporting"]["blocklist_environment"])
        else rule["text"],
        "severity": rule["severity"],
        "observed": observed
        if isinstance(observed, (int, float, bool))
        else safe_text(observed, policy),
        "status": "fail" if operation else "NO CHECK YET",
    }


def format_violation(violation: Mapping[str, Any]) -> str:
    return (
        f"{violation['rule_id']} [{violation['slot']}]: "
        f"{violation['offending_text']!r} — {violation['message']}"
    )
