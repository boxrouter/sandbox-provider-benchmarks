# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Write the successors of the documents that inherit one sbs.common, so they inherit a newer one.

    python scripts/successors.py --from 1.0.13 --to 1.0.16 --effective 2026-11-06 \\
        [--only <id> ...] [--facts <image facts JSON>] [--preserve-explicit-overrides]

Write the new common by hand first: it is the change. This command writes what follows from it.

1. The next version of each chosen document: ordinary versions selected within the exact --from
   common, or only the --only ids. A successor differs from its predecessor only in its version,
   its effective date, its common version and the image identity (when the two commons' images
   differ). It also moves its shared_trial partner's version and its own parameter tree: the tree
   is copied with the image identity replaced, and the row versions and digest are recomputed.
2. A generated document moves through its source. The Exec Latency pair is frozen into
   exec-latency-<version>.yaml, the source moves to the new versions, and
   scripts/build_exec_views.py rewrites exec-views.yaml.
3. A composite whose inputs all moved gets a next version. It inherits its inputs' new common and
   pins the inputs' new versions (tests/test_composite_inputs.py states both rules). A composite
   with only some inputs moved keeps its version and is named on stderr: the inputs it pins still
   share one common.
4. methodology/released.lock is updated.

When the image changes, a workload row that states facts read from the image (Image Build's
`standard` row) needs them from --facts: a JSON object with the image `digest` and the facts.
A missing common, missing facts or a split generated pair is refused before anything is written.
A common round can opt into --preserve-explicit-overrides to retain every explicit environment
field and parameter byte while changing only the common identity and successor references.
A document whose layout a text edit does not match stops the round partway, so run it on a clean
worktree and discard the changes if it stops. This replaces the one-off lane scripts of #616, #657
and #672.

To refresh only selected derived indices after their leaves have already advanced, use
--refresh-composites --only <index ids> --effective <date>, without --from/--to. It selects
ordinary input versions in the owner default common, or an explicit --common, at that date.
It moves no leaf or image; all-version maxima only allocate collision-free versions.
"""

import argparse
import importlib.util
import itertools
import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from yaml.nodes import MappingNode, ScalarNode, SequenceNode

from sandbox_benchmark_verifier import lock
from sandbox_benchmark_verifier.catalog import (
    context_region_errors,
    default_context,
    ordinary_documents,
)
from sandbox_benchmark_verifier.composites import composition_input_errors
from sandbox_benchmark_verifier.names import RetiredIdError, UnknownIdError, load_tables, resolve
from sandbox_benchmark_verifier.parameters import tree_digest, workload_version
from sandbox_benchmark_verifier.versions import version_key
from sandbox_benchmark_verifier.yaml_load import load_yaml

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "build_exec_views", Path(__file__).with_name("build_exec_views.py")
)
assert _SPEC and _SPEC.loader
build = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(build)
FACT_KEYS = ("compressed_bytes", "files", "layers", "uncompressed_bytes")


@dataclass
class Current:
    """The newest version of one document: where it is, its YAML block and its parsed form."""

    path: Path
    block: str
    document: dict[str, Any]

    @property
    def version(self) -> str:
        return str(self.document["version"])


@dataclass
class Round:
    root: Path
    old_common: str
    new_common: str
    effective: str
    images: list[tuple[str, str]]
    """(old, new) strings to replace: the image reference, then its bare digest."""
    facts: dict[str, int] | None
    moved: dict[str, str] = field(default_factory=dict)
    """Each document id the round moved, with its new version."""
    notes: list[str] = field(default_factory=list)
    """Composites the round left on their version, and why."""


@dataclass(frozen=True)
class BoundInput:
    """An input identity resolved by the methodology owner's published names API."""

    canonical_id: str
    current: Current


def blocks(text: str) -> list[str]:
    return re.split(r"(?m)^---\n", text)


def next_version(version: str) -> str:
    head, _, patch = version.rpartition(".")
    return f"{head}.{int(patch) + 1}"


def one(pattern: str, replacement: str, text: str) -> str:
    result, count = re.subn(pattern, replacement, text, count=1, flags=re.M)
    if count != 1:
        raise SystemExit(f"successors: no single match for {pattern!r}")
    return result


def history(root: Path) -> list[Current]:
    """All immutable versions, retained for exact archives and version allocation."""
    found = []
    for path in sorted((root / "methodology").rglob("*.yaml")):
        for block in blocks(path.read_text(encoding="utf-8")):
            document = load_yaml(block)
            if not isinstance(document, dict) or "id" not in document:
                continue
            found.append(Current(path, block, document))
    return found


def newest(root: Path, effective: str | None = None) -> dict[str, Current]:
    """All-version maxima; never a scientific context selector."""
    found: dict[str, Current] = {}
    for held in history(root):
        if effective is not None and str(held.document["effective"]) > effective:
            continue
        identifier = str(held.document["id"])
        if identifier not in found or version_key(held.version) > version_key(
            found[identifier].version
        ):
            found[identifier] = held
    return found


def selected_context(
    root: Path, common_version: str, effective: str | None = None
) -> dict[str, Current]:
    """Bind the common before selecting ordinary leaf or index versions."""
    records = history(root)
    try:
        selected = ordinary_documents(
            [row.document for row in records],
            common={"id": "sbs.common", "version": common_version},
            effective=effective,
        )
    except ValueError as error:
        raise SystemExit(f"successors: {error}") from error
    exact = {(str(row.document["id"]), row.version): row for row in records}
    return {
        identifier: exact[identifier, str(document["version"])]
        for identifier, document in selected.items()
    }


def common(root: Path, version: str) -> dict[str, Any]:
    path = root / f"methodology/00-common-{version}.yaml"
    if not path.exists():
        raise SystemExit(f"successors: {path.relative_to(root)} does not exist; write it first")
    document: dict[str, Any] = load_yaml(path.read_text(encoding="utf-8"))
    if str(document["version"]) != version:
        raise SystemExit(f"successors: {path.relative_to(root)} is not version {version}")
    return document


def canonical_document(identifier: str, tables: Any) -> str:
    """Resolve a stored document id through the owner's published identity table."""
    try:
        return resolve("document", identifier, tables=tables)
    except RetiredIdError as error:
        raise SystemExit(f"successors: retired document reference {identifier}") from error
    except UnknownIdError as error:
        raise SystemExit(f"successors: unknown document reference {identifier}") from error


def owner_tables(root: Path) -> Any:
    """Read this checkout's identity artifacts, or the package owner's artifacts in tree tests."""
    if (root / "names/names.yaml").is_file() and (root / "names/aliases.yaml").is_file():
        return load_tables(root)
    return load_tables()


def bind_document(identifier: str, current: dict[str, Current], tables: Any) -> BoundInput:
    """Select the canonical actual document, or the one owner-mapped actual document."""
    canonical = canonical_document(identifier, tables)
    candidates: list[Current] = []
    for actual_id, held in current.items():
        try:
            actual_canonical = resolve("document", actual_id, tables=tables)
        except (RetiredIdError, UnknownIdError):
            continue
        if actual_canonical == canonical:
            candidates.append(held)
    actual = current.get(canonical)
    if actual is not None:
        return BoundInput(canonical, actual)
    named_documents = {
        str(row["document"])
        for row in tables.names
        if row.get("id") == canonical
        and row.get("kind") in ("test", "index", "hero", "tab")
        and isinstance(row.get("document"), str)
    }
    named_candidates = [
        candidate for candidate in candidates if candidate.document["id"] in named_documents
    ]
    if len(named_candidates) == 1:
        return BoundInput(canonical, named_candidates[0])
    if len(named_candidates) > 1:
        ids = sorted(str(candidate.document["id"]) for candidate in named_candidates)
        raise SystemExit(
            f"successors: ambiguous owner-named actual documents for {identifier} "
            f"({canonical}): {ids}"
        )
    if len(candidates) == 1:
        return BoundInput(canonical, candidates[0])
    if not candidates:
        raise SystemExit(f"successors: missing actual document for {identifier} ({canonical})")
    ids = sorted(str(candidate.document["id"]) for candidate in candidates)
    raise SystemExit(
        f"successors: ambiguous actual documents for {identifier} ({canonical}): {ids}"
    )


def bind_inputs(held: Current, current: dict[str, Current], tables: Any) -> list[BoundInput]:
    """Bind the one composition vector, rejecting repeated canonical identities."""
    inputs = (held.document.get("composition") or {}).get("inputs") or []
    bound: list[BoundInput] = []
    seen: set[str] = set()
    for item in inputs:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise SystemExit(f"successors: {held.document['id']} has an invalid input reference")
        selected = bind_document(item["id"], current, tables)
        if selected.canonical_id in seen:
            raise SystemExit(
                f"successors: {held.document['id']} has duplicate input identity "
                f"{selected.canonical_id} after resolution"
            )
        seen.add(selected.canonical_id)
        bound.append(selected)
    return bound


def _mapping_value(node: Any, key: str) -> Any | None:
    if not isinstance(node, MappingNode):
        return None
    return next(
        (value for name, value in node.value if isinstance(name, ScalarNode) and name.value == key),
        None,
    )


def _patch_scalar(block: str, node: Any, value: str, edits: list[tuple[int, int, str]]) -> None:
    if not isinstance(node, ScalarNode):
        raise SystemExit("successors: expected a scalar methodology reference")
    start, end = node.start_mark.index, node.end_mark.index
    raw = block[start:end]
    if raw.startswith(("'", '"')) and raw[-1:] == raw[:1]:
        value = f"{raw[0]}{value}{raw[0]}"
    if raw != value:
        edits.append((start, end, value))


def _rewrite_composition_references(
    block: str,
    bindings: list[BoundInput],
    versions: list[str],
    tables: Any,
) -> str:
    """Rewrite member, scope and plot references through one already selected input vector."""
    root = yaml.compose(block)
    composition = _mapping_value(root, "composition")
    if not isinstance(composition, MappingNode):
        raise SystemExit("successors: composition is not an object")
    inputs = _mapping_value(composition, "inputs")
    if not isinstance(inputs, SequenceNode) or len(inputs.value) != len(bindings):
        raise SystemExit("successors: composition inputs changed while planning")
    if len(versions) != len(bindings):
        raise SystemExit("successors: composition input versions changed while planning")

    by_canonical = {
        item.canonical_id: (item, version) for item, version in zip(bindings, versions, strict=True)
    }
    edits: list[tuple[int, int, str]] = []
    for item_node, item, version in zip(inputs.value, bindings, versions, strict=True):
        identifier_node = _mapping_value(item_node, "id")
        version_node = _mapping_value(item_node, "version")
        _patch_scalar(block, identifier_node, str(item.current.document["id"]), edits)
        _patch_scalar(block, version_node, version, edits)

    scopes = _mapping_value(composition, "scopes")
    if isinstance(scopes, SequenceNode):
        for scope in scopes.value:
            members = _mapping_value(scope, "inputs")
            if not isinstance(members, SequenceNode):
                continue
            for member in members.value:
                if not isinstance(member, ScalarNode):
                    raise SystemExit("successors: composition scope input is not a document id")
                canonical = canonical_document(member.value, tables)
                selected = by_canonical.get(canonical)
                if selected is None:
                    raise SystemExit(
                        f"successors: scope member {member.value} is not in the input vector"
                    )
                _patch_scalar(block, member, str(selected[0].current.document["id"]), edits)

    plots = _mapping_value(composition, "plots")
    if isinstance(plots, MappingNode):
        for axis in ("x", "y"):
            reference = _mapping_value(plots, axis)
            if reference is None:
                continue
            identifier_node = _mapping_value(reference, "id")
            version_node = _mapping_value(reference, "version")
            if not isinstance(identifier_node, ScalarNode):
                raise SystemExit(f"successors: plot axis {axis} has no document id")
            canonical = canonical_document(identifier_node.value, tables)
            selected = by_canonical.get(canonical)
            if selected is None:
                raise SystemExit(
                    f"successors: plot axis {axis} {identifier_node.value} "
                    "is not in the input vector"
                )
            _patch_scalar(block, identifier_node, str(selected[0].current.document["id"]), edits)
            _patch_scalar(block, version_node, selected[1], edits)

    for start, end, replacement in sorted(edits, reverse=True):
        block = block[:start] + replacement + block[end:]
    return block


def ensure_acyclic(current: dict[str, Current], tables: Any, starts: set[str]) -> None:
    """Refuse a selected composite graph with a resolved dependency cycle before writing."""
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(identifier: str) -> None:
        if identifier in visited:
            return
        if identifier in visiting:
            raise SystemExit(f"successors: selected composite graph has a cycle at {identifier}")
        held = current.get(identifier)
        if held is None or not (held.document.get("composition") or {}).get("inputs"):
            visited.add(identifier)
            return
        visiting.add(identifier)
        for item in bind_inputs(held, current, tables):
            target = str(item.current.document["id"])
            visit(target)
        visiting.remove(identifier)
        visited.add(identifier)

    for identifier in sorted(starts):
        visit(identifier)


def image_replacements(old: dict[str, Any], new: dict[str, Any]) -> list[tuple[str, str]]:
    before = str(old["environment"]["image"]["reference"])
    after = str(new["environment"]["image"]["reference"])
    if before == after:
        return []
    return [(before, after), (before.rpartition(":")[2], after.rpartition(":")[2])]


def replace_images(text: str, images: list[tuple[str, str]]) -> str:
    for before, after in images:
        text = text.replace(before, after)
    return text


def inherits(text: str, old: str, new: str) -> str:
    return one(
        rf"^(\s*inherits:\n\s+id: sbs\.common\n\s+version: ){re.escape(old)}$", rf"\g<1>{new}", text
    )


def copy_parameters(work: Round, identifier: str, version: str, parameters: dict[str, Any]) -> str:
    """Copy a document's parameter tree to its successor's path; return the new tree digest."""
    source = work.root / parameters["path"]
    target = work.root / f"parameters/{identifier}/{version}"
    shutil.copytree(source, target)
    if not work.images:
        return tree_digest(target)
    for file in sorted(path for path in target.rglob("*") if path.is_file()):
        contents = file.read_bytes()
        named = any(before.encode() in contents for before, _ in work.images)
        if named and file.name != "workloads.json":
            raise SystemExit(f"successors: {file} names the image; only workloads.json may")
    workloads = target / "workloads.json"
    if workloads.exists():
        raw = replace_images(workloads.read_text(encoding="utf-8"), work.images)
        rows = json.loads(raw)
        new_image = work.images[-1][1]
        for row in rows:
            if new_image in json.dumps(row) and "facts" in row.get("data", {}):
                assert work.facts is not None, "run() refuses a round that needs facts without them"
                row["data"]["facts"] = dict(work.facts)
        before = json.loads(raw)
        for row in rows:
            row["version"] = workload_version(row)
        if raw == json.dumps(before, indent=2, sort_keys=True) + "\n":
            raw = json.dumps(rows, indent=2, sort_keys=True) + "\n"
        else:
            # Keep the file's own layout: only the row versions may change.
            for old_row, row in zip(before, rows, strict=True):
                raw = raw.replace(old_row["version"], row["version"])
            if json.loads(raw) != rows:
                raise SystemExit(f"successors: {workloads} changes more than its row versions")
        workloads.write_text(raw, encoding="utf-8")
    return tree_digest(target)


def succeed(work: Round, identifier: str, held: Current, text: str) -> str:
    """The successor's YAML block, with its parameter tree written."""
    version = work.moved[identifier]
    text = one(r"^version: .*$", f"version: {version}", text)
    text = one(r"^effective: .*$", f"effective: '{work.effective}'", text)
    text = inherits(text, work.old_common, work.new_common)
    text = replace_images(text, work.images)
    shared = (held.document.get("procedure") or {}).get("shared_trial")
    if shared and shared["with"] in work.moved:
        text = one(
            rf"^(\s+shared_trial:\n\s+with: {re.escape(shared['with'])}\n\s+version: )"
            rf"{re.escape(str(shared['version']))}$",
            rf"\g<1>{work.moved[shared['with']]}",
            text,
        )
    parameters = held.document.get("parameters") or {}
    if parameters.get("path"):
        digest = copy_parameters(work, identifier, version, parameters)
        text = text.replace(parameters["path"], f"parameters/{identifier}/{version}/")
        if text.count(parameters["sha256"]) != 1:
            raise SystemExit(f"successors: {identifier} names its parameter digest twice")
        text = text.replace(parameters["sha256"], digest)
    return text


def append(path: Path, block: str) -> None:
    """Append a document to its file without repeating the file's header comment lines.

    A successor starts from its predecessor's raw block, which holds the header (license, schema)
    when the predecessor was the file's first document.
    """
    text = path.read_text(encoding="utf-8")
    assert text.endswith("\n"), path
    header = set(itertools.takewhile(lambda line: line.startswith("#"), text.splitlines()))
    lines = block.splitlines(keepends=True)
    while lines and lines[0].rstrip("\n") in header:
        lines.pop(0)
    block = "".join(lines)
    path.write_text(f"{text}---\n{block if block.endswith(chr(10)) else block + chr(10)}", "utf-8")


def move_generated(work: Round) -> None:
    """Freeze the generated pair, move its source and regenerate."""
    output = work.root / build.OUTPUT
    text = output.read_text(encoding="utf-8")
    assert text.startswith(build.HEADER)
    first = load_yaml(text[len(build.HEADER) :].split("\n---\n")[0])
    frozen = output.with_name(f"{first['id']}-{first['version']}.yaml")
    if frozen.exists():
        raise SystemExit(f"successors: {frozen} exists")
    frozen.write_text(text[len(build.HEADER) :], encoding="utf-8")
    source_path = work.root / build.SOURCE
    source = source_path.read_text(encoding="utf-8")
    parsed = load_yaml(source)
    if work.images and any(
        before in json.dumps(json.loads(path.read_text(encoding="utf-8")))
        for before, _ in work.images
        for path in (work.root / parsed["shared"]["parameters"]["path"]).rglob("*.json")
    ):
        raise SystemExit("successors: the shared Exec Latency parameters name the image")
    source = inherits(source, work.old_common, work.new_common)
    source = replace_images(source, work.images)
    for view in parsed["views"].values():
        source = one(
            rf"^(    id: {re.escape(view['id'])}\n    version: ){re.escape(str(view['version']))}"
            r"\n    effective: .*$",
            rf"\g<1>{work.moved[view['id']]}\n    effective: '{work.effective}'",
            source,
        )
    source_path.write_text(source, encoding="utf-8")
    build.main(work.root)


def plan_composites(
    work: Round, current: dict[str, Current], version_heads: dict[str, Current]
) -> list[Current]:
    """Give each composite whose inputs all moved a successor on the new common; repeat for
    composites of composites. A composite with only some inputs moved keeps its version: it still
    pins inputs that inherit one common, the versions in force when it took effect. Plan the full
    graph before any successor files are written."""
    tables = owner_tables(work.root)
    canonical_composites = {
        identifier
        for identifier, held in current.items()
        if (held.document.get("composition") or {}).get("inputs")
        and bind_document(identifier, current, tables).current.document["id"] == identifier
    }
    ensure_acyclic(current, tables, canonical_composites)
    planning = dict(current)
    proposed: list[Current] = []
    changed = True
    while changed:
        changed = False
        for identifier in sorted(canonical_composites):
            held = planning[identifier]
            inputs = (held.document.get("composition") or {}).get("inputs") or []
            if identifier in work.moved or not inputs:
                continue
            bindings = bind_inputs(held, planning, tables)
            moved_inputs = [item for item in bindings if item.current.document["id"] in work.moved]
            if not moved_inputs:
                continue
            unmoved = sorted(
                str(item.current.document["id"])
                for item in bindings
                if item.current.document["id"] not in work.moved
            )
            if unmoved:
                note = f"{identifier} keeps {held.version}: inputs {unmoved} did not move"
                if note not in work.notes:
                    work.notes.append(note)
                continue
            versions = [work.moved[str(item.current.document["id"])] for item in bindings]
            text = _rewrite_composition_references(held.block, bindings, versions, tables)
            work.moved[identifier] = next_version(version_heads[identifier].version)
            composite_work = Round(
                work.root,
                str(held.document["inherits"]["version"]),
                work.new_common,
                work.effective,
                work.images,
                work.facts,
                work.moved,
                work.notes,
            )
            text = succeed(composite_work, identifier, held, text)
            successor = Current(held.path, text, load_yaml(text))
            planning[identifier] = successor
            proposed.append(successor)
            changed = True
    return proposed


def validate_planned(root: Path, documents: list[dict[str, Any]]) -> None:
    """Validate only the planned graph against exact history before appending definitions."""
    complete = [row.document for row in history(root)] + documents
    for document in documents:
        if "composition" in document:
            if errors := composition_input_errors(document, complete):
                raise SystemExit("successors: " + "; ".join(errors))


def run(
    root: Path,
    old_common: str,
    new_common: str,
    effective: str,
    only: list[str] | None = None,
    facts: dict[str, Any] | None = None,
    preserve_explicit_overrides: bool = False,
) -> dict[str, str]:
    """Write one successor round; return each moved document id with its new version."""
    current, version_heads = selected_context(root, old_common), newest(root)
    old, new = common(root, old_common), common(root, new_common)
    images = [] if preserve_explicit_overrides else image_replacements(old, new)
    if facts is not None and images and facts.get("digest") != images[0][1].rpartition("@")[2]:
        raise SystemExit("successors: --facts names a different image than the new common")
    work = Round(
        root,
        old_common,
        new_common,
        effective,
        images,
        {key: int(facts[key]) for key in FACT_KEYS} if facts is not None else None,
    )

    def chosen(identifier: str, held: Current) -> bool:
        inherited = (held.document.get("inherits") or {}).get("version")
        composite = bool((held.document.get("composition") or {}).get("inputs"))
        return (
            identifier != "sbs.common"
            and not composite
            and inherited == old_common
            and (only is not None or held.version == version_heads[identifier].version)
        )

    moving = {
        identifier: held
        for identifier, held in current.items()
        if chosen(identifier, held) and (only is None or identifier in only)
    }
    missing = sorted(set(only or ()) - set(moving))
    if missing:
        raise SystemExit(f"successors: {missing} are not documents that inherit {old_common}")
    for held in moving.values():
        if errors := context_region_errors(held.document, new):
            raise SystemExit("successors: " + "; ".join(errors))
    work.moved = {
        identifier: next_version(version_heads[identifier].version) for identifier in moving
    }
    if work.images and work.facts is None:
        stated = sorted(
            f"{identifier}/{row['id']}"
            for identifier, held in moving.items()
            if (held.document.get("parameters") or {}).get("path")
            and (rows := root / held.document["parameters"]["path"] / "workloads.json").exists()
            for row in json.loads(rows.read_text(encoding="utf-8"))
            if "facts" in row.get("data", {})
            and any(before in json.dumps(row) for before, _ in work.images)
        )
        if stated:
            raise SystemExit(f"successors: {stated} state facts read from the image; pass --facts")
    composite_successors = plan_composites(work, current, version_heads)
    validate_planned(
        root,
        [
            {
                **held.document,
                "version": work.moved[identifier],
                "effective": effective,
                "inherits": {"id": "sbs.common", "version": new_common},
            }
            for identifier, held in moving.items()
        ]
        + [held.document for held in composite_successors],
    )
    generated = {i: held for i, held in moving.items() if held.path == root / build.OUTPUT}
    if generated:
        source = load_yaml((root / build.SOURCE).read_text(encoding="utf-8"))
        together = {str(document["id"]) for document in build.documents(source)}
        if set(generated) != together:
            raise SystemExit(f"successors: the generated {sorted(together)} move together")
        move_generated(work)
    for identifier, held in sorted(moving.items()):
        if identifier not in generated:
            append(held.path, succeed(work, identifier, held, held.block))
    for held in composite_successors:
        append(held.path, held.block)
    lock.update(root)
    for note in work.notes:
        print(f"successors: {note}", file=sys.stderr)
    return work.moved


def refresh_composites(
    root: Path, effective: str, only: list[str], *, common_version: str | None = None
) -> dict[str, str]:
    """Refresh selected indices from owner inputs in force; never move a measured leaf.

    Prepare the complete selected dependency graph before appending anything, so a missing
    reference or mixed common cannot leave half a refresh behind.
    """
    context = common_version if common_version is not None else default_context(root)["version"]
    current, latest = selected_context(root, context, effective), selected_context(root, context)
    version_heads = newest(root)
    tables = owner_tables(root)
    requested = set(only)
    if not requested:
        raise SystemExit("successors: --refresh-composites requires --only")
    pending: set[str] = set()
    for identifier in sorted(requested):
        selected = bind_document(identifier, current, tables).current
        actual_id = str(selected.document["id"])
        if selected.document.get("category") != "index" or not selected.document.get(
            "composition", {}
        ).get("inputs"):
            raise SystemExit(f"successors: {identifier} is not an index in force")
        if actual_id not in latest or selected.version != latest[actual_id].version:
            raise SystemExit(f"successors: {identifier} has a later effective version")
        pending.add(actual_id)
    ensure_acyclic(current, tables, pending)

    planned: dict[str, Current] = {}
    while pending:
        completed: set[str] = set()
        for identifier in sorted(pending):
            held = current[identifier]
            bindings = bind_inputs(held, current, tables)
            if any(str(item.current.document["id"]) in pending for item in bindings):
                continue
            inherited = {
                tuple(sorted(item.current.document.get("inherits", {}).items()))
                for item in bindings
            }
            if len(inherited) != 1 or dict(next(iter(inherited))).get("id") != "sbs.common":
                raise SystemExit(f"successors: {identifier} inputs do not share one common")
            common_version = str(dict(next(iter(inherited)))["version"])
            versions = [item.current.version for item in bindings]
            planned_text = _rewrite_composition_references(held.block, bindings, versions, tables)
            changed = (
                planned_text != held.block
                or str(held.document["inherits"]["version"]) != common_version
            )
            if changed:
                work = Round(
                    root,
                    str(held.document["inherits"]["version"]),
                    common_version,
                    effective,
                    [],
                    None,
                    {identifier: next_version(version_heads[identifier].version)},
                )
                block = succeed(work, identifier, held, planned_text)
                proposed = Current(held.path, block, load_yaml(block))
                planned[identifier] = proposed
                current[identifier] = proposed
            completed.add(identifier)
        if not completed:
            raise SystemExit("successors: selected indices have a dependency cycle")
        pending -= completed
    validate_planned(root, [proposed.document for proposed in planned.values()])
    for proposed in planned.values():
        append(proposed.path, proposed.block)
    lock.update(root)
    return {identifier: held.version for identifier, held in planned.items()}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--from", dest="old", help="the common to move from")
    parser.add_argument("--to", dest="new", help="the new common, already written")
    parser.add_argument("--effective", required=True, help="the successors' date, YYYY-MM-DD")
    parser.add_argument("--common", help="exact source common for --refresh-composites")
    parser.add_argument("--only", nargs="+", help="move only these document ids")
    parser.add_argument("--facts", type=Path, help="JSON: the new image's digest and facts")
    parser.add_argument(
        "--preserve-explicit-overrides",
        action="store_true",
        help="move common identity without changing explicit image overrides or parameter bytes",
    )
    parser.add_argument(
        "--refresh-composites", action="store_true", help="refresh only selected index inputs"
    )
    args = parser.parse_args(argv)
    facts = json.loads(args.facts.read_text(encoding="utf-8")) if args.facts else None
    if args.refresh_composites:
        if args.old or args.new or facts is not None or args.preserve_explicit_overrides:
            parser.error("--refresh-composites does not move a common or image")
        moved = refresh_composites(
            ROOT, args.effective, args.only or [], common_version=args.common
        )
    else:
        if args.common:
            parser.error("--common is only for --refresh-composites; a round already binds --from")
        if not args.old or not args.new:
            parser.error("a common round requires --from and --to")
        moved = run(
            ROOT,
            args.old,
            args.new,
            args.effective,
            args.only,
            facts,
            preserve_explicit_overrides=args.preserve_explicit_overrides,
        )
    for identifier, version in sorted(moved.items()):
        print(identifier, "->", version)


if __name__ == "__main__":
    main(sys.argv[1:])
