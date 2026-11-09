# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""The cost split of a workload bar is an allocation by time share, stated once.

A workload's Cost view draws the segments of its Time view. Each segment's cost is the shown cost
times the segment's share of the Time bar under the same statistic, so the segments sum to the
statistic's cost total. The sentence below is the only owner of that wording.
"""

import re
from collections.abc import Mapping
from typing import Any

RULE_ID = "cost-segments-follow-time"


def cost_split_errors(criteria: Mapping[str, Any]) -> list[str]:
    """Errors in the one allocation sentence."""
    sentence = str(criteria.get("fact_list", {}).get("fact_cost_split_rule", ""))
    errors = []
    if not re.search(r"\bshare of the Time bar\b", sentence):
        errors.append(f"{RULE_ID}: fact_cost_split_rule must state the time-share allocation")
    errors.extend(
        f"{RULE_ID}: {code} is a per-workload copy; state the split in fact_cost_split_rule"
        for code in criteria.get("hover_templates", {})
        if code.endswith("_cost_split_group")
    )
    return errors
