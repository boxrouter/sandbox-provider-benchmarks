# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Convert fio JSON job results to methodology measurement records."""

import json
import platform
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

# Row -> (published test id, methodology id and version, measurement, unit, fio field)
METRICS = {
    "seq_read": (
        "disk-seq-read-1m",
        ("sequential-read", "1.1.18"),
        "sequential_read_mib_per_second",
        "MiB/s",
        "bw_bytes",
    ),
    "seq_write": (
        "disk-seq-write-1m",
        ("sequential-write", "1.1.18"),
        "sequential_write_mib_per_second",
        "MiB/s",
        "bw_bytes",
    ),
    "rand_read": (
        "disk-rand-read-4k",
        ("random-read", "1.1.17"),
        "random_read_iops",
        "IOPS",
        "iops",
    ),
    "rand_write": (
        "disk-rand-write-4k",
        ("random-write", "1.1.17"),
        "random_write_iops",
        "IOPS",
        "iops",
    ),
}


def measurements(paths: list[Path]) -> Iterator[dict[str, object]]:
    """Yield four fio records from one JSON output per workload row."""
    seen: set[str] = set()
    for path in paths:
        key = path.stem
        if key not in METRICS or key in seen:
            raise ValueError(f"unexpected or duplicate fio result: {key}")
        seen.add(key)
        document = json.loads(path.read_text())
        jobs = document.get("jobs")
        if not isinstance(jobs, list) or len(jobs) != 1:
            raise ValueError(f"{path} must contain one fio job")
        direction = "read" if key.endswith("read") else "write"
        stats = jobs[0].get(direction, {})
        test_id, (methodology, version), name, unit, field = METRICS[key]
        value = stats.get(field)
        if not isinstance(value, (int, float)):
            raise ValueError(f"{path} is missing {direction}.{field}")
        if field == "bw_bytes":
            value = value / (1024 * 1024)
        yield {
            "provider": "reference",
            "methodology": {"id": methodology, "version": version},
            "run_id": "reference-local",
            "trial": 1,
            "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "region": {
                "requested": {"value": "local", "source": "recorded"},
                "observed": {"value": "local", "source": "measured"},
            },
            "machine_config": platform.machine(),
            "test_id": test_id,
            "variant": "fio-1g",
            "measurement": name,
            "value": value,
            "unit": unit,
            "success": True,
            "exclusion_reason": None,
        }
    if seen != set(METRICS):
        raise ValueError("fio results must include all four workload rows")


if __name__ == "__main__":
    for record in measurements([Path(arg) for arg in sys.argv[1:]]):
        print(json.dumps(record, separators=(",", ":")))
