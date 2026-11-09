# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""The slots each panel kind must fill."""

from functools import cache

from sandbox_benchmark_verifier.content import content_criteria


@cache
def panel_required_slots() -> dict[str, tuple[str, ...]]:
    """Each concrete panel kind's required slots, read on first use: the criteria parse the
    methodology's YAML, which a process that only imports the verifier must not pay for."""
    return {
        kind: tuple(panel["required_fields"])
        for kind, panel in content_criteria()["panels"].items()
        if "required_fields" in panel and not panel.get("abstract")
    }
