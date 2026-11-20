# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""The sizes of the images Image Build starts from, read from the pinned workloads.

Each Image Build workload names its base image by digest (``data.base_image``) and carries that
image's ``facts``: compressed bytes, uncompressed bytes (what a provider writes to disk and what
``docker images`` shows), layer count and tar entries over all layers. A digest never changes, so
facts never go stale.
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from sandbox_benchmark_verifier.conventions import load_conventions

WORKLOADS = "parameters/image-build*/workloads.json"

FIELDS = ("compressed_bytes", "uncompressed_bytes", "layers", "files")


def _workloads(root: Path) -> Iterator[tuple[Path, list[dict[str, Any]]]]:
    """Every Image Build workload file with its parsed rows."""
    for path in sorted(root.glob(WORKLOADS)):
        yield path, json.loads(path.read_text(encoding="utf-8"))


def _facts_ok(facts: Any) -> bool:
    return isinstance(facts, dict) and all(
        isinstance(facts.get(field), int) and facts[field] >= 0 for field in FIELDS
    )


def standard_image_bytes(root: Path, digest: str) -> int | None:
    """The uncompressed bytes of the image with this digest, from the newest workloads naming it."""
    found = None
    for _, rows in _workloads(root):
        for row in rows:
            data = row.get("data", {})
            if str(data.get("base_image", "")).endswith(f"@{digest}") and _facts_ok(
                data.get("facts")
            ):
                found = int(data["facts"]["uncompressed_bytes"])
    return found


def byte_size_text(size: int) -> str:
    """A measured byte count by the conventions' presentation.byte_size rule ("7.24 GB")."""
    rule = load_conventions()["presentation"]["byte_size"]
    units = rule["units"]
    unit = next((row for row in reversed(units) if size >= row["factor"]), units[0])
    rounded = float(f"{size / unit['factor']:.{int(rule['significant_digits'])}g}")
    return f"{rounded:,g} {unit['unit']}"
