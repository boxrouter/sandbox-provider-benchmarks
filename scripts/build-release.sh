#!/usr/bin/env bash
# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
# Build the release wheel: require every methodology version locked, stamp the tagged commit.
set -euo pipefail
: "${GITHUB_SHA:?GITHUB_SHA must name the tagged commit}"
uv run --no-project --with pyyaml==6.0.3 --with jsonschema==4.26.0 env PYTHONPATH=src \
  python -m sandbox_benchmark_verifier.lock check --require-complete
uv run --locked sandbox-benchmark-verify text --output dist/text.json
python3 scripts/stamp-methodology-commit.py "$GITHUB_SHA"
uv build --wheel --out-dir dist
sha256sum dist/*.whl dist/text.json > dist/SHA256SUMS
