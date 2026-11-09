# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Sizes written in reader text, and the byte counts they are matched against.

A measured byte count (a recorded fact, such as an image's bytes) is drawn in decimal MB and GB;
a size a definition sets in binary (a payload, fio's blocks, the machine's memory) keeps its binary
unit. ``conventions/text-conventions.yaml`` (``methodology-style.recorded-sizes``) owns the rule;
reader_style runs it with these helpers.
"""

import json
import re
from collections.abc import Iterable, Sequence
from pathlib import Path

TYPED_SIZE = re.compile(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)\s*(KiB|MiB|GiB|kB|KB|MB|GB)\b")
BINARY_FACTORS = {"KiB": 2**10, "MiB": 2**20, "GiB": 2**30}
SIZE_FACTORS = {**BINARY_FACTORS, "kB": 10**3, "KB": 10**3, "MB": 10**6, "GB": 10**9}
# A definition's key that holds a size in a binary unit ("memory_gib") and that unit's bytes.
_UNIT_KEYS = {"_kib": 2**10, "_mib": 2**20, "_gib": 2**30}


def defined_sizes(leaves: Iterable[tuple[str, float]], recorded: Sequence[int]) -> set[int]:
    """Every whole binary size a definition sets (Files' 4 KiB payload, fio's 1 MiB blocks, the
    4 GiB machine): a "bytes" or "*_bytes" number, or a "*_gib"-style one, that is a whole number
    under 1,024 of KiB, MiB, GiB or TiB and is not a recorded fact. ``leaves`` are a definition's
    numbers with the keys that hold them."""
    sizes = set()
    for key, value in leaves:
        if key == "bytes" or key.endswith("_bytes"):
            factor = 1
        else:
            factor = next((f for end, f in _UNIT_KEYS.items() if key.endswith(end)), 0)
        size = value * factor
        if size <= 0 or size != int(size) or int(size) in recorded:
            continue
        if any(size % 1024**n == 0 and size // 1024**n < 1024 for n in range(1, 5)):
            sizes.add(int(size))
    return sizes


def near(match: re.Match[str], sizes: Iterable[int]) -> bool:
    """Whether a written size rounds to one of the byte counts in its own unit."""
    typed = float(match[1].replace(",", "")) * SIZE_FACTORS[match[2]]
    return any(abs(typed - size) <= size * 0.005 for size in sizes)


def recorded_sizes(root: Path) -> list[int]:
    """Every byte count the parameter files record as a fact of a workload (an image's size)."""
    sizes: set[int] = set()
    for path in sorted((root / "parameters").glob("*/*/workloads.json")):
        for workload in json.loads(path.read_text(encoding="utf-8")):
            facts = workload.get("data", {}).get("facts", {}) if isinstance(workload, dict) else {}
            sizes.update(v for k, v in facts.items() if k.endswith("_bytes") and isinstance(v, int))
    return sorted(sizes)
