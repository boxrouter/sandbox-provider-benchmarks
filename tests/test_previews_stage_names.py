# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""A Previews stage is named by its names row only: no second name, no typed copy of the name."""

from __future__ import annotations

from pathlib import Path

import pytest

from sandbox_benchmark_verifier.name_refs import name_reference_errors, source_name_errors

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "second_name", ["Start and routing", "start and routing", "Server listening"]
)
def test_a_second_name_for_a_previews_stage_is_rejected_everywhere(second_name: str) -> None:
    assert name_reference_errors(f"The {second_name} time.", root=ROOT, resolved=True)


@pytest.mark.parametrize("typed_name", ["URL Reachable", "Server Listening"])
def test_a_typed_copy_of_a_stage_name_is_rejected_in_a_template(typed_name: str) -> None:
    assert name_reference_errors(f"The {typed_name} time.", root=ROOT)
    assert not name_reference_errors("The {name:stage.between} time.", root=ROOT)


def test_no_editable_template_names_a_stage_except_by_reference() -> None:
    assert source_name_errors(ROOT) == []
