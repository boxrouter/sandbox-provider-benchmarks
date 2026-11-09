#!/usr/bin/env bash
# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
uv tool run --from 'reuse[charset-normalizer]==6.2.0' python "$(dirname "$0")/check_reuse.py" --root "${1:-.}"
