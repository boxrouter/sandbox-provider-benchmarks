# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Extract stress-ng hard-link throughput as an methodology record."""

import json
import platform
import re
import sys
from datetime import UTC, datetime
from pathlib import Path


def measurements(path: Path) -> dict[str, object]:
    """Yield the stress-ng hard-link rate, including the removal phase."""
    output = path.read_text()
    if re.findall(r"^\s*- stressor: (\S+)$", output, re.MULTILINE) != ["link"]:
        raise ValueError("expected exactly one stress-ng link result")
    match = re.search(
        r"^\s+links-createdremoved-per-sec: ([0-9]+(?:\.[0-9]+)?)$", output, re.MULTILINE
    )
    if match is None:
        raise ValueError("stress-ng YAML is missing the link rate including removal")
    value = float(match.group(1))
    return {
        "provider": "reference",
        "methodology": {"id": "hard-links", "version": "1.1.17"},
        "run_id": "reference-local",
        "trial": 1,
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "region": {
            "requested": {"value": "local", "source": "recorded"},
            "observed": {"value": "local", "source": "measured"},
        },
        "machine_config": platform.machine(),
        "test_id": "hardlink",
        "variant": "link-30s",
        "measurement": "links_per_second",
        "value": value,
        "unit": "links/s",
        "success": True,
        "exclusion_reason": None,
    }


if __name__ == "__main__":
    print(json.dumps(measurements(Path(sys.argv[1])), separators=(",", ":")))
