# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: LicenseRef-PolyForm-Shield-1.0.0
"""Run configured package-registry installs from verified caller-supplied inputs.

The shared script helper measures guest runtime and retains command output. Workload
checks determine whether an install succeeded and how failures are classified.
"""

import hashlib
import shlex

from sandbox_benchmarks.interface import StepFailed, StepOutcome, TrialContext, Workload
from sandbox_benchmarks.kit import command_script, replay, signatures
from sandbox_benchmarks.procedures.github_actions import AS_USER, WORKSPACE

SETUP_TIMEOUT_S = 60
RX = (
    'awk \'/:/ {sub(/^ */, ""); split($0, a, ":"); if (a[1] != "lo") '
    '{split(a[2], b, " "); s += b[1]}} END {print s + 0}\' /proc/net/dev'
)
"""Prints the bytes every non-loopback interface has received."""
PREPARE, RX_BEFORE, COMMAND, RX_AFTER = 1, 2, 3, 4
"""The numbers of the script's commands."""


def procedure(trial: TrialContext, workload: Workload) -> None:
    data = workload.data
    directory = trial.description.directory
    assert directory is not None
    for item in data["files"]:
        content = (directory / "inputs" / item["name"]).read_bytes()
        if hashlib.sha256(content).hexdigest() != item["sha256"]:
            raise ValueError(f"{item['name']} is not the workload's input file")
        trial.write("install-input", item["to"], content, timeout_s=SETUP_TIMEOUT_S)
    limit_s = trial.description.step("install").timeout_s
    text = command_script.script(
        [
            (AS_USER + shlex.quote(data["prepare"]), 0),
            (RX, None),
            (AS_USER + shlex.quote(data["command"]), 0),
            (RX, None),
        ],
        timeout_s=limit_s,
        shell="bash",
        env=data["env"],
    )
    ran = command_script.run(
        trial, text, cwd=WORKSPACE, env=data["env"], timeout_s=trial.description.lifetime_s
    )
    logged = {found.number: found for found in ran.log.commands}
    rx = _rx_bytes(ran.tails.get(RX_BEFORE), ran.tails.get(RX_AFTER))
    failure: tuple[int, int, bool] | None = None
    for step_id, number, command, result in (
        ("setup", PREPARE, data["prepare"], {}),
        (
            "install",
            COMMAND,
            data["command"],
            {"rx_bytes": rx, "hosts": data["hosts"], "output_bytes": ran.output_bytes.get(COMMAND)},
        ),
    ):
        found = logged.get(number)
        if found is None:
            trial.not_reached(step_id, result=result)
            continue
        timed_out = command_script.reached_limit(found, limit_s)
        outcome: StepOutcome = (
            "success" if found.exit_code == 0 else "timeout" if timed_out else "failure"
        )
        trial.logged(
            step_id,
            outcome=outcome,
            exit_code=found.exit_code,
            start_ns=found.start_ns,
            end_ns=found.end_ns,
            runtime_unknown=ran.log.runtime_unknown,
            command=command,
            result=result,
        )
        if found.exit_code != 0 and failure is None:
            failure = (number, found.exit_code, timed_out)
    if failure is not None:
        number, code, timed_out = failure
        step_id = "setup" if number == PREPARE else "install"
        tail = ran.tails.get(number, "")
        evidence = {"type": "Timeout" if timed_out else "ExitCode", "exit_code": code}
        if (full := signatures.first_line(tail.splitlines(), "DiskFull")) is not None:
            evidence = {"type": "DiskFull", "exit_code": code, "matched": full}
        elif (line := replay.network_line(tail)) is not None and not timed_out:
            evidence = {"type": "NetworkFailure", "exit_code": code, "matched": line}
        # The failing command's number: the page's one rule 21 (`apply_shared_failures`) reads it.
        raise StepFailed(
            "provider",
            step_id,
            evidence | {"command": number, "output_tail": replay.output_tail(tail)},
        )
    if COMMAND not in logged:
        evidence = {"type": "ScriptEnded", "logged": len(logged), "commands": 4}
        raise StepFailed("provider", "install", evidence)


def _rx_bytes(before: str | None, after: str | None) -> int | None:
    """The bytes received between the two readings, or None when either is missing."""
    if before is None or after is None:
        return None
    try:
        return int(after.split()[-1]) - int(before.split()[-1])
    except (IndexError, ValueError):
        return None
