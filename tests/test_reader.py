# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""The reader copy is wording only: apart from the scientific records, read as measured."""

import copy
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator

from sandbox_benchmark_verifier import reader, text
from sandbox_benchmark_verifier.catalog import (
    default_context,
    load_methodology,
    load_schema,
    ordinary_documents,
)
from sandbox_benchmark_verifier.content import content_criteria
from sandbox_benchmark_verifier.lock import read_lock
from sandbox_benchmark_verifier.methodology_page import reader_definition
from sandbox_benchmark_verifier.reader_steps import (
    headline_span_errors,
    split_trial_steps,
    timed_parts,
    trial_step_items,
    variant_commands,
    with_step_notes,
)
from sandbox_benchmark_verifier.versions import version_key

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]


def _source() -> dict[str, Any]:
    """A fresh copy of the reader source."""
    loaded = reader.load_reader(ROOT)
    assert loaded is not None
    return copy.deepcopy(loaded)


def _valid(source: dict[str, Any]) -> bool:
    return not list(Draft202012Validator(load_schema("reader")).iter_errors(source))


def test_the_reader_source_is_valid_and_names_every_test_and_index_once() -> None:
    source = reader.load_reader(ROOT)
    assert source is not None and _valid(source)
    assert reader.reader_errors(ROOT) == []
    assert len(source["methods"]) >= 30


@pytest.mark.parametrize(
    "key",
    ["metrics", "procedure", "statistics", "parameters", "inherits", "cost", "source", "version"],
)
def test_the_reader_schema_refuses_science_keys(key: str) -> None:
    source = _source()
    source["methods"]["sequential-read"][key] = {"anything": 1}
    assert not _valid(source)
    source = _source()
    source["methods"]["sequential-read"]["panel"][key] = "x"
    assert not _valid(source)


def test_a_fact_may_only_read_the_measured_definition(monkeypatch: pytest.MonkeyPatch) -> None:
    source = _source()
    source["methods"]["sequential-read"]["notes"]["how_to_read"] = (
        "The {param:sequential-read.1.1.18.statistics.primary} summarizes the trials."
    )
    monkeypatch.setattr(reader, "load_reader", lambda _root: source)
    assert any("pins a document version" in error for error in reader.reader_errors(ROOT))


def _measured(identifier: str, version: str) -> dict[str, Any]:
    return next(
        document
        for document in text._methodologies(ROOT)
        if (document["id"], document["version"]) == (identifier, version)
    )


def test_current_fio_result_uses_the_current_command_and_its_own_facts(
    bundle: dict[str, Any],
) -> None:
    method = bundle["reader"]["methods"]["sequential-read"]
    versions = bundle["methodologies"]["sequential-read"]
    version = max(versions, key=version_key)  # older versions are kept for pinned data
    copy_for_version = reader.reader_text(bundle, "sequential-read", version)
    assert version in versions
    assert copy_for_version["panel"]["command"] == method["text"]["panel"]["command"]
    measured = _measured("sequential-read", version)
    assert measured["parameters"]["path"] == f"parameters/sequential-read/{version}/"
    assert bundle["reader"]["source"]["path"] == "reader/methods.yaml"
    assert bundle["schema"] == "boxrouter.bench.text/3"


def _pinned(directory: Path, block_size: str) -> str:
    """A pinned fio workload directory: the current sequential-read parameters with the sequential
    read row at the given block size. Older real versions compare equal to it, bar the row."""
    directory.mkdir(parents=True)
    workloads = json.loads(
        (ROOT / "parameters" / "sequential-read" / "1.1.18" / "workloads.json").read_text()
    )
    workloads[0]["data"]["rows"]["disk-seq-read-1m"]["bs"] = block_size
    (directory / "workloads.json").write_text(json.dumps(workloads), encoding="utf-8")
    return str(directory)


def test_a_reader_command_follows_the_arguments_each_version_pinned(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    build_text_bundle: Callable[[Path], dict[str, Any]],
) -> None:
    current_version = "1.1.18"
    current_document = copy.deepcopy(_measured("sequential-read", current_version))
    new = copy.deepcopy(current_document)
    current_document["parameters"] = {"path": _pinned(tmp_path / "current", "1m")}
    new["version"] = "9.9.9"
    new["parameters"] = {"path": _pinned(tmp_path / "new", "4m")}
    new["environment"]["size"]["vcpu"] = 8
    new["timing_boundaries"]["stop"] = "A later version stops on a different rule."
    new["procedure"]["steps"][2]["action"] = "A later version runs fio with other settings."
    source = _source()
    source["methods"] = {"sequential-read": source["methods"]["sequential-read"]}
    source["methods"]["sequential-read"]["notes"]["how_to_read"] = (
        "The value comes from {param:measured.environment.size.vcpu} vCPUs."
    )
    monkeypatch.setattr(reader, "load_reader", lambda _root: source)
    real = text._methodologies
    monkeypatch.setattr(
        text,
        "_methodologies",
        lambda root: (
            [
                d
                for d in real(root)
                if (d["id"], d["version"]) != ("sequential-read", current_version)
            ]
            + [current_document, new]
        ),
    )
    method = build_text_bundle(ROOT)["reader"]["methods"]["sequential-read"]
    current = method["by_version"].get(current_version, method["text"])
    later = method["by_version"].get("9.9.9", method["text"])
    assert (
        current["panel"]["title"] == later["panel"]["title"] == "Disk Speed (fio) · Sequential Read"
    )
    assert current["panel"]["command"] == "fio --rw=read --bs=1m"
    assert later["panel"]["command"] == "fio --rw=read --bs=4m"
    assert current["notes"]["how_to_read"] == "The value comes from 2 vCPUs."
    assert later["notes"]["how_to_read"] == "The value comes from 8 vCPUs."
    # The two fixture versions differ in recorded facts, and the old one still holds its own.
    assert current_document["timing_boundaries"]["stop"] != new["timing_boundaries"]["stop"]
    assert (
        current_document["procedure"]["steps"][2]["action"]
        != new["procedure"]["steps"][2]["action"]
    )
    assert (
        current_document["environment"]["size"]["vcpu"],
        new["environment"]["size"]["vcpu"],
    ) == (2, 8)


def test_a_version_the_copy_cannot_describe_is_an_error_never_a_fallback(
    bundle: dict[str, Any],
) -> None:
    for identifier, method in bundle["reader"]["methods"].items():
        for version in method["unresolved_versions"]:
            with pytest.raises(reader.ReaderVersionError, match=version):
                reader.reader_text(bundle, identifier, version)
    with pytest.raises(reader.ReaderVersionError, match="9.9.9"):
        reader.reader_text(bundle, "sequential-read", "9.9.9")
    with pytest.raises(reader.ReaderVersionError, match="no-such-test"):
        reader.reader_text(bundle, "no-such-test", "1.0.0")


def test_an_unresolvable_version_is_recorded_not_replaced_by_newer_facts(
    monkeypatch: pytest.MonkeyPatch,
    build_text_bundle: Callable[[Path], dict[str, Any]],
) -> None:
    source = _source()
    source["methods"] = {"sequential-read": source["methods"]["sequential-read"]}
    source["methods"]["sequential-read"]["notes"]["how_to_read"] = (
        "The {param:measured.statistics.no_such_fact} reads this."
    )
    monkeypatch.setattr(reader, "load_reader", lambda _root: source)
    with pytest.raises(ValueError, match=r"sequential-read@1\.1\.\d+"):
        build_text_bundle(ROOT)


def test_a_broken_source_or_an_unknown_id_raises_a_named_error(
    monkeypatch: pytest.MonkeyPatch,
    build_text_bundle: Callable[[Path], dict[str, Any]],
) -> None:
    broken, unknown = _source(), _source()
    broken["methods"]["sequential-read"]["metrics"] = []
    unknown["methods"]["no-such-test"] = {"headline": "x"}
    monkeypatch.setattr(reader, "load_reader", lambda _root: broken)
    with pytest.raises(ValueError, match="methods/sequential-read.*'metrics' was unexpected"):
        build_text_bundle(ROOT)
    monkeypatch.setattr(reader, "load_reader", lambda _root: unknown)
    with pytest.raises(ValueError, match="'no-such-test' names no test or index"):
        build_text_bundle(ROOT)


def test_editing_the_reader_changes_no_scientific_record(
    monkeypatch: pytest.MonkeyPatch,
    bundle: dict[str, Any],
    build_text_bundle: Callable[[Path], dict[str, Any]],
) -> None:
    before = bundle
    lock_before = dict(read_lock(ROOT))
    source = _source()
    source["methods"]["sequential-read"]["panel"]["command"] = "fio --rw=read --bs=2m"
    monkeypatch.setattr(reader, "load_reader", lambda _root: source)
    after = build_text_bundle(ROOT)
    assert (
        after["reader"]["methods"]["sequential-read"]
        != before["reader"]["methods"]["sequential-read"]
    )
    assert (
        after["reader"]["methods"]["sequential-read"]["text"]["panel"]["command"]
        == "fio --rw=read --bs=2m"
    )
    for identifier, versions in before["methodologies"].items():
        for version, record in versions.items():
            scientific = {key: value for key, value in record.items() if key != "text"}
            after_record = after["methodologies"][identifier][version]
            assert scientific == {
                key: value for key, value in after_record.items() if key != "text"
            }
    assert dict(read_lock(ROOT)) == lock_before
    on_disk = yaml.safe_load((ROOT / "reader/methods.yaml").read_text())
    assert on_disk["methods"]["sequential-read"]["panel"]["command"] != "fio --rw=read --bs=2m"


def test_rendered_region_policies_resolve_the_exact_recorded_definition(
    bundle: dict[str, Any],
) -> None:
    documents = {
        (str(item.document["id"]), str(item.document["version"])): item.document
        for item in load_methodology(ROOT)
    }
    compared = 0
    for identifier, versions in bundle["methodologies"].items():
        for version, row in versions.items():
            environment = row["reader_definition"].get("environment", {})
            if "region_policy" not in environment:
                continue
            document = documents[(identifier, version)]
            expected = document.get("environment", {}).get("region_policy")
            if expected is None:
                inherited = document["inherits"]
                expected = documents[(inherited["id"], str(inherited["version"]))]["environment"][
                    "region_policy"
                ]
            assert environment["region_policy"] == expected, (identifier, version)
            compared += 1
    assert compared > 0


def test_bound_region_policies_coexist_without_a_global_reader_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    documents = {
        (str(item.document["id"]), str(item.document["version"])): item.document
        for item in load_methodology(ROOT)
    }
    versions = ("1.0.18",)
    future = copy.deepcopy(documents[("sbs.common", versions[-1])])
    future["version"] = "1.0.999"
    future["environment"]["region_policy"] = {"selection": "no_portable_region_request"}
    documents[("sbs.common", future["version"])] = future
    definitions: dict[str, Any] = {}
    for version in (*versions, future["version"]):
        identifier = f"synthetic-{version}"
        document = {
            "id": identifier,
            "version": "1.0.0",
            "inherits": {"id": "sbs.common", "version": version},
            "environment": {"network": "unknown"},
        }
        documents[(identifier, "1.0.0")] = document
        definitions[identifier] = document
    override: dict[str, Any] = {
        "id": "synthetic-override",
        "version": "1.0.0",
        "inherits": {"id": "sbs.common", "version": future["version"]},
        "environment": {"region_policy": "An explicit recorded leaf policy."},
    }
    definitions[override["id"]] = override
    original = copy.deepcopy(documents)
    lock_before = read_lock(ROOT)
    policy = content_criteria()["methodology_page"]
    rows = {
        identifier: {"1.0.0": {"reader_definition": reader_definition(document, documents, policy)}}
        for identifier, document in definitions.items()
    }
    source = _source()
    source["methods"] = {}
    monkeypatch.setattr(reader, "load_reader", lambda _root: source)
    reader.apply_reader_views(ROOT, rows, source)
    for identifier, document in definitions.items():
        shown = rows[identifier]["1.0.0"]["reader_definition"]
        inherited = document["inherits"]
        expected = document["environment"].get(
            "region_policy",
            documents[(inherited["id"], inherited["version"])]["environment"]["region_policy"],
        )
        assert shown["environment"]["region_policy"] == expected
        if identifier != override["id"]:
            assert shown["reader_sources"]["environment.region_policy"] == inherited
    assert documents == original
    assert read_lock(ROOT) == lock_before


def test_reader_schema_refuses_global_measured_environment_overrides() -> None:
    source = _source()
    source["common"]["environment"] = {"region_policy": "A replacement location policy."}
    assert not _valid(source)
    source = _source()
    source["common"]["metrics"] = []
    assert not _valid(source)


STEPS: list[dict[str, Any]] = [
    {"id": "create", "timer": {"start_event": "a", "stop_event": "b"}},
    {"id": "check", "timer": None},
    {"id": "run", "timer": {"start_event": "a", "stop_event": "b"}},
]
NOTE = (
    "[[steps: create]]Create sandbox [[steps: check]]and run readiness check (untimed); then "
    "[[steps: run]]run the command; then [[steps: ]]destroy."
)


def test_trial_steps_split_into_items_and_the_details_text_loses_only_the_markers() -> None:
    details, clauses = split_trial_steps(NOTE)
    assert (
        details
        == "Create sandbox and run readiness check (untimed); then run the command; then destroy."
    )
    assert [ids for ids, _ in clauses] == [["create"], ["check"], ["run"], []]
    note = NOTE.replace("[[steps: ]]destroy.", "")
    details, items = trial_step_items(note, STEPS, strict=True)
    assert (
        details == "Create sandbox and run readiness check (untimed); then run the command; then "
    )
    assert items == [
        {"steps": ["create"], "text": "Create sandbox.", "timed": True},
        {"steps": ["check"], "text": "Run readiness check.", "timed": False},
        {"steps": ["run"], "text": "Run the command.", "timed": True},
    ]


def test_an_untimed_item_loses_its_marker_word_because_the_tag_says_it() -> None:
    steps: list[dict[str, Any]] = [
        {"id": "prepare", "timer": None},
        {"id": "run", "timer": {"x": 1}},
    ]
    _, items = trial_step_items(
        "[[steps: prepare]]Push a layer (untimed); then [[steps: run]]time the call.",
        steps,
        strict=True,
    )
    assert [(item["text"], item["timed"]) for item in items] == [
        ("Push a layer.", False),
        ("Time the call.", True),
    ]


@pytest.mark.parametrize(
    ("note", "message"),
    [
        (
            "[[steps: create, run]]A; then [[steps: run, create]]B.",
            "step 'run' is in items 1 and 2",
        ),
        ("[[steps: create, check]]A.", "joins timed and untimed steps"),
        ("[[steps: create]]A; then [[steps: check]]B.", "timed steps without an item: run"),
        (
            "[[steps: create, nowhere]]A; then [[steps: run]]B.",
            "'nowhere', which the definition has no step for",
        ),
        (
            "[[steps: create]]A (untimed); then [[steps: run]]B.",
            "tagged timed but says it is untimed",
        ),
        ("Intro. [[steps: create, run]]A.", "text before the first"),
        ("A note with no markers.", "the definition has steps, so the note needs"),
    ],
)
def test_trial_step_items_must_cover_the_definition_steps_once(note: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        trial_step_items(note, STEPS, strict=True)


def test_a_note_without_markers_is_one_item_when_the_definition_has_no_steps() -> None:
    assert trial_step_items("Calculated from the inputs.", [], strict=True) == (
        "Calculated from the inputs.",
        [{"text": "Calculated from the inputs."}],
    )


def test_every_other_reader_of_the_source_sees_the_note_without_markers() -> None:
    marked = reader.load_reader(Path("."))
    plain = reader.plain_reader(Path("."))
    assert marked is not None and plain is not None
    notes = [
        method["notes"]["trial_steps"]
        for method in marked["methods"].values()
        if "trial_steps" in method.get("notes", {})
    ]
    assert any("[[steps:" in note for note in notes)
    assert not any(
        "[[steps:" in method.get("notes", {}).get("trial_steps", "")
        for method in plain["methods"].values()
    )


def test_a_sentence_that_writes_one_before_a_plural_noun_is_flagged() -> None:
    method = {
        "text": {
            "notes": {
                "procedure": "Up to 1 trials run. Sandbox 1 starts cold. 1 GiB, 1 process, 1 class."
            }
        },
        "by_version": {"1.0.0": {"notes": {"limitations": ["Each trace ran 1 times."]}}},
    }
    errors = reader.reader_count_errors({"methods": {"demo": method}})
    tail = "is a plural after one; word the count so it reads right for any number"
    assert errors == [
        f"demo.notes.procedure: '1 trials' {tail}",
        f"demo@1.0.0.notes.limitations[0]: '1 times' {tail}",
    ]


def test_a_sentence_that_prints_a_bare_integer_of_seven_digits_is_flagged() -> None:
    method = {
        "text": {
            "notes": {
                "procedure": "At least 2147483648 bytes free, 640,000,000 bytes, 1,048,576 bytes/s,"
                " run 123456 times, commit 1234567abc, 12345678.5 s, `seed = 20261004`, and"
                " 2 GiB are fine."
            }
        },
        "by_version": {"1.0.0": {"notes": {"limitations": ["A `1073741824` marker."]}}},
    }
    errors = reader.reader_integer_errors({"methods": {"demo": method}})
    tail = "is a bare integer; write a size with a unit or group the digits"
    assert errors == [
        f"demo.notes.procedure: '2147483648' {tail}",
        f"demo@1.0.0.notes.limitations[0]: '1073741824' {tail}",
    ]


def test_an_untimed_marker_is_an_untimed_item_with_no_step() -> None:
    note = (
        "[[untimed]]Push a layer (untimed); then [[steps: create]]create; then "
        "[[steps: run]]run; then [[untimed]]delete the copy (untimed)."
    )
    details, items = trial_step_items(note, STEPS, strict=True)
    assert (
        details == "Push a layer (untimed); then create; then run; then delete the copy (untimed)."
    )
    assert items == [
        {"text": "Push a layer.", "timed": False},
        {"steps": ["create"], "text": "Create.", "timed": True},
        {"steps": ["run"], "text": "Run.", "timed": True},
        {"text": "Delete the copy.", "timed": False},
    ]


def test_one_item_for_a_definition_of_several_steps_is_refused() -> None:
    steps: list[dict[str, Any]] = [
        {"id": "create", "timer": {"x": 1}},
        {"id": "run", "timer": {"x": 1}},
    ]
    with pytest.raises(ValueError, match="one item for a definition of 2 steps"):
        trial_step_items("[[steps: create, run]]Do both.", steps, strict=True)


SPINE: list[dict[str, Any]] = [
    {"id": "build", "name": "Image build", "timer": None, "timeout_seconds": 1800},
    {
        "id": "create",
        "name": "Create",
        "timer": {"from": "the create request", "sent": "x", "until": "the create call returns"},
        "timeout_seconds": 300,
    },
    {
        "id": "first",
        "name": "First command",
        "timer": {"start_event": "The command is sent.", "stop_event": "Its exit code returns."},
        "timeout_seconds": 120,
    },
    {
        "id": "destroy",
        "name": "Destroy",
        "timer": {"from": "the destroy request", "until": "the destroy call returns"},
        "timeout_seconds": 300,
    },
]


def test_items_say_whether_the_step_makes_the_headline_and_carry_no_time_limit() -> None:
    """Destroy is timed by the definition but is not part of the number (the band stops short)."""
    note = (
        "[[steps: build]]Build; then [[steps: create]]create; then [[steps: first]]run; then "
        "[[steps: destroy]]destroy."
    )
    _, items = trial_step_items(note, SPINE, strict=True, headline=["create", "first"])
    assert [item.get("headline") for item in items] == [None, True, True, None]
    assert not any("limit" in item for item in items)


def test_timed_parts_are_the_headline_steps_timers_in_either_timer_style() -> None:
    assert timed_parts(SPINE, ["create", "first"], strict=True) == [
        {"part": "Create", "starts": "the create request", "ends": "the create call returns"},
        {
            "part": "First command",
            "starts": "The command is sent.",
            "ends": "Its exit code returns.",
        },
    ]


@pytest.mark.parametrize(
    ("headline", "message"),
    [
        (["build"], "step 'build' has no timer"),
        (["create", "missing"], "the definition has no step missing"),
    ],
)
def test_a_headline_step_must_be_a_timed_step_of_the_newest_definition(
    headline: list[str], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        timed_parts(SPINE, headline, strict=True)
    assert timed_parts(SPINE, headline, strict=False) is not None


def test_every_reader_method_with_steps_names_the_headline_steps(bundle: dict[str, Any]) -> None:
    """The page draws a band over these steps and a Timed Part table of their timers."""
    source = reader.load_reader(ROOT)
    assert source is not None
    for identifier, entry in source["methods"].items():
        text = bundle["reader"]["methods"][identifier]["text"]["notes"]
        if "step_items" in text and any("steps" in item for item in text["step_items"]):
            assert entry.get("headline_steps"), identifier
            assert text.get("timed_parts"), identifier
            assert any(item.get("headline") for item in text["step_items"]), identifier


def test_the_headline_steps_span_what_the_first_metric_computes() -> None:
    """A band over other steps would misstate the method (46-1A)."""
    steps = [
        {"id": "create", "record_steps": ["create"]},
        {"id": "first", "record_steps": ["first-true"]},
        {"id": "destroy", "record_steps": ["destroy"]},
    ]
    metrics = [{"name": "tti", "formula": "end(first-true) - start(create)"}]
    headline_span_errors(metrics, steps, ["create", "first"])
    with pytest.raises(ValueError, match="the last step 'destroy' does not record 'first-true'"):
        headline_span_errors(metrics, steps, ["create", "first", "destroy"])
    with pytest.raises(ValueError, match="the first step 'first' does not record 'create'"):
        headline_span_errors(metrics, steps, ["first"])
    headline_span_errors([{"name": "m"}], steps, ["destroy"])


def test_a_test_page_metrics_opens_with_what_the_measure_is(bundle: dict[str, Any]) -> None:
    """Metrics is one definition sentence (card 40): "<Measure> is the ...", with no "The primary
    metric is", no field id and no "(..., lower is better)" parenthesis; the timed parts are the
    table beneath it, so no statistic sentence repeats At a Glance."""
    source = reader.load_reader(ROOT)
    assert source is not None
    problems = []
    for identifier, entry in source["methods"].items():
        if "headline_steps" not in entry or "metrics" not in entry.get("notes", {}):
            continue
        notes = bundle["reader"]["methods"][identifier]["text"]["notes"]
        first = notes["metrics"].strip().split("\n")[0]
        if re.match(r"(?:The (?:primary|headline) metric|Each provider is plotted)", first):
            problems.append(f"{identifier}: opens with a metric announcement: {first[:50]}")
        if re.search(r"\((?:[^()]*, )?(?:lower|higher) is better\)", first):
            problems.append(f"{identifier}: a direction in parentheses: {first[:50]}")
        if "how_to_read" in notes:
            problems.append(f"{identifier}: still has the statistic sentence (how_to_read)")
    assert problems == []


def test_variants_that_run_their_own_commands_are_a_table_and_the_rest_are_not() -> None:
    own = [
        {"id": "a", "label": "true", "command": "true"},
        {"id": "b", "label": "git --version", "command": "git --version"},
    ]
    assert variant_commands(own) == [{"command": "`true`"}, {"command": "`git --version`"}]
    assert variant_commands([own[0]]) == []
    assert variant_commands([own[0], {"id": "c", "command": "true"}]) == []
    assert variant_commands([{"id": "n5"}, {"id": "n10"}]) == []


def test_the_enumerations_the_page_draws_as_tables_are_resolved_rows(
    bundle: dict[str, Any],
) -> None:
    """Change 8: variants, jobs, stage classes and outcomes are resolved rows, never prose."""
    notes = {
        name: bundle["reader"]["methods"][name]["text"]["notes"]
        for name in ("sequential-tti", "ci-jobs", "deepswe")
    }

    commands = notes["sequential-tti"]["variant_commands"]
    assert [list(row) for row in commands] == [["command"]] * len(commands)
    assert commands
    for name, table in (
        ("ci-jobs", "jobs"),
        ("deepswe", "stage_classes"),
        ("deepswe", "outcomes"),
        ("sequential-tti", "variant_commands"),
    ):
        for row in notes[name][table]:
            assert all("{" not in cell for cell in row.values()), (name, table, row)


def test_a_ci_job_repository_and_commit_are_read_from_the_definition_not_typed() -> None:
    """The jobs table re-typed values the variants own (change 8 follow-up)."""
    source = yaml.safe_load((ROOT / "reader" / "methods.yaml").read_text(encoding="utf-8"))
    for row in source["methods"]["ci-jobs"]["tables"]["jobs"]:
        assert re.fullmatch(
            r"`\{param:measured\.procedure\.variants\.\d+\.parameters\.repository\}`",
            row["repository"],
        ), row
        assert re.fullmatch(
            r"\{commit:(measured\.procedure\.variants\.\d+\.parameters)\.repository\|\1\.commit\}",
            row["commit"],
        ), row


def test_a_setup_line_never_restates_what_at_a_glance_and_sandbox_and_trials_show() -> None:
    """Machine size, trials and parallelism have an owner; Setup keeps images, tools and counts."""
    source = yaml.safe_load((ROOT / "reader" / "methods.yaml").read_text(encoding="utf-8"))
    lines = {
        f"methods.{name}.setup": entry["notes"]["setup"]
        for name, entry in source["methods"].items()
        if isinstance(entry.get("notes", {}).get("setup"), str)
    }
    blocks = source["common"]["blocks"]
    lines["blocks.common.sandbox"] = blocks["common"]["sandbox"]
    lines["blocks.cpu.setup"] = blocks["cpu"]["setup"]
    lines["blocks.disk.setup_read"] = blocks["disk"]["setup_read"]
    restating = re.compile(
        r"\{machine:|\{param:measured\.procedure\.trials\}|trials_in_parallel"
        r"|\d+ (?:shared )?trials"
    )
    assert {name: line for name, line in lines.items() if restating.search(line)} == {}


# Sentences whose machine size sits inside a clause that is the only owner of another fact.
_MACHINE_KEPT = {("delayed-resume", "workload")}


def test_workload_and_procedure_never_restate_machine_size_trials_or_parallelism() -> None:
    """Machine size, trials and parallelism have their own owners in the page."""
    source = yaml.safe_load((ROOT / "reader" / "methods.yaml").read_text(encoding="utf-8"))
    found: dict[str, str] = {}
    for name, entry in source["methods"].items():
        for field in ("workload", "procedure"):
            line = entry.get("notes", {}).get(field)
            if not isinstance(line, str) or (name, field) in _MACHINE_KEPT:
                continue
            if re.search(
                r"\{machine:|concurrency_rounds|four shuffled rounds|readiness check\.$", line, re.M
            ):
                found[f"{name}.{field}"] = line
    for key, line in source["common"]["blocks"].items():
        for field, value in line.items():
            if field.startswith("shared_") and re.search(r"\{machine:|four shuffled rounds", value):
                found[f"blocks.{key}.{field}"] = value
    assert "concurrency_rounds" not in source["common"]["blocks"]["common"]


# Openings whose machine size is the argument itself (two providers selling the same size differ).
_OPENING_MACHINE_KEPT = {"blocks.cpu.opening", "methods.stream-triad.opening"}


def test_an_opening_never_restates_machine_size_or_trial_counts() -> None:
    """The opening says what the test measures and why; the size and the trials have their rows."""
    source = yaml.safe_load((ROOT / "reader" / "methods.yaml").read_text(encoding="utf-8"))
    openings = {
        f"methods.{name}.opening": entry["notes"]["opening"]
        for name, entry in source["methods"].items()
        if isinstance(entry.get("notes", {}).get("opening"), str)
    }
    openings |= {
        f"blocks.{name}.opening": block["opening"]
        for name, block in source["common"]["blocks"].items()
        if "opening" in block
    }
    restating = re.compile(r"\{machine:|\}-trial (?:runs|sets)")
    found = {
        key: line
        for key, line in openings.items()
        if key not in _OPENING_MACHINE_KEPT and restating.search(line)
    }
    assert found == {}


def test_one_metric_has_one_name_on_its_page(bundle: dict[str, Any]) -> None:
    """A Metrics sentence that starts with a metric's first two words says its whole name."""
    problems = []
    for name, method in bundle["reader"]["methods"].items():
        sentence = method["text"]["notes"].get("metrics", "")
        opening = re.split(r" is (?:the|a|an) | is ", sentence, maxsplit=1)[0]
        if not opening:
            continue
        versions = bundle["methodologies"][method["document"]]
        newest = versions[max(versions, key=version_key)]
        labels = [
            metric["reader_name"]
            for metric in newest.get("reader_definition", {}).get("metrics", [])
            if metric.get("reader_name") and ":" not in metric["reader_name"]
        ]
        lead = [word.casefold() for word in opening.split()[:2]]
        resembles = any([word.casefold() for word in label.split()[:2]] == lead for label in labels)
        if resembles and opening.casefold() not in {label.casefold() for label in labels}:
            problems.append(f"{name}: the sentence says {opening!r}, the metrics are {labels}")
    assert problems == []


def test_a_step_note_sits_under_the_item_that_holds_its_step() -> None:
    items = [{"steps": ["create"], "text": "Create."}, {"steps": ["a", "b"], "text": "A and B."}]
    noted = with_step_notes(items, {"b": "B retries."}, strict=True)
    assert [item.get("note") for item in noted] == [None, "B retries."]
    with pytest.raises(ValueError, match="no trial step item holds step 'gone'"):
        with_step_notes(items, {"gone": "x"}, strict=True)
    assert with_step_notes(items, {"gone": "x"}, strict=False) == items
    with pytest.raises(ValueError, match="already has a note"):
        with_step_notes(items, {"a": "x", "b": "y"}, strict=True)


def test_a_per_step_fact_is_a_step_note_and_not_also_a_procedure_sentence(
    bundle: dict[str, Any],
) -> None:
    """A sentence that a step note says is not said again in the procedure or workload prose."""
    for name, method in bundle["reader"]["methods"].items():
        text = method["text"]["notes"]
        prose = " ".join(str(text.get(field, "")) for field in ("procedure", "workload"))
        for item in text.get("step_items", []):
            for sentence in re.split(r"(?<=[.!?])\s+", item.get("note", "")):
                assert not sentence or sentence not in prose, (name, sentence)


def test_no_step_item_carries_a_time_limit_and_the_only_pill_label_is_timed(
    bundle: dict[str, Any],
) -> None:
    """only Timed pills, with no time; limits stay in the definitions."""
    for name, method in bundle["reader"]["methods"].items():
        for version in [method["text"], *method.get("by_version", {}).values()]:
            for item in version.get("notes", {}).get("step_items", []):
                assert "limit" not in item, (name, item)
    assert bundle["content_criteria"]["methodology_page"]["steps"] == {"timed": "Timed"}


def test_a_setup_line_joins_its_trailing_fragment_with_a_comma(bundle: dict[str, Any]) -> None:
    """the five choppy Setup lines take a comma before the fragment."""
    methods = bundle["reader"]["methods"]
    for name in ("7-zip-1-thread", "stream-triad", "sequential-read", "hard-links"):
        assert re.search(r"image, running \S.* build of ", methods[name]["text"]["notes"]["setup"])
    assert (
        ", without starting a sandbox."
        in methods["image-build-2vcpu-4gib"]["text"]["notes"]["setup"]
    )


def test_failures_draws_every_step_time_limit_once_as_a_table(bundle: dict[str, Any]) -> None:
    """Step · Time Limit, from the definition's steps that have a limit."""
    page = bundle["content_criteria"]["methodology_page"]
    assert "notes.time_limits" in page["sections"]["failures"]["sources"]
    assert (page["field_labels"]["step"], page["field_labels"]["time_limit"]) == (
        "Step",
        "Time Limit",
    )
    rows = 0
    for name, method in bundle["reader"]["methods"].items():
        versions = bundle["methodologies"][method["document"]]
        newest = versions[max(versions, key=version_key)]
        steps = newest["reader_definition"].get("procedure", {}).get("steps", [])
        limited = [step for step in steps if step.get("timeout_seconds") is not None]
        table = method["text"]["notes"].get("time_limits", [])
        assert len(table) == len(limited), name
        assert all(list(row) == ["step", "time_limit"] and "{" not in row["step"] for row in table)
        rows += len(table)
    assert rows >= 90


LIMIT_PLACEHOLDER = r"\{duration:measured\.failure_policy\.timeout_seconds\.([\w-]+)\}(.{0,9})"


def test_reader_prose_leaves_every_step_time_limit_to_the_failures_table(
    bundle: dict[str, Any],
) -> None:
    """a step's time limit is drawn once, in the Time Limit table."""
    limited = set()
    for method in bundle["reader"]["methods"].values():
        versions = bundle["methodologies"][method["document"]]
        for version in versions.values():
            steps = version["reader_definition"].get("procedure", {}).get("steps", [])
            limited |= {step["id"] for step in steps if step.get("timeout_seconds") is not None}
    assert _stated_limits((ROOT / "reader" / "methods.yaml").read_text(), limited) == []


def _stated_limits(source: str, limited: set[str]) -> list[str]:
    """The steps whose time limit the text states, other than as the name of the timeouts."""
    return [
        key
        for key, after in re.findall(LIMIT_PLACEHOLDER, source)
        if key in limited and not after.startswith(" timeouts")
    ]


def test_a_paragraph_that_states_a_step_time_limit_is_found() -> None:
    """Rule methodology-step-limits-one-table: a seeded limit in reader text is an offense."""
    limit = "{duration:measured.failure_policy.timeout_seconds.create}"
    assert _stated_limits(f"Create may take {limit} before it fails.", {"create"}) == ["create"]
    assert _stated_limits(f"Create may take {limit} before it fails.", {"exec"}) == []
    assert _stated_limits(f"The {limit} timeouts are counted as failures.", {"create"}) == []


def test_a_procedure_that_only_retold_its_steps_is_gone_and_its_facts_sit_under_steps(
    bundle: dict[str, Any],
) -> None:
    """retellings are cut; one-step facts are notes under the step."""
    methods = bundle["reader"]["methods"]
    retold = ["pause-resume", "delayed-resume", "snapshot-and-restore", "fork", "r2e-gym"]
    retold += ["terminal-bench-4-0", "previews", "reconnect", "image-build-2vcpu-4gib"]
    for name in (*retold, "package-registries", "hard-links"):
        assert not methods[name]["text"]["notes"].get("procedure"), name
    noted = {
        "pause-resume": ["data_back"],
        "delayed-resume": ["wait", "destroy"],
        "snapshot-and-restore": ["write_marker", "snapshot", "destroy_source"],
        "r2e-gym": ["create", "restore", "tool_calls", "reward"],
        "terminal-bench-4-0": ["steps", "destroy"],
        "burst-tti": ["ready"],
        "previews": ["url_poll", "websocket"],
        "package-registries": ["install"],
        "hard-links": ["hard-links", "destroy"],
    }
    for name, steps in noted.items():
        items = methods[name]["text"]["notes"]["step_items"]
        for step in steps:
            holder = [item for item in items if step in item.get("steps", [])]
            assert holder and holder[0].get("note"), (name, step)


def test_package_registries_lists_its_variants_as_bullets(bundle: dict[str, Any]) -> None:
    """the three install variants are "- " bullets after a lead-in."""
    workload = bundle["reader"]["methods"]["package-registries"]["text"]["notes"]["workload"]
    lead, items = workload.split("\n\n")[:2]
    assert lead.endswith("three cold-cache installation variants:")
    assert [line[:2] for line in items.split("\n")] == ["- "] * 3


def test_a_full_hash_in_a_sentence_is_refused_and_a_linked_one_is_not() -> None:
    """Rule methodology-hash-links: only a link carries the hash."""
    full = "a" * 64
    commit = "b" * 40
    method = {
        "text": {
            "notes": {
                "bad": f"verified by {full}",
                "copied": f"by [SHA-256](hash:{full})",
                "linked": f"at [commit](https://github.com/o/r/commit/{commit})",
            }
        },
        "by_version": {},
    }
    errors = reader.reader_hash_errors({"methods": {"demo": method}})
    assert len(errors) == 1
    assert "demo.notes.bad" in errors[0]


def test_no_compiled_sentence_writes_a_full_hash(bundle: dict[str, Any]) -> None:
    assert reader.reader_hash_errors(bundle["reader"]) == []


def test_every_typed_commit_is_one_the_methodology_records() -> None:
    """R2E-Gym's reader text once typed a commit that exists nowhere; as a link it would be dead."""
    source = (ROOT / "reader" / "methods.yaml").read_text(encoding="utf-8")
    typed = set(re.findall(r"\{commit:[^{}]*\|([0-9a-f]{40})\}", source))
    assert typed
    recorded = "\n".join(
        path.read_text(encoding="utf-8") for path in (ROOT / "methodology").rglob("*.yaml")
    )
    assert {commit for commit in typed if commit not in recorded} == set()


def test_a_marked_definition_field_is_a_link_in_the_reader_view(bundle: dict[str, Any]) -> None:
    for row in bundle["methodologies"]["terminal-bench-4-0"].values():
        view = row["reader_definition"]["environment"]["image"]["reference"]
        assert "@[sha256](hash:" in view


def test_every_r2e_gym_task_links_to_its_commit_under_its_newest_name(
    bundle: dict[str, Any],
) -> None:
    versions = bundle["methodologies"]["r2e-gym"].values()
    for row in versions:
        for variant in row["reader_definition"]["procedure"]["variants"]:
            commit = variant["parameters"]["task"].partition(":")[2]
            assert variant["link"].endswith(f"/commit/{commit}")
            assert re.fullmatch(r"(numpy|coveragepy)-[a-z-]+", variant["label"])


def test_the_r2e_gym_table_is_task_repository_and_linked_commit(bundle: dict[str, Any]) -> None:
    rows = bundle["reader"]["methods"]["r2e-gym"]["text"]["notes"]["tasks"]
    assert len(rows) == 10
    assert {tuple(row) for row in rows} == {("task", "repository", "commit")}
    assert rows[0]["repository"] == "`numpy/numpy`"
    assert rows[0]["commit"].startswith("[`0a476002`](https://github.com/numpy/numpy/commit/0a4760")
    assert {row["repository"] for row in rows} == {"`numpy/numpy`", "`nedbat/coveragepy`"}


def test_package_registries_links_the_commit_it_checks_out(bundle: dict[str, Any]) -> None:
    """A rewrite (#695) dropped the link and kept the word; the word is the link to its commit."""
    workload = bundle["reader"]["methods"]["package-registries"]["text"]["notes"]["workload"]
    url = "https://github.com/fastapi/fastapi/commit/69ddc2badf9da1123bba4d360225ae6b085985f6"
    assert f"checks out the pinned [commit]({url})." in workload


def test_default_context_text_survives_higher_other_cohort_and_retrospective_versions(
    monkeypatch: pytest.MonkeyPatch,
    build_text_bundle: Callable[[Path], dict[str, Any]],
) -> None:
    methods = text._methodologies(ROOT)
    originals = copy.deepcopy(methods)
    context = default_context(ROOT)
    current = ordinary_documents(methods, common=context)
    future_common = copy.deepcopy(dict(current["sbs.common"]))
    future_common["version"] = "1.0.999"
    future_common["environment"]["region_policy"] = {"selection": "no_portable_region_request"}
    future_leaf = copy.deepcopy(dict(current["github-actions"]))
    future_leaf["version"] = "9.0.0"
    future_leaf["inherits"] = {"id": "sbs.common", "version": "1.0.999"}
    future_leaf["environment"].pop("region_policy", None)
    retrospective = copy.deepcopy(dict(current["time-index"]))
    retrospective["version"] = "9.0.0"
    retrospective["composition"]["input_selection"] = "retrospective_exact"
    baseline = build_text_bundle(ROOT)
    monkeypatch.setattr(
        text, "_methodologies", lambda _root: [*methods, future_common, future_leaf, retrospective]
    )
    expanded = build_text_bundle(ROOT)
    assert expanded["default_context"] == context
    for identity in ("github-actions", "time-index", "sbs.common"):
        assert expanded["current_documents"][identity] == baseline["current_documents"][identity]
    for identifier in ("ci-jobs", "time-index", "common"):
        assert (
            expanded["reader"]["methods"][identifier]["text"]
            == baseline["reader"]["methods"][identifier]["text"]
        )
    shown = expanded["methodologies"]["github-actions"]["9.0.0"]["reader_definition"]
    assert shown["environment"]["region_policy"] == future_common["environment"]["region_policy"]
    assert methods == originals


OVERVIEW_COMPOSITES = ("time-index", "cost-index", "time-vs-cost")


def _overview_page_strings(bundle: dict[str, Any]) -> list[str]:
    """What the Overview page draws: the host's notes and each member's paragraph and limits."""
    methods = bundle["reader"]["methods"]
    panel_only = {"setup", "trial_steps", "how_to_read", "cost_summary", "cost_how_to_read"}
    drawn: list[str] = []
    for name in OVERVIEW_COMPOSITES:
        notes = methods[name]["text"]["notes"]
        for field, value in notes.items():
            host = name == "time-index"
            if field in {"step_items"} or (
                not host and (field in panel_only or field == "introduction")
            ):
                continue
            drawn.extend(value if isinstance(value, list) else [value])
    return [text for text in drawn if isinstance(text, str)]


def test_the_overview_page_has_one_metrics_paragraph_per_composite(bundle: dict[str, Any]) -> None:
    """one page for the three composites; each has its own Metrics paragraph."""
    methods = bundle["reader"]["methods"]
    for name in OVERVIEW_COMPOSITES:
        paragraph = methods[name]["text"]["notes"]["metric"]
        assert "\n\n" not in paragraph.strip(), name
        assert "metrics" not in methods[name]["text"]["notes"], name
    versions = bundle["methodologies"]["time-index"]
    newest = versions[max(versions, key=version_key)]
    tests = [metric.get("test") for metric in newest["reader_definition"]["metrics"]]
    assert sorted(set(tests)) == sorted(OVERVIEW_COMPOSITES)


def test_no_sentence_appears_twice_on_the_overview_page(bundle: dict[str, Any]) -> None:
    """the combined page says each fact once."""
    sentences = [
        sentence.strip().lstrip("- ")
        for text in _overview_page_strings(bundle)
        for sentence in re.split(r"(?<=[.!?])\s+", text)
        if len(sentence.split()) >= 6
    ]
    assert [s for at, s in enumerate(sentences) if s in sentences[:at]] == []


@pytest.mark.parametrize("common_version", ["1.0.10", "1.0.13"])
def test_page_member_timers_and_parts_follow_each_measured_host(common_version: str) -> None:
    common = {"id": "sbs.common", "version": common_version}
    host: dict[str, Any] = {
        "id": "host",
        "version": "1.0.0",
        "effective": "2026-10-31",
        "inherits": common,
    }
    step: dict[str, Any] = {
        "id": "call",
        "name": "Measured call",
        "timer": {"start_event": f"start {common_version}", "stop_event": f"stop {common_version}"},
    }
    member: dict[str, Any] = {
        "id": "member",
        "version": "1.0.0",
        "effective": "2026-10-30",
        "inherits": common,
        "procedure": {"steps": [step]},
    }
    other = copy.deepcopy(member)
    other.update(version="9.0.0", inherits={"id": "sbs.common", "version": "1.0.999"})
    other["procedure"]["steps"] = [
        {"id": "wrong", "timer": {"start_event": "wrong start", "stop_event": "wrong stop"}}
    ]
    retro = copy.deepcopy(other)
    retro.update(
        version="9.1.0", inherits=common, composition={"input_selection": "retrospective_exact"}
    )
    later = copy.deepcopy(other)
    later.update(version="1.0.1", inherits=common, effective="2026-11-01")
    parent = {**common, "effective": "2026-01-01"}
    documents: dict[tuple[str, str], dict[str, Any]] = {
        (doc["id"], doc["version"]): doc for doc in [parent, host, member, other, retro, later]
    }
    tests = {"member": {"page": "host"}}
    document_ids = {"member": "member"}
    original = copy.deepcopy(documents)
    assert reader._timed_by_page_members(
        host, identifier="host", tests=tests, document_ids=document_ids, documents=documents
    ) == {"call"}
    assert reader._member_parts(
        host,
        identifier="host",
        source={"methods": {"member": {"headline_steps": ["call"]}}},
        tests=tests,
        document_ids=document_ids,
        documents=documents,
    ) == [
        {
            "part": "Measured call",
            "starts": f"start {common_version}",
            "ends": f"stop {common_version}",
        }
    ]
    assert documents == original


def test_a_composite_page_draws_each_composites_metrics_paragraph(bundle: dict[str, Any]) -> None:
    """the Overview page's Metrics draws one paragraph per composite, so the
    composite page template reads the metrics (each composite's part), not the retired note."""
    contract = bundle["content_criteria"]["methodology_page"]
    for sources in (
        contract["calculation_template"]["sections"]["metrics"]["sources"],
        contract["sections"]["metrics"]["sources"],
    ):
        assert "metrics" in sources
        assert "notes.metrics" not in sources
