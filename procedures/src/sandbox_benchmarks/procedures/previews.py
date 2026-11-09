# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: LicenseRef-PolyForm-Shield-1.0.0
"""Measure preview startup and request performance in a sandbox. In each trial's
sandbox, a pinned Vite dev server is reached through the sandbox's preview URL from the job host.
After the untimed upload of the pinned app, the server's preview port is opened,
then `process.start` launches the server while the job host polls the
preview URL (`url-poll`) and an untimed command in the sandbox polls the server on loopback; the
sandbox's clock times the server from its launch to its first good local answer
(`server-listening`). Then the job host times requests through the preview on its one
kept-alive connection, and opens a WebSocket to the server's HMR socket, which works or not.
"""

import threading
from collections.abc import Mapping
from concurrent.futures import wait
from typing import Any

from sandbox_benchmarks.interface import CheckFailed, SandboxClock, TrialContext, Workload, spawn
from sandbox_benchmarks.kit import disk_tools, http_probe
from sandbox_benchmarks.kit.sandbox_clock import (
    epoch_ns,
    first_field,
    read_uptime,
    runtime_ns,
    uptime_cs,
)
from sandbox_benchmarks.procedures.primitives import repeat

DIRECTORY = "/workspace/previews"
LAUNCHED = f"{DIRECTORY}/launched"
"""Where the launch command writes the sandbox's clock readings just before the server starts."""
SETUP_TIMEOUT_S = 120
REQUEST_TIMEOUT_S = 10.0
"""Each request's own timeout on the job host, poll attempts included."""
WEBSOCKET_TIMEOUT_S = 10.0


def launch_command(data: Mapping[str, Any]) -> str:
    """The server's launch: the sandbox's clocks read into `LAUNCHED`, then the server in this
    shell's place, its output in a log beside the app."""
    return (
        f"{read_uptime('__bench_u')}; {first_field('__bench_u')}\n"
        f'echo "$EPOCHREALTIME $__bench_u" > {LAUNCHED}\n'
        f"exec {data['server']} > {DIRECTORY}/server.log 2>&1"
    )


def listening_command(data: Mapping[str, Any]) -> str:
    """Poll the server on loopback every `local_gap_s` until the expected body comes back with
    a 200, then print the sandbox's clock readings at that moment and at the launch."""
    url = f"http://127.0.0.1:{data['port']}{data['path']}"
    expected = f"{data['body']} 200"
    return (
        f"deadline=$((SECONDS + {data['limit_s']}))\n"
        f"until [ \"$(curl -s --max-time 2 -w ' %{{http_code}}' {url})\" = '{expected}' ]; do\n"
        "  [ $SECONDS -ge $deadline ] && { echo 'not listening'; exit 1; }\n"
        f"  sleep {data['local_gap_s']}\n"
        "done\n"
        "__bench_e=$EPOCHREALTIME\n"
        f"{read_uptime('__bench_u')}; {first_field('__bench_u')}\n"
        'echo "listening $__bench_e $__bench_u"\n'
        f'echo "launched $(cat {LAUNCHED})"'
    )


def procedure(trial: TrialContext, workload: Workload) -> None:
    data = workload.data
    # Create the directory before uploading the app.
    trial.setup("directory", f"mkdir -p {DIRECTORY}", timeout_s=SETUP_TIMEOUT_S)
    disk_tools.install(trial, DIRECTORY, data["app"])
    trial.setup(
        "unpack",
        f"cd {DIRECTORY} && tar -xf {data['app']['name']} && node --version",
        timeout_s=SETUP_TIMEOUT_S,
    )
    app = f"{DIRECTORY}/{data['app']['directory']}"
    body = data["body"].encode()
    opened = trial.preview("open-preview", data["port"])
    client = http_probe.Client(opened.url, opened.headers, timeout_s=REQUEST_TIMEOUT_S)
    try:
        _start(trial, data, app, client, body)
        repeat(
            trial,
            "request",
            data["requests"],
            lambda _: _request(trial, client, data["path"], body),
        )
    finally:
        client.close()
    socket = trial.host(
        "websocket",
        "websocket.connect",
        lambda: http_probe.websocket(
            opened.url,
            opened.headers,
            subprotocol=data["websocket"]["subprotocol"],
            expect=data["websocket"]["expect"],
            timeout_s=WEBSOCKET_TIMEOUT_S,
        ),
    )
    trial.add_result("websocket", {"works": socket.works, **socket.detail})


def _start(
    trial: TrialContext, data: Mapping[str, Any], app: str, client: http_probe.Client, body: bytes
) -> None:
    """Start the server while the job host polls its preview URL; the poll is sent first, so
    `url-poll` spans the start's send."""
    stop = threading.Event()
    polled = spawn(
        lambda: http_probe.poll(
            client,
            data["path"],
            body,
            gap_s=data["poll_gap_s"],
            limit_s=data["limit_s"],
            stop=stop,
        )
    )
    try:
        trial.launch("start-server", launch_command(data), cwd=app)
        ran = trial.setup(
            "listening", listening_command(data), timeout_s=data["limit_s"] + 30, owner="provider"
        )
        _listening(trial, data, ran.stdout)
    except BaseException:
        stop.set()  # The poll ends after its attempt in flight; its result is not recorded.
        # Wait for it, so the procedure never closes the client while the poll's thread is
        # inside a request on it.
        wait([polled], timeout=REQUEST_TIMEOUT_S + 1)
        raise
    trial.host("url-poll", "http.get", polled.result)
    trial.add_result("url-poll", {"attempts": _attempts(polled.result())})


def _attempts(found: http_probe.Polled) -> list[dict[str, Any]]:
    return [attempt.as_dict() for attempt in found.attempts]


def _listening(trial: TrialContext, data: Mapping[str, Any], stdout: str) -> None:
    """`server-listening` from the two readings: the launch's and the first good answer's."""
    readings = dict(line.split(" ", 1) for line in stdout.splitlines() if " " in line)
    try:
        end_ns, end_cs = _reading(readings["listening"])
        start_ns, start_cs = _reading(readings["launched"])
    except (KeyError, ValueError) as error:
        raise CheckFailed("script", "server-listening", {"type": "NoClockReading"}) from error
    _, unknown = runtime_ns(SandboxClock(start_ns, end_ns, start_cs, end_cs))
    trial.logged(
        "server-listening",
        outcome="success",
        exit_code=0,
        start_ns=start_ns,
        end_ns=end_ns,
        runtime_unknown=unknown,
        command=listening_command(data),
    )


def _reading(text: str) -> tuple[int, int | None]:
    epoch, _, uptime = text.partition(" ")
    found = epoch_ns(epoch.strip())
    if found is None:
        raise ValueError(f"not an $EPOCHREALTIME reading: {epoch!r}")
    return found, uptime_cs(uptime.strip())


def _request(trial: TrialContext, client: http_probe.Client, path: str, body: bytes) -> None:
    got = trial.host("request", "http.get", lambda: http_probe.first_byte(client, path, body))
    trial.add_result("request", {"reused": got.reused})
