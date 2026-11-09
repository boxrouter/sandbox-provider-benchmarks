#!/usr/bin/env bash
# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
command -v stress-ng >/dev/null || { echo "stress-ng 0.19.02 is required as stress-ng" >&2; exit 127; }
version=$(stress-ng --version | awk '{print $3}')
[[ "$version" == "0.19.02" ]] || { echo "stress-ng 0.19.02 required; found $version" >&2; exit 2; }
work=${1:-$(mktemp -d "${TMPDIR:-.}/sbs-hardlink.XXXXXX")}
mkdir -p "$work"
free_kib=$(df -Pk "$work" | awk 'NR==2 {print $4}')
[[ "$free_kib" -ge 102400 ]] || { echo "hardlink reference test requires 100 MiB free in $work" >&2; exit 2; }
stress-ng --vm 1 --vm-bytes 256M --verify --timeout 1s --temp-path "$work" --metrics-brief
stress-ng --link 1 --metrics --no-rand-seed -t 30 --temp-path "$work" --yaml "$work/stress.yaml"
python3 "$(dirname "$0")/parse.py" "$work/stress.yaml"
