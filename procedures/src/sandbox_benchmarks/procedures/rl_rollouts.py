# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: LicenseRef-PolyForm-Shield-1.0.0
"""Replay a supplied R2E-Gym rollout group without a model.

The context supplies snapshot and sandbox operations. Caller-supplied traces are checked
before replay, then the shared script helper measures commands and validates expected exits.
"""

import hashlib
import json
import re
import shlex
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from sandbox_benchmarks.interface import (
    CreateIntent,
    Missed,
    TrialContext,
    Workload,
)
from sandbox_benchmarks.kit import command_script, programs, replay

FETCHED = Path("local") / "rl-rollouts" / "fetched.json"
"""Resolve the supplied rollout data and tools for the selected workload."""
TESTBED = "/testbed"
TOOLS = "/usr/local/bin"
"""Where R2E-Gym's `add_commands` copies its tools, without their `.py`."""
SETUP_TIMEOUT_S = 60
FUNCTION = re.compile(r"<function\s*=\s*([^>]+)>")
PARAMETER = re.compile(r"<parameter\s*=\s*([^>]+)>(.*?)</parameter>", re.DOTALL)
EXIT_CODE = re.compile(r"Exit code: (?:Error: Exit code )?(-?\d+)\n")
"""How R2E-Gym's `execute_bash` observation starts: `Exit code: 0`, or the runtime's
`Exit code: Error: Exit code N`."""
GROUPS = {"view": "read", "search": "read", "execute_bash": "run", "finish": "finish"}
"""Each step's display group by its tool, or by `file_editor`'s command; other edits write."""


def action(text: str) -> tuple[str, dict[str, str]]:
    """R2E-Gym's `Action.from_string`: the function's name and its parameters, stripped."""
    found = FUNCTION.search(text)
    name = found.group(1).strip() if found else ""
    return name, {key.strip(): value.strip() for key, value in PARAMETER.findall(text)}


def bash_command(name: str, parameters: Mapping[str, str]) -> str:
    """R2E-Gym's `Action.to_bashcmd`: the tool, its `command` first, then `--key 'value'`."""
    if name in ("finish", "submit"):
        return "echo '<<<Finished>>>'"
    parts = [shlex.quote(name)]
    if "command" in parameters:
        parts.append(shlex.quote(parameters["command"]))
    for key, value in parameters.items():
        if key != "command":
            parts += [f"--{key}", shlex.quote(str(value))]
    return " ".join(parts)


def run_text(command: str, limit_s: int) -> str:
    """The text R2E-Gym's runtime sends for `command`: `timeout <limit> <command> `."""
    return f"timeout {limit_s} {command} "


def run_limit(text: str) -> int:
    """The limit in a `run_text`."""
    return int(text.split()[1])


def extract(row: Mapping[str, Any]) -> dict[str, Any]:
    """A recorded rollout's replayed steps and its patch's SHA-256: every step with a
    function, as R2E-Gym's `run_action` ran it, with the exit code its observation recorded
    (`execute_bash` only)."""
    limit_s = int(row["max_exec_time"])
    steps = []
    for step in row["trajectory_steps"]:
        name, parameters = action(step["action"] or "")
        if not name:
            continue
        code = None
        if name == "execute_bash":
            found = EXIT_CODE.match(str(step["observation"]))
            code = int(found.group(1)) if found else None
        kind = parameters.get("command", name) if name == "file_editor" else name
        steps.append(
            {
                "text": run_text(bash_command(name, parameters), limit_s),
                "tool": name,
                "group": GROUPS.get(kind, "write"),
                "exit_code": code,
            }
        )
    patch = hashlib.sha256((row["output_patch"] or "").encode()).hexdigest()
    return {"steps": steps, "patch_sha256": patch, "reward": row["reward"]}


def rollout_set_sha256(rollouts: Mapping[str, Any]) -> str:
    return replay.set_sha256(rollouts)


@dataclass(frozen=True)
class Protocol:
    rollout_set_sha256: str
    tools: dict[str, str]
    """Each tool's SHA-256, by its name in the sandbox."""
    environment: dict[str, str]
    reset: tuple[str, ...]
    patch: str
    reward: str


def load_protocol(path: Path) -> Protocol:
    data = json.loads(path.read_text(encoding="utf-8"))
    return Protocol(
        rollout_set_sha256=data["rollout_set_sha256"],
        tools={name: tool["sha256"] for name, tool in data["tools"].items()},
        environment=dict(data["environment"]),
        reset=tuple(data["reset"]),
        patch=data["patch"],
        reward=data["reward"],
    )


@cache
def inputs(directory: Path) -> tuple[Protocol, dict[str, Any], dict[str, str]]:
    """The protocol, the fetched rollouts and the tools, checked once per batch; raises Missed,
    before any command is sent, when they are absent or differ."""
    protocol = load_protocol(directory / "protocol.json")
    path = directory.parent.parent / FETCHED
    if not path.exists():
        raise Missed("the rollout data is unavailable for this workload")
    data = json.loads(path.read_text(encoding="utf-8"))
    if rollout_set_sha256(data["rollouts"]) != protocol.rollout_set_sha256:
        raise Missed(f"the fetched rollouts are not the protocol's: {path} has another SHA-256")
    for name, text in data["tools"].items():
        if hashlib.sha256(text.encode()).hexdigest() != protocol.tools.get(name):
            raise Missed(f"the fetched tool {name} is not the protocol's")
    return protocol, data["rollouts"], data["tools"]


def create_intents(workload: Workload) -> tuple[CreateIntent, ...]:
    """The workload's one fresh create, naming the pinned snapshot settings it runs under."""
    return (CreateIntent("create", ("snapshots",), {"snapshots": workload.data["snapshots"]}),)


def procedure(trial: TrialContext, workload: Workload) -> None:
    directory = trial.description.directory
    assert directory is not None
    protocol, rollouts, tools = inputs(directory)
    env = protocol.environment
    snapshot: dict[str, Any] | None = None
    try:
        with trial.sandbox(1):
            for name, text in tools.items():
                trial.write(
                    "tool-upload", f"{TOOLS}/{name}", text.encode(), timeout_s=SETUP_TIMEOUT_S
                )
            reset = [replay.Step(text, None, "reset", {"tool": "reset"}) for text in protocol.reset]
            replay.run(trial, reset, cwd=TESTBED, env=env, step_id="reset")
            snapshot = trial.snapshot("snapshot")
        for n, rollout_id in enumerate(workload.data["rollouts"], start=2):
            rollout = rollouts[rollout_id]
            with trial.sandbox(n, snapshot=snapshot):
                _rollout(trial, protocol, rollout_id, rollout)
    finally:
        if snapshot is not None:
            trial.delete_snapshot(snapshot)


def _rollout(
    trial: TrialContext, protocol: Protocol, rollout_id: str, rollout: Mapping[str, Any]
) -> None:
    """Replay one rollout in this sandbox, then its patch (untimed, checked) and its reward."""
    steps = [
        replay.Step(
            step["text"],
            step["exit_code"],
            step["group"],
            {"tool": step["tool"], "rollout": rollout_id, "program": _step_program(step)},
            limit_s=run_limit(step["text"]),
        )
        for step in rollout["steps"]
    ]
    replay.run(trial, steps, cwd=TESTBED, env=protocol.environment)
    found = trial.setup(
        "patch",
        f"cd {TESTBED} && sh -c {shlex.quote(protocol.patch)} 2>&1 | tr -d '\\r' | sha256sum"
        " | cut -d ' ' -f 1",
        timeout_s=SETUP_TIMEOUT_S,
    ).stdout.strip()
    replay.check_patch(found, rollout["patch_sha256"], rollout=rollout_id)
    # Rule 56: the reward runs the task's tests (limit 330 s), so it is started in the background
    # and polled, timed by the sandbox's clock. Any exit code is a result; only the time limit, or
    # a script that never ran it, is the provider's failure.
    command_script.run_step(
        trial,
        "reward",
        protocol.reward,
        cwd=TESTBED,
        purpose="reward",
        result={"rollout": rollout_id, "recorded_reward": rollout["reward"]},
        env=protocol.environment,
        any_exit=True,
    )


def _step_program(step: Mapping[str, Any]) -> str:
    """Name the shell command inside execute_bash; other R2E tools keep their tool name.

    Read the already-recorded --cmd argument without changing the pinned extraction or script.
    """
    if step["tool"] == "execute_bash":
        words = shlex.split(step["text"])
        return programs.program(words[words.index("--cmd") + 1])
    return str(step["tool"])
