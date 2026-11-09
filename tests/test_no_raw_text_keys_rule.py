# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""The no-raw-text-keys rule rejects ownerless reader content."""

import pytest

from sandbox_benchmark_verifier.conventions import load_conventions

pytestmark = pytest.mark.unit


def test_no_raw_text_keys_rejects_unowned_reader_content() -> None:
    rule = next(
        rule
        for rule in load_conventions()["presentation"]["rules"]
        if rule["id"] == "no-raw-text-keys"
    )
    assert rule["text"] == (
        "No page surface shows a text key, placeholder or filler; "
        "text without an owner value draws nothing."
    )
    assert rule["predicate"] == {
        "kind": "forbidden_pattern",
        "value": ["<[a-z][a-z0-9_]*>", "<filler human verified text>"],
        "evidence_required": True,
        "checked_by": "web",
    }
    assert rule["applies_to"] == {"slots": ["presentation.page", "presentation.provider-page"]}
    assert (rule["severity"], rule["target"]) == ("error", "record")
