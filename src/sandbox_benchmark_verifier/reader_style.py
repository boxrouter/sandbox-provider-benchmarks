# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""The methodology style rules, checked over the reader notes and Common.

``conventions/text-conventions.yaml`` (``methodology_style``) owns every rule: its text, patterns
and tables. This module only runs them over ``reader/methods.yaml``: each ``notes`` field (a list's
items one by one) and everything under ``common``. Placeholders such as ``{param:...}`` are not
prose, so the pattern rules read the text with them removed.

Text written before a rule existed may break it. ``conventions/methodology-style-exemptions.yaml``
lists those items per rule; the conversion of each tab deletes its entries and nothing adds one.
An entry whose item no longer breaks its rule is an error, so the list only shrinks.
"""

import json
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from sandbox_benchmark_verifier.conventions import SOURCE, load_conventions
from sandbox_benchmark_verifier.name_refs import reader_strings
from sandbox_benchmark_verifier.names import load_tables
from sandbox_benchmark_verifier.placeholders import LINK_MARK
from sandbox_benchmark_verifier.reader import plain_reader
from sandbox_benchmark_verifier.reader_numbers import (
    COUNT_WORD,
    COUNTED,
    VARIANT_WORD,
    VERSION,
    Units,
    definition_numbers,
    number_of,
    stem,
    unit_word,
    versions_held,
    written_number,
)
from sandbox_benchmark_verifier.reader_sizes import (
    BINARY_FACTORS,
    TYPED_SIZE,
    defined_sizes,
    near,
    recorded_sizes,
)
from sandbox_benchmark_verifier.yaml_load import load_yaml

EXEMPTIONS_PATH = "conventions/methodology-style-exemptions.yaml"
_PLACEHOLDER = re.compile(r"\{(\w+):[^{}]*\}")
_ANY_PLACEHOLDER = re.compile(r"\{[^{}]*\}")
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+(?=[A-Z{`])")
_CODE_SPAN = re.compile(r"`[^`]*`")
_NUMBER = re.compile(
    r"(?<![\w.\-/])(?P<number>\d[\d,]*(?:\.\d+)?)"
    r"(?:-(?P<hyphened>[A-Za-z]+)(?![\w\-])|(?![\w\-]|\.\d)(?:\s+(?P<word>[A-Za-z][A-Za-z/]*))?)"
)
_HEADING_LINE = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)
_NAME_REFERENCE = re.compile(r"\{name:(?:test\.)?([\w-]+)\}")
COMMON_KEY = "common"

Offense = tuple[str, str, str]  # rule id, item key, message


@dataclass(frozen=True)
class Item:
    """One piece of reader text: a notes field (a list's items apart) or a Common string."""

    key: str
    text: str
    method: str | None = None
    tab: str | None = None
    section: str | None = None
    is_common: bool = False

    @property
    def prose(self) -> str:
        """The text without placeholders, which are references and not prose."""
        return _ANY_PLACEHOLDER.sub(" ", self.text)


@dataclass(frozen=True)
class Context:  # pylint: disable=too-many-instance-attributes
    """What the rules need besides the text: the owner tables, the measured definitions and the
    notes as each page draws them."""

    rows: Mapping[str, Mapping[str, Any]]  # by id; a tab wins over the test of the same id
    names: Sequence[Mapping[str, Any]]  # every row of the names table
    documents: Mapping[str, Mapping[str, Any]]
    method_documents: Mapping[str, str]
    conventions: Mapping[str, Any]
    threshold: float
    introductions: Mapping[str, str] | None = None  # by test id, as the reader reads them
    rendered: Mapping[str, Mapping[str, Any]] | None = None  # each method's notes as drawn
    # the ranking status labels the owner writes, current and retired
    status_labels: Sequence[str] = ()
    # the byte counts the parameter files record as facts
    recorded_sizes: Sequence[int] = ()


def _ancestors(rows: Mapping[str, Mapping[str, Any]], identifier: str) -> Iterator[str]:
    parent = rows.get(identifier, {}).get("parent")
    seen = {identifier}
    while parent and parent in rows and parent not in seen:
        seen.add(str(parent))
        yield str(parent)
        parent = rows[parent].get("parent")


def _placement(
    rows: Mapping[str, Mapping[str, Any]], identifier: str
) -> tuple[str | None, str | None]:
    """The tab and the top-level section a test, index or tab sits in."""
    chain = [identifier, *_ancestors(rows, identifier)]
    tab = next((name for name in chain if rows.get(name, {}).get("kind") == "tab"), None)
    section = next((name for name in chain if rows.get(name, {}).get("kind") == "section"), None)
    return tab, section


def reader_items(source: Mapping[str, Any], rows: Mapping[str, Mapping[str, Any]]) -> list[Item]:
    """Every checked string of the reader source, keyed the way the exemptions name it."""
    items: list[Item] = []
    for identifier, method in source.get("methods", {}).items():
        tab, section = _placement(rows, identifier)
        for field, value in method.get("notes", {}).items():
            for key, text in reader_strings(value, f"{identifier}.{field}"):
                items.append(
                    Item(
                        key,
                        text,
                        method=identifier,
                        tab=tab,
                        section=section,
                        is_common=identifier == COMMON_KEY,
                    )
                )
    for key, text in reader_strings(source.get("common", {}), COMMON_KEY):
        parts = key.split(".")
        scope = parts[2] if len(parts) > 3 and parts[1] == "blocks" else None
        tab = scope if scope and rows.get(scope, {}).get("kind") == "tab" else None
        section = _placement(rows, scope)[1] if scope and scope != COMMON_KEY else None
        items.append(Item(key, text, tab=tab, section=section, is_common=True))
    return items


def _sentences(text: str) -> list[str]:
    return [part for part in _SENTENCE_BREAK.split(text.strip()) if part]


def _words(sentence: str) -> list[str]:
    return _PLACEHOLDER.sub(r"{\1}", sentence).casefold().split()


def _flags(rule: Mapping[str, Any]) -> int:
    return re.IGNORECASE if rule.get("flags") == "i" else 0


def _in_scope(rule: Mapping[str, Any], item: Item) -> bool:
    sections = rule.get("sections")
    fields = rule.get("fields")
    return (not sections or item.section in sections) and (
        not fields or item.key.split(".")[-1].split("[")[0] in fields
    )


def _forbidden_pattern(rule: Mapping[str, Any], items: Sequence[Item], _: Context) -> list[Offense]:
    return [
        (rule["id"], item.key, f"{match.group(0)!r}: {rule['text']}")
        for item in items
        if _in_scope(rule, item)
        for pattern in rule["patterns"]
        if (match := re.search(pattern, item.text if rule.get("raw") else item.prose, _flags(rule)))
    ]


def _phrase_table(rule: Mapping[str, Any], items: Sequence[Item], _: Context) -> list[Offense]:
    return [
        (rule["id"], item.key, f"{phrase['idea']}: write {phrase['canonical']!r}")
        for item in items
        for phrase in rule["phrases"]
        for variant in phrase["variants"]
        if re.search(variant, item.text)
    ]


def _test_words(context: Context, method: str, minimum: int) -> set[str]:
    """The words that name this test among its siblings, and its variants, jobs and stages."""

    def words(row: Mapping[str, Any]) -> set[str]:
        return {stem(word) for word in re.findall(r"[a-z0-9]+", str(row.get("name", "")).lower())}

    tests = [row for row in context.names if row.get("kind") == "test"]
    own = next((row for row in tests if str(row["id"]) == method), None)
    if own is None:
        return set()
    shared = set.intersection(
        *(words(row) for row in tests if row.get("parent") == own.get("parent"))
    )
    named = words(own) - shared
    for row in context.names:
        if row.get("kind") in ("variant", "job") and row.get("parent") == method:
            named |= words(row)
    return {word for word in named if len(word) >= minimum}


def _test_specific(sentence: str, test_words: set[str], rule: Mapping[str, Any]) -> bool:
    """A sentence that carries this test's own name, number, parameter value or variant word."""
    shared = tuple(rule["shared_parameters"])
    if "{name:" in sentence or any(
        not reference.startswith(shared)
        for reference in re.findall(r"\{(?:param|machine):(measured\.[^{}]*)\}", sentence)
    ):
        return True
    prose = _ANY_PLACEHOLDER.sub(" ", sentence)
    return bool(re.search(r"\d", prose)) or any(
        stem(word) in test_words for word in re.findall(r"[a-z0-9]+", prose.lower())
    )


def _candidate_sentences(
    rule: Mapping[str, Any], items: Sequence[Item], context: Context
) -> list[tuple[Item, list[str]]]:
    """The sentences long enough to compare that carry nothing of their own test."""
    specific = {
        method: _test_words(context, method, rule["min_word_length"])
        for method in {item.method for item in items if item.method}
    }
    return [
        (item, words)
        for item in items
        for sentence in _sentences(item.text)
        if len(words := _words(sentence)) >= rule["min_words"]
        and not _test_specific(sentence, specific.get(item.method or "", set()), rule)
    ]


def _repeated_sentence(
    rule: Mapping[str, Any], items: Sequence[Item], context: Context
) -> list[Offense]:
    """A sentence another test (or Common) already says: it belongs in Common or the tab block."""
    sentences = _candidate_sentences(rule, items, context)
    offenses: dict[str, str] = {}
    for index, (item, words) in enumerate(sentences):
        for other, other_words in sentences[index + 1 :]:
            if item.method == other.method and item.method is not None:
                continue
            if item.is_common and other.is_common:
                continue
            matcher = SequenceMatcher(None, words, other_words)
            if (
                matcher.real_quick_ratio() < context.threshold
                or matcher.ratio() < context.threshold
            ):
                continue
            for mine, theirs in ((item, other), (other, item)):
                if mine.is_common:
                    continue
                owner = (
                    "Common"
                    if theirs.is_common
                    else ("the tab block" if theirs.tab and theirs.tab == mine.tab else "Common")
                )
                offenses.setdefault(
                    mine.key, f"says what {theirs.key} says; say it once in {owner}"
                )
    return [(rule["id"], key, f"{rule['text']} ({message})") for key, message in offenses.items()]


def _headings(rule: Mapping[str, Any], items: Sequence[Item], context: Context) -> list[Offense]:
    starters = re.compile(rf"^(?:{'|'.join(rule['clause_starters'])})\b", re.IGNORECASE)
    verbs = re.compile(rf"\b(?:{'|'.join(rule['verb_endings'])})$", re.IGNORECASE)
    found = [(item.key, heading) for item in items for heading in _HEADING_LINE.findall(item.text)]
    found.extend(
        (f"conventions.slots.{slot}.heading", rule_slot["heading"])
        for slot, rule_slot in context.conventions["slots"].items()
        if "heading" in rule_slot
    )
    return [
        (rule["id"], key, f"{heading!r}: {rule['text']}")
        for key, heading in found
        if starters.search(heading.strip()) or verbs.search(heading.strip())
    ]


def _in_a_name(context: Context, text: str, start: int, version: str) -> bool:
    """The version is part of a name the names table owns ("Claude Opus 5.5")."""
    before = re.findall(r"[\w-]+", text[:start])[-1:]
    return bool(before) and any(
        f"{before[0]} {version}" in str(row.get("name", "")) for row in context.names
    )


def _definition_digits(
    rule: Mapping[str, Any], items: Sequence[Item], context: Context
) -> list[Offense]:
    """A number with a unit equals a value in that unit; a bare count, a count of the same thing,
    in digits or as a word; a count before "variants", the number of the definition's variants; a
    version, one the definition's source names."""
    ignored = {float(value) for value in rule["ignore_values"]}
    units = Units(rule["units"])
    held: dict[str, list[tuple[str, float]]] = {}
    offenses: list[Offense] = []
    for item in items:
        document = context.method_documents.get(item.method or "")
        if document is None or document not in context.documents:
            continue
        definition = context.documents[document]
        leaves = held.setdefault(document, list(definition_numbers(definition)))
        text = _CODE_SPAN.sub(" ", item.prose)
        found = {
            written_number(match)
            for pattern in (_NUMBER, COUNT_WORD)
            for match in pattern.finditer(text)
            if number_of(match["number"]) not in ignored
            and "/" not in unit_word(match)
            and units.matches(number_of(match["number"]), unit_word(match), leaves)
        }
        variants = definition.get("procedure", {}).get("variants", [])
        found |= {
            f"{match['number']}{match['after']}"
            for match in COUNTED.finditer(text)
            if variants
            and VARIANT_WORD.search(match["after"])
            and number_of(match["number"]) == len(variants)
            and number_of(match["number"]) not in ignored
        }
        versions = versions_held(definition)
        found |= {
            match[0]
            for match in VERSION.finditer(text)
            if match[0] in versions and not _in_a_name(context, text, match.start(), match[0])
        }
        if found:
            offenses.append((rule["id"], item.key, f"{', '.join(sorted(found))}: {rule['text']}"))
    return offenses


def _workload_names(context: Context) -> dict[str, list[str]]:
    """Each workload id and its spellings: the display name, with and without a version."""
    names: dict[str, list[str]] = {}
    for identifier, row in context.rows.items():
        if row.get("kind") == "test" and "workloads" in _ancestors(context.rows, identifier):
            name = str(row["name"])
            names[identifier] = sorted({name, re.sub(r"\s+\d+(?:\.\d+)*$", "", name)})
    return names


def _other_workload(
    rule: Mapping[str, Any], items: Sequence[Item], context: Context
) -> list[Offense]:
    workloads = _workload_names(context)
    offenses: list[Offense] = []
    for item in items:
        if item.method not in workloads:
            continue
        named = {
            other
            for other, spellings in workloads.items()
            if other != item.method
            and any(
                re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", item.text) for name in spellings
            )
        }
        named |= {
            ref
            for ref in _NAME_REFERENCE.findall(item.text)
            if ref in workloads and ref != item.method
        }
        if named:
            offenses.append((rule["id"], item.key, f"{', '.join(sorted(named))}: {rule['text']}"))
    return offenses


def _defined_term(
    rule: Mapping[str, Any], items: Sequence[Item], context: Context
) -> list[Offense]:
    """The retired word, apart from its definition and the current names that contain it."""
    term = re.compile(rf"\b{re.escape(rule['term'])}\b", re.IGNORECASE)
    names = [str(value) for row in context.names if term.search(str(value := row.get("name", "")))]
    definition = [re.compile(pattern, re.IGNORECASE) for pattern in rule["defined_term_patterns"]]
    offenses: list[Offense] = []
    for item in items:
        text = item.prose
        for name in names:
            text = text.replace(name, " ")
        for sentence in _sentences(text):
            if term.search(sentence) and not any(
                pattern.search(sentence) for pattern in definition
            ):
                offenses.append((rule["id"], item.key, rule["text"]))
                break
    return offenses


def _introduction_problems(
    rule: Mapping[str, Any], item: Item, context: Context, *, index: bool = False
) -> list[str]:
    """What keeps one Introduction from the shape: a verb first, no name, no metric."""
    document = context.documents.get(context.method_documents.get(item.method or "", ""), {})
    metrics: list[Mapping[str, Any]] = list(document.get("metrics", []))
    unit = str(metrics[0].get("unit", "")) if metrics else ""
    verb = rule["time_verb" if unit in rule["time_units"] else "other_verb"]
    verbs = rule["index_verbs"] if index else [verb]
    problems: list[str] = []
    resolved = (context.introductions or {}).get(item.method or "", item.text)
    first = (resolved if index else item.text).split(maxsplit=1)[
        :1
    ]  # a composite's text is a parameter
    if first and first[0].startswith("{name:"):
        problems.append(f"start with {' or '.join(verbs)}, not with a name")
    elif not first or first[0] not in verbs:
        problems.append(f"start with {' or '.join(verbs)}")
    if len(resolved) > rule["max_length"]:
        problems.append(f"{len(resolved)} characters, at most {rule['max_length']}")
    if len(_sentences(resolved)) != 1:
        problems.append("write one sentence")
    names = {str(metric["name"]) for metric in metrics}
    problems.extend(
        f"drop the metric id {span!r}"
        for span in _CODE_SPAN.findall(item.text)
        if span.strip("`").replace(" ", "_").lower() in names
    )
    if unit and re.search(rf"(?<![\w/]){re.escape(unit)}(?![\w/])", item.prose, re.IGNORECASE):
        problems.append(f"drop the unit {unit!r}; the axis carries it")
    if re.search(rule["direction_pattern"], item.prose, re.IGNORECASE):
        problems.append("drop the direction; the subtitle carries it")
    return problems


def _view_subtitle_problems(rule: Mapping[str, Any], text: str) -> list[str]:
    """What keeps a combined view's subtitle from the Introduction's shape."""
    verbs = [rule["time_verb"], rule["other_verb"]]
    problems: list[str] = []
    if not text.split() or text.split()[0] not in verbs:
        problems.append(f"start with {' or '.join(verbs)}")
    if len(text) > rule["max_length"]:
        problems.append(f"{len(text)} characters, at most {rule['max_length']}")
    if len(_sentences(text)) != 1:
        problems.append("write one sentence")
    if re.search(rule["direction_pattern"], text, re.IGNORECASE):
        problems.append("drop the direction; the subtitle carries it")
    return problems


def _introduction_sentence(
    rule: Mapping[str, Any], items: Sequence[Item], context: Context
) -> list[Offense]:
    """A test's or composite's Introduction is one short sentence: verb first, no name."""
    kinds = {
        str(row["id"]): str(row["kind"])
        for row in context.names
        if row.get("kind") in ("test", "index")
    }
    offenses = [
        (rule["id"], item.key, f"{problem}: {rule['text']}")
        for item in items
        if item.key.endswith(".introduction") and item.method in kinds
        for problem in _introduction_problems(
            rule, item, context, index=kinds[item.method or ""] == "index"
        )
    ]
    for (
        row
    ) in context.names:  # a combined view's subtitle stands for its merged page's Introduction
        if row.get("kind") == "view" and "subtitle" in row:
            offenses.extend(
                (rule["id"], f"names.{row['id']}.subtitle", f"{problem}: {rule['text']}")
                for problem in _view_subtitle_problems(rule, str(row["subtitle"]))
            )
    return offenses


_CODE_PLACEHOLDER = re.compile(r"\{(\w+):([^{}]*)\}")


def _code_mark_problem(rule: Mapping[str, Any], span: str) -> str | None:
    """Why a code mark is not for what a reader could type, else None."""
    body = span.strip("`").strip()
    holder = _CODE_PLACEHOLDER.fullmatch(body)
    if holder:
        key = holder[2].rsplit(".", 1)[-1]
        return None if holder[1] == "param" and key in rule["value_keys"] else "label"
    body = _CODE_PLACEHOLDER.sub("0", body)  # a value inside a command or path is typed there
    if body in rule["plain_words"]:
        return "label"
    words = body.split()
    typable = (
        body in rule["commands"]
        or body in rule["environment_variables"]
        or (words and words[0] in rule["commands"])
        or any(re.fullmatch(pattern, body) for pattern in rule["typable_patterns"])
    )
    if typable:
        return None
    return "field id" if re.fullmatch(rule["field_id_pattern"], body) else "label"


def _code_marks(rule: Mapping[str, Any], items: Sequence[Item], _: Context) -> list[Offense]:
    """Code marks are for what a reader could type: a command, path, flag, environment variable or
    API call. A field id is deleted; a label, number or unit is plain text."""
    deferred = set(rule["deferred_fields"])
    offenses: list[Offense] = []
    for item in items:
        if item.key.split(".")[1:2] and item.key.split(".")[1].split("[")[0] in deferred:
            continue
        for span in _CODE_SPAN.findall(item.text):
            problem = _code_mark_problem(rule, span)
            if problem == "field id":
                offenses.append(
                    (rule["id"], item.key, f"{span}: delete the field id; {rule['text']}")
                )
            elif problem:
                offenses.append(
                    (
                        rule["id"],
                        item.key,
                        f"{span}: write a label, number or unit as plain text; {rule['text']}",
                    )
                )
    return offenses


def list_block(markers: Mapping[str, str], block: str) -> str | None:
    """The kind of list a block is ("bullet" or "numbered"), else None: every line of a list block
    starts with the same marker. The web renderer reads the same markers from the conventions
    (``methodology_style.list_markers``)."""
    lines = block.split("\n")
    for kind, marker in markers.items():
        if all(re.match(marker, line) for line in lines):
            return str(kind)
    return None


def _block_problem(rule: Mapping[str, Any], markers: Mapping[str, str], block: str) -> str | None:
    """What is wrong with one blank-line-separated block of a reader note, else None. A rendered
    block is measured as drawn (a link is its word); a source block still holding placeholders
    counts each as "x"."""
    text = _ANY_PLACEHOLDER.sub("x", LINK_MARK.sub(r"\1", block)).replace("`", "")
    kind = list_block(markers, block)
    if kind is None:
        if any(re.match(marker, line) for line in block.split("\n") for marker in markers.values()):
            return "a list is its own block"
        if len(text) > rule["max_length"]:
            return f"a paragraph of {len(text)} characters"
        return _parallel_sentences(rule, text)
    too_long = [line for line in text.split("\n") if len(line) > rule["max_item_length"]]
    return f"a list item of {len(too_long[0])} characters" if too_long else None


def _parallel_sentences(rule: Mapping[str, Any], text: str) -> str | None:
    """Three or more detailed sentences in a row that start with the same word."""
    run = 1
    sentences = _sentences(text)
    for before, after in zip(sentences, sentences[1:], strict=False):
        same = before.split()[:1] == after.split()[:1] and len(after) >= rule["min_item_length"]
        run = run + 1 if same else 1
        if run == rule["min_items"]:
            return f"{run} parallel sentences start with {after.split()[0]!r}"
    return None


def _drawn_text(item: Item, context: Context) -> str | None:
    """The item as its page draws it, every placeholder resolved; None for a shared block, which
    is measured inside each note that draws it. Without rendered notes, the source text."""
    if context.rendered is None:
        return item.text
    if item.method is None:
        return None if item.key.startswith(f"{COMMON_KEY}.blocks.") else item.text
    notes = context.rendered.get(item.method, {})
    field = item.key.split(".")[1].split("[")[0]
    return dict(reader_strings(notes.get(field), f"{item.method}.{field}")).get(item.key, item.text)


def _paragraph_shape(
    rule: Mapping[str, Any], items: Sequence[Item], context: Context
) -> list[Offense]:
    """A drawn reader paragraph is at most about 400 characters, a list item at most 1200, a list
    is a block of its own, and three or more detailed parallel sentences are written as a list. A
    paragraph the rule exempts by its opening words stays; an exemption that matches nothing is an
    offense, so the list only holds paragraphs that are still too long."""
    deferred = set(rule["deferred_fields"])
    markers = context.conventions["methodology_style"]["list_markers"]
    exempt = {entry["starts"]: False for entry in rule.get("exempt_paragraphs", [])}
    offenses: list[Offense] = []
    for item in items:
        if item.key.split(".")[1:2] and item.key.split(".")[1].split("[")[0] in deferred:
            continue
        text = _drawn_text(item, context)
        for block in re.split(r"\n\s*\n", (text or "").strip()):
            problem = _block_problem(rule, markers, block)
            if problem and (start := next((s for s in exempt if block.startswith(s)), None)):
                exempt[start] = problem.startswith("a paragraph of") or exempt[start]
                if problem.startswith("a paragraph of"):
                    continue
            if problem:
                offenses.append((rule["id"], item.key, f"{problem}: {rule['text']}"))
    offenses.extend(
        (rule["id"], "exempt_paragraphs", f"{start!r} exempts no paragraph over the limit")
        for start, used in exempt.items()
        if not used and context.rendered is not None
    )
    return offenses


def _metric_lead(context: Context, method: str) -> str:
    """The name a metric paragraph starts with: the test's name up to its size or variant."""
    name = str(context.rows.get(method, {}).get("name", method))
    return re.split(r" \(|,", name, maxsplit=1)[0]


def _metric_paragraph(
    rule: Mapping[str, Any], items: Sequence[Item], context: Context
) -> list[Offense]:
    """A Metrics paragraph is one paragraph that starts with its metric's name and "is the", never a
    verb, and a test never holds both a paragraph and the old `metrics` note (no second copy)."""
    holders = {item.method for item in items if item.key == f"{item.method}.metric"}
    offenses = [
        (rule["id"], f"{item.key}", f"a second copy of the metric text: {rule['text']}")
        for item in items
        if item.key == f"{item.method}.metrics" and item.method in holders
    ]
    for item in items:
        if item.key != f"{item.method}.metric" or item.method is None:
            continue
        lead = _metric_lead(context, item.method)
        starts = tuple(f"{{name:{kind}.{item.method}}} is the " for kind in ("test", "index")) + (
            f"{lead} is the ",
        )
        if not item.text.startswith(starts):
            offenses.append(
                (rule["id"], item.key, f"does not start with {lead!r} is the: {rule['text']}")
            )
        if re.search(r"\n\s*\n", item.text.strip()):
            offenses.append((rule["id"], item.key, f"more than one paragraph: {rule['text']}"))
    return offenses


def _colon_problem(rule: Mapping[str, Any], text: str) -> str | None:
    """A colon that does not follow a noun: right after a closing parenthesis, a code mark, a
    number, or a verb of the rule's list."""
    plain = _ANY_PLACEHOLDER.sub(" ", _CODE_SPAN.sub(" ", text))
    for found in re.finditer(r":(?=\s)", plain):
        before = plain[: found.start()].rstrip()
        word = re.findall(r"[A-Za-z]+", before[-20:])
        if not before or before[-1] in ")]`" or before[-1].isdigit():
            return f"a colon after {before[-12:]!r}"
        if word and word[-1].lower() in rule["verbs"]:
            return f"a colon after the verb {word[-1]!r}"
    return None


def _colon_after_noun(rule: Mapping[str, Any], items: Sequence[Item], _: Context) -> list[Offense]:
    fields = set(rule["fields"])
    return [
        (rule["id"], item.key, f"{problem}: {rule['text']}")
        for item in items
        if item.key.rsplit(".", 1)[-1] in fields
        if (problem := _colon_problem(rule, item.text))
    ]


def _one_numbered_list(
    rule: Mapping[str, Any], items: Sequence[Item], context: Context
) -> list[Offense]:
    """A test's reader text holds no numbered list: its generated trial steps are the one."""
    marker = re.compile(rf"^{context.conventions['methodology_style']['list_markers']['numbered']}")
    return [
        (rule["id"], item.key, rule["text"])
        for item in items
        if not item.is_common and any(marker.match(line) for line in item.text.splitlines())
    ]


def status_labels(root: Path) -> list[str]:
    """Every ranking status label the owner writes, current and retired, that prose must not type.

    "Ranked" is left out: it is an ordinary word.
    """
    path = root / "contracts/text/1.0.0.json"
    if not path.is_file():
        return []
    owner = json.loads(path.read_text(encoding="utf-8"))
    current = [
        item["label"] for item in owner.get("ranking_statuses", {}).values() if "label" in item
    ]
    retired = owner.get("ranking_status_footer", {}).get("retired_labels", [])
    return sorted(
        {label for label in [*current, *retired] if label != "Ranked" and "{" not in label}
    )


def _status_label(
    rule: Mapping[str, Any], items: Sequence[Item], context: Context
) -> list[Offense]:
    """Reader text reads a ranking status label through its placeholder, never typed."""
    return [
        (rule["id"], item.key, f"types the status label {label!r}: {rule['text']}")
        for item in items
        for label in context.status_labels
        # The owner's Title Case and the sentence-case form a reader sentence could type.
        if label in item.prose or label.capitalize() in item.prose
    ]


_NOTE_WORD = re.compile(r"[a-z0-9$_./~-]+")
# A timed status a note gives its step: a tag that says untimed ("(setup, untimed on chart)", "an
# untimed load step"), or one that says timed ("(setup, timed, not charted)", "timed by").
_SAYS_UNTIMED_TAG = re.compile(r"\([^()]*\buntimed\b[^()]*\)|\buntimed (?:\w+ )?step\b", re.I)
_SAYS_TIMED = re.compile(r"\([^()]*(?<![\w-])timed\b[^()]*\)|(?<![\w-])timed by\b", re.I)


def _drawn_words(text: str) -> list[str]:
    return _NOTE_WORD.findall(LINK_MARK.sub(r"\1", text).replace("`", "").casefold())


def _shared_run(first: Sequence[str], second: Sequence[str]) -> list[str]:
    match = SequenceMatcher(None, first, second, autojunk=False).find_longest_match(
        0, len(first), 0, len(second)
    )
    return list(first[match.a : match.a + match.size])


def _step_note_restates(
    rule: Mapping[str, Any], _: Sequence[Item], context: Context
) -> list[Offense]:
    """A step note does not repeat the step's wording or contradict its timed status."""
    offenses: list[Offense] = []
    for method, notes in (context.rendered or {}).items():
        for index, item in enumerate(notes.get("step_items") or []):
            note = item.get("note")
            if not note:
                continue
            key = f"{method}.step_items[{index}].note"
            run = _shared_run(_drawn_words(item["text"]), _drawn_words(note))
            if len(run) >= rule["min_shared_words"]:
                offenses.append(
                    (rule["id"], key, f"repeats its step's words {' '.join(run)!r}: {rule['text']}")
                )
            if item.get("timed") and _SAYS_UNTIMED_TAG.search(note):
                offenses.append((rule["id"], key, f"says untimed on a timed step: {rule['text']}"))
            if not item.get("timed") and _SAYS_TIMED.search(note):
                offenses.append((rule["id"], key, f"says timed on an untimed step: {rule['text']}"))
    return offenses


def _recorded_size(
    rule: Mapping[str, Any], items: Sequence[Item], context: Context
) -> list[Offense]:
    """Reader text never types a size that a recorded fact holds; it reads it through the
    placeholder, and the drawn text shows it in decimal units. A size a definition sets in binary
    is never drawn in decimal ones. A written size matches a byte count when it rounds to it in its
    own unit."""
    leaves = (
        leaf for document in context.documents.values() for leaf in definition_numbers(document)
    )
    defined = defined_sizes(leaves, context.recorded_sizes)
    offenses: dict[tuple[str, str], str] = {}
    for item in items:
        for match in TYPED_SIZE.finditer(item.prose):
            if near(match, context.recorded_sizes):
                offenses[(item.key, match[0])] = f"types {match[0]!r}"
        for match in TYPED_SIZE.finditer(_drawn_text(item, context) or ""):
            binary = match[2] in BINARY_FACTORS
            if binary and near(match, context.recorded_sizes):
                offenses.setdefault(
                    (item.key, match[0]), f"draws a measured {match[0]!r} in binary"
                )
            elif not binary and near(match, defined):
                offenses[(item.key, match[0])] = f"draws a defined binary size as {match[0]!r}"
    return [(rule["id"], key, f"{text}: {rule['text']}") for (key, _), text in offenses.items()]


CHECKS: Mapping[str, Callable[[Mapping[str, Any], Sequence[Item], Context], list[Offense]]] = {
    "metric-paragraph": _metric_paragraph,
    "step-note": _step_note_restates,
    "status-label": _status_label,
    "recorded-size": _recorded_size,
    "colon-after-noun": _colon_after_noun,
    "forbidden-pattern": _forbidden_pattern,
    "phrase-table": _phrase_table,
    "repeated-sentence": _repeated_sentence,
    "heading": _headings,
    "definition-digits": _definition_digits,
    "other-workload": _other_workload,
    "defined-term": _defined_term,
    "introduction-sentence": _introduction_sentence,
    "code-marks": _code_marks,
    "paragraph-shape": _paragraph_shape,
    "one-numbered-list": _one_numbered_list,
}


def style_offenses(items: Sequence[Item], context: Context) -> list[Offense]:
    """Every rule's offenses before exemptions, in the order of the rules."""
    offenses: list[Offense] = []
    for rule in context.conventions["methodology_style"]["rules"]:
        check = CHECKS.get(rule["check"])
        if check is not None:  # "none" is a rule with no check yet; its owner is on the rule
            offenses.extend(check(rule, items, context))
    return offenses


def load_exemptions(root: Path) -> dict[str, list[str]]:
    """The per-rule lists of items that predate their rule; absent in a tree that has none."""
    path = root / EXEMPTIONS_PATH
    if not path.is_file():
        return {}
    loaded: dict[str, Any] = load_yaml(path.read_text(encoding="utf-8")) or {}
    rules: dict[str, list[str]] = loaded.get("rules", {})
    return rules


def apply_exemptions(
    offenses: Sequence[Offense], exemptions: Mapping[str, Sequence[str]], rule_ids: set[str]
) -> list[str]:
    """The offenses not exempted, and every entry that exempts nothing (delete it)."""
    errors = [
        f"{EXEMPTIONS_PATH}: {rule}: not a methodology style rule"
        for rule in exemptions
        if rule not in rule_ids
    ]
    seen = {(rule, key) for rule, key, _ in offenses}
    for rule, keys in exemptions.items():
        errors.extend(
            f"{EXEMPTIONS_PATH}: {rule}: {key} breaks no such rule now; delete the entry"
            for key in keys
            if (rule, key) not in seen
        )
    exempt = {(rule, key) for rule, keys in exemptions.items() for key in keys}
    errors.extend(
        f"reader style: {key}: {rule}: {message}"
        for rule, key, message in offenses
        if (rule, key) not in exempt
    )
    return errors


def reader_style_context(
    root: Path,
    method_documents: Mapping[str, str],
    documents: Mapping[str, Mapping[str, Any]],
    introductions: Mapping[str, str] | None = None,
    rendered: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[list[Item], Context] | None:
    """The reader items and rule context of a tree, or None where it has no reader or no rules."""
    source = plain_reader(root)
    if source is None or not (root / SOURCE).is_file():
        return None
    conventions = load_conventions(root)
    if "methodology_style" not in conventions:
        return None
    names = load_tables(root).names
    rows: dict[str, Mapping[str, Any]] = {}
    for row in names:
        if str(row["id"]) not in rows or rows[str(row["id"])]["kind"] == "test":
            rows[str(row["id"])] = row
    context = Context(
        status_labels=status_labels(root),
        recorded_sizes=recorded_sizes(root),
        rows=rows,
        names=names,
        documents=documents,
        method_documents=method_documents,
        conventions=conventions,
        threshold=conventions["uniqueness"]["similarity_threshold"],
        introductions=introductions or {},
        rendered=rendered,
    )
    return reader_items(source, rows), context


def reader_style_errors(
    root: Path,
    method_documents: Mapping[str, str],
    documents: Mapping[str, Mapping[str, Any]],
    introductions: Mapping[str, str] | None = None,
    rendered: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[str]:
    """Style errors of the reader source that no exemption covers, and stale exemptions."""
    prepared = reader_style_context(root, method_documents, documents, introductions, rendered)
    if prepared is None:
        return []
    items, context = prepared
    rule_ids = {rule["id"] for rule in context.conventions["methodology_style"]["rules"]}
    return apply_exemptions(style_offenses(items, context), load_exemptions(root), rule_ids)
