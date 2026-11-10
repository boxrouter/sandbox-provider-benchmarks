# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Executable reader-text criteria exported with the owned text bundle."""

from __future__ import annotations

import json
import re
from collections.abc import Collection, Mapping
from difflib import SequenceMatcher
from typing import Any

from sandbox_benchmark_verifier.catalog import _assets
from sandbox_benchmark_verifier.conventions import (
    compatibility_errors,
    compatibility_exports,
    load_conventions,
)
from sandbox_benchmark_verifier.cost_split import cost_split_errors
from sandbox_benchmark_verifier.drill_down_rows import drill_down_row_errors
from sandbox_benchmark_verifier.image_sizes import byte_size_text, standard_image_bytes
from sandbox_benchmark_verifier.lock import read_lock
from sandbox_benchmark_verifier.name_refs import canonical_name_map, resolve_name_references
from sandbox_benchmark_verifier.names import load_tables
from sandbox_benchmark_verifier.option_hovers import option_hover_errors
from sandbox_benchmark_verifier.placeholders import (
    PLACEHOLDER_OPENERS,
    parse_placeholders,
    resolve_template,
)
from sandbox_benchmark_verifier.public_provenance import RULE_ID as PUBLIC_PROVENANCE_RULE
from sandbox_benchmark_verifier.reader_concepts import (
    _template_documents,
    hover_source_errors,
    hover_templates,
    reasons,
    shared_concepts,
)
from sandbox_benchmark_verifier.scope_kinds import scope_kinds
from sandbox_benchmark_verifier.stage_hovers import (
    HOVER_ROLES,
    boundary_source_errors,
    stage_hover_errors,
)
from sandbox_benchmark_verifier.status_footer import (
    footer_group_errors,
    provider_notes_errors,
    ranking_footer_errors,
    status_details_errors,
    status_label_pattern,
)
from sandbox_benchmark_verifier.substitution import substitution_errors
from sandbox_benchmark_verifier.subtitles import (
    rendered_direction_errors,
    score_descriptions,
    score_fact_errors,
    template_errors,
    workload_subtitles,
)
from sandbox_benchmark_verifier.text_predicates import lookup
from sandbox_benchmark_verifier.timing import load_timing_contract
from sandbox_benchmark_verifier.versions import newest

# A clock belongs to a step's timer, which the stage hover renders; keyed text never restates it.
CLOCK_CLAUSE = re.compile(r"(?i)\b(?:job-host|sandbox|monotonic)\s+clock\b|\bclock\s+measures\b")

# Criteria roots the conventions own; consumers read them under the same keys.
CONVENTION_ROOTS = ("fact_scope", "headings", "taxonomy_order")


def _standard_image_size(page: dict[str, Any]) -> None:
    """The standard image's size is the one its Image Build workload facts hold, never typed."""
    image = page["standard_image"]
    if "size" in image:
        raise ValueError("standard_image.size is read from the Image Build facts; delete it")
    (digest,) = image["versions"]
    size = standard_image_bytes(_assets(), digest)
    if size is None:
        raise ValueError(f"no Image Build workload has facts for the standard image {digest}")
    image["size"] = byte_size_text(size)


def content_criteria() -> dict[str, Any]:
    """Read the one packaged criterion set used by authors and consumers."""
    result: dict[str, Any] = json.loads(
        (_assets() / "contracts/text/1.0.0.json").read_text(encoding="utf-8")
    )
    result["conventions"] = load_conventions()
    for root in CONVENTION_ROOTS:
        if root in result:
            raise ValueError(
                f"{root} is owned by the conventions; delete it from contracts/text/1.0.0.json"
            )
        result[root] = result["conventions"][root]
    result["public_source_provenance"] = next(
        rule["predicate"]
        for rule in result["conventions"]["rules"]
        if rule["id"] == PUBLIC_PROVENANCE_RULE
    )
    result["public_source_provenance"] = {
        **result["public_source_provenance"],
        **json.loads(
            (_assets() / "contracts/text/public-source-provenance.json").read_text(encoding="utf-8")
        ),
    }
    result["sentence_rule"] = result["conventions"]["sentence_rule"]
    result.update(compatibility_exports(result["conventions"]))
    explanations = _assets() / "contracts/text/asked-vs-measured.json"
    if explanations.is_file():
        result["fact_explanations"] = json.loads(explanations.read_text(encoding="utf-8"))
    result["drill_down_rows"] = json.loads(
        (_assets() / "contracts/text/drill-down-rows.json").read_text(encoding="utf-8")
    )
    result["fact_list"] = json.loads(
        (_assets() / "contracts/text/fact-list.json").read_text(encoding="utf-8")
    )
    result["methodology_page"] = json.loads(
        (_assets() / "contracts/text/methodology-page.json").read_text(encoding="utf-8")
    )
    _standard_image_size(result["methodology_page"])
    # The executable timing rules the runner reads, so reader text can read their values.
    result["timing"] = load_timing_contract()
    documents: dict[str, Mapping[str, Any]] = dict(newest(_template_documents(_assets()).values()))
    tables = load_tables()
    canonical_names = canonical_name_map(tables)
    # {param:...} references are resolved here, once; content.* parameters read the criteria.
    documents["content"] = result
    for key in ("fact_list", "statistics_selector", "methodology_page", "provider_capabilities"):
        result[key] = resolve_name_references(
            result[key], names=canonical_names, documents=documents
        )
    result["workload_subtitles"] = workload_subtitles()
    copy_path = _assets() / "contracts/text/index-copy.json"
    if copy_path.is_file():
        result["index_copy"] = resolve_name_references(
            json.loads(copy_path.read_text(encoding="utf-8")),
            names=canonical_names,
            documents=documents,
        )
        result["index_copy"]["descriptions"].update(
            score_descriptions(result["conventions"]["subtitle"], load_tables().names)
        )
    result["shared_concepts"] = shared_concepts(
        result.get("fact_explanations", {}), result.get("index_copy", {})
    )
    result["hover_templates"] = hover_templates(result)
    for code, entry in result["hover_templates"].items():
        entry["slot"] = f"tooltip.{code}"
        result["slots"][entry["slot"]] = {
            **result["slots"]["tooltip"],
            "form": entry["form"],
            "min_length": 1 if entry["form"] != "sentence" else 8,
        }
    result["reasons"] = reasons(result)
    for code, entry in result["reasons"].items():
        if "template" in entry:
            entry["slot"] = f"tooltip.reason.{code}"
            result["slots"][entry["slot"]] = {
                **result["slots"]["tooltip"],
                "form": entry["form"],
                "min_length": 1 if entry["form"] != "sentence" else 8,
            }
    result["canonical_order"] = [
        {key: row[key] for key in ("kind", "id", "parent") if key in row} for row in tables.names
    ]
    result["scope_kinds"] = scope_kinds(result["canonical_order"])
    status_policy = result["ranking_status_footer"]
    for status in result["ranking_statuses"].values():
        if "alias_of" in status:
            status.update(result["ranking_statuses"][status["alias_of"]])
        status.update(
            template=status_policy["explanation_template"],
            reason_source=status_policy["reason_source"],
        )
    result["slots"][status_policy["bar_row_slot"]]["forbidden_patterns"] = [
        status_label_pattern(label)
        for label in sorted(
            {status["label"] for status in result["ranking_statuses"].values()}
            | set(status_policy["retired_labels"])
        )
    ]
    resolved: dict[str, Any] = resolve_name_references(
        result, names=canonical_names, documents=documents, names_only=True
    )
    return resolved


def _public_source_criteria_errors(criteria: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    source_view = criteria.get("public_source_provenance", {}).get("registry_view", {})
    source_fields = {field["path"]: field for field in source_view.get("fields", [])}
    for path in ("id", "version", "licence.spdx"):
        if source_fields.get(path, {}).get("format") != "code":
            errors.append(
                f"public upstream {path} must be preserved as code, without date inference"
            )
    for path in ("project", "url", "licence.status", "licence.evidence_url"):
        if path not in source_fields:
            errors.append(f"public upstream registry view must preserve {path}")
    for path, field in source_fields.items():
        if not isinstance(field.get("label"), str) or not field["label"].strip():
            errors.append(f"public upstream {path} must have an owner-written label")
    return errors


def _methodology_page_criteria_errors(criteria: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    for key, label in criteria.get("methodology_page", {}).get("field_labels", {}).items():
        if not isinstance(label, str) or not label.strip() or label == key or "_" in label:
            errors.append(f"{key}: methodology labels must be owner-written, not schema keys")
    page = criteria.get("methodology_page", {})
    for key, value_format in page.get("field_formats", {}).items():
        if value_format not in {"code", "duration_seconds", "machine_size", "plain_number"}:
            errors.append(f"{key}: unsupported owned methodology value format")
    if "machine_size" in page.get("field_formats", {}).values() and not all(
        f"{{{name}}}" in str(page.get("machine_size", "")) for name in ("vcpu", "memory_gib")
    ):
        errors.append("machine_size must be a template with {vcpu} and {memory_gib}")
    errors.extend(_standard_image_errors(page.get("standard_image")))
    if any(key in page.get("byline", {}) for key in ("version", "effective")):
        errors.append("methodology pages must not show document versions or effective dates")
    for identifier, formula in page.get("formulas", {}).items():
        tex = formula.get("tex")
        symbols = [row.get("symbol") for row in formula.get("variables", [])]
        if (
            not formula.get("aria_label")
            or not isinstance(tex, str)
            or not tex.strip()
            or not symbols
        ):
            errors.append(
                f"{identifier}: formulas require spoken text, a TeX expression and variables"
            )
            continue
        # A listed variable stands in its own formula: not as part of a longer name or a command.
        missing = [
            symbol
            for symbol in symbols
            if not isinstance(symbol, str)
            or not re.search(rf"(?<![A-Za-z\\]){re.escape(symbol)}(?![A-Za-z_^])", tex)
        ]
        if missing:
            errors.append(
                f"{identifier}: every variable must be written exactly as its formula writes "
                f"it, subscripts included: {missing}"
            )
    errors.extend(_subtraction_errors(page.get("formulas", {})))
    return errors + _symbol_meaning_errors(page.get("formulas", {}))


_SUBTRACTION = re.compile(r"[A-Za-z](?:\s*-\s*[A-Za-z])+")


def _only_subtracts(tex: str) -> bool:
    """Every statement of the expression only subtracts symbols: end minus start."""
    statements = [part for part in re.split(r";|\\quad", tex) if part.strip()]
    for statement in statements:
        if statement.count("=") != 1:
            return False
        right = statement.split("=", 1)[1]
        right = re.sub(r"\\mathrm\{[^{}]*\}|\\,|\\ ", "", right)
        right = re.sub(r"_\{[^{}]*\}|_\w", "", right)
        if not _SUBTRACTION.fullmatch(right.strip()):
            return False
    return bool(statements)


def _subtraction_errors(formulas: Mapping[str, Any]) -> list[str]:
    """A test's formula is not a bare end-minus-start subtraction (the page says it in words);
    Common's formulas are exempt."""
    return [
        f"{identifier}: a test formula that only subtracts a start from an end is not shown"
        for identifier, formula in formulas.items()
        if formula.get("tests") != ["sbs.common"]
        and isinstance(formula.get("tex"), str)
        and _only_subtracts(formula["tex"])
    ]


def _symbol_meaning_errors(formulas: Mapping[str, Any]) -> list[str]:
    """A symbol means one thing on a page: the formulas shown together never give it two
    definitions."""
    errors: list[str] = []
    meanings: dict[str, dict[str, str]] = {}
    for identifier, formula in formulas.items():
        for page in {identifier, *formula.get("tests", [])}:
            seen = meanings.setdefault(page, {})
            for row in formula.get("variables", []):
                symbol, definition = row.get("symbol"), row.get("definition")
                if seen.setdefault(symbol, definition) != definition:
                    errors.append(f"{page}: symbol {symbol} has two meanings ({identifier})")
    return errors


def _standard_image_errors(image: Any) -> list[str]:
    """The standard image is public: its page, version labels and pull wording are owner text."""
    if not isinstance(image, Mapping):
        return ["methodology pages require the standard_image presentation"]
    errors: list[str] = []
    if not str(image.get("registry_page", "")).startswith("https://"):
        errors.append("standard_image.registry_page must be an https link")
    for digest, version in image.get("versions", {}).items():
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest) or not str(version).strip():
            errors.append(f"standard_image.versions: {digest} needs a sha256 digest and a version")
    labels = image.get("labels", {})
    if set(labels) != {"digest", "version", "registry", "pull"} or not all(labels.values()):
        errors.append("standard_image.labels need digest, version, registry and pull wording")
    for key in ("reference_path", "size", "registry_link", "copy", "copied"):
        if not str(image.get(key, "")).strip():
            errors.append(f"standard_image.{key} is required")
    return errors


def _hover_errors(criteria: Mapping[str, Any]) -> list[str]:
    """Hover templates, reasons and selector descriptions: sources, roles and reader rules."""
    errors: list[str] = []
    errors.extend(hover_source_errors())
    for table in ("hover_templates", "reasons"):
        for code, entry in criteria.get(table, {}).items():
            if "template" in entry and entry.get("role") not in HOVER_ROLES:
                errors.append(f"{code}: a hover template declares its role {sorted(HOVER_ROLES)}")
    for row in load_tables().names:
        description = row.get("selector", {}).get("description")
        if description is not None:
            errors.extend(
                f"{row['id']}: selector description: {error}"
                for error in slot_errors(description, criteria["slots"]["tooltip"])
            )
    for code, entry in criteria.get("reasons", {}).items():
        if "template" not in entry:
            continue  # a code that says nothing: not reader information
        template = entry["template"]
        if set(re.findall(r"\{([a-z_]+)\}", template)) != set(entry.get("runtime_fields", [])):
            errors.append(f"{code}: reason fields must match declared runtime fields")
        example = re.sub(r"\{[a-z_]+\}", "Value", template)
        errors.extend(
            f"{code}: {error}"
            for error in slot_errors(example, criteria["slots"][entry["slot"]], generated=True)
        )
        errors.extend(
            f"{code}: {error}"
            for error in reader_literal_errors(
                entry.get("authored_template", template), criteria, entry["runtime_fields"]
            )
        )
    return errors


def _hover_template_errors(criteria: Mapping[str, Any]) -> list[str]:
    """Each hover template declares its runtime fields, says no clock and reads as its slot form."""
    errors: list[str] = []
    for code, entry in criteria.get("hover_templates", {}).items():
        template = entry.get("template", "")
        fields = set(re.findall(r"\{([a-z_]+)\}", template))
        if fields != set(entry.get("runtime_fields", [])):
            errors.append(f"{code}: hover template fields must match declared runtime fields")
        if CLOCK_CLAUSE.search(template):
            errors.append(
                f"{code}: a hover does not restate a clock; the timer of the step says it, once"
            )
        rule = criteria["slots"][entry["slot"]]
        example = re.sub(r"\{[a-z_]+\}", "Value", template)
        # A hover that is one owner field by reference (a stage description) is that field's text.
        if not re.fullmatch(r"\{param:[^{}]+\}", entry.get("authored_template", template)):
            errors.extend(
                f"{code}: {error}" for error in slot_errors(example, rule, generated=True)
            )
        errors.extend(
            f"{code}: {error}"
            for error in reader_literal_errors(
                entry.get("authored_template", template), criteria, entry["runtime_fields"]
            )
        )
    return errors


def criteria_errors(criteria: Mapping[str, Any]) -> list[str]:
    """Validate source extensions before exposing them to any consumer."""
    errors = compatibility_errors(criteria)
    errors.extend(_public_source_criteria_errors(criteria))
    errors.extend(_methodology_page_criteria_errors(criteria))
    errors.extend(substitution_errors(criteria))
    errors.extend(cost_split_errors(criteria))
    errors.extend(drill_down_row_errors(criteria))
    errors.extend(template_errors(criteria["conventions"]["subtitle"]))
    errors.extend(score_fact_errors(criteria["fact_list"]))
    for code, entry in criteria.get("fact_explanations", {}).items():
        if "label" in entry and (
            not entry["label"] or len(entry["label"]) > criteria["slots"]["title"]["max_length"]
        ):
            errors.append(f"{code}: missing or overlong label")
        fields = set(re.findall(r"\{([a-z_]+)\}", entry.get("template", "")))
        if fields != set(entry.get("runtime_fields", [])):
            errors.append(f"{code}: template fields must match declared runtime fields")
        rule = {
            **criteria["slots"]["tooltip"],
            "max_sentences": next(
                item["predicate"]["max_sentences"]
                for item in criteria["conventions"]["presentation"]["rules"]
                if item["id"] == "fact-secondary-line"
            ),
        }
        example = re.sub(r"\{[a-z_]+\}", "Value", entry.get("template", ""))
        errors.extend(f"{code}: {error}" for error in slot_errors(example, rule, generated=True))
    for code, label in criteria.get("fact_list", {}).items():
        if not isinstance(label, str) or not label.strip():
            errors.append(f"{code}: missing fact-list label")
    calendar = criteria.get("conventions", {}).get("presentation", {}).get("calendar_date")
    if (
        not isinstance(calendar, Mapping)
        or not calendar.get("locale")
        or not calendar.get("options")
    ):
        errors.append("calendar dates require one owner locale and format")
    elif set(re.findall(r"\{([^{}]+)\}", calendar.get("range_template", ""))) != {"from", "to"}:
        errors.append("calendar date ranges require the from and to fields")
    errors.extend(_hover_template_errors(criteria))
    errors.extend(_hover_errors(criteria))
    policy = criteria["ranking_status_footer"]
    for status, binding in policy["reason_templates"].items():
        template = policy.get(binding["field"], "")
        fields = set(re.findall(r"\{([a-z_]+)\}", template))
        if not template or fields != set(binding["runtime_fields"]):
            errors.append(f"{status}: reason template fields must match declared runtime fields")
        if "provider" in fields or "class" in fields:
            errors.append(f"{status}: reason template must omit provider and class names")
    errors.extend(footer_group_errors(criteria))
    errors.extend(status_details_errors(criteria))
    errors.extend(_public_trial_errors(criteria))
    errors.extend(_reader_entry_errors(criteria))
    return errors


def _public_trial_errors(criteria: Mapping[str, Any]) -> list[str]:
    policy = criteria["ranking_status_footer"]
    if (
        "failing_often" in criteria["ranking_statuses"]
        or "failure_reason_template" in policy
        or "github_actions_bar_failures" in criteria.get("hover_templates", {})
        or {"fact_excluded", "fact_burst_failed"} & criteria.get("fact_list", {}).keys()
    ):
        return [
            "public trial presentation must contain successful samples and blocked reasons only"
        ]
    return []


def _reader_entry_errors(criteria: Mapping[str, Any]) -> list[str]:
    rules = criteria.get("reader_entries", {})
    errors: list[str] = []
    seen: dict[str, str] = {}
    for code, entry in criteria.get("shared_concepts", {}).items():
        if any(not entry.get(field) for field in rules.get("required_fields", [])):
            errors.append(f"{code}: missing required reader-entry field")
        text = " ".join(str(entry.get(field, "")) for field in ("name", "meaning", "example"))
        if len(text) > rules.get("max_length", 280):
            errors.append(f"{code}: reader entry exceeds length bound")
        if text.count(";") > rules.get("max_semicolons", 1):
            errors.append(f"{code}: too many semicolons")
        if entry.get("source") not in rules.get("source_badges", []):
            errors.append(f"{code}: unknown source badge")
        for pattern in criteria["conventions"]["banned_patterns"]["internal_ids"]:
            if re.search(pattern, text):
                errors.append(f"{code}: internal identifier in reader text")
        errors.extend(f"{code}: {error}" for error in _reader_prose_errors(text, criteria))
        for field in ("meaning", "example"):
            if field not in entry:
                continue
            errors.extend(
                f"{code}.{field}: {error}"
                for error in slot_errors(entry.get(field, ""), criteria["slots"]["details.setup"])
            )
        meaning = " ".join(str(entry.get("meaning", "")).casefold().split())
        if meaning and meaning in seen:
            errors.append(f"{code}: duplicate definition of {seen[meaning]}")
        seen[meaning] = code
    return errors


def _reader_prose_errors(text: str, criteria: Mapping[str, Any]) -> list[str]:
    return [
        "reader prose describes the interface or a status flag"
        for pattern in criteria.get("reader_prose", {}).get("forbidden_patterns", [])
        if re.search(pattern, text)
    ]


def _similarity_errors(values: Mapping[str, str], criteria: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    threshold = criteria.get("uniqueness", {}).get("similarity_threshold", 1.0)
    for slot, text in values.items():
        if not slot.startswith("details."):
            continue
        for other in criteria.get("uniqueness", {}).get("compare_details_with", []):
            compared = values.get(other, "")
            if (
                compared
                and SequenceMatcher(
                    None, text.casefold().split(), compared.casefold().split()
                ).ratio()
                >= threshold
            ):
                errors.append(f"{slot}: near-duplicate of {other}")
    return errors


def _shared_definition_errors(values: Mapping[str, str], criteria: Mapping[str, Any]) -> list[str]:
    threshold = criteria.get("uniqueness", {}).get("similarity_threshold", 1.0)
    return [
        f"{slot}: definition belongs to shared concept {code}; use a link"
        for slot, text in values.items()
        if slot.startswith("details.")
        for code, entry in criteria.get("shared_concepts", {}).items()
        if "meaning" in entry
        and SequenceMatcher(
            None, text.casefold().split(), entry["meaning"].casefold().split()
        ).ratio()
        >= threshold
    ]


def slot_rule(criteria: Mapping[str, Any], slot: str, kind: str | None = None) -> dict[str, Any]:
    """Resolve a slot rule, including explicitly owned literal templates."""
    rule = dict(criteria["slots"][slot])
    for template in criteria.get("literal_templates", {}).values():
        if template["slot"] == slot and kind in template["panel_kinds"]:
            rule.update(form="literal", const=lookup(criteria, template["source"]))
    return rule


def is_literal_template(
    value: Any, category: Any, *, criteria: Mapping[str, Any] | None = None
) -> bool:
    """Recognize exact source references without relaxing other sentence rules."""
    source = content_criteria() if criteria is None else criteria
    return any(
        template["category"] == category
        and value in (template["reference"], lookup(source, template["source"]))
        for template in source.get("literal_templates", {}).values()
    )


def slot_errors(
    value: str,
    slot: Mapping[str, Any],
    *,
    generated: bool = False,
    proper_names: Collection[str] | None = None,
) -> list[str]:
    """Validate resolved text against a slot's form and bounds.

    A sentence may start with a tool name its owner writes in lowercase (reader.capital-start
    allowed_starts), exactly as owned: `proper_names` is that list, the conventions' by default.
    """
    errors: list[str] = []
    names = load_conventions()["proper_names"] if proper_names is None else proper_names
    owned_start = re.compile("|".join(rf"{re.escape(name)}(?![\w-])" for name in names) or "(?!)")
    text = " ".join(value.split())
    if not slot["min_length"] <= len(text) <= slot["max_length"]:
        errors.append(f"length must be {slot['min_length']}..{slot['max_length']} characters")
    if slot["form"] == "literal" and text != slot.get("const"):
        errors.append("text differs from its owned literal template")
    if slot["form"] == "sentence":
        sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text)
        if len(sentences) > slot["max_sentences"]:
            errors.append(f"at most {slot['max_sentences']} sentences are allowed")
        for sentence in sentences:
            starts = re.match(r"[A-Z0-9]", sentence) or owned_start.match(sentence)
            if not starts or not sentence.endswith("."):
                errors.append("a sentence must start with a capital and end with a period")
            if generated and not re.search(slot["predicate_pattern"], sentence, re.IGNORECASE):
                errors.append("sentence has no finite predicate")
    if any(opener in text for opener in PLACEHOLDER_OPENERS):
        errors.append("unresolved source placeholder")
    if any(re.search(pattern, text) for pattern in slot.get("forbidden_patterns", [])):
        errors.append("ranking status belongs only in the chart footer")
    return errors


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [text for item in value for text in _strings(item)]
    return []


def _literal_errors(
    template: str, names: Mapping[str, str], document: Mapping[str, Any]
) -> list[str]:
    errors = []
    plain = template
    for placeholder in reversed(parse_placeholders(template, allow_literal_braces=True)):
        plain = plain[: placeholder.start] + " " + plain[placeholder.end :]
    plain = re.sub(r"`[^`]*`|https?://\S+", " ", plain)
    for name in sorted(set(names.values()), key=len, reverse=True):
        # A quantity word is ordinary prose; multiword canonical identities are references.
        if (
            len(name.split()) < 2
            and not any(character.isdigit() for character in name)
            and not (not name.isupper() and any(character.isupper() for character in name[1:]))
        ):
            continue
        if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", plain):
            errors.append(f"canonical name {name!r} requires a name placeholder")
            plain = re.sub(re.escape(name), " ", plain, flags=0)
    if re.search(r"(?<![\w.])\d+(?:\.\d+)?(?![\w.])", plain):
        errors.append("numeric fact requires a parameter placeholder")
    for metric in document.get("metrics", []):
        unit = metric.get("unit")
        if unit and re.search(rf"(?<!\w){re.escape(unit)}(?!\w)", plain):
            errors.append(f"unit {unit!r} requires a unit placeholder")
        direction = str(metric.get("direction", "")).replace("_", " ")
        if direction and direction.casefold() in plain.casefold():
            errors.append("comparison direction requires a direction placeholder")
    return list(dict.fromkeys(errors))


def reader_literal_errors(
    template: str,
    criteria: Mapping[str, Any],
    runtime_fields: list[str] | None = None,
) -> list[str]:
    """Reject authored page facts while allowing declared data substitutions."""
    plain = template
    for placeholder in reversed(parse_placeholders(template, allow_literal_braces=True)):
        plain = plain[: placeholder.start] + " " + plain[placeholder.end :]
    errors: list[str] = []

    def runtime(match: re.Match[str]) -> str:
        if match.group(1) not in (runtime_fields or []):
            errors.append(f"undeclared runtime placeholder {match.group(1)}")
        return " "

    plain = re.sub(r"\{([a-z_][a-z0-9_]*)\}", runtime, plain)
    plain = re.sub(r"https?://\S+", " ", plain)
    for fact, pattern in criteria.get("fact_scope", {}).get("literal_patterns", {}).items():
        for match in re.finditer(pattern, plain):
            errors.append(f"literal {fact} fact {match.group(0)!r}; omit it or use a placeholder")
    return errors


def document_reader_literal_errors(
    document: Mapping[str, Any], criteria: Mapping[str, Any]
) -> list[str]:
    """Apply fact-literal rules to the reader paths declared by slot owners."""
    paths = set(criteria["placeholders"]["check_fields"])
    paths.update(
        f"procedure.{kind}[].{path}"
        for kind in ("variants", "stages")
        for path in criteria["placeholders"]["check_fields"]
        if path.startswith("notes.")
    )
    paths.update(
        slot["source"]
        for slot in criteria.get("hover_slots", {}).values()
        if slot.get("owner") == "methodology" and slot.get("source", "").startswith("procedure.")
    )
    return [
        f"{document['id']}@{document['version']}.{path}[{index}]: {error}"
        for path in sorted(paths)
        for index, template in enumerate(_reader_path_values(document, path.split(".")))
        for error in reader_literal_errors(template, criteria)
    ]


def _reader_path_values(value: Any, path: list[str]) -> list[str]:
    if not path:
        return _strings(value)
    if not isinstance(value, Mapping):
        return []
    segment = path[0]
    child = value.get(segment.removesuffix("[]"))
    if segment.endswith("[]"):
        return [text for item in child or [] for text in _reader_path_values(item, path[1:])]
    return _reader_path_values(child, path[1:])


def document_content_errors(
    document: Mapping[str, Any],
    *,
    documents: Mapping[str, Mapping[str, Any]],
    names: Mapping[str, str],
    criteria: Mapping[str, Any] | None = None,
) -> list[str]:
    """Check the current reader revision, leaving historical versions immutable."""
    source = content_criteria() if criteria is None else criteria
    return [
        *_source_slot_errors(document, documents, names, source),
        *_method_page_errors(document, source),
        *_source_similarity_errors(document, documents, names, source),
        *_coverage_errors(document, documents, names, source),
        *document_reader_literal_errors(document, source),
        *stage_hover_errors(document, limit=source["slots"]["tooltip"]["max_length"]),
        *boundary_source_errors(document),
        *(
            []
            if f"{document['id']}@{document['version']}" in read_lock(_assets())
            else option_hover_errors(document, limit=source["slots"]["tooltip"]["max_length"])
        ),
    ]


def _panel_kind(document: Mapping[str, Any]) -> str | None:
    """The kind a document's panel text is checked as: a frontier, another index, or none."""
    if document.get("category") != "index":
        return None
    return "time_vs_cost" if document["id"] == "time-vs-cost" else "index"


def _source_slot_errors(
    document: Mapping[str, Any],
    documents: Mapping[str, Mapping[str, Any]],
    names: Mapping[str, str],
    criteria: Mapping[str, Any],
) -> list[str]:
    errors: list[str] = []
    seen: dict[str, str] = {}
    directions = load_tables().metric_label_grammar.get("tokens", {}).get("directions", {})
    for slot_id in criteria["slots"]:
        slot = slot_rule(criteria, slot_id, _panel_kind(document))
        path = slot.get("source", "")
        if path not in criteria["placeholders"]["check_fields"]:
            continue
        for index, template in enumerate(_strings(lookup(document, path))):
            location = f"{document['id']}@{document['version']}.{path}" + (
                f"[{index}]" if path.endswith("limitations") else ""
            )
            resolved = resolve_template(
                template,
                names=names,
                documents=documents,
                directions=directions,
            )
            errors.extend(f"{location}: {message}" for message in slot_errors(resolved, slot))
            errors.extend(
                f"{location}: {message}" for message in _literal_errors(template, names, document)
            )
            normalized = " ".join(resolved.casefold().split())
            if normalized in seen:
                errors.append(f"{location}: duplicates {seen[normalized]}")
            seen[normalized] = location
    return errors


def _coverage_errors(
    document: Mapping[str, Any],
    documents: Mapping[str, Mapping[str, Any]],
    names: Mapping[str, str],
    criteria: Mapping[str, Any],
) -> list[str]:
    template = lookup(document, "notes.setup")
    if not isinstance(template, str):
        return []
    resolved = resolve_template(template, names=names, documents=documents)
    patterns = (
        criteria.get("coverage", {}).get("details.setup", {}).get(document.get("category"), [])
    )
    errors = [
        f"{document['id']}: measurement text lacks required clock/provenance coverage"
        for pattern in patterns
        if not re.search(pattern, resolved, re.I)
    ]
    names_in_document = {metric["name"] for metric in document.get("metrics", [])}
    for field in ("summary", "introduction", "setup", "how_to_read"):
        for token in parse_placeholders(
            str(lookup(document, f"notes.{field}") or ""), allow_literal_braces=True
        ):
            if token.kind in ("unit", "direction") and token.target not in names_in_document:
                errors.append(f"{document['id']}: {field} references another metric's {token.kind}")
    return errors


def _source_similarity_errors(
    document: Mapping[str, Any],
    documents: Mapping[str, Mapping[str, Any]],
    names: Mapping[str, str],
    criteria: Mapping[str, Any],
) -> list[str]:
    values = {
        slot: resolve_template(text, names=names, documents=documents)
        for slot, rule in criteria["slots"].items()
        if (text := lookup(document, rule.get("source", ""))) and isinstance(text, str)
    }
    return text_similarity_errors(values, criteria)


def text_similarity_errors(values: Mapping[str, str], criteria: Mapping[str, Any]) -> list[str]:
    """Near-duplicates, shared-concept restatements and interface prose among resolved slot text."""
    errors = _similarity_errors(values, criteria)
    errors.extend(_shared_definition_errors(values, criteria))
    for slot, text in values.items():
        errors.extend(f"{slot}: {error}" for error in _reader_prose_errors(text, criteria))
    return errors


def _method_page_errors(document: Mapping[str, Any], criteria: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    identifier = f"{document['id']}@{document['version']}"
    page_template = next(
        (
            item
            for item in criteria["method_pages"].values()
            if item["category"] == document.get("category")
        ),
        None,
    )
    if page_template:
        for block in page_template["required_blocks"]:
            path = criteria["method_blocks"][block]["source"]
            if not lookup(document, path):
                errors.append(f"{identifier}: missing method block {block} ({path})")
    return errors


def rendered_content_errors(criteria: Mapping[str, Any], scope: Mapping[str, Any]) -> list[str]:
    """Check a neutral DOM capture against the producer's exported criteria.

    A capture contains a scope kind, ordered slot records, the actual display unit,
    canonical names and header facts. Browser adapters only collect these values.
    """
    errors: list[str] = []
    slots = scope.get("slots", [])
    by_id = {slot["slot"]: slot for slot in slots}
    panel_kind = "hero" if scope.get("variant") == "hero" else scope.get("kind")
    panel = criteria.get("panels", {}).get(panel_kind, criteria)
    required = panel.get("required_slots", [])
    for slot_id in required:
        if not by_id.get(slot_id, {}).get("text", "").strip():
            errors.append(f"missing required slot {slot_id}")
    seen: dict[str, str] = {}
    for actual in slots:
        slot_id = actual["slot"]
        text = actual.get("text", "").strip()
        errors.extend(_rendered_slot_errors(criteria, scope, actual))
        normalized = " ".join(text.casefold().split())
        if normalized and normalized in seen:
            errors.append(f"{slot_id}: duplicates {seen[normalized]}")
        seen[normalized] = slot_id
    errors.extend(_rendered_order_errors(slots, panel))
    errors.extend(
        _similarity_errors({slot: row.get("text", "") for slot, row in by_id.items()}, criteria)
    )
    errors.extend(
        _shared_definition_errors(
            {slot: row.get("text", "") for slot, row in by_id.items()}, criteria
        )
    )
    errors.extend(_heading_errors(scope.get("headings", []), criteria))
    errors.extend(_taxonomy_errors(scope.get("taxonomy_lists", []), criteria))
    errors.extend(_rendered_metric_errors(criteria, scope, by_id))
    errors.extend(ranking_footer_errors(criteria, slots))
    errors.extend(provider_notes_errors(criteria, slots))
    errors.extend(rendered_direction_errors(criteria, scope, slots))
    return errors


def _without_direction(criteria: Mapping[str, Any], slot: Mapping[str, Any]) -> str:
    """A description's text before the subtitle separator and its closing direction phrase."""
    text: str = slot.get("text", "")
    if slot["slot"] != "description":
        return text
    policy = criteria["conventions"]["subtitle"]
    for phrase in (*policy["directions"].values(), policy["time_vs_cost_direction"]):
        if text.endswith(policy["separator"] + phrase):
            return text.removesuffix(policy["separator"] + phrase)
    return text


def _rendered_slot_errors(
    criteria: Mapping[str, Any], scope: Mapping[str, Any], actual: Mapping[str, Any]
) -> list[str]:
    slot_id = actual["slot"]
    if slot_id not in criteria["slots"]:
        return [f"unknown text slot {slot_id}"]
    rule = slot_rule(criteria, slot_id, scope.get("kind"))
    if rule is None:
        return [f"unknown text slot {slot_id}"]
    if scope.get("workload_subtitle") and slot_id in (
        "subtitle",
        "description",
    ):
        rule = {**rule, "form": "label", "max_length": float("inf")}  # the template owns it
    errors = [
        f"{slot_id}: {message}"
        for text in actual.get("items", [_without_direction(criteria, actual)])
        for message in slot_errors(
            text.strip(), rule, proper_names=criteria["conventions"]["proper_names"]
        )
    ]
    text = actual.get("text", "").strip()
    expected_name = scope.get("canonical_names", {}).get(slot_id)
    if expected_name is not None and text != expected_name:
        errors.append(f"{slot_id}: differs from the canonical name")
    for fact in scope.get("page_facts", []):
        if fact["id"] in criteria.get("fact_scope", {}).get("page", []):
            for form in fact.get("surface_forms", [fact.get("text", "")]):
                if form and form.casefold() in text.casefold():
                    errors.append(f"{slot_id}: repeats page fact {fact['id']}")
    if scope.get("page_facts") and re.search(
        criteria.get("fact_scope", {}).get("date_pattern", r"(?!)"), text
    ):
        errors.append(f"{slot_id}: measurement date belongs at page level")
    return errors


def _rendered_metric_errors(
    criteria: Mapping[str, Any], scope: Mapping[str, Any], by_id: Mapping[str, Any]
) -> list[str]:
    errors: list[str] = []
    axis = by_id.get("axis-title", {}).get("text", "")
    display_unit = scope.get("display_unit")
    if display_unit and axis and str(display_unit) not in axis:
        errors.append("axis-title: unit differs from plotted display unit")
    quantity = scope.get("quantity_label")
    owner = criteria.get("uniqueness", {}).get("quantity_owner")
    if quantity and owner:
        for slot_id in ("axis-title", "caption"):
            if slot_id != owner and re.search(
                rf"\b{re.escape(quantity)}\b", by_id.get(slot_id, {}).get("text", ""), re.I
            ):
                errors.append(f"{slot_id}: repeats the {owner} quantity")
    return errors


def _rendered_order_errors(slots: list[dict[str, Any]], panel: Mapping[str, Any]) -> list[str]:
    order = panel.get("slot_order", panel.get("details_order", []))
    actual = list(dict.fromkeys(slot["slot"] for slot in slots if slot["slot"] in order))
    if actual != [slot for slot in order if slot in actual]:
        return ["Text slots are in the wrong order"]
    return []


def _heading_errors(headings: list[dict[str, Any]], criteria: Mapping[str, Any]) -> list[str]:
    policy = criteria.get("headings", {})
    aliases = {name: group[0] for group in policy.get("equivalent_names", []) for name in group}
    ancestors: list[tuple[int, str]] = []
    errors: list[str] = []
    previous = ""
    for heading in headings:
        name = " ".join(heading["text"].casefold().split())
        name = aliases.get(name, name)
        level = heading["level"]
        while ancestors and ancestors[-1][0] >= level:
            ancestors.pop()
        if (policy.get("forbid_ancestor_repeat") and name in [entry[1] for entry in ancestors]) or (
            policy.get("forbid_adjacent_repeat") and name == previous
        ):
            errors.append(f"repeated heading {heading['text']}")
        ancestors.append((level, name))
        previous = name
    return errors


def _taxonomy_errors(lists: list[dict[str, Any]], criteria: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    for listing in lists:
        order = [
            row["id"]
            for row in criteria.get("canonical_order", [])
            if row["kind"] == listing["kind"] and row.get("parent") == listing.get("parent")
        ]
        actual = listing["ids"]
        if actual != [identifier for identifier in order if identifier in actual]:
            errors.append("list differs from canonical taxonomy order")
    return errors
