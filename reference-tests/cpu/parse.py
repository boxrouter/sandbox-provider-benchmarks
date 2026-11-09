# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Extract portable measurements from 7-Zip benchmark output."""

import json
import platform
import re
import sys
from collections.abc import Iterator
from datetime import UTC, datetime

AVERAGE = re.compile(
    r"^Avr:\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+\|\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)", re.MULTILINE
)
TOTAL = re.compile(r"^Tot:\s+(\d+)\s+(\d+)\s+(\d+)", re.MULTILINE)


def measurements(output: str) -> Iterator[dict[str, object]]:
    """Yield methodology records for 7-Zip's compress, decompress and total MIPS ratings."""
    average, total = AVERAGE.search(output), TOTAL.search(output)
    if average is None or total is None:
        raise ValueError("7-Zip output is missing its Avr or Tot benchmark row")
    groups = [int(value) for value in average.groups()]
    for name, value in (
        ("compress_mips", groups[3]),
        ("decompress_mips", groups[7]),
        ("total_mips", int(total.group(3))),
    ):
        yield {
            "provider": "reference",
            "methodology": {"id": "7-zip-1-thread", "version": "1.1.18"},
            "run_id": "reference-local",
            "trial": 1,
            "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "region": {
                "requested": {"value": "local", "source": "recorded"},
                "observed": {"value": "local", "source": "measured"},
            },
            "machine_config": platform.machine(),
            "test_id": "cpu-7zip-1t",
            "variant": "standard",
            "measurement": name,
            "value": value,
            "unit": "MIPS",
            "success": True,
            "exclusion_reason": None,
        }


def main() -> None:
    for record in measurements(sys.stdin.read()):
        print(json.dumps(record, separators=(",", ":")))


if __name__ == "__main__":
    main()
