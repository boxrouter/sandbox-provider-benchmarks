# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: LicenseRef-PolyForm-Shield-1.0.0
"""Replay verified DeepSWE commands and compare the resulting patch with the recorded one.

The caller supplies the protocol and fetched trace data explicitly. The proxy and replay
script start before command logging; a mismatching exit code or patch is a trace failure.
"""

import hashlib
import json
import shlex
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

from sandbox_benchmarks.assets import guest_script
from sandbox_benchmarks.interface import Missed, TrialContext, Workload
from sandbox_benchmarks.kit import replay
from sandbox_benchmarks.kit.replay import Groups, load_groups

SUBMIT = "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
"""mini-swe-agent's last command, which DeepSWE recorded as "action was not executed"."""
APP = "/app"
PATCH = "/logs/artifacts/model.patch"
PRE_ARTIFACTS = "/tmp/pre_artifacts.sh"
PROXY_PORT_FILE = "/tmp/deep-swe-proxy-port"
"""Where the proxy writes the port it listens on."""
PROXY_TIMEOUT_S = 30
SETUP_TIMEOUT_S = 60
PRE_ARTIFACTS_TIMEOUT_S = 60


@dataclass(frozen=True)
class Command:
    text: str
    exit_code: int
    """The exit code DeepSWE recorded; a negative one is a kill by that signal."""


@dataclass(frozen=True)
class Trace:
    commands: tuple[Command, ...]
    patch_sha256: str
    """The SHA-256 of the trial's published `model.patch`."""


@dataclass(frozen=True)
class Task:
    pre_artifacts_sha256: str
    env: dict[str, str]
    """The image's own `ENV`, from its config by digest."""


@dataclass(frozen=True)
class Protocol:
    trace_set_sha256: str
    proxy_port: int
    proxy_token: str
    environment: dict[str, str]
    groups: Groups
    tasks: dict[str, Task]
    source: Mapping[str, str] = field(default_factory=dict)

    def env(self, task: str, port: int | None = None) -> dict[str, str]:
        """Every command's environment: the image's `ENV`, including the `PATH` a
        login shell would reset, mini-swe-agent's variables, then the proxy's, at the `port`
        it listens on (the protocol's first, by default)."""
        proxy = f"http://agent:{self.proxy_token}@127.0.0.1:{port or self.proxy_port}"
        return {
            **self.tasks[task].env,
            **self.environment,
            **{name: proxy for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")},
            # Keep loopback names excluded from proxying.
            **{name: "localhost,127.0.0.1,::1" for name in ("NO_PROXY", "no_proxy")},
        }


def load_protocol(path: Path) -> Protocol:
    data = json.loads(path.read_text(encoding="utf-8"))
    return Protocol(
        trace_set_sha256=data["trace_set_sha256"],
        proxy_port=data["proxy"]["port"],
        proxy_token=data["proxy"]["token"],
        environment=dict(data["environment"]),
        groups=load_groups(data["groups"]),
        tasks={
            name: Task(task["pre_artifacts_sha256"], dict(task["env"]))
            for name, task in data["tasks"].items()
        },
        source=dict(data.get("source", {})),
    )


def extract(trajectory: Mapping[str, Any]) -> list[dict[str, Any]]:
    """A trajectory's replayed commands: `steps[].tool_calls[].arguments.command` of
    the agent's steps, in order, each with the `returncode` its observation recorded; the
    final submit, which was never run, left out."""
    commands = []
    for step in trajectory["steps"]:
        if step.get("source") != "agent":
            continue
        results = (step.get("observation") or {}).get("results") or []
        for call, result in zip(step.get("tool_calls") or [], results, strict=False):
            text = call["arguments"]["command"]
            if text.strip() == SUBMIT:
                continue
            commands.append(
                {"command": text, "exit_code": json.loads(result["content"])["returncode"]}
            )
    return commands


def trace_set_sha256(traces: Mapping[str, Any]) -> str:
    return replay.set_sha256(traces)


@dataclass(frozen=True)
class Fetched:
    traces: dict[str, Trace]
    pre_artifacts: dict[str, str]
    """Each task's `pre_artifacts.sh`, by task."""


@cache
def load_fetched(path: Path, sha256: str) -> Fetched:
    """The supplied trace data, checked against its recorded digest; raises Missed when absent or
    differs, before any command is sent."""
    if not path.exists():
        raise Missed("the trace data is unavailable for this workload")
    data = json.loads(path.read_text(encoding="utf-8"))
    if trace_set_sha256(data["traces"]) != sha256:
        raise Missed(f"the fetched traces are not the protocol's: {path} has another SHA-256")
    traces = {
        trial: Trace(
            tuple(Command(c["command"], c["exit_code"]) for c in trace["commands"]),
            trace["patch_sha256"],
        )
        for trial, trace in data["traces"].items()
    }
    return Fetched(traces, dict(data["pre_artifacts"]))


def proxy_command(source: str, port: int) -> str:
    """Start the proxy at `port`, or the first free port after it; it writes the port it got
    to `PROXY_PORT_FILE`."""
    return f"exec python3 -c {shlex.quote(source)} {port} {PROXY_PORT_FILE}"


def proxy_ready() -> str:
    """Wait untimed for the proxy to accept a connection, then print its selected port."""
    return (
        f"for _ in $(seq {PROXY_TIMEOUT_S * 10}); do "
        f"read -r p < {PROXY_PORT_FILE} 2>/dev/null && "
        '(exec 3<>"/dev/tcp/127.0.0.1/$p") 2>/dev/null && { echo "$p"; exit 0; }; '
        "sleep 0.1; done; "
        'echo "the proxy is not listening" >&2; exit 1'
    )


@cache
def inputs(protocol_path: Path, data_path: Path) -> tuple[Protocol, Fetched]:
    """The supplied protocol and trace data, verified before any command: the
    traces against the trace set's SHA-256 and each task's `pre_artifacts.sh` against its
    own; raises Missed, before any command is sent, when they are absent or differ."""
    protocol = load_protocol(protocol_path)
    fetched = load_fetched(data_path, protocol.trace_set_sha256)
    for name, script in fetched.pre_artifacts.items():
        if hashlib.sha256(script.encode()).hexdigest() != protocol.tasks[name].pre_artifacts_sha256:
            raise Missed(f"the fetched pre_artifacts.sh of {name} is not the protocol's")
    return protocol, fetched


def procedure(
    trial: TrialContext, workload: Workload, *, protocol: Protocol, fetched: Fetched
) -> None:
    task_name, trial_id = workload.data["task"], workload.data["trial"]
    trace = fetched.traces[trial_id]

    # The same proxy program is used for each trace set.
    proxy = guest_script("deny_proxy.py")
    start = proxy_command(proxy, protocol.proxy_port)
    trial.start("proxy", start, cwd="/", timeout_s=SETUP_TIMEOUT_S)
    ready = trial.setup("proxy-ready", proxy_ready(), timeout_s=PROXY_TIMEOUT_S + 5)
    steps = [
        replay.Step(
            command.text,
            command.exit_code,
            protocol.groups.group(command.text),
            {"program": protocol.groups.program(command.text)},
        )
        for command in trace.commands
    ]
    replay.run(trial, steps, cwd=APP, env=protocol.env(task_name, int(ready.stdout)))

    script = fetched.pre_artifacts[task_name]
    trial.write("pre-artifacts-upload", PRE_ARTIFACTS, script.encode(), timeout_s=SETUP_TIMEOUT_S)
    patch = trial.setup(
        "pre-artifacts",
        f'bash {PRE_ARTIFACTS} >&2 && sha256sum {PATCH} | cut -d " " -f 1',
        timeout_s=PRE_ARTIFACTS_TIMEOUT_S,
    ).stdout.strip()
    trial.observe("patch_sha256", patch, "measured")
    trial.observe("patch_match", patch == trace.patch_sha256, "measured")
    replay.check_patch(patch, trace.patch_sha256)
