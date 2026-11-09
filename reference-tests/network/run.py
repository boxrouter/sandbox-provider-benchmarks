# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Prepare pinned public inputs and time one package install workload."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

FASTAPI_COMMIT = "69ddc2badf9da1123bba4d360225ae6b085985f6"
AXIOS_COMMIT = "e6b79d9cef796ca3a903bb83e51d4752b019fd67"


def run(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: int = 120,
) -> None:
    """Run an untimed setup command, preserving its diagnostic output."""
    subprocess.run(command, cwd=cwd, env=env, check=True, stdout=sys.stderr, timeout=timeout)


def clone_at(url: str, destination: Path, commit: str) -> None:
    """Clone a public source repository and check out its pinned revision."""
    run(["git", "clone", "--quiet", url, str(destination)])
    run(["git", "-C", str(destination), "checkout", "--quiet", "--detach", commit])


def timed(command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> float:
    """Run one selected command and return its elapsed wall time in milliseconds."""
    started = time.monotonic_ns()
    run(command, cwd=cwd, env=env, timeout=600)
    return round((time.monotonic_ns() - started) / 1_000_000, 3)


def prepare_fastapi(work: Path) -> Path:
    source = work / "fastapi"
    clone_at("https://github.com/fastapi/fastapi.git", source, FASTAPI_COMMIT)
    return source


def execute(variant: str, work: Path) -> float:
    work.mkdir(parents=True, exist_ok=True)
    if variant == "git_clone":
        destination = work / "fastapi-clone"
        started = time.monotonic_ns()
        run(
            ["git", "clone", "--quiet", "https://github.com/fastapi/fastapi.git", str(destination)],
            timeout=600,
        )
        run(
            ["git", "-C", str(destination), "checkout", "--quiet", "--detach", FASTAPI_COMMIT],
            timeout=600,
        )
        return round((time.monotonic_ns() - started) / 1_000_000, 3)
    if variant == "npm_ci":
        source = work / "axios"
        clone_at("https://github.com/axios/axios.git", source, AXIOS_COMMIT)
        env = {**os.environ, "npm_config_cache": str(work / "npm-cache")}
        return timed(["npm", "ci"], cwd=source, env=env)
    if variant == "pip_install":
        source = prepare_fastapi(work)
        requirements = work / "requirements.txt"
        run(
            [
                "uv",
                "export",
                "--locked",
                "--group",
                "tests",
                "--format",
                "requirements-txt",
                "--no-hashes",
                "--output-file",
                str(requirements),
            ],
            cwd=source,
        )
        environment = work / "venv"
        run(["uv", "venv", "--python", "3.11", "--seed", str(environment)])
        env = {
            **os.environ,
            "PIP_CACHE_DIR": str(work / "pip-cache"),
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        }
        return timed(
            [str(environment / "bin/pip"), "install", "--requirement", str(requirements)],
            cwd=source,
            env=env,
        )
    raise ValueError(f"unknown workload variant: {variant}")


def main() -> None:
    variant, work = sys.argv[1], Path(sys.argv[2])
    result = {"variant": variant, "duration_ms": execute(variant, work)}
    (work / "result.json").write_text(json.dumps(result, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    main()
