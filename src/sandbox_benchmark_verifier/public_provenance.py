# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Current-document checks for public-source-only provenance; never scan removal history."""

import re

RULE_ID = "public-source-provenance"
_REVISION = r"v?\d+\.\d+(?:\.\d+)?(?:[-+][A-Za-z0-9.-]+)?"
_COMPONENT = r"\b(?:core|adapters?|runners?)(?:[-_]kit)?\b"
_SEPARATORS = r"[\s`*_():=/-]*"
CLOSED_REVISIONS = (
    re.compile(
        _COMPONENT
        + r"(?:\s+for\s+[A-Za-z][A-Za-z0-9_.-]*)?"
        + _SEPARATORS
        + r"(?:(?:versions?|revisions?|releases?|workspace)"
        + _SEPARATORS
        + r")?"
        + _REVISION,
        re.IGNORECASE,
    ),
    re.compile(r"\bworkspace-v\d+\.\d+(?:\.\d+)?\b", re.IGNORECASE),
    re.compile(
        _COMPONENT + r"[^\n]{0,60}?\b(?:commit|revision)" + _SEPARATORS + r"[0-9a-f]{7,64}\b",
        re.IGNORECASE,
    ),
)


def closed_revision_errors(text: str) -> list[str]:
    """Check current prose only. Public upstream SDK/project revisions are not closed components.

    This stdlib-only function can be loaded from an exact shared-source pin without installing
    the verifier. Diagnostics contain no matched values, so callers cannot disclose rejected data.
    """
    return [
        f"{RULE_ID}: closed implementation revision at character {match.start()}"
        for pattern in CLOSED_REVISIONS
        for match in pattern.finditer(text)
    ]
