# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""The trial steps a reader method draws: its items, their notes and the timed parts.

A method's ``notes.trial_steps`` marks which definition steps each clause describes; the compile
turns the clauses into items (tagged timed or not, and whether it makes the number) and the
headline steps' timers into the Timed Part table.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from typing import Any

from sandbox_benchmark_verifier.placeholders import seconds_text

# Where a trial step's sentence starts in ``notes.trial_steps``: ``[[steps: create, ready_check]]``
# names the definition steps the clause that follows describes, and ``[[untimed]]`` starts a clause
# the definition has no step for (an action done outside the timed steps). The marker is the only
# addition to the Details text, which reads the note without it.
STEP_MARK = re.compile(r"\[\[(?:steps:([^\[\]]*)|(untimed))\]\]")
# A clause that ends by saying it is untimed ("... the git patch (untimed),").
_SAYS_UNTIMED = re.compile(r"\(untimed\)\s*[;,.]?\s*(?:then\s*)?$")


def _step_item_text(segment: str, *, timed: bool) -> str:
    """One clause as a sentence: no joining words, capitalised; ``(untimed)`` only where timed."""
    text = re.sub(r"^(?:then|and)\s+", "", segment.strip())
    text = re.sub(r"(?:[;,.]\s*)?\bthen\s*$", "", text).strip()
    text = re.sub(r"[;,.]\s*$", "", text).strip()
    if not timed:
        text = re.sub(r"\s*\(untimed\)\s*$", "", text).strip()
        text = re.sub(r"[;,.]\s*$", "", text).strip()
    return f"{text[:1].upper()}{text[1:]}." if text else ""


def split_trial_steps(note: str) -> tuple[str, list[tuple[list[str] | None, str]]]:
    """The Details text of ``notes.trial_steps`` and its clauses (step ids, wording); a clause
    marked ``[[untimed]]`` has no step ids (None)."""
    marks = list(STEP_MARK.finditer(note))
    clean = STEP_MARK.sub("", note)
    if marks and note[: marks[0].start()].strip():
        raise ValueError("notes.trial_steps: text before the first [[steps: ...]] marker")
    clauses = []
    for index, mark in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(note)
        ids = None if mark[2] else [part.strip() for part in mark[1].split(",") if part.strip()]
        clauses.append((ids, note[mark.end() : end]))
    return clean, clauses


def _check_step_items(
    clauses: Sequence[tuple[list[str] | None, str]], timed: Mapping[str, bool]
) -> None:
    """The newest version's items cover the definition's steps (see ``trial_step_items``)."""
    seen: dict[str, int] = {}
    for index, (ids, wording) in enumerate(clauses):
        if ids == [] or not _step_item_text(wording, timed=True):
            raise ValueError(
                f"notes.trial_steps: item {index + 1} names no step or has no sentence"
            )
        if ids is None:
            continue
        if len({timed[step_id] for step_id in ids if step_id in timed}) > 1:
            raise ValueError(
                f"notes.trial_steps: item {index + 1} joins timed and untimed steps; "
                "give each its own item, and mark an action with no step [[untimed]]"
            )
        if timed.get(ids[0]) and _SAYS_UNTIMED.search(wording.strip()):
            raise ValueError(
                f"notes.trial_steps: item {index + 1} is tagged timed but says it is "
                "untimed; give the untimed action its own [[untimed]] item"
            )
        for step_id in ids:
            if step_id not in timed:
                raise ValueError(
                    f"notes.trial_steps: item {index + 1} names {step_id!r}, "
                    "which the definition has no step for"
                )
            if step_id in seen:
                raise ValueError(
                    f"notes.trial_steps: step {step_id!r} is in items "
                    f"{seen[step_id] + 1} and {index + 1}"
                )
            seen[step_id] = index
    if len(clauses) == 1 and len(timed) > 1:
        raise ValueError(
            f"notes.trial_steps: one item for a definition of {len(timed)} steps; "
            "give each step or action its own item"
        )
    missing = sorted(
        step_id for step_id, is_timed in timed.items() if is_timed and step_id not in seen
    )
    if missing:
        raise ValueError(f"notes.trial_steps: timed steps without an item: {', '.join(missing)}")


def trial_step_items(
    note: str,
    measured_steps: Sequence[Mapping[str, Any]],
    *,
    strict: bool,
    timed_by_members: Collection[str] = (),
    headline: Collection[str] = (),
) -> tuple[str, list[dict[str, Any]]]:
    """The Details text and the page's items of one measured version, each tagged timed or not.

    An item also says whether it holds one of the ``headline`` steps, the ones that make the number
    (``headline``). It carries no time limit: the page shows none, and the
    definition keeps each step's limit.

    With ``strict`` (the newest version) the items must cover the definition's steps: every timed
    step in exactly one item, no step in two, every item naming a step the definition has. A
    method without markers keeps its note whole when the definition has no steps to list. A page
    that holds several tests also counts a step as timed when the definition of another test on the
    page times a step of that id (``timed_by_members``).
    """
    clean, clauses = split_trial_steps(note)
    timed = {
        str(step["id"]): bool(step.get("timer")) or str(step["id"]) in timed_by_members
        for step in measured_steps
    }
    if not clauses:
        if strict and timed:
            raise ValueError(
                "notes.trial_steps: the definition has steps, "
                "so the note needs [[steps: ...]] markers"
            )
        return clean, [{"text": clean}]
    if strict:
        _check_step_items(clauses, timed)
    items: list[dict[str, Any]] = []
    for ids, wording in clauses:
        if ids is None:
            items.append({"text": _step_item_text(wording, timed=False), "timed": False})
            continue
        item_timed = any(timed.get(step_id, False) for step_id in ids)
        item: dict[str, Any] = {
            "steps": ids,
            "text": _step_item_text(wording, timed=item_timed),
            "timed": item_timed,
        }
        if any(step_id in headline for step_id in ids):
            item["headline"] = True
        items.append(item)
    return clean, items


_SPAN = re.compile(r"^end\(([\w-]+)\)\s*-\s*start\(([\w-]+)\)$")


def headline_span_errors(
    metrics: Sequence[Mapping[str, Any]],
    measured_steps: Sequence[Mapping[str, Any]],
    headline: Collection[str],
) -> None:
    """The headline steps span the number the definition's first metric computes.

    A metric written ``end(Y) - start(X)`` starts where the first headline step records X and ends
    where the last one records Y, so a band over other steps would misstate the method.
    """
    match = _SPAN.match(str(metrics[0].get("formula") or "")) if metrics else None
    steps = [step for step in measured_steps if str(step["id"]) in headline]
    if not (match and steps):
        return
    end, start = match.groups()
    if start not in steps[0].get("record_steps", []):
        raise ValueError(
            f"headline_steps: the first step {steps[0]['id']!r} does not record {start!r}, "
            f"where the metric {metrics[0]['name']} starts"
        )
    if end not in steps[-1].get("record_steps", []):
        raise ValueError(
            f"headline_steps: the last step {steps[-1]['id']!r} does not record {end!r}, "
            f"where the metric {metrics[0]['name']} ends"
        )


def timed_parts(
    measured_steps: Sequence[Mapping[str, Any]], headline: Collection[str], *, strict: bool
) -> list[dict[str, str]]:
    """The headline steps' timers as rows: the part, where its clock starts and where it ends.

    A step's timer says it either as ``start_event`` and ``stop_event`` or as ``from`` and
    ``until``. With ``strict`` (the newest version) every headline step must be one of the
    definition's timed steps.
    """
    rows = []
    for step in measured_steps:
        if str(step["id"]) not in headline:
            continue
        timer = step.get("timer") or {}
        starts = timer.get("start_event") or timer.get("from")
        ends = timer.get("stop_event") or timer.get("until")
        if not (starts and ends):
            if strict:
                raise ValueError(
                    f"headline_steps: step {step['id']!r} has no timer with a start and an end"
                )
            continue
        rows.append({"part": str(step["name"]), "starts": str(starts), "ends": str(ends)})
    if strict:
        missing = sorted(set(headline) - {str(step["id"]) for step in measured_steps})
        if missing:
            raise ValueError(f"headline_steps: the definition has no step {', '.join(missing)}")
    return rows


def variant_commands(variants: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    """The commands a definition's variants run, one row each, when each runs its own command.

    A page whose variants differ in the command they run (Sequential TTI's four first commands)
    shows them as a one-column table of full commands without repeating variant labels; variants
    that run one command or none (a burst size, a data load) are already At a Glance's Variants.
    """
    commands = [str(variant.get("command", "")) for variant in variants]
    if len(variants) < 2 or not all(commands) or len(set(commands)) != len(commands):
        return []
    return [{"command": f"`{command}`"} for command in commands]


def with_step_notes(
    items: Sequence[Mapping[str, Any]], notes: Mapping[str, str], *, strict: bool
) -> list[dict[str, Any]]:
    """The items, each with the note of a step it holds (``note``), drawn under it on the page.

    A note is keyed by a step id; with ``strict`` (the newest version) every note must belong to
    exactly one item, and an item holds at most one note. Older versions skip a note whose step
    they do not have.
    """
    result = [dict(item) for item in items]
    for step_id, text in notes.items():
        owners = [item for item in result if step_id in item.get("steps", [])]
        if not owners:
            if strict:
                raise ValueError(f"step_notes: no trial step item holds step {step_id!r}")
            continue
        if "note" in owners[0]:
            raise ValueError(f"step_notes: the item holding {step_id!r} already has a note")
        owners[0]["note"] = text
    return result


def time_limits(measured_steps: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    """The definition's steps that have a time limit, one row each (step, time_limit), in order.

    Failures and Exclusions draws them as the Step · Time Limit table: a
    step's limit appears there once, and nowhere on the page's pills.
    """
    return [
        {
            "step": str(step.get("name") or step["id"]),
            "time_limit": seconds_text(step["timeout_seconds"]),
        }
        for step in measured_steps
        if step.get("timeout_seconds") is not None
    ]
