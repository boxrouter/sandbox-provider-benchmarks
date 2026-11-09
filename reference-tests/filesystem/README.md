# Filesystem hard-link reference test

Requires stress-ng **0.19.02**, built from the upstream release tag archive at
<https://github.com/ColinIanKing/stress-ng/archive/refs/tags/V0.19.02.tar.gz>, SHA-256
`15a07030a14bbfd5991e9ba3fbfbc24ed3ae72a2c946b033c06b820bc16f41c4` (accessed 2026-10-25).
Download, verify and build
the release in a user directory:

```sh
curl -fL https://github.com/ColinIanKing/stress-ng/archive/refs/tags/V0.19.02.tar.gz -o stress-ng.tar.gz
echo '15a07030a14bbfd5991e9ba3fbfbc24ed3ae72a2c946b033c06b820bc16f41c4  stress-ng.tar.gz' | sha256sum -c -
tar -xzf stress-ng.tar.gz
make -C stress-ng-0.19.02 -j2 stress-ng
PATH="$PWD/stress-ng-0.19.02:$PATH" ./run.sh
```

The script creates the working directory, runs the 30 s single-worker hard-link test with a 256 MiB memory
probe and emits the successful hard links per second as a methodology record (test `hardlink`, variant `link-30s`). The committed fixture keeps
stress-ng's real `metrics` section and omits host-identifying `system-info` fields:
`tests/fixtures/reference-tests/filesystem.yaml`.
