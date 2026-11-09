#!/usr/bin/env bash
# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
script_dir=$(cd "$(dirname "$0")" && pwd)
command -v fio >/dev/null || { echo "fio 3.39 is required as fio" >&2; exit 127; }
version=$(fio --version)
[[ "$version" == "fio-3.39" ]] || { echo "fio 3.39 required; found $version" >&2; exit 2; }
work=${1:-$(mktemp -d "${TMPDIR:-.}/sbs-fio.XXXXXX")}
mkdir -p "$work"
work=$(realpath "$work")
if [[ $(df -Pk "$work" | awk 'NR==2 {print $4}') -lt 2097152 ]]; then
  echo "fio reference test requires 2 GiB free in $work" >&2
  exit 2
fi
cd "$work"
fio --output-format=json --output="$work/prep.json" --eta=never --name=prep --filename=data.bin --size=1g --rw=write --bs=1m --ioengine=libaio --iodepth=64 --direct=1 --end_fsync=1 --refill_buffers=1
for spec in seq_read:read:1m seq_write:write:1m rand_read:randread:4k rand_write:randwrite:4k; do
  IFS=: read -r id rw bs <<<"$spec"
  RW="$rw" BS="$bs" fio --output-format=json --output="$work/$id.json" --eta=never "$script_dir/disk.fio"
done
python3 "$script_dir/parse.py" "$work/seq_read.json" "$work/seq_write.json" "$work/rand_read.json" "$work/rand_write.json"
