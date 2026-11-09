# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""docs/fields.md is generated from the schemas, so it cannot drift from them."""

import runpy
from collections.abc import Callable
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[1]
page_text = cast(
    Callable[[], str], runpy.run_path(str(ROOT / "scripts" / "field_reference.py"))["page_text"]
)


def test_field_reference_is_generated_from_the_schemas() -> None:
    assert (ROOT / "docs/fields.md").read_text(encoding="utf-8") == page_text(), (
        "docs/fields.md is stale; run python scripts/field_reference.py --write"
    )
