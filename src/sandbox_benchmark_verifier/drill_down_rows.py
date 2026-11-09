# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""A drill-down row is either a describing row or an event row, decided once beside the labels.

A describing row (`every_result`) shows for every provider of every chart. An event row
(`when_present`) shows only where its event happened, with no "None" row. The map from a fact code
to its kind is the one owner; no chart keeps a list of its own. One key names rows with no fact
code and no label of their own: `result_step` (the prefix of each step a Pause and Resume chart
draws as a fact). A status reason has its label in the ranking-status owner rather than the
fact-label list.
"""

from collections.abc import Mapping
from typing import Any

RULE_ID = "drill-down-row-kinds"
KINDS = ("every_result", "when_present")
NO_CODE = ("result_step",)


def drill_down_row_errors(criteria: Mapping[str, Any]) -> list[str]:
    """Errors in the kind map: a kind that is neither of the two, or a code with no label."""
    labelled = {*criteria.get("fact_list", {}), *criteria.get("fact_explanations", {})}
    statuses = criteria.get("ranking_statuses", {})
    fallback = statuses.get("not_displayed", {}) if isinstance(statuses, Mapping) else {}
    label = fallback.get("label") if isinstance(fallback, Mapping) else None
    if isinstance(label, str) and label.strip():
        labelled.add("reason")
    errors = []
    for code, kind in criteria.get("drill_down_rows", {}).items():
        if kind not in KINDS:
            errors.append(f"{RULE_ID}: {code} is {kind!r}, not one of {', '.join(KINDS)}")
        if code not in labelled and code not in NO_CODE:
            errors.append(f"{RULE_ID}: {code} names no fact label")
    return errors
