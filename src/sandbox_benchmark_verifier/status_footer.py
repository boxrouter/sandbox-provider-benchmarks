# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Checks of the status footer under a graph: its template, its clauses and the owner's statuses."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from sandbox_benchmark_verifier.name_refs import reader_strings
from sandbox_benchmark_verifier.yaml_load import load_yaml, load_yaml_all

OWNER = Path("contracts/text/1.0.0.json")
CONVENTIONS = Path("conventions/text-conventions.yaml")
_PLACEHOLDER = re.compile(r"\{[^{}]*\}")


def ranking_reason_errors(
    criteria: Mapping[str, Any], reason: str, provider: str = ""
) -> list[str]:
    """Validate a publication reason fragment without rewriting its facts."""
    rules = criteria["ranking_status_footer"]["reason_rules"]
    errors: list[str] = []
    if len(reason) > rules["max_length"]:
        errors.append("ranking reason exceeds its length bound")
    if reason.casefold() in {value.casefold() for value in rules["forbidden_generic_reasons"]}:
        errors.append("ranking reason must state the published cause")
    if any(
        re.search(rf"(?i)\b{re.escape(word)}\b", reason) for word in rules["forbidden_unit_words"]
    ):
        errors.append("ranking reason must use the test's ranking unit")
    if rules["provider_name"] == "omit" and provider:
        # A class qualifier belongs to the provider label, outside the reason.
        name = provider.partition(" (")[0]
        if re.search(rf"(?i)(?<!\w){re.escape(name)}(?!\w)", reason):
            errors.append("ranking reason repeats its provider name")
    if rules["terminal_punctuation"] == "omit" and reason.endswith((".", ";", ":")):
        errors.append("ranking reason must be a fragment without terminal punctuation")
    return errors


def status_label_pattern(label: str) -> str:
    """A bar row may not carry a status label; a unit slot in a label matches any unit word."""
    parts = re.split(r"(\{[Uu]nit\})", label)
    body = "".join(
        rf"(?:{re.escape(part)}|[a-z]+)" if re.fullmatch(r"\{[Uu]nit\}", part) else re.escape(part)
        for part in parts
    )
    return rf"(?i)(?<!\w){body}(?!\w)"


def _group_clause(
    criteria: Mapping[str, Any], head: str, row: Mapping[str, Any]
) -> tuple[str, list[str]]:
    """A provider's clause on a line that groups statuses, and what is wrong with it.

    A provider whose line is shared by several statuses names its own status in lower case
    (the line's own status says nothing more); the reason sentence belongs in the drill-down.
    A provider with no status of its own may carry a short note (a count of its trials).
    """
    statuses = criteria["ranking_statuses"]
    own = row.get("status")
    if not own:
        return str(row.get("reason") or ""), []
    definition = statuses.get(own)
    if not definition or definition.get("footer_group", own) != head:
        return "", [f"{own} does not belong on the {head} footer line"]
    errors = []
    if row.get("reason"):
        errors.append("a footer line gives the status only; the reason belongs in the drill-down")
    short = str(definition["label"]).lower()
    return ("" if short == str(statuses[head]["label"]).lower() else short), errors


def _clauses(
    criteria: Mapping[str, Any], status: str, providers: list[dict[str, Any]]
) -> tuple[list[str], list[str]]:
    """Each provider's parenthetical on a footer line, and what is wrong with them."""
    policy = criteria["ranking_status_footer"]
    grouped = any(
        item.get("footer_group") == status for item in criteria["ranking_statuses"].values()
    )
    clauses: list[str] = []
    errors: list[str] = []
    for row in providers:
        clause, row_errors = (
            _group_clause(criteria, status, row) if grouped else (str(row.get("reason") or ""), [])
        )
        clauses.append(clause)
        errors.extend(row_errors)
        if grouped and len(clause) > policy["group_clause_max_length"]:
            errors.append("a status line gives a short clause, not a sentence")
        errors.extend(ranking_reason_errors(criteria, clause, row["provider"]))
    return clauses, [f"chart.status-footer: {error}" for error in errors]


def ranking_footer_errors(criteria: Mapping[str, Any], slots: list[dict[str, Any]]) -> list[str]:
    policy = criteria.get("ranking_status_footer", {})
    errors: list[str] = []
    seen: set[str] = set()
    for slot in slots:
        if slot["slot"] != policy.get("footer_slot"):
            continue
        status = slot.get("status")
        definition = criteria["ranking_statuses"].get(status)
        if not definition or not definition["footer"]:
            errors.append("chart.status-footer: unknown or ranked status")
            continue
        incomplete = status in policy.get("incomplete_statuses", [])
        label = definition["label"]
        if label in seen:
            errors.append("chart.status-footer: each status needs one combined footer line")
        seen.add(label)
        providers = slot.get("providers", [])
        if not providers or any(not row.get("provider") for row in providers):
            errors.append("chart.status-footer: missing provider")
            continue
        if definition.get("reason") is False and any(row.get("reason") for row in providers):
            errors.append(f"chart.status-footer: the {label} line names its providers only")
        clauses, clause_errors = _clauses(criteria, str(status), providers)
        errors.extend(clause_errors)
        # The owner's closing sentence is on the line exactly when its names open a drill-down.
        expected = policy["incomplete_footer_template" if incomplete else "template"].format(
            marker=policy.get("incomplete_marker", ""),
            label=label,
            open_hint=policy["open_hint"] if slot.get("opens_details") else "",
            providers=policy["separator"].join(
                policy["provider_template"].format(
                    provider=row["provider"],
                    reason_clause=policy["reason_template"].format(reason=clause) if clause else "",
                )
                for row, clause in zip(providers, clauses, strict=True)
            ),
        )
        if slot.get("text") != expected:
            errors.append("chart.status-footer: differs from the owned status footer template")
    return errors


def status_details_errors(criteria: Mapping[str, Any]) -> list[str]:
    """An optional status policy flag (details, reason) is a boolean."""
    return [
        f"{status}: status.{flag} must be a boolean"
        for status, item in criteria.get("ranking_statuses", {}).items()
        for flag in ("details", "reason")
        if flag in item and not isinstance(item[flag], bool)
    ]


def footer_group_errors(criteria: Mapping[str, Any]) -> list[str]:
    """The contract's statuses that share a footer line name a line of their own, and say how."""
    statuses = criteria["ranking_statuses"]
    errors = [
        f"{status}: footer_group must name a footer status that is its own line"
        for status, item in statuses.items()
        if item.get("footer_group") is not None
        and (
            item["footer_group"] == status
            or not statuses.get(item["footer_group"], {}).get("footer")
            or "footer_group" in statuses[item["footer_group"]]
        )
    ]
    if criteria["ranking_status_footer"].get("group_clause") != "own_label_lowercase":
        errors.append("a grouped footer line names each provider's own status in lower case")
    return errors


def provider_notes_errors(criteria: Mapping[str, Any], slots: list[dict[str, Any]]) -> list[str]:
    """The line naming the providers that have a note of their own: names, then the owner hint."""
    policy = criteria["ranking_status_footer"]
    errors: list[str] = []
    for slot in slots:
        if slot["slot"] != policy["provider_notes_slot"]:
            continue
        providers = slot.get("providers", [])
        if not providers or any(not row.get("provider") for row in providers):
            errors.append(f"{slot['slot']}: missing provider")
            continue
        if any(row.get("reason") or row.get("status") for row in providers):
            errors.append(
                f"{slot['slot']}: a provider is named alone; its note is in the drill-down"
            )
        expected = policy["names_template"].format(
            providers=policy["separator"].join(row["provider"] for row in providers),
            open_hint=policy["open_hint"] if slot.get("opens_details") else "",
        )
        if slot.get("text") != expected:
            errors.append(f"{slot['slot']}: differs from the owned provider notes template")
    return errors


def _owned(relative: Path, location: str) -> bool:
    """The owner's own labels, and a conventions slot's name, which names the slot after its id
    ("not-displayed" is "Not Displayed" as "tooltip" is "Tooltip") and is never drawn."""
    if relative == OWNER:
        # The capabilities panel's group label names a capability the provider lacks, not a
        # ranking status, so it has its own string with the same words.
        return location == "provider_capabilities.not_supported" or location.startswith(
            ("ranking_statuses.", "ranking_status_footer.retired_labels")
        )
    return relative == CONVENTIONS and bool(re.fullmatch(r"slots\.[^.]+\.name", location))


def _owner_labels(owner: Mapping[str, Any]) -> dict[str, str | None]:
    """Each label the owner writes with its status, and each retired label with None. "Ranked" is
    an ordinary word ("ranked" links its statistic) and is not checked."""
    labels: dict[str, str | None] = {
        item["label"]: status
        for status, item in owner.get("ranking_statuses", {}).items()
        if isinstance(item, Mapping)
        and isinstance(item.get("label"), str)
        and item["label"] != "Ranked"
        and "{" not in item["label"]
    }
    retired = owner.get("ranking_status_footer", {}).get("retired_labels", [])
    labels.update({label: None for label in retired if "{" not in label})
    return labels


def _source_strings(root: Path) -> Iterator[tuple[Path, str, str]]:
    """Every string of the contracts and the conventions with its file and location."""
    sources = [p for p in sorted((root / "contracts").rglob("*")) if p.is_file()]
    sources += [root / CONVENTIONS] if (root / CONVENTIONS).is_file() else []
    for source in sources:
        if source.suffix not in {".json", ".yaml", ".yml"}:
            continue
        relative = source.relative_to(root)
        raw = source.read_text(encoding="utf-8")
        if source.suffix == ".json":
            loaded: Any = json.loads(raw)
        else:
            loaded = load_yaml(raw) if relative == CONVENTIONS else list(load_yaml_all(raw))
        for location, text in reader_strings(loaded):
            yield relative, location, text


def status_label_owner_errors(root: Path) -> list[str]:
    """A ranking status label exists once, at content.ranking_statuses; every other place reads it
    through ``{param:content.ranking_statuses.<status>.label}``.

    Contracts and conventions fail on a string that is a label in any casing, with or without its
    punctuation ("Not Displayed:"), and contracts also on a label typed inside a longer string in
    the owner's casing. A retired label is never re-typed. Text inside a placeholder is not typed.
    The owner writes each label in Title Case, and the conventions' ``naming.label_catalogs``
    table checks that (a sentence-case label fails ``label_case_errors``); a heading that names a
    status reads it like any other place.
    """
    path = root / OWNER
    if not path.is_file():
        return []
    labels = _owner_labels(json.loads(path.read_text(encoding="utf-8")))
    errors: list[str] = []
    for relative, location, text in _source_strings(root):
        if _owned(relative, location):
            continue
        plain = _PLACEHOLDER.sub(" ", text)
        whole = plain.strip().rstrip(".:;").casefold()
        for label, status in labels.items():
            inside = relative != CONVENTIONS and re.search(
                rf"(?<!\w){re.escape(label)}(?!\w)", plain
            )
            if whole != label.casefold() and not inside:
                continue
            hint = (
                "it is retired and must not be re-typed"
                if status is None
                else f"read it through {{param:content.ranking_statuses.{status}.label}}"
            )
            errors.append(f"{relative}.{location}: types the status label {label!r}; {hint}")
    return errors


_CAP = re.compile(r"(?i)\bconcurren\w*|\bat once\b|\baccount\b")
_NOT_OFFERED = re.compile(r"(?i)(?<![a-z])not[ _]offered(?![a-z])")
_SENTENCE = re.compile(r"(?<=[.;])\s+")


def concurrency_cap_status_errors(root: Path) -> list[str]:
    """A sentence about a concurrency cap names the Account Limit status, never Not Offered,
    in the methodology documents, the reader text and the contracts.

    Not Offered says no plan of the provider offers the option; a burst above the limit of
    BoxRouter's own account with the provider is that account's limit (``account_limit``).
    """
    sources = [
        *sorted((root / "methodology").rglob("*.yaml")),
        *sorted((root / "reader").glob("*.yaml")),
        *sorted((root / "contracts").rglob("*.json")),
    ]
    errors: list[str] = []
    for source in sources:
        raw = source.read_text(encoding="utf-8")
        loaded: Any = json.loads(raw) if source.suffix == ".json" else list(load_yaml_all(raw))
        for location, text in reader_strings(loaded):
            for sentence in _SENTENCE.split(text):
                if _NOT_OFFERED.search(sentence) and _CAP.search(sentence):
                    errors.append(
                        f"{source.relative_to(root)}.{location}: a concurrency cap is the "
                        "account's limit (account_limit), not not offered"
                    )
    return errors
