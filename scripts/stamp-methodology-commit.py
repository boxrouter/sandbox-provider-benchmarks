#!/usr/bin/env python3
# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Stamp METHODOLOGY_COMMIT in the catalog with the commit being released."""

import re
import sys
from pathlib import Path

CATALOG = Path("src/sandbox_benchmark_verifier/catalog.py")
PATTERN = re.compile(r'^METHODOLOGY_COMMIT = "[0-9a-f]{40}"$', re.MULTILINE)


def stamp(text: str, commit: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise SystemExit("commit must be a 40-character lowercase hex sha")
    stamped, count = PATTERN.subn(f'METHODOLOGY_COMMIT = "{commit}"', text)
    if count != 1:
        raise SystemExit("expected exactly one METHODOLOGY_COMMIT assignment")
    return stamped


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: stamp-methodology-commit.py <commit-sha>")
    CATALOG.write_text(stamp(CATALOG.read_text(encoding="utf-8"), sys.argv[1]), encoding="utf-8")
