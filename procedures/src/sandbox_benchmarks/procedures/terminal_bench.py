# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: LicenseRef-PolyForm-Shield-1.0.0
"""Replay supplied Terminal-Bench recorded sessions without a model.

Recorded commands, edits and writes are converted to shell steps with their expected
exits. Input hashes are verified before the shared replay helper executes the session.
"""

import base64
import json
import re
import shlex
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

from sandbox_benchmarks.interface import Missed, TrialContext, Workload
from sandbox_benchmarks.kit import replay
from sandbox_benchmarks.kit.replay import Groups, load_groups

FETCHED = Path("local") / "terminal-bench" / "fetched.json"
"""Resolve the supplied session data for the selected workload."""
APP = "/app"
EXIT_CODE = re.compile(r"Exit code (-?\d+)")
"""How Claude Code's Bash result starts when the command exited non-zero."""
INTERPRETED = 1
"""The exit code of a Bash result Claude Code did not call an error but gave a
`returnCodeInterpretation` ("No matches found" for grep, "Files differ" for diff): every such
interpretation is of exit code 1."""
EDIT = (
    'my ($p, $o, $n, $all) = @ARGV; $o = pack("H*", $o); $n = pack("H*", $n); '
    'open(my $f, "<:raw", $p) or exit 1; my $s = do { local $/; <$f> }; close $f; '
    "my $c = () = $s =~ /\\Q$o\\E/g; exit 1 if $c == 0 || ($c > 1 && !$all); "
    "if ($all) { $s =~ s/\\Q$o\\E/$n/g } else { $s =~ s/\\Q$o\\E/$n/ } "
    'open($f, ">:raw", $p) or exit 1; print $f $s; close $f or exit 1;'
)
"""Claude Code's Edit as a perl program: the file, the old and new text in hex, and 1 to
replace every occurrence."""
KEPT = ("Bash", "Edit", "Write")
"""The tools whose calls are replayed; the others change nothing in the sandbox."""


@dataclass(frozen=True)
class Protocol:
    trace_set_sha256: str
    environment: dict[str, str]
    groups: Groups
    tasks: dict[str, dict[str, str]]
    """Each task's image `ENV`, by task."""
    source: Mapping[str, str] = field(default_factory=dict)

    def env(self, task: str) -> dict[str, str]:
        """Every step's environment: the image's `ENV`, then the protocol's."""
        return {**self.tasks[task], **self.environment}


def load_protocol(path: Path) -> Protocol:
    data = json.loads(path.read_text(encoding="utf-8"))
    return Protocol(
        trace_set_sha256=data["trace_set_sha256"],
        environment=dict(data["environment"]),
        groups=load_groups(data["groups"]),
        tasks={name: dict(task["env"]) for name, task in data["tasks"].items()},
        source=dict(data.get("source", {})),
    )


def extract(trajectory: Mapping[str, Any], session: Iterable[str]) -> list[dict[str, Any]]:
    """A session's replayed steps: its `Bash`, `Edit` and `Write` calls in order,
    each with its arguments, the exit code its result recorded and, for `Bash`, the directory
    the session log (`session`, its JSON lines) recorded on the call. A call whose result is
    an error with no exit code (a refused command, a failed edit) changed nothing and is left
    out; a `Bash` result that is not an error but carries Claude Code's `returnCodeInterpretation`
    exited 1."""
    directories = {}
    for line in session:
        event = json.loads(line)
        content = (event.get("message") or {}).get("content")
        for block in content if isinstance(content, list) else []:
            if block.get("type") == "tool_use":
                directories[block["id"]] = event.get("cwd")
    steps = []
    for step in trajectory["steps"]:
        results = (step.get("observation") or {}).get("results") or []
        by_call = {result.get("source_call_id"): result for result in results}
        for call in step.get("tool_calls") or []:
            tool = call["function_name"]
            if tool not in KEPT:
                continue
            result = by_call.get(call["tool_call_id"]) or {}
            extra = result.get("extra") or {}
            used = (extra.get("tool_result_metadata") or {}).get("tool_use_result")
            interpreted = isinstance(used, dict) and used.get("returnCodeInterpretation")
            code = INTERPRETED if interpreted else 0
            if extra.get("tool_result_is_error"):
                found = EXIT_CODE.match(result.get("content") or "")
                if tool != "Bash" or found is None:
                    continue
                code = int(found.group(1))
            arguments = call["arguments"]
            if tool == "Bash":
                directory = directories[call["tool_call_id"]]
                found_step = {"command": arguments["command"], "directory": directory}
            elif tool == "Edit":
                names = ("file_path", "old_string", "new_string")
                found_step = {name: arguments[name] for name in names}
                found_step["replace_all"] = bool(arguments.get("replace_all"))
            else:
                found_step = {name: arguments[name] for name in ("file_path", "content")}
            steps.append({"tool": tool, **found_step, "exit_code": code})
    return steps


def render(step: Mapping[str, Any]) -> str:
    """The step's text in the script: a `Bash` command as recorded, an edit as a process."""
    if step["tool"] == "Bash":
        text: str = step["command"]
        return text
    path = step["file_path"]
    if step["tool"] == "Edit":
        old, new = (step[name].encode().hex() for name in ("old_string", "new_string"))
        flag = "1" if step["replace_all"] else "0"
        return shlex.join(("perl", "-e", EDIT, path, old, new, flag))
    encoded = base64.b64encode(step["content"].encode()).decode()
    folder = shlex.quote(str(Path(path).parent))
    return f"mkdir -p {folder} && base64 -d > {shlex.quote(path)} <<'EOF'\n{encoded}\nEOF"


def trace_set_sha256(traces: Mapping[str, Any]) -> str:
    return replay.set_sha256(traces)


@cache
def inputs(directory: Path) -> tuple[Protocol, dict[str, list[dict[str, Any]]]]:
    """The task directory's protocol and the fetched sessions, checked once per batch against
    the trace set's SHA-256; raises Missed, before any command is sent, when it is absent or
    differs."""
    protocol = load_protocol(directory / "protocol.json")
    path = directory.parent.parent / FETCHED
    if not path.exists():
        raise Missed("the session data is unavailable for this workload")
    traces: dict[str, list[dict[str, Any]]] = json.loads(path.read_text(encoding="utf-8"))
    if trace_set_sha256(traces) != protocol.trace_set_sha256:
        raise Missed(f"the fetched traces are not the protocol's: {path} has another SHA-256")
    return protocol, traces


def procedure(trial: TrialContext, workload: Workload) -> None:
    directory = trial.description.directory
    assert directory is not None
    protocol, traces = inputs(directory)
    steps = []
    for step in traces[workload.data["trial"]]:
        text = render(step)
        bash = step["tool"] == "Bash"
        group = protocol.groups.group(text) if bash else "write"
        program = protocol.groups.program(text) if bash else step["tool"]
        result = {"tool": step["tool"], "program": program}
        where = step.get("directory") if bash else None
        steps.append(replay.Step(text, step["exit_code"], group, result, where))
    env = protocol.env(workload.data["task"])
    replay.run(trial, steps, cwd=APP, env=env, shell="bash")
