# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Checks on one names-table row: sentence form, presentation, numbered and per-unit words, page."""

from typing import Any


def sentence_errors(row: dict[str, Any], where: str) -> list[str]:
    """A sentence form is the lower-case word of a statistic or view, used inside a sentence."""
    if "sentence" not in row or (
        row["kind"] in ("statistic", "view") and row["sentence"] == row["sentence"].lower()
    ):
        return []
    return [f"{where}: a sentence form belongs to a statistic or view and is lower case"]


def presentation_errors(row: dict[str, Any], rows: list[dict[str, Any]], where: str) -> list[str]:
    """Presentation belongs to a test or tab; its references must resolve in this table."""
    presentation = row.get("presentation")
    if presentation is None:
        return []
    if row["kind"] not in ("test", "tab"):
        return [f"{where}: presentation belongs to a test or a tab"]
    errors: list[str] = []
    capability = presentation.get("capability")
    if capability is not None and not any(
        r["kind"] == "capability" and r["id"] == capability for r in rows
    ):
        errors.append(f"{where}: presentation capability {capability!r} is not a capability name")
    workloads = {r["id"] for r in rows if r["kind"] == "subsection"}
    if presentation.get("own_time_total") and row.get("parent") not in workloads:
        errors.append(f"{where}: only a workload test can draw its own time total")
    return errors


def capability_name_errors(row: dict[str, Any], where: str) -> list[str]:
    """A capability's name is its written name, never its id (the id is a catalog key)."""
    if row["kind"] != "capability" or row["name"] != row["id"]:
        return []
    return [f"{where}: the name {row['name']!r} is the id; write the capability's name"]


def view_subtitle_errors(row: dict[str, Any], where: str) -> list[str]:
    """A combined view (a view of a panel, not of the overview) has its own subtitle."""
    if row["kind"] == "view":
        if row.get("parent") != "overview" and "subtitle" not in row:
            return [f"{where}: a combined view needs its own subtitle"]
        if row.get("parent") == "overview" and "subtitle" in row:
            return [f"{where}: an overview measure view has no subtitle of its own"]
    elif "subtitle" in row:
        return [f"{where}: a subtitle belongs to a combined view"]
    return []


def numbered_errors(row: dict[str, Any], where: str) -> list[str]:
    """A numbered unit ("Sandbox 1") is one owner string on a per-unit word, with {n} in it."""
    if "numbered" not in row:
        return []
    if row["kind"] != "per-unit":
        return [f"{where}: a numbered form belongs to a per-unit word"]
    if row["numbered"].replace("{n}", "").strip().lower() != row["name"]:
        return [f"{where}: numbered {row['numbered']!r} must be the word {row['name']!r} and {{n}}"]
    return []


def unit_label_errors(row: dict[str, Any], rows: list[dict[str, Any]], where: str) -> list[str]:
    """A test's selector reads its unit word: the label is the unit's name, capitalised."""
    label = row.get("selector", {}).get("label")
    unit = next(
        (r["name"] for r in rows if r["kind"] == "per-unit" and r["id"] == row.get("unit")), ""
    )
    if "unit" not in row or label is None or label.lower() == unit:
        return []
    return [f"{where}: selector label {label!r} must be the unit word {unit!r}"]


def unit_description_errors(row: dict[str, Any], where: str) -> list[str]:
    """A counted row describes its unit once, as one sentence: a capital and a full stop. A test's
    unit sentences are its contracts/text/workload-subtitles.json entry, so it has none here."""
    if "unit" not in row:
        return []
    if row["kind"] == "test":
        if "unit_description" in row:
            return [
                f"{where}: a test's unit sentences are in contracts/text/workload-subtitles.json"
            ]
        return []
    description = row.get("unit_description", "")
    if description and description[0].isupper() and description.endswith("."):
        return []
    return [f"{where}: needs a unit_description that is a sentence, with a capital and a full stop"]


def reads_as_errors(row: dict[str, Any], rows: list[dict[str, Any]], where: str) -> list[str]:
    """A per-unit word of a locked definition may read as another per-unit word."""
    target = row.get("reads_as")
    if target is None:
        return []
    known = {r["id"] for r in rows if r["kind"] == "per-unit" and "reads_as" not in r}
    if row["kind"] == "per-unit" and target in known:
        return []
    return [f"{where}: reads_as must name a per-unit word that is not itself read as another"]


def common_section_errors(row: dict[str, Any], where: str) -> list[str]:
    """A hero row's `common_section` says its document is drawn as that section of the Common
    methodology page, which is then its page."""
    if "common_section" not in row:
        return []
    if row["kind"] != "hero" or "document" not in row:
        return [f"{where}: only a hero with a document is a section of the Common page"]
    return []


def page_errors(row: dict[str, Any], rows: list[dict[str, Any]], where: str) -> list[str]:
    """A test's (or index's) `page` names the test (index) of the same tab (section) whose
    methodology page draws it too.

    The page is the tab's: it holds every test of the tab, and the tab's name is its name.
    """
    target_id = row.get("page")
    if target_id is None:
        return []
    if row["kind"] not in ("test", "index"):
        return [f"{where}: only a test or an index has a page"]
    kind = row["kind"]
    target = next((r for r in rows if r["kind"] == kind and r["id"] == target_id), None)
    if target is None:
        return [f"{where}: page {target_id!r} is not {'an index' if kind == 'index' else 'a test'}"]
    errors = []
    if target.get("parent") != row.get("parent"):
        errors.append(f"{where}: page {target_id!r} is not in the same tab")
    if "page" in target:
        errors.append(f"{where}: page {target_id!r} has a page itself (no chains)")
    if "document" not in row:
        errors.append(f"{where}: a test with a page keeps its own document, which its data binds")
    for other in rows:
        if (
            other["kind"] == kind
            and other.get("parent") == row.get("parent")
            and target_id not in (other["id"], other.get("page"))
        ):
            errors.append(
                f"{where}: the page of {target_id!r} is its tab's page, so {other['id']!r} "
                "needs it too"
            )
    return errors


def with_page_titles(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The rows with each methodology page's title on the test that hosts the page.

    A page is hosted by a test (or an index) without a `page`. The only page of a results tab or
    section is titled by its name, the name its chart panel and the rail use; a page of a tab with
    several pages keeps its test's name.
    """
    tabs = {row["id"]: row["name"] for row in rows if row["kind"] in ("tab", "section")}
    hosts = [row for row in rows if row["kind"] in ("test", "index") and "page" not in row]
    pages = {parent: sum(row.get("parent") == parent for row in hosts) for parent in tabs}
    return [
        {
            **row,
            "page_title": tabs[row.get("parent")]
            if pages.get(row.get("parent")) == 1
            else row["name"],
        }
        if row in hosts
        else row
        for row in rows
    ]
