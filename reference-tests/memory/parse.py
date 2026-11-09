# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Extract the best STREAM Triad rate as an methodology measurement record."""

import json
import platform
import re
import sys
from collections.abc import Iterator
from datetime import UTC, datetime

TRIAD = re.compile(r"^Triad:\s+([0-9]+(?:\.[0-9]+)?)", re.MULTILINE)


def measurements(output: str) -> Iterator[dict[str, object]]:
    """Yield the best validated Triad rate in decimal GB/s."""
    if "STREAM version $Revision: 5.10 $" not in output:
        raise ValueError("expected STREAM 5.10 output")
    if "Solution Validates:" not in output:
        raise ValueError("STREAM solution did not validate")
    match = TRIAD.search(output)
    if match is None:
        raise ValueError("STREAM output is missing the Triad rate")
    yield {
        "provider": "reference",
        "methodology": {"id": "stream-triad", "version": "1.1.18"},
        "run_id": "reference-local",
        "trial": 1,
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "region": {
            "requested": {"value": "local", "source": "recorded"},
            "observed": {"value": "local", "source": "measured"},
        },
        "machine_config": platform.machine(),
        "test_id": "stream-triad",
        "variant": "standard",
        "measurement": "triad_gigabytes_per_second",
        "value": round(float(match.group(1)) / 1000, 6),
        "unit": "GB/s",
        "success": True,
        "exclusion_reason": None,
    }


if __name__ == "__main__":
    for record in measurements(sys.stdin.read()):
        print(json.dumps(record, separators=(",", ":")))
