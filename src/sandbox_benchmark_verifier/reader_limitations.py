# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""The tests a limitation applies to, on a page that holds several tests.

A page host's ``notes.limitations`` is read by every test on the page, so each limitation starts
with ``[[tests: list, write-4kib]]``, naming the tests it applies to, or ``[[tests: all]]`` for
every test on the page. The compile turns the list into ``limitation_items`` (each limitation with
its tests, ``all`` written out) and leaves ``limitations`` as the plain sentences the methodology
page draws. A page that holds no other test carries no marker.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from typing import Any

ALL_TESTS = "all"
LIMITATION_MARK = re.compile(r"^\s*\[\[tests:([^\[\]]*)\]\]\s*")


def split_limitation(text: str) -> tuple[list[str] | None, str]:
    """A limitation's test ids (None when it has no marker) and its plain sentence."""
    mark = LIMITATION_MARK.match(text)
    if mark is None:
        return None, text
    return [part.strip() for part in mark[1].split(",") if part.strip()], text[mark.end() :]


def limitation_items(
    limitations: Sequence[str], members: Collection[str]
) -> tuple[list[str], list[dict[str, Any]]]:
    """The plain sentences and, when any carries a marker, one item (text, tests) for each; the
    tests of ``all`` are the page's ``members``, in sorted order."""
    split = [split_limitation(text) for text in limitations]
    plain = [text for _, text in split]
    if all(tests is None for tests, _ in split):
        return plain, []
    return plain, [
        {"text": text, "tests": sorted(members) if tests == [ALL_TESTS] else tests or []}
        for tests, text in split
    ]


def limitation_errors(
    identifier: str, limitations: Sequence[str], members: Collection[str], *, host: bool
) -> list[str]:
    """What is wrong with the markers of one method's limitations.

    A page host (``host``) tags every limitation with tests it holds (``members``: the host and
    each test whose page it is); any other method carries no marker.
    """
    errors: list[str] = []
    for index, text in enumerate(limitations):
        where = f"{identifier}.limitations[{index}]"
        tests, _ = split_limitation(text)
        if not host:
            if tests is not None:
                errors.append(f"{where}: only a page that holds other tests tags a limitation")
            continue
        if tests is None:
            errors.append(
                f"{where}: this page holds other tests, so each limitation starts with "
                "[[tests: ...]] naming the tests it applies to"
            )
            continue
        if not tests:
            errors.append(f"{where}: [[tests: ...]] names no test")
        if ALL_TESTS in tests and len(tests) > 1:
            errors.append(f"{where}: [[tests: all]] stands alone")
            continue
        errors.extend(
            f"{where}: {test!r} is not a test on this page"
            for test in tests
            if test not in members and test != ALL_TESTS
        )
        if len(set(tests)) != len(tests):
            errors.append(f"{where}: [[tests: ...]] names a test twice")
    return errors


def page_members(tests: Mapping[str, Mapping[str, Any]], identifier: str) -> set[str]:
    """The host and the tests whose methodology page is the host's."""
    return {identifier} | {member for member, row in tests.items() if row.get("page") == identifier}
