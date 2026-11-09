# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Generate a machine panel's title from fields, and check its command against the run.

A machine test names its standard in ``source.name`` (the title's tool), its tab owns the panel
name and measure, and the test's ``panel`` block holds its command. No finished title is written by
hand; the title is the one panel-title template filled with those parts. The panel's subtitle is
the test's Introduction.
"""

import copy
import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from sandbox_benchmark_verifier.names import tables_view
from sandbox_benchmark_verifier.panel_titles import panel_title, title_errors
from sandbox_benchmark_verifier.placeholders import resolve_template
from sandbox_benchmark_verifier.versions import newest


def _tabs(root: Path) -> dict[str, Mapping[str, Any]]:
    """The tab row of every test document, by document id."""
    rows = tables_view(root).names
    tabs = {str(row["id"]): row for row in rows if row.get("kind") == "tab"}
    return {
        str(row["document"]): tabs[str(row["parent"])]
        for row in rows
        if row.get("kind") == "test" and "document" in row and str(row.get("parent")) in tabs
    }


def _selections(root: Path) -> dict[str, str]:
    """The option a test is among its tab's other tests, by document id.

    A tab with no selector of its own whose tests are the options of one selector (Disk's fio
    tests) names the chosen option in the title; any other tab's title has no selection.
    """
    rows = tables_view(root).names
    own_selector = {
        str(row["id"]) for row in rows if row.get("kind") == "tab" and "selector" in row
    }
    options: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        option = row.get("selector", {}).get("option") if row.get("kind") == "test" else None
        if option is not None and "document" in row and str(row.get("parent")) not in own_selector:
            options.setdefault(str(row["parent"]), []).append(row)
    return {
        str(row["document"]): str(row["selector"]["option"])
        for tests in options.values()
        if len(tests) > 1
        for row in tests
    }


def title_parts(
    root: Path,
    document: Mapping[str, Any],
    *,
    tabs: Mapping[str, Mapping[str, Any]] | None = None,
    selections: Mapping[str, str] | None = None,
) -> dict[str, str] | None:
    """The owner parts of a machine test's title, or None when the test is not a machine test.

    A caller looping over documents passes ``tabs`` and ``selections`` read once.
    """
    tab = (_tabs(root) if tabs is None else tabs).get(str(document["id"]))
    if tab is None or "measure" not in tab:
        return None
    selected = _selections(root) if selections is None else selections
    return {
        "category": str(tab["name"]),
        "variant": selected.get(str(document["id"]), ""),
        "measure": str(tab["measure"]),
        "tool": str(document["source"]["name"]),
    }


def _has_sources(root: Path) -> bool:
    """A partial tree (a unit test's) holds no machine panels."""
    return (root / "conventions" / "text-conventions.yaml").is_file() and (
        root / "names" / "names.yaml"
    ).is_file()


def apply_panel_text(root: Path, documents: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the documents with each panel block's title and resolved command written in."""
    if not _has_sources(root):
        return list(documents)
    tabs = _tabs(root)
    selections = _selections(root)
    by_version = {f"{item['id']}.{item['version']}": item for item in documents}
    names = {str(row["id"]): str(row["name"]) for row in tables_view(root).names}
    result: list[dict[str, Any]] = []
    for document in documents:
        panel = document.get("panel")
        tab = tabs.get(str(document["id"]))
        parts = title_parts(root, document, tabs=tabs, selections=selections)
        if not isinstance(panel, Mapping) or tab is None or parts is None:
            result.append(document)
            continue
        generated = copy.deepcopy(document)
        generated["panel"] = {
            **panel,
            "title": panel_title(root, **parts),
            "command": resolve_template(str(panel["command"]), names=names, documents=by_version),
        }
        result.append(generated)
    return result


def _normalized(command: str) -> str:
    return re.sub(r"(^|\s)\./", r"\1", command).lower()


def _run_commands(
    root: Path, document: Mapping[str, Any], documents: Mapping[str, Mapping[str, Any]]
) -> list[str]:
    """The command lines the pinned workload parameters say the test runs."""
    shared = document.get("procedure", {}).get("shared_trial")
    owner = documents[f"{shared['with']}.{shared['version']}"] if shared else document
    path = root / str(owner["parameters"]["path"]) / "workloads.json"
    vcpu = str(document["environment"]["size"]["vcpu"])
    commands: list[str] = []
    for workload in json.loads(path.read_text(encoding="utf-8")):
        data = workload["data"]
        if isinstance(data.get("command"), str):
            commands.append(data["command"])
        for row in data.get("rows", {}).values():
            threads = row.get("threads")
            if isinstance(row.get("command"), str):
                count = vcpu if threads == "requested" else str(threads)
                commands.append(row["command"].replace("{threads}", count))
            if "rw" in row and "bs" in row:
                commands.append(f"fio --rw={row['rw']} --bs={row['bs']}")
    return [_normalized(command) for command in commands]


def _document_errors(
    root: Path,
    document: Mapping[str, Any],
    generated: Mapping[str, Mapping[str, Any]],
    *,
    tabs: Mapping[str, Mapping[str, Any]],
    selections: Mapping[str, str],
) -> list[str]:
    """Errors of one machine test's panel block and the title generated from it."""
    label = f"{document['id']}.{document['version']}"
    text = generated[label]["panel"]
    standard = str(document["source"]["name"])
    errors = (
        [] if standard in text["title"] else [f"{label}: the panel title does not name {standard}"]
    )
    parts = title_parts(root, document, tabs=tabs, selections=selections)
    if parts is not None:
        errors.extend(f"{label}: {error}" for error in title_errors(root, text["title"], **parts))
    command = text["command"]
    if not any(_normalized(command) in run for run in _run_commands(root, document, generated)):
        errors.append(f"{label}: panel.command `{command}` is not in the pinned workload")
    return errors


def panel_errors(root: Path, documents: Sequence[dict[str, Any]]) -> list[str]:
    """Errors for every machine test: the panel block, its title and its command."""
    if not _has_sources(root):
        return []
    errors: list[str] = []
    latest = newest(documents)
    generated = {
        f"{item['id']}.{item['version']}": item for item in apply_panel_text(root, documents)
    }
    tabs = _tabs(root)
    selections = _selections(root)
    titles: dict[str, set[str]] = {}
    for identifier, tab in tabs.items():
        document = latest.get(identifier)
        if "measure" not in tab or document is None:
            continue
        if not isinstance(document.get("panel"), Mapping):
            errors.append(
                f"{identifier}.{document['version']}: the {tab['id']} tab's test has no panel block"
            )
            continue
        errors.extend(_document_errors(root, document, generated, tabs=tabs, selections=selections))
        titles.setdefault(str(tab["id"]), set()).add(
            generated[f"{identifier}.{document['version']}"]["panel"]["title"]
        )
    selected = {str(tab["id"]) for identifier, tab in tabs.items() if identifier in selections}
    tests_per_tab = Counter(str(tab["id"]) for tab in tabs.values())
    errors.extend(
        f"the {tab_id} tab's tests generate different titles: {sorted(found)}"
        for tab_id, found in titles.items()
        if tab_id not in selected and len(found) > 1
    )
    errors.extend(
        f"the {tab_id} tab's tests must each generate their own title: {sorted(found)}"
        for tab_id, found in titles.items()
        if tab_id in selected and len(found) != tests_per_tab[tab_id]
    )
    return errors
