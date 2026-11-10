# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""The methodology style rules reject what they name and only shrink."""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from sandbox_benchmark_verifier.conventions import load_conventions
from sandbox_benchmark_verifier.reader_sizes import recorded_sizes
from sandbox_benchmark_verifier.reader_style import (
    COMMON_KEY,
    Context,
    Item,
    apply_exemptions,
    load_exemptions,
    status_labels,
    style_offenses,
)

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
# The most items each rule may still exempt. The conversion of a tab deletes entries; lower a number
# here when a list shrinks. A rule missing here may exempt nothing. Never raise one.
EXEMPTION_CEILING: dict[str, int] = {
    "methodology-style.reuse-phrasing": 11,
    "methodology-style.say-once": 69,
    "methodology-style.no-intensifiers": 1,
}

ROWS: dict[str, dict[str, Any]] = {
    "workloads": {"id": "workloads", "kind": "section"},
    "coding-workloads": {"id": "coding-workloads", "kind": "subsection", "parent": "workloads"},
    "deepswe": {"id": "deepswe", "kind": "test", "name": "DeepSWE", "parent": "coding-workloads"},
    "terminal-bench-4-0": {
        "id": "terminal-bench-4-0",
        "kind": "test",
        "name": "Terminal-Bench 4.0",
        "parent": "coding-workloads",
    },
    "exec": {"id": "exec", "kind": "tab", "name": "Exec"},
    "exec-latency": {
        "id": "exec-latency",
        "kind": "test",
        "name": "Latency per Call",
        "parent": "exec",
    },
    "disk": {"id": "disk", "kind": "tab", "name": "Disk"},
}
for _name in ("Sequential Read", "Sequential Write", "Random Read", "Random Write"):
    _id = _name.lower().replace(" ", "-")
    ROWS[_id] = {"id": _id, "kind": "test", "name": _name, "parent": "disk"}
DOCUMENTS = {
    "doc.deepswe": {
        "id": "doc.deepswe",
        "version": "1.0.0",
        "trials": 40,
        "limit": 300,
        "timeout_seconds": 300,
        "min_successes": 20,
        "environment": {"vcpu": 2, "memory_gib": 4},
        "payload_bytes": 4096,
        "block": "64k",  # fio writes a size as a string
    }
}


def _context() -> Context:
    conventions = load_conventions(ROOT)
    return Context(
        rows=ROWS,
        names=list(ROWS.values()),
        documents=DOCUMENTS,
        method_documents={"deepswe": "doc.deepswe"},
        conventions=conventions,
        threshold=conventions["uniqueness"]["similarity_threshold"],
        status_labels=status_labels(ROOT),
        recorded_sizes=recorded_sizes(ROOT),
    )


def _item(key: str, text: str, method: str | None = None, **extra: Any) -> Item:
    return Item(key, text, method=method or key.split(".")[0], **extra)


def _rules(items: list[Item], rule: str) -> list[str]:
    return [
        key
        for found, key, _ in style_offenses(items, _context())
        if found == f"methodology-style.{rule}"
    ]


@pytest.mark.parametrize(
    ("rule", "text"),
    [
        ("no-intensifiers", "It is actually faster."),
        ("no-intensifiers", "Exactly one call runs."),
        ("no-negative-framing", "It never starts early and never ends late."),
        ("no-negative-framing", "The run — a long one — ends."),
        ("no-abstract-gets", "This test measures what a provider gets."),
        ("noun-phrase-headings", "## When the trial ends\nText."),
        ("noun-phrase-headings", "## How the clock works"),
        (
            "reuse-phrasing",
            "The cost is the sum of the provider-sourced costs of the sandboxes, divided by units.",
        ),
        (
            "reuse-phrasing",
            "The {param:measured.statistics.primary} is shown in {unit:total_time}.",
        ),
        ("reuse-phrasing", "The benchmark measures the providers' start time."),
        ("latency", "The latency includes the network."),
        ("numbers-are-references", "Runs 40 trials per provider."),
        ("numbers-are-references", "A limit of 1,000 and 300 seconds."),
        ("numbers-are-references", "Waits 5 minutes for the sandbox."),
        ("numbers-are-references", "Needs 4 GiB of memory and 2 vCPUs."),
        ("numbers-are-references", "Writes a 4 KiB file."),
        ("numbers-are-references", "Reads 64 KiB blocks."),
        ("numbers-are-references", "Writes a 4096-byte payload."),
        ("numbers-are-references", "Writes 4,096-byte payloads."),
        ("one-numbered-list", "1. Populate the files.\n2. Read them back."),
        ("one-numbered-list", "Before the steps:\n\n1. Populate the files.\n2. Read them back."),
    ],
)
def test_a_rule_rejects_the_text_it_names(rule: str, text: str) -> None:
    assert _rules([_item("deepswe.introduction", text)], rule) == ["deepswe.introduction"]


@pytest.mark.parametrize(
    ("rule", "text"),
    [
        ("no-intensifiers", "It dispatched the call."),
        ("no-intensifiers", "The {param:content.exact.path} is read."),
        ("no-negative-framing", "It starts early; the clock never stops."),
        ("no-abstract-gets", "Time to send a single call."),
        ("noun-phrase-headings", "## Trial Steps"),
        ("reuse-phrasing", "Providers compared on the time from create to first command."),
        ("latency", "Latency is the total time minus the runtime."),
        ("latency", "The Latency per Call chart shows time."),
        ("numbers-are-references", "Runs {param:measured.trials} trials per provider."),
        ("numbers-are-references", "Runs `-mmt40` once, with 1 thread."),
        ("numbers-are-references", "Runs on a 7-Zip build of R2E-Gym."),
        ("numbers-are-references", "Reads a 4 MiB file and 20 passes at 2 GiB."),
        ("numbers-are-references", "Moves 400 MiB/s with 300 files."),
        ("numbers-are-references", "Reads 128 KiB blocks."),
        ("numbers-are-references", "Sends a 5-byte ping."),
        ("numbers-are-references", "Writes a {size:measured.payload_bytes} payload."),
        ("one-numbered-list", "- Populate the files.\n- Read them back."),
        ("one-numbered-list", "Step 1. reads the file, and step 2. writes it."),
    ],
)
def test_a_rule_accepts_the_text_it_allows(rule: str, text: str) -> None:
    assert _rules([_item("deepswe.introduction", text)], rule) == []


def _numbers(text: str) -> list[str]:
    """The numbers rule on a definition with four variants and a versioned source."""
    document = {
        **DOCUMENTS["doc.deepswe"],
        "procedure": {"variants": [{"id": v} for v in ("a", "b", "c", "d")]},
        "source": {"ids": ["app.tar.xz", "vite 8.3.4"]},
    }
    names = [*ROWS.values(), {"id": "opus", "kind": "variant", "name": "Claude Opus 5.5 max"}]
    context = replace(_context(), documents={"doc.deepswe": document}, names=names)
    return [
        message.split(":")[0]
        for found, _, message in style_offenses([_item("deepswe.workload", text)], context)
        if found == "methodology-style.numbers-are-references"
    ]


def test_a_count_word_a_variant_count_or_a_source_version_is_a_typed_number() -> None:
    """Argon typed counts and a version the definition holds."""
    assert _numbers("Runs forty trials.") == []  # forty is not a count word of the rule
    assert _numbers("Runs two vCPUs.") == ["two vCPUs"]
    assert _numbers("The four first-command variants compare.") == [
        "four first-command variants compare"
    ]
    assert _numbers("Across 4 cold-cache installation variants.") == [
        "4 cold-cache installation variants"
    ]
    assert _numbers("Runs a pinned Vite 8.3.4 archive.") == ["8.3.4"]


def test_a_count_of_other_things_or_a_version_in_a_name_is_not_a_typed_number() -> None:
    assert _numbers("The three wait durations, four groups and four in sequence.") == []
    assert _numbers("Three variants of five stages.") == []
    assert _numbers("Recorded by Claude Opus 5.5 (max reasoning), on 1.83 GiB arrays.") == []


@pytest.mark.parametrize(
    "text",
    [
        "The primary metric is Burst time (`burst_time`, in seconds, lower is better).",
        "The primary metric is Fork time (in milliseconds).",
        "The chart is drawn in ms.",
    ],
)
def test_the_metrics_text_types_no_time_unit(text: str) -> None:
    assert _rules([_item("fork.metrics", text)], "no-typed-time-unit") == ["fork.metrics"]
    assert _rules([_item("fork.setup", text)], "no-typed-time-unit") == []


def test_the_metrics_text_may_name_a_rate_unit() -> None:
    text = "The primary metric is Hard links (`links_per_second`, in links/s, higher is better)."
    assert _rules([_item("hard-links.metrics", text)], "no-typed-time-unit") == []


def _step_note_offenses(text: str, note: str, *, timed: bool) -> list[str]:
    item = {"steps": ["install"], "text": text, "timed": timed, "note": note}
    context = replace(_context(), rendered={"package-registries": {"step_items": [item]}})
    return [
        message.split(":")[0]
        for found, _, message in style_offenses([], context)
        if found == "methodology-style.step-note-not-step"
    ]


def test_a_step_note_that_retells_its_step_or_its_timing_is_refused() -> None:
    """Package Registries step 2 and its note describe one step and its timing consistently."""
    step = (
        "Run a background script that clears `~/.npm` (setup, timed, not charted), records "
        "`/proc/net/dev` received bytes, runs the selected install command (Install)."
    )
    retold = "A script clears caches, snapshots `/proc/net/dev` received bytes, runs the command."
    assert _step_note_offenses(step, retold, timed=True) == [
        "repeats its step's words '/proc/net/dev received bytes runs the'"
    ]
    untimed = "A script clears caches (setup, untimed on chart)."
    assert _step_note_offenses(step, untimed, timed=True) == ["says untimed on a timed step"]
    timed = "The command is timed by `$EPOCHREALTIME`."
    assert _step_note_offenses("Delete the snapshot.", timed, timed=False) == [
        "says timed on an untimed step"
    ]


def test_a_step_note_may_add_what_its_step_does_not_say() -> None:
    note = (
        "A background script runs the variant's install command timed by `$EPOCHREALTIME` and "
        "`/proc/uptime` (Install, polled every ten seconds)."
    )
    step = "Run the selected install command (Install), and record received bytes again."
    assert _step_note_offenses(step, note, timed=True) == []
    wall = "The harness compares guest wall-clock time against `/proc/uptime`."
    assert _step_note_offenses("Verify standby and destroy sandbox.", wall, timed=False) == []


def test_the_agent_is_only_rejected_in_sandbox_operations_and_machine_performance() -> None:
    text = "The agent runs the command."
    assert _rules([_item("exec-x.setup", text, section="sandbox-operations")], "not-the-agent") == [
        "exec-x.setup"
    ]
    assert _rules([_item("cpu-x.setup", text, section="machine-performance")], "not-the-agent") == [
        "cpu-x.setup"
    ]
    assert _rules([_item("deepswe.setup", text, section="workloads")], "not-the-agent") == []


@pytest.mark.parametrize(
    "text",
    [
        "Each trial creates a fresh sandbox and dispatches 100 calls.",
        "After creating a fresh sandbox, the files are set up.",
        "The trial passes the untimed readiness check first.",
        "Immediately after a new sandbox is created from the snapshot, the command runs.",
        "The trial times each call before destroying the sandbox.",
        "After all three rows finish, the sandbox is destroyed and confirmed gone.",
    ],
)
def test_the_measurement_prose_never_retells_a_lifecycle_step(text: str) -> None:
    """the numbered trial steps are the one place a procedure is told."""
    for key in ("exec-x.setup", "exec-x.workload", "common.blocks.exec.shared_procedure"):
        assert _rules([_item(key, text)], "measurement-prose-not-steps") == [key]
    assert _rules([_item("exec-x.limitations", text)], "measurement-prose-not-steps") == []


@pytest.mark.parametrize(
    "text",
    [
        "Each trial uses a sandbox created from BoxRouter's pinned standard image.",
        "The Load Runtimes step runs without a prior readiness check.",
        "Each child Python process starts with a clean address space.",
    ],
)
def test_the_measurement_prose_may_say_what_the_steps_cannot(text: str) -> None:
    assert _rules([_item("exec-x.workload", text)], "measurement-prose-not-steps") == []


def test_a_workload_names_no_other_workload() -> None:
    items = [
        _item("deepswe.setup", "It follows Terminal-Bench sessions."),
        _item("deepswe.limitations[0]", "It uses {name:test.terminal-bench-4-0} tasks."),
        _item("deepswe.limitations[1]", "It replays DeepSWE traces."),
        _item("exec.setup", "DeepSWE is another workload."),
    ]
    assert _rules(items, "one-workload-per-page") == ["deepswe.setup", "deepswe.limitations[0]"]


def test_parallel_sentences_of_sibling_tests_are_not_said_twice() -> None:
    items = [
        _item(
            "sequential-read.introduction", "Sequential read throughput from a file.", tab="disk"
        ),
        _item(
            "sequential-write.introduction", "Sequential write throughput to a file.", tab="disk"
        ),
        _item("random-read.setup", "Fio reads random blocks with direct IO.", tab="disk"),
        _item("random-write.setup", "Fio writes random blocks with direct IO.", tab="disk"),
        _item("sequential-read.setup", "Runs for 60 seconds, then the result is read.", tab="disk"),
        _item(
            "sequential-write.setup", "Runs for 60 seconds, then the result is read.", tab="disk"
        ),
        _item("random-read.limitations[0]", "Small cached files are not covered here.", tab="disk"),
        _item(
            "random-write.limitations[0]", "Small cached files are not covered here.", tab="disk"
        ),
    ]
    assert _rules(items, "say-once") == [
        "random-read.limitations[0]",
        "random-write.limitations[0]",
    ]


def test_a_sentence_said_twice_belongs_in_common_or_the_tab_block() -> None:
    sentence = "Only successful trials contribute to the cost value."
    items = [
        _item("a.cost_how_to_read", sentence, tab="files"),
        _item("b.cost_how_to_read", sentence, tab="files"),
        _item("c.cost_how_to_read", "Only the median contributes to the chart."),
        _item("common.blocks.files.cost", sentence, method=None, tab="files", is_common=True),
    ]
    assert _rules(items, "say-once") == ["a.cost_how_to_read", "b.cost_how_to_read"]
    assert _rules(items[:2], "say-once") == ["a.cost_how_to_read", "b.cost_how_to_read"]
    assert _rules([items[0], items[2]], "say-once") == []


def test_the_defined_term_and_dispatched_are_on_no_banned_list() -> None:
    style = load_conventions(ROOT)["methodology_style"]
    allowed = [row["word"] for row in style["allowed_words"]]
    assert allowed == ["dispatched"]
    for rule in style["rules"]:
        for pattern in rule.get("patterns", []):
            assert not re.search(pattern, "Dispatched dispatched", re.IGNORECASE), rule["id"]


def test_the_colon_rule_follows_a_noun_in_the_metric_paragraphs() -> None:

    for text in (
        "Resume is the sum of two timed stages: Resume and Read.",
        "Each step is assigned to one of four stages: `Write` or `Read`.",
    ):
        assert _rules([_item("deepswe.metric", text)], "colon") == [], text
    for text in (
        "Resume is the sum of Resume (the `resume` call): and Read.",
        "Resume reads: the payload.",
        "Resume is the sum of 40: trials.",
        "Resume is: a duration.",
    ):
        assert _rules([_item("deepswe.metric", text)], "colon") == ["deepswe.metric"], text
    # Only the metric paragraphs are checked for now; other notes keep their colons.
    assert _rules([_item("deepswe.procedure", "Runs 40: trials (ok): fine.")], "colon") == []


def test_a_metric_paragraph_starts_with_its_name_and_is_the() -> None:

    good = _item("deepswe.metric", "{name:test.deepswe} is the sum of command runtimes.")
    assert _rules([good], "metric-paragraph") == []
    assert (
        _rules([_item("deepswe.metric", "DeepSWE is the sum of runtimes.")], "metric-paragraph")
        == []
    )
    for text in (
        "Times the sum of command runtimes.",
        "The sum of command runtimes is the time.",
        "{name:test.deepswe} sums the command runtimes.",
        "{name:test.deepswe} is the sum.\n\nA second paragraph.",
        "{name:test.deepswe} is the sum of two parts:\n\n- A: one.\n- B: two.\n\nBeside it."
        "\n\nAnd more.",
        "{name:test.deepswe} is the sum.\n\nA paragraph.\n\n- A: one.",
    ):
        assert _rules([_item("deepswe.metric", text)], "metric-paragraph") == ["deepswe.metric"], (
            text
        )
    parts = "{name:test.deepswe} is the sum of two parts:\n\n- A: one.\n- B: two.\n\nBeside it."
    assert _rules([_item("deepswe.metric", parts)], "metric-paragraph") == []
    both = [good, _item("deepswe.metrics", "{name:test.deepswe} is the sum.")]
    assert _rules(both, "metric-paragraph") == ["deepswe.metrics"]
    # A test's name may be shortened to the part before its size or variant.
    row = {"id": "image-build", "kind": "test", "name": "Image Build (2 vCPU / 4 GiB)"}
    context = _context()
    rows = {**context.rows, "image-build": row}
    short = replace(context, rows=rows)
    item = _item("image-build.metric", "Image Build is the duration of `image.prepare`.")
    assert not [o for o in style_offenses([item], short) if o[0].endswith("metric-paragraph")]


def test_an_exemption_hides_its_offense_and_a_stale_one_is_an_error() -> None:
    offenses = [("methodology-style.latency", "a.introduction", "message")]
    ids = {"methodology-style.latency"}
    assert apply_exemptions(offenses, {"methodology-style.latency": ["a.introduction"]}, ids) == []
    assert (
        "delete the entry"
        in apply_exemptions([], {"methodology-style.latency": ["a.introduction"]}, ids)[0]
    )
    assert apply_exemptions(offenses, {}, ids)[0].startswith("reader style: a.introduction")
    assert "not a methodology style rule" in apply_exemptions([], {"other": ["x"]}, ids)[0]


def test_common_keeps_its_numbered_list_because_it_has_no_trial_steps() -> None:
    common = _item("common.ready_check", "1. Create a sandbox.\n2. Run the check.", is_common=True)
    assert _rules([common], "one-numbered-list") == []


def test_exemptions_only_shrink() -> None:
    exemptions = load_exemptions(ROOT)
    style = load_conventions(ROOT)["methodology_style"]
    ids = {rule["id"] for rule in style["rules"]}
    assert set(exemptions) <= ids
    for rule, keys in exemptions.items():
        assert len(keys) == len(set(keys)), rule
        assert len(keys) <= EXEMPTION_CEILING.get(rule, 0), (
            f"{rule} exempts {len(keys)} items, above its ceiling; entries are only deleted"
        )
    assert COMMON_KEY == "common"


def _introduction_context(introduction: str) -> Context:
    rows = {
        **ROWS,
        "exec-latency": {
            "id": "exec-latency",
            "kind": "test",
            "name": "Time per Call",
            "parent": "exec",
        },
    }
    documents = {
        "doc.time": {"metrics": [{"name": "total_time", "unit": "ms"}]},
        "doc.speed": {"metrics": [{"name": "total_mips", "unit": "MIPS"}]},
    }
    conventions = load_conventions(ROOT)
    return Context(
        rows=rows,
        names=list(rows.values()),
        documents=documents,
        method_documents={"exec-latency": "doc.time", "sequential-read": "doc.speed"},
        conventions=conventions,
        threshold=conventions["uniqueness"]["similarity_threshold"],
        introductions={"exec-latency": introduction, "sequential-read": introduction},
    )


def _introduction(method: str, text: str, resolved: str | None = None) -> list[str]:
    context = _introduction_context(resolved or text)
    return [
        message
        for rule, _, message in style_offenses(
            [Item(f"{method}.introduction", text, method=method)], context
        )
        if rule == "methodology-style.introduction-sentence"
    ]


@pytest.mark.parametrize(
    ("method", "text"),
    [
        ("exec-latency", "Times one call, from the request until it returns."),
        ("sequential-read", "Measures read speed with fio."),
        ("exec-latency", "Times {param:measured.procedure.variants.0.parameters.calls} calls."),
    ],
)
def test_an_introduction_of_the_one_shape_passes(method: str, text: str) -> None:
    assert _introduction(method, text) == []


@pytest.mark.parametrize(
    ("method", "text", "problem"),
    [
        ("exec-latency", "One call is timed from the request.", "start with Times"),
        ("exec-latency", "{name:test.exec-latency} times one call.", "not with a name"),
        ("exec-latency", "{name:tab.exec} times one call.", "not with a name"),
        ("exec-latency", "Measures one call.", "start with Times"),
        ("exec-latency", "times one call.", "start with Times"),
        ("sequential-read", "Times read speed.", "start with Measures"),
        ("exec-latency", "Times one call (`total_time`).", "metric id"),
        ("exec-latency", "Times one call in ms.", "unit 'ms'"),
        ("sequential-read", "Measures speed in MIPS.", "unit 'MIPS'"),
        ("exec-latency", "Times one call, lower is better.", "direction"),
        ("exec-latency", "Times one call. It ends.", "one sentence"),
    ],
)
def test_an_introduction_that_breaks_the_shape_says_what_to_fix(
    method: str, text: str, problem: str
) -> None:
    assert any(problem in message for message in _introduction(method, text)), text


def _view_subtitle(text: str) -> list[str]:
    base = _introduction_context("Times one call.")
    view = {"kind": "view", "id": "files-read-write", "name": "Read and Write", "subtitle": text}
    context = replace(base, names=[*base.names, view])
    return [
        message
        for rule, key, message in style_offenses([], context)
        if rule == "methodology-style.introduction-sentence"
        and key == "names.files-read-write.subtitle"
    ]


def test_a_combined_views_subtitle_follows_the_introduction_shape() -> None:
    assert _view_subtitle("Times repeated requests that read a file.") == []
    assert any(
        "start with Times or Measures" in m
        for m in _view_subtitle("Repeated requests read a file.")
    )
    assert any("one sentence" in m for m in _view_subtitle("Times a read. Then a write."))
    assert any("direction" in m for m in _view_subtitle("Times a read, lower is better."))


def test_an_introduction_is_measured_as_the_reader_reads_it() -> None:
    text = "Times one call."
    assert _introduction("exec-latency", text, resolved="Times " + "x" * 200) != []
    assert _introduction("exec-latency", text, resolved="Times one call.") == []


def test_no_exemption_is_left_for_the_introduction_rule() -> None:
    assert load_exemptions(ROOT)["methodology-style.introduction-sentence"] == []


@pytest.mark.parametrize(
    "text",
    [
        "The burst time is `burst_time`.",
        "Waits `60 s` for the sandbox.",
        "Reads `1m` blocks of `{param:measured.parameters.workload.rows.a.bs}`.",
        "The stage `{name:stage.create}` ends.",
        "The `Build` stage runs.",
        "Runs on `x86_64` with `numpy`.",
    ],
)
def test_a_code_mark_is_not_a_field_id_a_label_a_number_or_a_unit(text: str) -> None:
    assert set(_rules([_item("deepswe.procedure", text)], "code-marks")) == {"deepswe.procedure"}


@pytest.mark.parametrize(
    "text",
    [
        "Runs `git clone` and `npm ci` in `/workspace/app`.",
        "Passes `--no-rand-seed` and `-t 30`, sets `HTTP_PROXY` and `http_proxy`.",
        "Calls `files.write` and `image.prepare`, then the `resume` API call.",
        "Runs `./7zzs b -mmt{param:measured.environment.size.vcpu}`.",
        "Checks `{param:measured.parameters.workload.tool.sha256}` and `e5b8a7278660`.",
        "Serves `127.0.0.1:{param:measured.procedure.variants.0.parameters.port}`.",
        "Pulls `public.ecr.aws/d3j8x8q7/swe-bench-202605:<tag>-v1.1`.",
    ],
)
def test_a_code_mark_may_hold_what_a_reader_could_type(text: str) -> None:
    assert _rules([_item("deepswe.procedure", text)], "code-marks") == []


def test_the_metrics_opening_is_left_to_its_owner_for_now() -> None:
    item = _item("deepswe.metrics", "The primary metric is Burst time (`burst_time`, in `s`).")
    assert _rules([item], "code-marks") == []


def _shape(text: str) -> list[str]:
    return _rules([_item("deepswe.procedure", text)], "paragraph-shape")


def _varied(count: int) -> str:
    """Sentences that do not start alike, so only the length is under test."""
    starts = ["First", "Then", "Next", "After", "Later", "Soon", "Once", "Finally", "Last", "Also"]
    return " ".join(
        f"{starts[i % len(starts)]} the harness runs a step of the trial and records what it saw."
        for i in range(count)
    )


def test_a_paragraph_over_400_characters_is_refused_and_a_list_item_may_run_to_1200() -> None:
    assert _shape(_varied(10)) == ["deepswe.procedure"]
    assert _shape(_varied(6)) == ["deepswe.procedure"]
    assert _shape(_varied(4) + "\n\n" + _varied(4)) == []
    assert _shape(f"Steps:\n\n- {_varied(10)}\n- {_varied(3)}") == []
    assert _shape(f"Steps:\n\n- {_varied(20)}\n- Two.") == ["deepswe.procedure"]


def test_a_link_counts_as_the_word_it_draws() -> None:
    """A hash link draws its word only: its hidden hash and page are not paragraph characters."""
    hash_link = f"[SHA-256](hash:{'a' * 64})"
    commit_link = f"[commit](https://github.com/o/r/commit/{'b' * 40})"
    marked = f"{_varied(5)} By {hash_link} at {commit_link}."
    drawn = f"{_varied(5)} By SHA-256 at commit."
    assert len(drawn) < 400 < len(marked)
    assert _shape(marked) == []


def _drawn_shape(source: str, drawn: str) -> list[str]:
    context = replace(_context(), rendered={"deepswe": {"workload": drawn}})
    return [
        key
        for rule, key, _ in style_offenses([_item("deepswe.workload", source)], context)
        if rule == "methodology-style.paragraph-shape"
    ]


def test_a_paragraph_is_measured_as_the_page_draws_it() -> None:
    """a short source paragraph whose placeholders expand past 400 is refused."""
    source = "We evaluate {param:measured.a}, {param:measured.b}, and {param:measured.c}."
    drawn = f"We evaluate {'a' * 150}, {'b' * 150}, and {'c' * 150}."
    assert _shape(source) == []
    # The rule's exemptions match nothing in this one-paragraph page, so each is reported too.
    assert _drawn_shape(source, drawn) == ["deepswe.workload", *["exempt_paragraphs"] * 2]


def test_an_exempt_paragraph_stays_and_an_exemption_that_matches_nothing_is_refused() -> None:
    starts = [
        entry["starts"]
        for rule in load_conventions(ROOT)["methodology_style"]["rules"]
        for entry in rule.get("exempt_paragraphs", [])
    ]
    drawn = "\n\n".join(f"{start} {'x' * 400}." for start in starts)
    assert _drawn_shape("source", drawn) == []
    assert _drawn_shape("source", drawn.split("\n\n", 1)[1]) == ["exempt_paragraphs"]


def test_a_list_is_a_block_of_its_own() -> None:
    assert _shape("The steps are:\n- first step\n- second step") == ["deepswe.procedure"]
    assert _shape("The steps are:\n\n- first step\n- second step") == []
    assert _shape("1. first step\n2. second step") == []


def test_a_list_item_holds_a_second_level_of_one_indent_and_no_third() -> None:
    assert _shape("1. first step:\n   - one\n   - two\n2. second step") == []
    assert _shape("- first\n  - nested by two spaces\n- second") == []
    assert _shape("1. first step:\n   - one\n      - too deep") == ["deepswe.procedure"]
    assert _shape("1. first step:\n   - one\n  - indented differently") == ["deepswe.procedure"]
    assert _shape("Prose first:\n   - indented item") == ["deepswe.procedure"]
    assert _shape("1. first step:\n   - " + _varied(20)) == ["deepswe.procedure"]


def test_three_parallel_detailed_sentences_are_written_as_a_list() -> None:
    parallel = (
        "A success passes every step of the trial and holds the guest clock gate. "
        "A failure is any error of the provider that the trial records as its own. "
        "A script error is a failure of our own benchmark script and is re-run."
    )
    assert _shape(parallel) == ["deepswe.procedure"]
    assert _shape(parallel.replace("A script error", "Our script error")) == []
    assert _shape(parallel.replace(". A", ".\n\nA")) == []


def test_every_rendered_metric_paragraph_starts_with_its_name_and_the_old_note_is_gone(
    bundle: dict[str, Any],
) -> None:
    """One Metrics paragraph per metric, and one per composite on the Overview page; none
    repeats the introduction."""
    methods = bundle["reader"]["methods"]
    held = {
        identifier: entry["text"]["notes"]
        for identifier, entry in methods.items()
        if "metric" in entry["text"]["notes"]
    }
    names = {row["id"]: row["name"] for row in bundle["names"] if row["kind"] in {"test", "index"}}
    # Every test and index row has a Metrics paragraph.
    assert set(names) - set(held) == set()
    for identifier, notes in held.items():
        lead = re.split(r" \(|,", names[identifier], maxsplit=1)[0]
        text = notes["metric"]
        # A variant of another test (its size) starts with its full name, as the source rule allows.
        assert text.startswith((f"{lead} is the ", f"{names[identifier]} is the ")), identifier
        # The first paragraph is the metric's one sentence shape; a list of its parts may follow.
        assert "\n" not in text.split("\n\n")[0] and "{" not in text, identifier
        assert "metrics" not in notes, identifier  # no second copy
        assert notes["introduction"] not in text, identifier

    assert not [name for name, entry in methods.items() if "metrics" in entry["text"]["notes"]]


def test_reader_text_never_types_a_ranking_status_label() -> None:

    for label in ("Not supported", "Not offered", "Not measured", "Not displayed", "Not ranked"):
        typed = _item("pause-resume.failure_policy", f"A provider is shown as {label}.")
        assert _rules([typed], "status-labels") == ["pause-resume.failure_policy"], label
    read = "A provider is shown as {param:content.ranking_statuses.in_development.label}."
    assert _rules([_item("pause-resume.failure_policy", read)], "status-labels") == []
    assert (
        _rules(
            [_item("pause-resume.setup", "The run is ranked once it has trials.")], "status-labels"
        )
        == []
    )


def test_reader_text_never_types_a_size_a_recorded_fact_holds() -> None:

    for text in (
        "The data-science image (7.24 GB unpacked).",
        "It is 6.74 GiB unpacked.",
        "It is 2.27 GiB.",
    ):
        item = _item("image-build.workload", text)
        assert _rules([item], "recorded-sizes") == ["image-build.workload"], text
    read = (
        "The image ({imagesize:measured.parameters.variants.data-science.facts.uncompressed_bytes})"
    )
    assert _rules([_item("image-build.workload", read)], "recorded-sizes") == []
    assert (
        _rules(
            [_item("hard-links.workload", "At least 1 GiB free, 4 KiB files.")], "recorded-sizes"
        )
        == []
    )


def test_a_measured_size_draws_in_decimal_and_a_defined_one_in_its_binary_unit() -> None:

    read = (
        "The image ({imagesize:measured.parameters.variants.data-science.facts.uncompressed_bytes})"
    )
    item = _item("image-build.workload", read)
    for drawn, offending in (("The image (6.74 GiB).", True), ("The image (7.24 GB).", False)):
        context = replace(_context(), rendered={"image-build": {"workload": drawn}})
        found = [key for rule, key, _ in style_offenses([item], context) if "recorded" in rule]
        assert found == (["image-build.workload"] if offending else []), drawn
    # The machine's 4 GiB, a 4 KiB payload and fio's 64k blocks are defined in binary.
    for text in ("A 4.29 GB machine.", "A 4.1 kB payload.", "65.5 kB blocks."):
        assert _rules([_item("deepswe.workload", text)], "recorded-sizes") == [
            "deepswe.workload"
        ], text
    assert _rules([_item("deepswe.workload", "A 4 GiB machine, 4 KiB.")], "recorded-sizes") == []


def test_an_introduction_never_names_the_machine_size() -> None:

    for text in (
        "Times a layer for a {machine:measured.environment.size} machine.",
        "Times a layer on a 4 vCPU sandbox.",
        "Times a layer on 8 GiB of memory.",
    ):
        item = _item("image-build.introduction", text)
        assert _rules([item], "introduction-no-machine-size") == ["image-build.introduction"], text
    good = _item("image-build.introduction", "Times how long a provider takes to prepare a layer.")
    assert _rules([good], "introduction-no-machine-size") == []
    metrics = _item(
        "image-build.metric", "Ready to create {machine:measured.environment.size} sandboxes."
    )
    assert _rules([metrics], "introduction-no-machine-size") == []


def test_an_introduction_does_not_make_the_job_host_the_subject() -> None:

    typed = _item("fork.introduction", "Times the job host creating a sandbox from a snapshot.")
    assert _rules([typed], "introduction-not-the-job-host") == ["fork.introduction"]
    good = _item("fork.introduction", "Times how long a provider takes to create a sandbox.")
    assert _rules([good], "introduction-not-the-job-host") == []


def _staged() -> Context:
    """The test context with two timed parts: a named stage and a measured definition's own."""
    stage = {"id": "install", "kind": "stage", "name": "Install", "parent": "ci-jobs"}
    websocket = {"id": "websocket", "kind": "stage", "name": "WebSocket", "parent": "previews"}
    first = {"id": "first-command", "name": "First command"}
    documents = {"doc.burst": {"id": "doc.burst", "procedure": {"stages": [first]}}}
    return replace(_context(), names=[*ROWS.values(), stage, websocket], documents=documents)


def _stage_labels(items: list[Item], context: Context | None = None) -> list[str]:
    context = context or _staged()
    return [
        f"{key}: {message.split(':')[0]}"
        for rule, key, message in style_offenses(items, context)
        if rule == "methodology-style.stage-labels"
    ]


def test_reader_text_never_types_a_timed_part_name() -> None:
    for text in (
        "The sum of Create and First command.",  # a name of two words is typed anywhere
        "Teams wait less on Install than here.",  # a single word typed mid-sentence
        "Each of four stages (Install, Lint).",
    ):
        assert _stage_labels([_item("ci-jobs.limitations", text)]) != [], text
    read = "Teams wait less on {name:stage.install} and {param:measured.procedure.stages.1.name}."
    assert _stage_labels([_item("ci-jobs.limitations", read)]) == []
    for text in (
        "Install the toolchain first.",  # a verb that starts a sentence
        "Create sandbox, then run it.",
        "[[steps: create]]Install the packages.",
        "- Install the packages.",
        "A WebSocket upgrade succeeds.",  # an ordinary word as well as a name
        "A job runs `Install` and `First command`.",  # a code mark is not prose
    ):
        assert _stage_labels([_item("ci-jobs.setup", text)]) == [], text


def test_a_timed_part_is_never_typed_as_a_mark_or_inside_a_code_mark() -> None:
    assert _stage_labels([_item("ci-jobs.metric", "Stages: *Install* and more.")]) != []
    assert _stage_labels([_item("ci-jobs.metric", "Stages: `{name:stage.install}` run.")]) != []
    assert _stage_labels([_item("ci-jobs.metric", "Stages: {name:stage.install} run.")]) == []


def test_the_compiled_notes_mark_only_timed_part_names() -> None:
    context = replace(_staged(), rendered={"ci-jobs": {"metric": "Stages *Install* and *Other*."}})
    assert _stage_labels([], context) == [
        "ci-jobs.notes.metric: marks 'Other', which is no timed part's name"
    ]
