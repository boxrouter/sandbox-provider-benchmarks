# CPU reference test

Requires 7-Zip **25.01** as `7zzs`. Download the official Linux x64 archive from
<https://github.com/ip7z/7zip/releases/download/25.01/7z2501-linux-x64.tar.xz> and verify SHA-256
`4ca3b7c6f2f67866b92622818b58233dc70367be2f36b498eb0bdeaaa44b53f4` (accessed 2026-10-25).
Download, verify and extract the archive, then run the script with the binary on `PATH`:

```sh
curl -fL https://github.com/ip7z/7zip/releases/download/25.01/7z2501-linux-x64.tar.xz -o 7z.tar.xz
echo '4ca3b7c6f2f67866b92622818b58233dc70367be2f36b498eb0bdeaaa44b53f4  7z.tar.xz' | sha256sum -c -
mkdir -p "$HOME/.local/share/7zip-25.01"
tar -xJf 7z.tar.xz -C "$HOME/.local/share/7zip-25.01"
PATH="$HOME/.local/share/7zip-25.01:$PATH" ./run.sh
```

The script invokes `7zzs b -mmt1` and emits complete methodology measurement records as JSON Lines
(test `cpu-7zip-1t`, variant `standard`; the published value is `total_mips`). The extracted compress,
decompress and total ratings follow the published CPU definition; this generic test has no
BoxRouter integration. The parser fixture is a captured run in `tests/fixtures/reference-tests/cpu.txt`.
