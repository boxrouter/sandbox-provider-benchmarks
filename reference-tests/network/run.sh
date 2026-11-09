#!/usr/bin/env bash
# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "usage: $0 {git_clone|npm_ci|pip_install} [work-directory]" >&2
  exit 2
fi
variant=$1
case "$variant" in
  git_clone) command -v git >/dev/null || { echo "git is required" >&2; exit 127; } ;;
  npm_ci)
    command -v git >/dev/null || { echo "git is required" >&2; exit 127; }
    command -v npm >/dev/null || { echo "npm is required" >&2; exit 127; }
    ;;
  pip_install)
    command -v git >/dev/null || { echo "git is required" >&2; exit 127; }
    command -v uv >/dev/null || { echo "uv is required to export the pinned lockfile" >&2; exit 127; }
    command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 127; }
    uv_version=$(uv --version | awk '{print $2}')
    [[ "$uv_version" == "0.13.4" ]] || { echo "uv 0.13.4 required; found $uv_version" >&2; exit 2; }
    ;;
  *) echo "unknown workload: $variant" >&2; exit 2 ;;
esac
script_dir=$(cd "$(dirname "$0")" && pwd)
if [[ $# -eq 2 ]]; then
  mkdir -p "$2"
  work_dir=$(cd "$2" && pwd)
else
  work_dir=$(mktemp -d "${TMPDIR:-.}/sbs-network.XXXXXX")
fi
python3 "$script_dir/run.py" "$variant" "$work_dir"
python3 "$script_dir/parse.py" < "$work_dir/result.json"
