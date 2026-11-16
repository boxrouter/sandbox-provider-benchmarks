# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""A page that holds several tests tags each limitation with the tests it applies to."""

from pathlib import Path
from typing import Any

import pytest

from sandbox_benchmark_verifier import reader
from sandbox_benchmark_verifier.reader_limitations import (
    limitation_errors,
    limitation_items,
    page_members,
    split_limitation,
)

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
MEMBERS = {"read-4kib", "list", "write-4kib"}


def test_a_marker_names_the_tests_and_leaves_the_plain_sentence() -> None:
    assert split_limitation("[[tests: list, write-4kib]]A sentence.") == (
        ["list", "write-4kib"],
        "A sentence.",
    )
    assert split_limitation("A sentence.") == (None, "A sentence.")


def test_all_stands_for_every_test_on_the_page_in_the_items() -> None:
    plain, items = limitation_items(["[[tests: list]]One.", "[[tests: all]]Two."], MEMBERS)
    assert plain == ["One.", "Two."]
    assert items == [
        {"text": "One.", "tests": ["list"]},
        {"text": "Two.", "tests": ["list", "read-4kib", "write-4kib"]},
    ]
    assert limitation_items(["One."], MEMBERS) == (["One."], [])


@pytest.mark.parametrize(
    ("limitation", "message"),
    [
        ("No marker.", "each limitation starts with"),
        ("[[tests: ]]Empty.", "names no test"),
        ("[[tests: fork]]Not on the page.", "'fork' is not a test on this page"),
        ("[[tests: list, list]]Twice.", "names a test twice"),
        ("[[tests: all, list]]Mixed.", "stands alone"),
    ],
)
def test_a_page_host_refuses_an_untagged_or_wrongly_tagged_limitation(
    limitation: str, message: str
) -> None:
    errors = limitation_errors("read-4kib", [limitation], MEMBERS, host=True)
    assert len(errors) == 1 and message in errors[0]


def test_a_page_that_holds_no_other_test_carries_no_marker() -> None:
    assert limitation_errors("fork", ["A sentence."], {"fork"}, host=False) == []
    errors = limitation_errors("fork", ["[[tests: all]]A sentence."], {"fork"}, host=False)
    assert len(errors) == 1 and "only a page that holds other tests" in errors[0]


def test_every_limitation_of_a_shared_page_in_the_reader_is_tagged() -> None:
    """The Files page's six limitations were untagged, so Details showed the first three on List,
    Write and Read alike (a write limitation under List)."""
    assert reader.reader_errors(ROOT) == []
    source = reader.load_reader(ROOT)
    assert source is not None
    hosts = {
        row.get("page")
        for row in reader._names_rows(ROOT)[1].values()  # noqa: SLF001
    } - {None}
    assert {"read-4kib", "exec-latency-per-100-calls", "sequential-read"} <= hosts
    for host in hosts:
        assert all(
            split_limitation(text)[0] for text in source["methods"][host]["notes"]["limitations"]
        ), host


def test_each_files_test_is_shown_only_its_own_limitations(bundle: dict[str, Any]) -> None:
    notes = bundle["reader"]["methods"]["read-4kib"]["text"]["notes"]
    by_test = {
        test: [item["text"] for item in notes["limitation_items"] if test in item["tests"]]
        for test in sorted(page_members(reader._names_rows(ROOT)[1], "read-4kib"))  # noqa: SLF001
    }
    assert not any("files.write" in text for text in by_test["list"])
    assert not any("files.list" in text for text in by_test["write-4kib"])
    assert not any("files.list" in text for text in by_test["read-4kib"])
    assert any("files.list" in text for text in by_test["list"])
