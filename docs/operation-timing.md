# Time sandbox operations with your own SDK

The create, exec and file benchmarks time calls to a provider. Our adapters are not published, so
this page gives the timing contract: implement it with the provider's own SDK, write one JSON record
per observation and compare it with [`sandbox-benchmark-verify compare`](../README.md#reproduce-a-result).
The methodology in `methodology/03-sandbox-operations/` is the authority; where this page and a
methodology differ, the methodology wins.

## What every operation benchmark shares

- **Sandbox:** 2 vCPU and 4 GiB, from the standard image pinned in the methodology (`environment.image`; [pull it
  here](../README.md#standard-image)), in the provider's US region. A fresh sandbox for every trial.
- **Clock:** the monotonic clock of the machine that calls the SDK, read around each call
  (`clocks.operation`). A duration is the clock after the call returns minus the clock before it is sent.
  Never use timestamps the provider reports.
- **Ready check, not timed:** after create returns, run `true` until it succeeds, retrying on HTTP 502,
  503, 504, connection refused and connection reset, every 100 ms, at most 40 attempts of at most 30 s
  each (`ready_check`). Time starts only after it passes, except in the create benchmark, which skips it.
- **Concurrency:** the methodology's `procedure.concurrency.trials_in_parallel` trials at once and one
  sandbox at a time inside a trial.
- **Failures:** a call that errors or times out is a record with `success: false` and `value: null`.
  Do not drop failed trials. Delete everything you create.
- **Statistics:** you do not compute any. `compare` takes the median by nearest rank and its 95%
  interval by seeded bootstrap, both from the methodology's `statistics`.

## The record

One JSON object per observation (JSON Lines, or one JSON array), following
`schemas/measurement.schema.json`:

```json
{"provider": "mine", "methodology": {"id": "read-4kib", "version": "1.1.6"}, "run_id": "my-run-1",
 "trial": 7, "timestamp": "2026-11-01T09:12:03Z", "region": "us", "machine_config": "2x4",
 "test_id": "file-read-4kib", "variant": "shared-trial", "measurement": "total_time",
 "value": 28.2, "unit": "ms", "success": true, "exclusion_reason": null}
```

`compare` uses `test_id`, `variant`, `measurement`, `value`, `unit`, `success` and `exclusion_reason`.
`test_id` and `variant` are the ids the publication lists for the test, and `measurement` is the first
metric of the test's methodology; `compare` reports records it leaves out. Values are in the
methodology's unit, here milliseconds.

## Create and start time (`start-time`, `-python3`, `-node`, `-git`)

Methodology `sequential-tti`, metric `time_to_interactive`, one record per sandbox.

- **Start:** the create request is dispatched, once the previous sandbox is confirmed destroyed.
- **Stop:** the exit code of the first command is back at the caller. The command is sent as soon as
  create returns, with nothing in between and no ready check. The test id names the command: `true`
  (`start-time`), `python3 -c 'import json, sqlite3, ssl, asyncio, urllib.request, email.parser,
  xml.etree.ElementTree'` (`start-time-python3`), `node -e 'require("crypto"); require("zlib");
  require("http")'` (`start-time-node`) or `git --version` (`start-time-git`).
- **Trial:** build a new image that appends one small layer holding the trial's unique id to the
  standard image, so the provider has never seen it; then create, run the command and destroy ten
  sandboxes one after another. Sandbox 1 starts cold.
- **Record:** `variant` is `n-<position>`, the sandbox's position in its trial (`n-1` to `n-10`).
  The destroy call and the image build are recorded by us but are not part of the value.

## Exec latency (`true`)

Methodology `exec-latency`, metric `total_time`; `runtime` and `latency` are in the same methodology.

- **Start:** a `true` command call is dispatched. **Stop:** its exit code and the sandbox's clock
  readings are back at the caller.
- **Trial:** one fresh sandbox, the ready check, then 100 calls one at a time, each sent when the
  previous returned. Destroy the sandbox.
- **Record:** one record per call. `compare` refuses a publication that pools calls per trial and
  resamples whole trials for its interval; it says so.

## Files (`file-read-4kib`, `file-write-4kib`, `file-list`)

Methodologies `read-4kib`, `write-4kib` and `list`, metric `total_time`, one record per call, variant
`shared-trial`. Each runs 100 calls one after another in a fresh sandbox, after the ready check:

- **List:** an untimed command first creates 200 empty files in the target directory. Start: a list
  request is dispatched. Stop: the names are back at the caller.
- **Write:** a fixed 4 KiB payload to one path. Start: the write request is dispatched. Stop: the write
  call returns, after the bytes are staged and in place.
- **Read:** the payload is first written to the path 100 times, untimed. Start: a read request is
  dispatched. Stop: the complete file is back at the caller; check its SHA-256 there, outside the
  timing.

## Check your result

```sh
sandbox-benchmark-verify compare my-results.jsonl --publication <publication id> \
  --provider <provider or entry id from the publication> --test file-read-4kib
```

Use at least the number of trials in the methodology (`procedure.trials`), because an interval over
few trials is wide and agrees with almost anything.
