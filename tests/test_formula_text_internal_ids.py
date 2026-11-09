# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""A formula's symbols and definitions on a methodology page carry no internal identifier."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from sandbox_benchmark_verifier.conventions import load_conventions
from sandbox_benchmark_verifier.text_predicates import check_program

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
RULE = "reader.no-internal_ids"


def _findings(text: str) -> list[str]:
    scope = {"slots": [{"slot": "description", "text": text}], "context": "panel"}
    return [
        item["rule_id"]
        for item in check_program(scope, load_conventions())
        if item["rule_id"] == RULE
    ]


def _formula_strings() -> list[tuple[str, str]]:
    formulas = json.loads((ROOT / "contracts/text/methodology-page.json").read_text("utf-8"))[
        "formulas"
    ]
    strings: list[tuple[str, str]] = []
    for formula_id, formula in formulas.items():
        strings.append((f"{formula_id}.aria_label", formula["aria_label"]))
        for variable in formula["variables"]:
            # The symbol is TeX written as the expression writes it (`t_k`), like the expression.
            strings.append(
                (f"{formula_id}.{variable['symbol']}.definition", variable["definition"])
            )
    return strings


def test_no_formula_string_holds_an_internal_identifier() -> None:
    strings = _formula_strings()
    assert len(strings) > 80
    assert [where for where, text in strings if _findings(text)] == []


def test_a_dotted_version_after_a_document_name_is_a_path_not_a_revision_tag() -> None:
    """The consumer's render check skips the version pattern, so only this pattern decides."""
    revision_tag = re.compile(load_conventions()["banned_patterns"]["internal_ids"][1])
    assert not revision_tag.search("parameters/deepswe/1.1.14/")
    assert not revision_tag.search("parameters/7-zip-1-thread/1.1.10/")
    assert revision_tag.search("The revision is deepswe/1.")
    assert revision_tag.search("The revision is `deepswe/12` here.")


def test_no_formula_is_set_in_monospace_as_an_identifier() -> None:
    """A TeX subscript such as t_k is math; `\\mathtt` is how a code identifier would be drawn."""
    formulas = json.loads((ROOT / "contracts/text/methodology-page.json").read_text("utf-8"))[
        "formulas"
    ]
    assert [name for name, formula in formulas.items() if "\\mathtt" in formula["tex"]] == []
