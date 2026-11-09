# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Rule reader.no-internal_ids exempts exactly the listed allowed literals, as whole tokens."""

import pytest

from sandbox_benchmark_verifier.conventions import load_conventions
from sandbox_benchmark_verifier.text_predicates import check_program

pytestmark = pytest.mark.unit

RULE = "reader.no-internal_ids"
LISTED = [
    "sha_ni",
    "avx2",
    "avx512f",
    "apply_patch",
    "/root/run_tests.sh",
    "sh -c <command>",
    "bash -c <command>",
    "timeout 120 <command>",
    "timeout 90 <tool> <arguments>",
    "./7zzs b -mmt<N>",
    "<provider>",
    "<digest>",
    "<task tag>",
    "namanjain12/<repository>_final",
    "namanjain12/numpy_final",
    "namanjain12/coveragepy_final",
    "node_modules",
]


def _findings(text: str) -> list[str]:
    scope = {"slots": [{"slot": "description", "text": text}], "context": "panel"}
    return [
        item["rule_id"]
        for item in check_program(scope, load_conventions())
        if item["rule_id"] == RULE
    ]


def test_the_list_is_exactly_the_agreed_literals() -> None:
    assert load_conventions()["allowed_literals"] == LISTED


@pytest.mark.parametrize("literal", LISTED)
def test_a_listed_literal_passes_bare_and_in_a_code_span(literal: str) -> None:
    assert _findings(f"The command is `{literal}` here.") == []
    assert _findings(f"Run {literal}, then stop.") == []
    assert _findings(f"Run {literal}.") == []


def test_an_unlisted_identifier_still_fails_in_a_code_span() -> None:
    assert _findings("The sandbox is recorded as `threads_differ`.") == [RULE]
    assert _findings("Providers with the `pause_resume` capability.") == [RULE]


def test_a_listed_literal_is_exempt_only_as_a_whole_token() -> None:
    assert _findings("The flag `sha_ni_extra` is set.") == [RULE]
    assert _findings("The flag `extra_sha_ni` is set.") == [RULE]
    assert _findings("The flag `sha_ni-2` is set.") == [RULE]
    assert _findings("The file `/tmp/root/run_tests.sh` runs.") == [RULE]
    assert _findings("The file `/root/run_tests.sh.bak` runs.") == [RULE]
    assert _findings("Run `sh -c <command>` and `threads_differ`.") == [RULE]
