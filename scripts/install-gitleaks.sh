#!/usr/bin/env bash
# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail

destination="${1:?destination directory is required}"
mkdir -p "$destination"
archive="$(mktemp "$destination/gitleaks.XXXXXX")"
trap 'rm -f "$archive"' EXIT
curl --fail --location --silent --show-error \
  https://github.com/gitleaks/gitleaks/releases/download/v8.30.0/gitleaks_8.30.0_linux_x64.tar.gz \
  --output "$archive"
printf '%s  %s\n' \
  79a3ab579b53f71efd634f3aaf7e04a0fa0cf206b7ed434638d1547a2470a66e "$archive" \
  | sha256sum --check --status
tar -xzf "$archive" -C "$destination" gitleaks
chmod 755 "$destination/gitleaks"
