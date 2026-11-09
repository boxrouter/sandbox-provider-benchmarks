#!/usr/bin/env bash
# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
command -v 7zzs >/dev/null || { echo "7zzs (7-Zip 25.01) is required" >&2; exit 127; }
version=$(7zzs i | sed -n 's/^7-Zip (z) \([0-9.]*\).*/\1/p' | head -n 1)
[[ "$version" == "25.01" ]] || { echo "7-Zip 25.01 required; found ${version:-unknown}" >&2; exit 2; }
7zzs b -mmt1 | python3 "$(dirname "$0")/parse.py"
