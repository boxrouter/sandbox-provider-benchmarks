# fio disk reference test

Requires fio **3.39**, built from the upstream release tag archive at
<https://github.com/axboe/fio/archive/refs/tags/fio-3.39.tar.gz>, SHA-256
`e2f4ff137061b44ceb83a55eb9ca8856fe188db6d9b00cb59f8629c9162afe0a` (accessed 2026-10-25).
Build fio with Linux AIO
(`libaio`) support. To build it in a user directory, fetch the upstream libaio 0.3.113 release
from <https://codeberg.org/jmoyer/libaio/releases/download/libaio-0.3.113/libaio-0.3.113.tar.gz>,
verify SHA-256 `2c44d1c5fd0d43752287c9ae1eb9c023f04ef848ea8d4aafa46e9aedb678200b` (accessed
2026-10-25). These commands build both tools under `$HOME/.local`:

```sh
curl -fL https://codeberg.org/jmoyer/libaio/releases/download/libaio-0.3.113/libaio-0.3.113.tar.gz -o libaio.tar.gz
echo '2c44d1c5fd0d43752287c9ae1eb9c023f04ef848ea8d4aafa46e9aedb678200b  libaio.tar.gz' | sha256sum -c -
tar -xzf libaio.tar.gz
make -C libaio-0.3.113 prefix="$HOME/.local" install
curl -fL https://github.com/axboe/fio/archive/refs/tags/fio-3.39.tar.gz -o fio.tar.gz
echo 'e2f4ff137061b44ceb83a55eb9ca8856fe188db6d9b00cb59f8629c9162afe0a  fio.tar.gz' | sha256sum -c -
tar -xzf fio.tar.gz
cd fio-fio-3.39
CFLAGS="-I$HOME/.local/include" LDFLAGS="-L$HOME/.local/lib -Wl,-rpath,$HOME/.local/lib" ./configure --disable-native
make fio
PATH="$PWD:$PATH" ../run.sh
```

Run the script on a filesystem with at least 2 GiB free; the
default directory is a temporary directory below the current directory and retains its JSON output
and workload files. Setup writes a 1 GiB
file using direct I/O and fresh buffers, then runs sequential read/write at 1 MiB and random
read/write at 4 KiB, each with 64 requests in flight, a 5 s ramp and a 60 s measured interval.
The standalone `disk.fio` job file receives `RW` and `BS` for each row. The script prints the four
fio metrics as methodology JSON Lines (tests `disk-seq-read-1m`, `disk-seq-write-1m`, `disk-rand-read-4k`
and `disk-rand-write-4k`, variant `fio-1g`). Captured fio JSON fixtures live in
`tests/fixtures/reference-tests/disk/`.
