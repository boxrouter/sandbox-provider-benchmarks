# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Convert one package-install runtime to an methodology measurement record."""

import json
import platform
import sys
from datetime import UTC, datetime
from typing import Any

VARIANTS = {"git_clone": "git-clone", "npm_ci": "npm-ci", "pip_install": "pip-install"}


def measurement(output: str) -> dict[str, Any]:
    """Parse the timed workload result and emit its methodology record."""
    result = json.loads(output)
    variant, value = result.get("variant"), result.get("duration_ms")
    if variant not in VARIANTS or not isinstance(value, (int, float)) or value < 0:
        raise ValueError("expected one known package-install variant and a nonnegative duration")
    return {
        "provider": "reference",
        "methodology": {"id": "package-registries", "version": "1.1.16"},
        "run_id": "reference-local",
        "trial": 1,
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "region": {
            "requested": {"value": "local", "source": "recorded"},
            "observed": {"value": "local", "source": "measured"},
        },
        "machine_config": platform.machine(),
        "test_id": "install",
        "variant": VARIANTS[variant],
        "measurement": "install_runtime_ms",
        "value": value,
        "unit": "ms",
        "success": True,
        "exclusion_reason": None,
    }


if __name__ == "__main__":
    print(json.dumps(measurement(sys.stdin.read()), separators=(",", ":")))
