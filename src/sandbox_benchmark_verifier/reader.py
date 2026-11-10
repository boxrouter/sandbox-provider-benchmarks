# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""The current reader copy of every test, apart from the versioned scientific documents.

``reader/methods.yaml`` holds the wording (headline, notes, panel command) keyed by
a test's canonical name id. A wording edit changes this file and nothing in ``methodology/``: no
scientific version, no release-lock hash, no re-run. A statement of fact in the copy reads the
measured definition of the result it describes, ``{param:measured.<path>}``; the compiler resolves
it against each measured version, never against the newest one.
"""

import copy
import json
import re
import subprocess
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping, Sequence
from functools import partial
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from sandbox_benchmark_verifier.catalog import (
    _PACKAGED_ASSETS,
    METHODOLOGY_COMMIT,
    default_context,
    load_schema,
    ordinary_documents,
    repository_url,
)
from sandbox_benchmark_verifier.content import (
    content_criteria,
    criteria_errors,
    slot_errors,
    text_similarity_errors,
)
from sandbox_benchmark_verifier.conventions import load_conventions
from sandbox_benchmark_verifier.glance_clock import clock_labels, clock_row
from sandbox_benchmark_verifier.machine_panels import apply_panel_text
from sandbox_benchmark_verifier.methodology_page import member_documents
from sandbox_benchmark_verifier.name_refs import reader_strings
from sandbox_benchmark_verifier.names import load_tables, shows_cost
from sandbox_benchmark_verifier.pinned_workload import pinned_workload
from sandbox_benchmark_verifier.placeholders import (
    BLOCK,
    LINK_MARK,
    PlaceholderError,
    commit_url,
    mark_sha256,
    with_blocks,
)
from sandbox_benchmark_verifier.reader_steps import (
    STEP_MARK,
    headline_span_errors,
    time_limits,
    timed_parts,
    trial_step_items,
    variant_commands,
    with_step_notes,
)
from sandbox_benchmark_verifier.reader_when import gate_errors, gated, ungated
from sandbox_benchmark_verifier.subtitles import generated_notes
from sandbox_benchmark_verifier.versions import document_version
from sandbox_benchmark_verifier.yaml_load import dump_yaml, load_yaml

READER_PATH = "reader/methods.yaml"
COMMON_SCOPE = "common"
# What a test whose methodology page is another test's keeps: its own sentences (the chart
# subtitle, metric paragraph and limits drawn under its heading on the page).
PAGE_MEMBER_NOTES = {"introduction", "metric", "limitations"}

# An index page also keeps the notes its results panel draws but its page does not.
INDEX_PANEL_NOTES = {"setup", "trial_steps", "how_to_read", "cost_summary", "cost_how_to_read"}

# A page hosting other tests keeps its own Short Name and subtitle.
PAGE_NOTES = {"page_headline", "page_introduction"}
_MEASURED = re.compile(
    r"\{(param|commit|hash|count|countword|version|duration|size|imagesize|machine|product):([^{}]*)\}"
)
SLOT_CHECKED_NOTES = (
    "trial_steps",
    "limitations",
    "opening",
    "workload",
    "procedure",
    "metrics",
    "metric",
    "calculation",
    "failure_policy",
    "statistics_rules",
    "median_rule",
    "percentile_rule",
    "interval_rule",
    "workload_value",
    "stage_rule",
    "independence",
)
# Notes that quote facts only the newer versions of a document state: an older version without the
# facts draws none of this note, while any other unresolved reference makes the version unresolved.
VERSIONED_NOTES = ("independence",)
# A reference to another document version pins a fact the reader cannot follow to the result.
_WORKLOAD = re.compile(r"\{(?:param:|hash:[^|{}]*\|)measured\.parameters\.workload\.")
_VERSIONED = re.compile(
    r"\{(?:param:(?!measured\.|content\.|reader\.)|(?:count|countword|version|duration|size|imagesize|machine|product):(?!measured\.|content\.)"
    r"|hash:[^|{}]*\|(?!measured\.)"
    r"|commit:(?:[^|{}]*\|)*(?!measured\.|[0-9a-f]{40}\}|(?:[a-z]+:)?[\w.-]+/[\w.-]+\||[A-Za-z0-9-]+\|))[^{}]*\}"
)


def load_reader(root: Path) -> dict[str, Any] | None:
    """The parsed reader source, or None in a tree that has none (a unit test's).

    ``notes.trial_steps`` still carries its step markers; every reader except the compile takes
    the source through ``plain_reader``.
    """
    path = root / READER_PATH
    if not path.is_file():
        return None
    source: dict[str, Any] = load_yaml(path.read_text(encoding="utf-8"))
    return source


def plain_reader(root: Path) -> dict[str, Any] | None:
    """The reader source with each trial-step note as the Details text reads it (no markers) and
    every version gate taken as holding."""
    loaded = load_reader(root)
    if loaded is None:
        return None
    source: dict[str, Any] = _map_strings(copy.deepcopy(loaded), ungated)
    for method in source.get("methods", {}).values():
        notes = method.get("notes", {}) if isinstance(method, dict) else {}
        if isinstance(notes.get("trial_steps"), str):
            notes["trial_steps"] = STEP_MARK.sub("", notes["trial_steps"])
    return source


def _map_strings(value: Any, change: Callable[[str], str]) -> Any:
    if isinstance(value, str):
        return change(value)
    if isinstance(value, dict):
        return {key: _map_strings(item, change) for key, item in value.items()}
    if isinstance(value, list):
        return [_map_strings(item, change) for item in value]
    return value


def _strings(value: Any, path: str = "") -> list[tuple[str, str]]:
    if isinstance(value, str):
        return [(path, value)]
    if isinstance(value, Mapping):
        return [item for key, child in value.items() for item in _strings(child, f"{path}.{key}")]
    return []


def _raw_strings(source: Any) -> list[tuple[str, str]]:
    """Every string of the authored reader source, gates and markers still in it."""
    return _strings(source or {})


def _names_rows(root: Path) -> tuple[dict[str, Mapping[str, Any]], dict[str, Mapping[str, Any]]]:
    """Block scopes (tabs, sections, subsections) by id, and tests and indexes by id."""
    scopes: dict[str, Mapping[str, Any]] = {}
    tests: dict[str, Mapping[str, Any]] = {}
    for row in load_tables(root).names:
        if row["kind"] in {"test", "index"}:
            tests[str(row["id"])] = row
        elif row["kind"] in {"tab", "section", "subsection"}:
            scopes[str(row["id"])] = row
    return scopes, tests


def _scopes_of(
    scopes: Mapping[str, Mapping[str, Any]], tests: Mapping[str, Mapping[str, Any]], identifier: str
) -> set[str]:
    """The block scopes a test may read: common, and the tab and section it sits in."""
    allowed = {COMMON_SCOPE}
    parent = tests.get(identifier, {}).get("parent")
    while parent and parent in scopes and parent not in allowed:
        allowed.add(str(parent))
        parent = scopes[str(parent)].get("parent")
    return allowed


def _cost_wording(value: Any, path: str = "") -> list[str]:
    """The paths of reader fields that are cost wording: a field named for cost, or a read of a
    shared cost block."""
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            found.extend(_cost_wording(child, f"{path}.{key}"))
            if "cost" in str(key):
                found.append(f"{path}.{key}")
    elif isinstance(value, str) and re.search(
        r"\{param:reader\.common\.blocks\.[a-z0-9-]+\.cost", value
    ):
        found.append(path)
    return found


def cost_scope_errors(source: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> list[str]:
    """Rule cost-scope: a test under a section that shows no cost carries no
    cost wording; its Cost section is not drawn."""
    return [
        f"{READER_PATH}: {identifier}{path}: cost-scope: its section shows no cost (names table "
        "shows_cost), so it carries no cost wording"
        for identifier, entry in source.get("methods", {}).items()
        if not shows_cost(identifier, rows)
        for path in sorted(set(_cost_wording(entry)))
    ]


def reader_errors(root: Path) -> list[str]:
    """Schema errors, and any fact reference that is not to the measured definition."""
    source = plain_reader(root)
    if source is None:
        return []
    errors = [
        f"{READER_PATH}: {'/'.join(str(part) for part in error.absolute_path)}: {error.message}"
        for error in Draft202012Validator(load_schema("reader")).iter_errors(source)
    ]
    for identifier, entry in source.get("methods", {}).items():
        errors.extend(
            f"{READER_PATH}: {identifier}{path}: {match} pins a document version; "
            "read the measured definition with {param:measured.<path>}"
            for path, text in _strings(entry)
            for match in _VERSIONED.findall(text)
        )
    errors.extend(
        f"{READER_PATH}: {path}: {error}"
        for path, text in _raw_strings(load_reader(root))
        for error in gate_errors(text)
    )
    scopes, tests = _names_rows(root)
    errors.extend(cost_scope_errors(source, load_tables(root).names))
    for identifier, entry in source.get("methods", {}).items():
        page = tests.get(identifier, {}).get("page")
        kept = PAGE_MEMBER_NOTES | (
            INDEX_PANEL_NOTES if tests.get(identifier, {}).get("kind") == "index" else set()
        )
        extra = sorted(set(entry.get("notes", {})) - kept) if page else []
        if extra:
            errors.append(
                f"{READER_PATH}: {identifier}: its methodology page is {page}'s, so its copy keeps "
                f"only its own sentences ({', '.join(sorted(kept))}), not "
                f"{', '.join(extra)}"
            )
    # The page wording (rule page-host-wording) is the page's, so only a test whose page holds
    # other tests has it.
    hosts = {row.get("page") for row in tests.values()}
    errors.extend(
        f"{READER_PATH}: {identifier}: {', '.join(page_notes)} is a page's wording, and no other "
        "test's page is this test's"
        for identifier, entry in source.get("methods", {}).items()
        if identifier not in hosts
        if (page_notes := sorted(set(entry.get("notes", {})) & PAGE_NOTES))
    )
    # A methodology page draws its subtitle from its test's introduction, or from page_introduction
    # when its page holds other tests (the web page reads nothing else), so each one has it.
    methods = source.get("methods", {})
    errors.extend(
        f"{READER_PATH}: {identifier}: its methodology page has no subtitle; give it notes.{note}"
        for identifier, row in tests.items()
        if row.get("document") and not row.get("page")
        for note in ["page_introduction" if identifier in hosts else "introduction"]
        if identifier in methods and note not in methods[identifier].get("notes", {})
    )
    blocks = source.get("common", {}).get("blocks", {})
    errors.extend(
        f"{READER_PATH}: common.blocks.{scope}: names no tab, section or common"
        for scope in blocks
        if scope != COMMON_SCOPE and scope not in scopes
    )
    for identifier, entry in source.get("methods", {}).items():
        allowed = _scopes_of(scopes, tests, identifier)
        errors.extend(
            f"{READER_PATH}: {identifier}{path}: reads block {match[1]}.{match[2]}, but only the "
            "blocks of its own tab, its section and common are open to it"
            for path, text in _strings(entry)
            for match in BLOCK.finditer(text)
            if match[1] not in allowed
        )
    return errors


def reader_slot_errors(reader: Mapping[str, Any], criteria: Mapping[str, Any]) -> list[str]:
    """Check the newer notes (a list's items one by one) against the slot that owns each field.

    The older fields keep the checks in ``tests/test_reader_conventions.py``; existing sentences
    in them may run past a slot's sentence count.
    """
    owners = {
        slot["source"].removeprefix("notes."): slot
        for slot in criteria["slots"].values()
        if slot.get("source", "").removeprefix("notes.") in SLOT_CHECKED_NOTES
    }
    errors: list[str] = []
    for identifier, method in reader["methods"].items():
        for field, value in method["text"].get("notes", {}).items():
            if field not in owners:
                continue
            items = value if isinstance(value, list) else [value]
            limit = owners[field].get("max_items")
            if limit is not None and len(items) > limit:
                count = len(items)
                errors.append(
                    f"{identifier}.notes.{field}: at most {limit} items are allowed, got {count}"
                )
            errors.extend(
                f"{identifier}.notes.{field}[{index}]: {error}"
                for index, item in enumerate(items)
                for error in slot_errors(item, owners[field])
            )
    return errors


# "1 trials": a number written before a plural noun. A name that carries a number ("Sandbox 1
# starts") has a capitalised word before the digit, and a noun ending in ss, us or is is no plural.
_ONE_PLURAL = re.compile(r"(?<![\w.$/-])1 ([a-z]{3,}s)\b")
_NOT_PLURAL = re.compile(r"(?:ss|us|is)$")
# A run of seven digits or more that is no part of a word, a hash, a grouped number or a decimal.
# A seed is an identifier, not a count: "seed = 20261004".
_SEED = re.compile(r"seed\W*$")
_FULL_HASH = re.compile(r"(?<![0-9a-f])[0-9a-f]{40,}(?![0-9a-f])")
_BARE_INTEGER = re.compile(r"(?<![\w.,$/-])\d{7,}(?![\w,]|\.\d)")


def _reader_sentences(reader: Mapping[str, Any]) -> Iterator[tuple[str, str]]:
    """Every string of every version of every method's text, with where it is."""
    for identifier, method in reader["methods"].items():
        texts = {"": method["text"], **{f"@{v}": t for v, t in method["by_version"].items()}}
        for version, text in texts.items():
            yield from reader_strings(text, f"{identifier}{version}")


def reader_count_errors(reader: Mapping[str, Any]) -> list[str]:
    """Every sentence of every version that writes "1" before a plural noun: word the count so
    it reads right for any number ("trials run in batches of up to 1", "each replayed 1x")."""
    errors: list[str] = []
    for where, sentence in _reader_sentences(reader):
        for match in _ONE_PLURAL.finditer(sentence):
            before = sentence[: match.start()].split()
            if _NOT_PLURAL.search(match[1]) or (before and before[-1][:1].isupper()):
                continue
            errors.append(
                f"{where}: '1 {match[1]}' is a plural after one; "
                "word the count so it reads right for any number"
            )
    return sorted(set(errors))


def reader_integer_errors(reader: Mapping[str, Any]) -> list[str]:
    """Every sentence that prints an integer of seven or more digits with no grouping: a byte
    count is written as a size (`{size:...}`: "2 GiB", "640,000,000 bytes"). A seed is exempt."""
    return sorted(
        {
            f"{where}: '{match[0]}' is a bare integer; write a size with a unit or group the digits"
            for where, sentence in _reader_sentences(reader)
            for match in _BARE_INTEGER.finditer(sentence)
            if not _SEED.search(sentence[: match.start()])
        }
    )


def reader_hash_errors(reader: Mapping[str, Any]) -> list[str]:
    """Every sentence that writes a full hash outside a link: a SHA-256 is {hash:...} and a Git
    commit is {commit:...} (rule methodology-hash-links)."""
    return sorted(
        {
            f"{where}: '{match[0]}' is a full hash; link it with {{hash:...}} or {{commit:...}}"
            for where, sentence in _reader_sentences(reader)
            for match in _FULL_HASH.finditer(LINK_MARK.sub(" ", sentence))
        }
    )


def reader_similarity_errors(reader: Mapping[str, Any], criteria: Mapping[str, Any]) -> list[str]:
    """Check each method's displayed notes against the rules that compare one slot with another."""
    errors: list[str] = []
    for identifier, method in reader["methods"].items():
        notes = method["text"].get("notes", {})
        values = {
            slot: notes[source.removeprefix("notes.")]
            for slot, rule in criteria["slots"].items()
            if (source := rule.get("source", "")).startswith("notes.")
            and isinstance(notes.get(source.removeprefix("notes.")), str)
        }
        errors.extend(f"{identifier}.{error}" for error in text_similarity_errors(values, criteria))
    errors.extend(reader_count_errors(reader))
    errors.extend(reader_integer_errors(reader))
    errors.extend(reader_hash_errors(reader))
    return errors


def _source_commit(root: Path) -> str | None:
    """The commit of the source being compiled: a release stamps it, a checkout says it.

    Any other source has none: a stale release marker is never stamped on it.
    """
    if root.resolve() == _PACKAGED_ASSETS.resolve():
        return METHODOLOGY_COMMIT
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    commit = result.stdout.strip()
    return commit if result.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", commit) else None


def reader_common(root: Path) -> dict[str, Any]:
    """Shared reader wording; measured environment facts stay with their bound definitions."""
    source = plain_reader(root)
    common: dict[str, Any] = (source or {}).get("common", {})
    return common


def exported_common(common: Mapping[str, Any], criteria: Mapping[str, Any]) -> dict[str, Any]:
    """Publish the named composite sentence block; other shared blocks remain sources."""
    exported: dict[str, Any] = {}
    keys = criteria["conventions"]["details"]["composite"]["sentence_source"].split(".")[2:]
    block: Any = common
    for key in keys:
        block = block.get(key) if isinstance(block, Mapping) else None
    if block is None:
        return exported
    target = exported
    for key in keys[:-1]:
        target = target.setdefault(key, {})
    target[keys[-1]] = block
    return exported


def _apply_headline_clocks(
    root: Path,
    methodologies: Mapping[str, Mapping[str, Any]],
    methods: Mapping[str, Mapping[str, Any]],
) -> None:
    """Write each version's headline clock into its reader view (``clocks.headline``).

    A method names the clocks its headline number is read from (``clock``); At a Glance's Clock
    row is their names-table labels (glance_clock), not the job host's clock for every page.
    """
    labels = clock_labels(load_tables(root).names)
    for identifier, entry in ((load_reader(root) or {}).get("methods", {})).items():
        if "clock" not in entry:
            continue
        for row in methodologies[methods[identifier]["document"]].values():
            clocks = row["reader_definition"].setdefault("clocks", {})
            clocks["headline"] = clock_row(entry["clock"], labels)


def _apply_hash_marks(
    root: Path,
    methodologies: Mapping[str, Mapping[str, Any]],
    methods: Mapping[str, Mapping[str, Any]],
) -> None:
    """Draw a definition text's SHA-256 as a link on its word in every version's reader view.

    A method's ``hash_marks`` names each such field and its word; a released record keeps the text.
    """
    for identifier, entry in ((load_reader(root) or {}).get("methods", {})).items():
        for path, label in entry.get("hash_marks", {}).items():
            *parents, key = path.split(".")
            for row in methodologies[methods[identifier]["document"]].values():
                holder = row["reader_definition"]
                for parent in parents:
                    holder = holder[parent]
                holder[key] = mark_sha256(label, holder[key])


def _variant_commit(commits: Mapping[str, Any], variant: Mapping[str, Any]) -> tuple[str, str]:
    """The repository and commit a variant's parameter names ("<image>:<commit>")."""
    image, _, commit = variant["parameters"][commits["field"]].partition(":")
    return commits["repositories"][image], commit


def _newest_labels(versions: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    """Each variant's label in the newest version that has it: a task keeps one name everywhere."""
    labels: dict[str, str] = {}
    for version in sorted(versions, key=document_version):
        labels |= {row["id"]: row["label"] for row in version["procedure"]["variants"]}
    return labels


def _apply_variant_commits(
    root: Path,
    methodologies: Mapping[str, Mapping[str, Any]],
    methods: Mapping[str, Mapping[str, Any]],
) -> None:
    """Write each variant's newest label and its commit's page into every version's reader view."""
    urls = content_criteria()["methodology_page"]["hash_link"]["commit_urls"]
    for identifier, entry in ((load_reader(root) or {}).get("methods", {})).items():
        if commits := entry.get("variant_commits"):
            rows = methodologies[methods[identifier]["document"]].values()
            labels = _newest_labels(row["reader_definition"] for row in rows)
            for row in rows:
                for variant in row["reader_definition"]["procedure"]["variants"]:
                    variant["label"] = labels[variant["id"]]
                    variant["link"] = commit_url(*_variant_commit(commits, variant), urls)


def apply_reader_views(
    root: Path, methodologies: Mapping[str, Mapping[str, Any]], reader: Mapping[str, Any]
) -> None:
    """Write headline clocks, marked hashes and variant links; preserve bound environment facts."""
    _apply_headline_clocks(root, methodologies, reader["methods"])
    _apply_hash_marks(root, methodologies, reader["methods"])
    _apply_variant_commits(root, methodologies, reader["methods"])


def reader_source(root: Path) -> dict[str, Any]:
    """Where the compiled copy came from: the path and the commit and URL it was compiled at."""
    commit = _source_commit(root)
    return {
        "path": READER_PATH,
        "source_commit": commit,
        "source_url": f"{repository_url()}/blob/{commit}/{READER_PATH}" if commit else None,
    }


def compile_reader(
    root: Path,
    *,
    documents: Mapping[tuple[str, str], Mapping[str, Any]],
    document_ids: Mapping[str, str],
    project: Callable[[Mapping[str, Any]], dict[str, Any]],
) -> dict[str, Any] | None:
    """Compile each method's copy against its measured versions.

    ``document_ids`` maps a canonical method id to its document id; ``project`` turns a document
    carrying the reader's wording into the text slots a page draws (headline, notes, panel).
    The entry's ``text`` is resolved against the newest measured version; ``by_version`` holds only
    the versions whose resolved copy differs, and ``unresolved_versions`` those the copy cannot
    describe (a fact path the measured definition does not have). A consumer asked for one of those
    must fail, naming the id and version (``reader_text``); it never falls back to the newest text
    or to newer scientific facts.
    """
    source = load_reader(root)
    if source is None:
        return None
    if errors := reader_errors(root):
        raise ValueError(f"{READER_PATH} is not valid: {'; '.join(errors)}")
    methods: dict[str, Any] = {}
    current = ordinary_documents(list(documents.values()), common=default_context(root))
    _, tests = _names_rows(root)
    aliases = load_tables(root).aliases.get("document", {})
    for identifier, entry in source["methods"].items():
        if identifier not in document_ids:
            raise ValueError(f"{READER_PATH}: {identifier!r} names no test or index")
        document_id = document_ids[identifier]
        entry = with_blocks(entry, source.get("common", {}).get("blocks", {}))
        versions = sorted(
            (
                _withpinned_workload(root, doc, documents, entry)
                for (doc_id, _), doc in documents.items()
                if doc_id == document_id
            ),
            key=document_version,
        )
        methods[identifier] = {
            "document": document_id,
            **_compile_method(
                root,
                identifier,
                _with_subtitles(root, identifier, entry),
                versions,
                project,
                partial(
                    _timed_by_page_members,
                    identifier=identifier,
                    tests=tests,
                    document_ids=document_ids,
                    documents=documents,
                    aliases=aliases,
                ),
                partial(
                    _member_parts,
                    identifier=identifier,
                    source=source,
                    tests=tests,
                    document_ids=document_ids,
                    documents=documents,
                    aliases=aliases,
                ),
                current_version=str(current[document_id]["version"])
                if document_id in current
                else None,
            ),
        }
    return methods


def compile_presentation(root: Path) -> dict[str, Any]:
    """Project only the methodology owner's explicitly reader-facing presentation data."""
    criteria = content_criteria()
    if errors := criteria_errors(criteria):
        raise ValueError(f"presentation source is not valid: {'; '.join(errors)}")
    conventions = load_conventions(root)
    tables = load_tables(root)
    schema = presentation_schema()
    hover_templates = {
        code: _selected(entry, tuple(schema["$defs"]["hoverFields"]["properties"]))
        for code, entry in criteria["hover_templates"].items()
    }
    hover_slots = {
        code: _selected(entry, tuple(schema["$defs"]["hoverFields"]["properties"]))
        for code, entry in criteria["hover_slots"].items()
    }
    rule_ids = _presentation_rule_ids(schema)
    rules = [rule for rule in conventions["presentation"]["rules"] if rule["id"] in rule_ids]
    names = [
        {key: row[key] for key in schema["$defs"]["sectionName"]["properties"]}
        for row in tables.names
        if row["kind"] == schema["$defs"]["sectionName"]["properties"]["kind"]["const"]
    ]
    presentation = {
        "schema": schema["properties"]["schema"]["const"],
        "content_criteria": {
            "hover_templates": hover_templates,
            "hover_slots": hover_slots,
            "statistics_selector": {
                key: criteria["statistics_selector"][key]
                for key in schema["$defs"]["statisticsSelector"]["properties"]
            },
            "conventions": {"presentation": {"rules": rules}},
        },
        "names": names,
    }
    validate_presentation(presentation)
    return presentation


def presentation_schema() -> dict[str, Any]:
    """Return the owner-exported schema used by consumers to validate presentation payloads."""
    return load_schema("presentation")


def _presentation_rule_ids(schema: Mapping[str, Any]) -> set[str]:
    """Read permitted rule ids from the owner schema's closed rule variants."""
    one_of = schema["properties"]["content_criteria"]["properties"]["conventions"]["properties"][
        "presentation"
    ]["properties"]["rules"]["items"]["oneOf"]
    definitions = schema["$defs"]
    ids: set[str] = set()
    for reference in one_of:
        definition = definitions[reference["$ref"].rsplit("/", 1)[1]]
        ids.add(str(definition["allOf"][1]["properties"]["id"]["const"]))
    return ids


def validate_presentation(
    payload: Mapping[str, Any], *, pinned_names: Sequence[Mapping[str, Any]] | None = None
) -> None:
    """Fail closed on unknown payload fields and, when supplied, changed pinned identities."""
    schema = presentation_schema()
    errors = list(Draft202012Validator(schema).iter_errors(payload))
    if errors:
        paths = ["/".join(str(part) for part in error.absolute_path) for error in errors]
        raise ValueError(f"presentation payload is invalid: {', '.join(paths)}")
    if pinned_names is None:
        return
    section_kind = schema["$defs"]["sectionName"]["properties"]["kind"]["const"]
    expected_sections = {
        str(row["id"]): row
        for row in pinned_names
        if row.get("kind") == section_kind and isinstance(row.get("id"), str)
    }
    projected = {str(row["id"]): row for row in payload["names"]}
    if len(projected) != len(payload["names"]) or set(projected) != set(expected_sections):
        raise ValueError("presentation names must preserve the pinned section identities")
    if any(row.get("kind") != section_kind for row in projected.values()):
        raise ValueError("presentation names cannot change a pinned taxonomy kind")


def _selected(entry: Mapping[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {key: entry[key] for key in fields if key in entry}


def _compile_method(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    root: Path,
    identifier: str,
    entry: Mapping[str, Any],
    versions: list[Mapping[str, Any]],
    project: Callable[[Mapping[str, Any]], dict[str, Any]],
    timed_by_members: Callable[[Mapping[str, Any]], Collection[str]] | None = None,
    member_parts: Callable[[Mapping[str, Any]], Sequence[Mapping[str, str]]] | None = None,
    *,
    current_version: str | None = None,
) -> dict[str, Any]:
    """One method's copy against each of its measured versions, newest first resolved."""
    resolved: dict[str, dict[str, Any]] = {}
    unresolved: list[str] = []
    headline = entry.get("headline_steps", [])

    def compiled(measured: Mapping[str, Any], wording: Mapping[str, Any]) -> dict[str, Any]:
        carrier = _carrier(measured, wording, strict=str(measured["version"]) == current_version)
        text = project(apply_panel_text(root, [carrier])[0])
        note = text.get("notes", {}).get("trial_steps")
        steps = measured.get("procedure", {}).get("steps", [])
        strict = str(measured["version"]) == current_version
        if strict and any(step.get("timer") for step in steps) and not headline:
            raise ValueError("headline_steps: name the steps that make the number")
        if strict:
            headline_span_errors(measured.get("metrics", []), steps, headline)
        if isinstance(note, str):
            clean, items = trial_step_items(
                note,
                steps,
                strict=strict,
                timed_by_members=timed_by_members(measured) if timed_by_members else (),
                headline=headline,
            )
            if step_notes := entry.get("step_notes"):
                flat = {f"stepnote_{key}": value for key, value in step_notes.items()}
                words = project(_carrier(measured, {"notes": flat}))["notes"]
                items = with_step_notes(
                    items, {key: words[f"stepnote_{key}"] for key in step_notes}, strict=strict
                )
            text["notes"] = {**text["notes"], "trial_steps": clean, "step_items": items}
        text["notes"] = {**text["notes"], **_resolved_tables(project, measured, entry, versions)}
        if limits := time_limits(steps):
            # Step names may name a stage or a test ({name:stage.create}).
            flat = {f"limit{at}_step": row["step"] for at, row in enumerate(limits)}
            words = project(_carrier(measured, {"notes": flat}))["notes"]
            text["notes"] = {
                **text["notes"],
                "time_limits": [
                    {**row, "step": words[f"limit{at}_step"]} for at, row in enumerate(limits)
                ],
            }
        parts = [
            *timed_parts(steps, headline, strict=strict),
            *(member_parts(measured) if member_parts else ()),
        ]
        if parts := [row for at, row in enumerate(parts) if row not in parts[:at]]:
            # Step names and timer phrases may name other tests ({name:test.7-zip-all-vcpus}).
            flat = {
                f"part{at}_{key}": value
                for at, row in enumerate(parts)
                for key, value in row.items()
            }
            words = project(_carrier(measured, {"notes": flat}))["notes"]
            text["notes"] = {
                **text["notes"],
                "timed_parts": [
                    {key: words[f"part{at}_{key}"] for key in row} for at, row in enumerate(parts)
                ],
            }
        return text

    for measured in versions:
        version = str(measured["version"])
        try:
            resolved[version] = compiled(measured, entry)
        except (PlaceholderError, ValueError) as error:
            if str(measured["version"]) == current_version:
                raise ValueError(f"{identifier}@{version}: {error}") from error
            try:
                resolved[version] = compiled(measured, _without_versioned_notes(entry))
            except (PlaceholderError, ValueError):
                unresolved.append(version)
    return {
        "text": resolved.get(current_version or "", {}),
        "by_version": {
            version: text
            for version, text in resolved.items()
            if text != resolved.get(current_version or "", {})
        },
        "unresolved_versions": unresolved,
    }


def _page_members(  # pylint: disable=too-many-arguments
    identifier: str,
    measured: Mapping[str, Any],
    tests: Mapping[str, Mapping[str, Any]],
    document_ids: Mapping[str, str],
    documents: Mapping[tuple[str, str], Mapping[str, Any]],
    *,
    aliases: Mapping[str, str] | None = None,
) -> list[tuple[str, Mapping[str, Any]]]:
    rows = [
        {"kind": "test", **row, "id": member, "document": document_ids[member]}
        for member, row in tests.items()
        if member in document_ids
    ]
    return member_documents(measured, identifier, documents, rows, aliases or {})


def _timed_by_page_members(  # pylint: disable=too-many-arguments
    measured: Mapping[str, Any],
    *,
    identifier: str,
    tests: Mapping[str, Mapping[str, Any]],
    document_ids: Mapping[str, str],
    documents: Mapping[tuple[str, str], Mapping[str, Any]],
    aliases: Mapping[str, str] | None = None,
) -> set[str]:
    """The timers of members bound to this measured host's common and effective date."""
    return {
        str(step["id"])
        for _, member in _page_members(
            identifier, measured, tests, document_ids, documents, aliases=aliases
        )
        for step in member.get("procedure", {}).get("steps", [])
        if step.get("timer")
    }


def _resolved_tables(
    project: Callable[[Mapping[str, Any]], dict[str, Any]],
    measured: Mapping[str, Any],
    entry: Mapping[str, Any],
    versions: Sequence[Mapping[str, Any]],
) -> dict[str, list[dict[str, str]]]:
    """The tables a page draws as notes: the reader's own rows, resolved against this version, a
    row per variant that names a commit, and the definition's variants where each runs its own
    command."""
    tables: dict[str, list[dict[str, str]]] = {}
    rows = {name: list(rows) for name, rows in entry.get("tables", {}).items()}
    if commits := entry.get("variant_commits"):
        labels = _newest_labels(versions)
        rows[commits["table"]] = [
            {
                commits["field"]: labels[variant["id"]],
                "repository": f"`{_variant_commit(commits, variant)[0]}`",
                "commit": f"{{commit:{_variant_commit(commits, variant)[0]}|"
                f"measured.procedure.variants.{at}.parameters.{commits['field']}}}",
            }
            for at, variant in enumerate(measured["procedure"]["variants"])
        ]
    flat = {
        f"{name}{at}_{key}": value
        for name, table in rows.items()
        for at, row in enumerate(table)
        for key, value in row.items()
    }
    words = project(_carrier(measured, {"notes": flat}))["notes"] if flat else {}
    for name, table in rows.items():
        tables[name] = [
            {key: words[f"{name}{at}_{key}"] for key in row} for at, row in enumerate(table)
        ]
    if commands := variant_commands(measured.get("procedure", {}).get("variants", [])):
        tables["variant_commands"] = commands
    return tables


def _member_parts(  # pylint: disable=too-many-arguments
    measured: Mapping[str, Any],
    *,
    identifier: str,
    source: Mapping[str, Any],
    tests: Mapping[str, Mapping[str, Any]],
    document_ids: Mapping[str, str],
    documents: Mapping[tuple[str, str], Mapping[str, Any]],
    aliases: Mapping[str, str] | None = None,
) -> list[dict[str, str]]:
    """The headline parts from members selected for this exact measured host."""
    parts: list[dict[str, str]] = []
    for member, document in _page_members(
        identifier, measured, tests, document_ids, documents, aliases=aliases
    ):
        if headline := source["methods"].get(member, {}).get("headline_steps"):
            parts += timed_parts(
                document.get("procedure", {}).get("steps", []), headline, strict=True
            )
    return parts


def _without_versioned_notes(entry: Mapping[str, Any]) -> dict[str, Any]:
    notes = {
        key: text for key, text in entry.get("notes", {}).items() if key not in VERSIONED_NOTES
    }
    return {**entry, "notes": notes}


def _withpinned_workload(
    root: Path,
    measured: Mapping[str, Any],
    documents: Mapping[tuple[str, str], Mapping[str, Any]],
    entry: Mapping[str, Any],
) -> Mapping[str, Any]:
    """The measured document, carrying its pinned workload when the reader copy reads it."""
    text = dump_yaml(entry)
    reads_variants = "measured.parameters.variants." in text
    if not _WORKLOAD.search(text) and not reads_variants:
        return measured
    enriched = copy.deepcopy(dict(measured))
    parameters = dict(enriched.get("parameters", {}))
    if _WORKLOAD.search(text):
        parameters["workload"] = pinned_workload(root, measured, documents)
    if reads_variants:  # each variant's recorded data (an image's facts), by the variant's id
        path = root / str(measured["parameters"]["path"]) / "workloads.json"
        parameters["variants"] = {
            item["id"]: item["data"] for item in json.loads(path.read_text(encoding="utf-8"))
        }
    enriched["parameters"] = parameters
    return enriched


def _carrier(
    measured: Mapping[str, Any], entry: Mapping[str, Any], *, strict: bool = False
) -> dict[str, Any]:
    """The measured document with its wording replaced by the reader's, facts read from itself.

    A sentence gated on a parameter (``[[when ...]]``) stays only where the measured definition
    satisfies it; ``strict`` makes a gate on a path the definition lacks an error.
    """
    entry = gated(entry, measured, strict=strict)
    carrier = copy.deepcopy(dict(measured))
    for key in ("headline", "notes", "panel"):
        carrier.pop(key, None)
    prefix = f"{measured['id']}.{measured['version']}."

    def bind(match: re.Match[str]) -> str:
        """Point each measured path of one reference (every factor of a product) at this version."""
        target = re.sub(r"(^|\*|\|)measured\.", lambda found: found[1] + prefix, match[2])
        return f"{{{match[1]}:{target}}}"

    wording = load_yaml(_MEASURED.sub(bind, dump_yaml(entry, allow_unicode=True)))
    carrier.update(wording)
    return carrier


def _with_subtitles(root: Path, identifier: str, entry: Mapping[str, Any]) -> dict[str, Any]:
    """The entry with a workload's cost_summary generated from the subtitle template."""
    generated = generated_notes(root, identifier)
    return {**entry, "notes": {**entry.get("notes", {}), **generated}} if generated else dict(entry)


class ReaderVersionError(LookupError):
    """The reader copy cannot describe the asked method or measured version."""


def reader_text(bundle: Mapping[str, Any], identifier: str, version: str) -> dict[str, Any]:
    """The reader copy of a method as it reads for one measured version, or a deliberate error.

    A method or version the bundle does not know, and a version its copy cannot describe, raise
    ``ReaderVersionError`` naming both; nothing falls back to the newest copy or facts.
    """
    methods = bundle["reader"]["methods"]
    if identifier not in methods:
        raise ReaderVersionError(f"no reader copy for method {identifier!r}")
    method = methods[identifier]
    if version not in bundle["methodologies"].get(method["document"], {}):
        raise ReaderVersionError(f"method {identifier!r} has no measured version {version!r}")
    if version in method["unresolved_versions"]:
        raise ReaderVersionError(
            f"the reader copy of {identifier!r} cannot describe measured version {version!r}"
        )
    text: dict[str, Any] = method["by_version"].get(version, method["text"])
    return text
