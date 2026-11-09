# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""The owner's rule for a reason the page shows for a missing or failed result."""

from collections.abc import Mapping
from typing import Any


def exclusion_reason_errors(criteria: Mapping[str, Any], reason: str) -> list[str]:
    """A displayed reason is one of the generic set the methodology owns, never free text.

    Provider files and data recipes pick a code from the set. The public reason stays concise;
    run data records the specific cause.
    """
    allowed = criteria["exclusion_reason_rules"]["generic_reasons"]
    if reason in allowed.values():
        return []
    return [f"reason {reason!r} is not one of the generic reasons {sorted(allowed.values())}"]
