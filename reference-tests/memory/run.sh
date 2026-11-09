#!/usr/bin/env bash
# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
command -v stream >/dev/null || { echo "STREAM 5.10 is required as stream" >&2; exit 127; }
threads=${STREAM_THREADS:-2}
[[ "$threads" =~ ^[1-9][0-9]*$ ]] || { echo "STREAM_THREADS must be a positive integer" >&2; exit 2; }
grep -aqF 'Revision: 5.10' "$(command -v stream)" || { echo "STREAM 5.10 required" >&2; exit 2; }
OMP_NUM_THREADS="$threads" stream | python3 "$(dirname "$0")/parse.py"
