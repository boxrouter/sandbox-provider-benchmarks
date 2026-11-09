# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""The sizes of the images Image Build starts from, generated into the pinned workloads.

Each Image Build workload names its base image by digest (``data.base_image``) and carries that
image's ``facts``: compressed bytes, uncompressed bytes (what a provider writes to disk and what
``docker images`` shows), layer count and tar entries over all layers. Nobody types them:
``sandbox-benchmark-verify image-sizes`` reads each registry once per digest, reusing the facts a
digest already has in any workload, and the validation fails while a workload has none. A digest
never changes, so facts never go stale.
"""

import json
import re
import tarfile
import time
import urllib.request
import zlib
from collections.abc import Callable, Iterator, Mapping
from functools import partial
from pathlib import Path
from typing import Any, TypeVar

from sandbox_benchmark_verifier.conventions import load_conventions
from sandbox_benchmark_verifier.versions import version_key

_T = TypeVar("_T")
WORKLOADS = "parameters/image-build*/*/workloads.json"
COMMAND = "sandbox-benchmark-verify image-sizes"
_PARTS = re.compile(r"([^/]+)/(.+)@(sha256:[0-9a-f]{64})")
_ACCEPT = ", ".join(
    (
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    )
)
_TOKENS = {
    "docker.io": (
        "https://auth.docker.io/token?service=registry.docker.io&scope=repository:{repo}:pull"
    ),
    "ghcr.io": "https://ghcr.io/token?scope=repository:{repo}:pull",
    "public.ecr.aws": "https://public.ecr.aws/token/?scope=repository:{repo}:pull",
}
_HOSTS = {"docker.io": "registry-1.docker.io"}


FIELDS = ("compressed_bytes", "uncompressed_bytes", "layers", "files")


def _workloads(root: Path) -> Iterator[tuple[Path, list[dict[str, Any]]]]:
    """Every Image Build workload file with its parsed rows, oldest version first."""
    for path in sorted(root.glob(WORKLOADS), key=lambda p: version_key(p.parent.name)):
        yield path, json.loads(path.read_text(encoding="utf-8"))


def _facts_ok(facts: Any) -> bool:
    return isinstance(facts, dict) and all(
        isinstance(facts.get(field), int) and facts[field] >= 0 for field in FIELDS
    )


def image_fact_errors(root: Path) -> list[str]:
    """Every Image Build workload whose base image has no (or malformed) facts, with the fix."""
    return [
        f"{path.relative_to(root)}: workload {row['id']} ({row['data']['base_image']}) has no "
        f"image facts; run `{COMMAND}`"
        for path, rows in _workloads(root)
        for row in rows
        if "base_image" in row.get("data", {}) and not _facts_ok(row["data"].get("facts"))
    ]


def standard_image_bytes(root: Path, digest: str) -> int | None:
    """The uncompressed bytes of the image with this digest, from the newest workloads naming it."""
    found = None
    for _, rows in _workloads(root):
        for row in rows:
            data = row.get("data", {})
            if str(data.get("base_image", "")).endswith(f"@{digest}") and _facts_ok(
                data.get("facts")
            ):
                found = int(data["facts"]["uncompressed_bytes"])
    return found


def byte_size_text(size: int) -> str:
    """A measured byte count by the conventions' presentation.byte_size rule ("7.24 GB")."""
    rule = load_conventions()["presentation"]["byte_size"]
    units = rule["units"]
    unit = next((row for row in reversed(units) if size >= row["factor"]), units[0])
    rounded = float(f"{size / unit['factor']:.{int(rule['significant_digits'])}g}")
    return f"{rounded:,g} {unit['unit']}"


def _get(url: str, headers: Mapping[str, str] | None = None) -> Any:
    request = urllib.request.Request(url, headers=dict(headers or {}))
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def _manifest(host: str, repo: str, digest: str, auth: str | None) -> dict[str, Any]:
    headers = {"Accept": _ACCEPT, **({"Authorization": f"Bearer {auth}"} if auth else {})}
    body: dict[str, Any] = _get(f"https://{host}/v2/{repo}/manifests/{digest}", headers)
    return body


class _Unpacked:
    """A readable view of one layer blob's decompressed bytes that counts what passes through."""

    def __init__(self, response: Any, kind: str) -> None:
        if "zstd" in kind:
            raise ValueError(f"layer media type {kind} is not supported; add a decoder for it")
        self._response = response
        self._inflater = zlib.decompressobj(16 + zlib.MAX_WBITS) if "gzip" in kind else None
        self._buffer = b""
        self.size = 0

    def read(self, count: int = -1) -> bytes:
        """Up to ``count`` decompressed bytes; fewer only at the end of the blob."""
        while count < 0 or len(self._buffer) < count:
            chunk = self._response.read(1 << 20)
            if not chunk:
                if self._inflater is not None:
                    self._buffer += self._inflater.flush()
                    self._inflater = None
                break
            self._buffer += self._inflater.decompress(chunk) if self._inflater else chunk
        taken = self._buffer if count < 0 else self._buffer[:count]
        self._buffer = self._buffer[len(taken) :]
        self.size += len(taken)
        return taken


def _token(registry: str, repo: str) -> str | None:
    """A pull token for the repository; they expire in minutes, so each layer asks for its own."""
    if registry not in _TOKENS:
        return None
    token: str = _get(_TOKENS[registry].format(repo=repo))["token"]
    return token


def _layer_facts(registry: str, repo: str, layer: Mapping[str, Any]) -> tuple[int, int]:
    """The unpacked bytes and the tar entries of one layer blob, streamed and counted."""
    auth = _token(registry, repo)
    headers = {"Authorization": f"Bearer {auth}"} if auth else {}
    host = _HOSTS.get(registry, registry)
    request = urllib.request.Request(
        f"https://{host}/v2/{repo}/blobs/{layer['digest']}", headers=headers
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        unpacked = _Unpacked(response, str(layer["mediaType"]))
        with tarfile.open(fileobj=unpacked, mode="r|") as archive:  # type: ignore[call-overload]
            files = sum(1 for _ in archive)
        while unpacked.read(1 << 20):
            pass
    return unpacked.size, files


def _retrying(call: Callable[[], _T], attempts: int = 4) -> _T:
    """The call's result, trying again after a network failure (registries drop streams)."""
    for attempt in range(attempts):
        try:
            return call()
        except OSError:
            if attempt == attempts - 1:
                raise
            time.sleep(5 * (attempt + 1))
    raise AssertionError("unreachable")


def read_size(reference: str) -> dict[str, int]:
    """Read one image's sizes from its registry (linux/amd64); run once per digest."""
    registry, repo, digest = _PARTS.fullmatch(reference).groups()  # type: ignore[union-attr]
    host = _HOSTS.get(registry, registry)
    body = _retrying(lambda: _manifest(host, repo, digest, _token(registry, repo)))
    if "manifests" in body:
        chosen = next(
            m
            for m in body["manifests"]
            if m.get("platform", {}).get("os") == "linux"
            and m["platform"].get("architecture") == "amd64"
        )
        body = _retrying(lambda: _manifest(host, repo, chosen["digest"], _token(registry, repo)))
    layers = body["layers"]
    facts = [_retrying(partial(_layer_facts, registry, repo, layer)) for layer in layers]
    return {
        "compressed_bytes": sum(layer["size"] for layer in layers),
        "uncompressed_bytes": sum(unpacked for unpacked, _ in facts),
        "layers": len(layers),
        "files": sum(files for _, files in facts),
    }


def fill_facts(root: Path, read: Callable[[str], dict[str, int]] = read_size) -> list[str]:
    """Give every workload without facts those of its base image.

    A registry is read only for a digest no workload has facts for yet; each file is saved when
    done, so a failure keeps the rest."""
    known: dict[str, dict[str, int]] = {}
    for _, rows in _workloads(root):
        for row in rows:
            data = row.get("data", {})
            if "base_image" in data and _facts_ok(data.get("facts")):
                known[data["base_image"]] = data["facts"]
    filled: list[str] = []
    for path, rows in _workloads(root):
        changed = False
        for row in rows:
            data = row.get("data", {})
            if "base_image" not in data or _facts_ok(data.get("facts")):
                continue
            reference = data["base_image"]
            if reference not in known:
                known[reference] = read(reference)
            data["facts"] = dict(sorted(known[reference].items()))
            filled.append(f"{path.relative_to(root)}: {row['id']}")
            changed = True
        if changed:
            document = json.dumps(rows, indent=2, sort_keys=True) + "\n"
            path.write_text(document, encoding="utf-8")
    return filled
