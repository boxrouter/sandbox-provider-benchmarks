# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Placeholder grammar, resolution, and duplicate-literal scan tests."""

from collections.abc import Mapping
from typing import Any

import pytest

from sandbox_benchmark_verifier.conventions import load_conventions
from sandbox_benchmark_verifier.placeholders import (
    PlaceholderError,
    fio_size_bytes,
    parse_placeholders,
    percent_text,
    resolve_template,
    seconds_text,
    size_text,
)

pytestmark = pytest.mark.unit

NAMES = {"workload.ci.github_actions": "GitHub Actions", "zustand": "zustand"}
DOCUMENTS: dict[str, Mapping[str, Any]] = {
    "workload.ci": {"environment": {"size": {"vcpu": 1}}},
    "workload.ci.github_actions": {
        "environment": {"size": {"vcpu": 4, "memory_gib": 8}},
        "procedure": {
            "trials": 10,
            "sandboxes_per_trial": 1,
            "variants": [{"id": "zustand", "label": "zustand (Node)"}],
        },
        "metrics": [{"name": "job_time", "unit": "ms", "direction": "lower_is_better"}],
    },
}


def test_parser_accepts_all_placeholder_forms_and_preserves_offsets() -> None:
    template = "{name:zustand}: {param:workload.ci.github_actions.procedure.trials} trials"
    parsed = parse_placeholders(template)

    assert [(item.kind, item.target) for item in parsed] == [
        ("name", "zustand"),
        ("param", "workload.ci.github_actions.procedure.trials"),
    ]
    assert template[parsed[0].start : parsed[0].end] == "{name:zustand}"


@pytest.mark.parametrize(
    "template",
    ["{name:}", "{price:cpu}", "{name:with space}", "{param:doc}", "left {unit:job_time"],
)
def test_parser_rejects_malformed_or_unknown_placeholders(template: str) -> None:
    with pytest.raises(PlaceholderError):
        parse_placeholders(template)


def test_resolver_uses_canonical_name_parameters_metric_unit_and_direction() -> None:
    template = (
        "{name:zustand}: {param:workload.ci.github_actions.procedure.trials} trials, "
        "{param:workload.ci.github_actions.environment.size.vcpu} vCPU / "
        "{param:workload.ci.github_actions.environment.size.memory_gib} GiB; "
        "{unit:job_time}, {direction:job_time}."
    )

    assert resolve_template(template, names=NAMES, documents=DOCUMENTS) == (
        "zustand: 10 trials, 4 vCPU / 8 GiB; ms, lower_is_better."
    )


@pytest.mark.parametrize(
    ("template", "message"),
    [
        ("{name:missing}", "unknown canonical name 'missing'"),
        ("{param:workload.ci.github_actions.unknown}", "unknown parameter path"),
        ("{unit:missing}", "unknown unit for metric"),
    ],
)
def test_resolver_rejects_dangling_placeholders(template: str, message: str) -> None:
    with pytest.raises(PlaceholderError, match=message):
        resolve_template(template, names=NAMES, documents=DOCUMENTS)


def test_resolver_rejects_ambiguous_metric_definitions() -> None:
    documents = {
        **DOCUMENTS,
        "another.document": {
            "metrics": [{"name": "job_time", "unit": "seconds", "direction": "lower_is_better"}]
        },
    }

    with pytest.raises(PlaceholderError, match="ambiguous unit"):
        resolve_template("{unit:job_time}", names=NAMES, documents=documents)


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"a": "{param:demo.a}"}, "cyclic placeholder reference: param:demo.a -> param:demo.a"),
        (
            {"a": "{param:demo.b}", "b": "{param:demo.a}"},
            "cyclic placeholder reference: param:demo.a -> param:demo.b -> param:demo.a",
        ),
        ({"a": "{param:absent.value}"}, "unknown document in parameter placeholder 'absent.value'"),
        ({"a": "{param:demo.absent}"}, "unknown parameter path 'demo.absent'"),
        ({"a": "{name:absent}"}, "unknown canonical name 'absent'"),
        ({"a": ["value"]}, "parameter 'demo.a' is not a scalar text value"),
    ],
)
def test_recursive_resolver_rejects_invalid_references(
    values: dict[str, Any], message: str
) -> None:
    with pytest.raises(PlaceholderError) as error:
        resolve_template("{param:demo.a}", names={}, documents={"demo": values})
    assert str(error.value) == message


def test_recursive_resolver_reuses_nested_array_parameters_and_names() -> None:
    documents = {
        "demo.1.0.0": {
            "values": [{"text": "{name:demo}: {param:demo.1.0.0.count}"}],
            "count": 4,
        }
    }
    template = "{param:demo.1.0.0.values.0.text} / {param:demo.1.0.0.values.0.text}"
    assert resolve_template(template, names={"demo": "Demo"}, documents=documents) == (
        "Demo: 4 / Demo: 4"
    )


def test_a_parameter_reads_the_longest_document_id_it_continues() -> None:
    documents: dict[str, Mapping[str, Any]] = {
        "demo": {"1": {"count": 1}},
        "demo.1": {"count": 2},
        "demo.1.count": {"x": 3},
    }
    assert resolve_template("{param:demo.1.count}", names={}, documents=documents) == "2"
    assert resolve_template("{param:demo.1.count.x}", names={}, documents=documents) == "3"
    with pytest.raises(PlaceholderError, match="unknown document"):
        resolve_template("{param:other.count}", names={}, documents=documents)


@pytest.mark.parametrize("template", ["echo {a,b}", "{command:arg}", "printf '{\"ok\":true}'"])
def test_resolver_preserves_literal_braces(template: str) -> None:
    assert resolve_template(template, names={}, documents={}) == template


@pytest.mark.parametrize(
    "template", ["{name:}", "{param:demo}", "{name:missing", "{name:bad space}"]
)
def test_resolver_rejects_malformed_recognized_references(template: str) -> None:
    with pytest.raises(PlaceholderError):
        resolve_template(template, names={}, documents={})


def test_recursive_resolver_detects_name_cycles() -> None:
    with pytest.raises(PlaceholderError, match="cyclic placeholder reference"):
        resolve_template("{name:demo}", names={"demo": "{name:demo}"}, documents={})


def test_units_lists_the_members_per_unit_names_in_member_order() -> None:
    names = {"unit.a": "trace", "unit.b": "session", "unit.c": "rollout"}
    documents = {"idx": {"composition": {"inputs": ["a", "b"]}}}
    assert (
        resolve_template("typical {units:idx}", names=names, documents=documents)
        == "typical trace or session"
    )
    # Adding a member changes the list, in the composite's member order.
    documents = {"idx": {"composition": {"inputs": ["a", "b", "c"]}}}
    assert (
        resolve_template("{units:idx}", names=names, documents=documents)
        == "trace, session or rollout"
    )
    documents = {"idx": {"composition": {"inputs": ["c", "a"]}}}
    assert resolve_template("{units:idx}", names=names, documents=documents) == "rollout or trace"
    documents = {"idx": {"composition": {"inputs": ["a"]}}}
    assert resolve_template("{units:idx}", names=names, documents=documents) == "trace"


def test_units_need_a_unit_for_every_member() -> None:
    documents = {"idx": {"composition": {"inputs": ["a", "b"]}}}
    with pytest.raises(PlaceholderError, match="no unit"):
        resolve_template("{units:idx}", names={"unit.a": "trace"}, documents=documents)


def test_quantity_reads_the_metric_it_names() -> None:
    documents = {"idx": {"metrics": [{"name": "typical_time", "quantity": "time"}]}}
    assert (
        resolve_template("a {quantity:typical_time} statistic", names={}, documents=documents)
        == "a time statistic"
    )


CALLS: dict[str, Mapping[str, Any]] = {
    "demo.1.0.0": {
        "procedure": {
            "trials": 100,
            "variants": [{"id": "a"}, {"id": "b"}, {"id": "c"}],
            "parameters": {
                "calls": 100,
                "wait_seconds": 900,
                "payload_bytes": 4096,
                "jitter": 0.2,
                "unit": "ms",
            },
        }
    }
}


def _resolve(template: str) -> str:
    return resolve_template(template, names={}, documents=CALLS)


def test_count_renders_the_length_of_a_list_parameter() -> None:
    assert _resolve("{count:demo.1.0.0.procedure.variants} variants") == "3 variants"
    with pytest.raises(PlaceholderError, match="is not a list"):
        _resolve("{count:demo.1.0.0.procedure.trials}")


@pytest.mark.parametrize(
    ("seconds", "text"),
    [(0, "0 s"), (45, "45 s"), (60, "60 s"), (90, "90 s"), (900, "15 min"), (3600, "1 h")],
)
def test_duration_follows_the_one_unit_rule(seconds: int, text: str) -> None:
    assert seconds_text(seconds) == text


def test_duration_reads_a_seconds_parameter() -> None:
    assert _resolve("{duration:demo.1.0.0.procedure.parameters.wait_seconds}") == "15 min"
    with pytest.raises(PlaceholderError, match="is not a number"):
        _resolve("{duration:demo.1.0.0.procedure.variants}")


def test_product_multiplies_whole_number_parameters_with_thousands_separators() -> None:
    template = "{product:demo.1.0.0.procedure.trials*demo.1.0.0.procedure.parameters.calls} calls"
    assert _resolve(template) == "10,000 calls"
    with pytest.raises(PlaceholderError):
        parse_placeholders("{product:demo.1.0.0.procedure.trials}")


@pytest.mark.parametrize(
    ("size", "text"),
    [
        (1, "1 byte"),
        (1000, "1,000 bytes"),
        (1024, "1 KiB"),
        (4096, "4 KiB"),
        (1536, "1,536 bytes"),
        (1048576, "1 MiB"),
        (1073741824, "1 GiB"),
        (4294967296, "4 GiB"),
    ],
)
def test_size_follows_the_one_binary_unit_rule(size: int, text: str) -> None:
    assert size_text(size) == text


@pytest.mark.parametrize(
    ("fraction", "text"),
    [(0.2, "20%"), (0.075, "7.5%"), (1, "100%"), (0, "0%"), (0.1 + 0.2, "30%")],
)
def test_percent_reads_a_fraction_as_a_percentage(fraction: float, text: str) -> None:
    assert percent_text(fraction) == text


def test_percent_reads_a_fraction_parameter() -> None:
    assert _resolve("±{percent:demo.1.0.0.procedure.parameters.jitter} jitter") == "±20% jitter"
    with pytest.raises(PlaceholderError, match="is not a number"):
        _resolve("{percent:demo.1.0.0.procedure.parameters.unit}")


def test_size_reads_a_byte_count_parameter() -> None:
    assert _resolve("{size:demo.1.0.0.procedure.parameters.payload_bytes}") == "4 KiB"
    with pytest.raises(PlaceholderError, match="is not a number"):
        _resolve("{size:demo.1.0.0.procedure.parameters.unit}")


@pytest.mark.parametrize(
    ("value", "size"),
    [
        ("4k", 4096),
        ("1M", 1048576),
        (" 2g ", 2147483648),
        ("4", None),
        ("4 KiB", None),
        (4096, None),
    ],
)
def test_fio_writes_a_size_as_a_string(value: object, size: int | None) -> None:
    assert fio_size_bytes(value) == size


def test_axis_placeholder_names_the_axis_that_plots_the_index() -> None:
    documents = {
        "doc": {"composition": {"plots": {"x": {"id": "index.cost"}, "y": {"id": "index.speed"}}}}
    }
    names = {"axis.x": "Horizontal Axis", "axis.y": "Vertical Axis"}
    assert resolve_template("{axis:index.cost}", names=names, documents=documents) == (
        "horizontal axis"
    )
    assert resolve_template("{axis:index.speed}", names=names, documents=documents) == (
        "vertical axis"
    )
    with pytest.raises(PlaceholderError, match="no single chart axis"):
        resolve_template("{axis:index.other}", names=names, documents=documents)


HASH_64 = "a" * 64
COMMIT = "b" * 40
PAGE = {
    "methodology_page": {
        "hash_link": {
            "commit_urls": {
                "github": "https://github.com/{repository}/commit/{commit}",
                "huggingface": "https://huggingface.co/datasets/{repository}/commit/{commit}",
            }
        }
    }
}


def _resolved_hash(template: str, **parameters: str) -> str:
    return resolve_template(template, names={}, documents={"doc": parameters, "content": PAGE})


def test_a_sha256_parameter_is_a_link_on_its_word() -> None:
    assert (
        _resolved_hash("by {hash:SHA-256|doc.sha}", sha=HASH_64) == f"by [SHA-256](hash:{HASH_64})"
    )


def test_a_text_that_writes_a_sha256_keeps_its_words_and_its_word_becomes_the_link() -> None:
    assert (
        _resolved_hash("({hash:sha256|doc.image})", image=f"ghcr.io/x@sha256:{HASH_64}")
        == f"(ghcr.io/x@[sha256](hash:{HASH_64}))"
    )


def test_a_git_commit_is_a_direct_link_to_its_page() -> None:
    url = f"https://github.com/o/r/commit/{COMMIT}"
    assert _resolved_hash(f"at {{commit:commit|o/r|{COMMIT}}}") == f"at [commit]({url})"
    assert (
        _resolved_hash(
            "{commit:commit|o/r|doc.command}", command=f"checkout of commit {COMMIT}, ok"
        )
        == f"checkout of [commit]({url}), ok"
    )


def test_a_repository_on_another_host_names_it() -> None:
    assert _resolved_hash(f"{{commit:revision|huggingface:o/r|{COMMIT}}}") == (
        f"[revision](https://huggingface.co/datasets/o/r/commit/{COMMIT})"
    )


def test_a_commit_without_a_word_is_linked_on_its_short_form() -> None:
    url = f"https://github.com/o/r/commit/{COMMIT}"
    assert _resolved_hash("{commit:doc.repo|doc.sha}", repo="o/r", sha=COMMIT) == (
        f"[`{COMMIT[:8]}`]({url})"
    )
    assert _resolved_hash("{commit:o/r|doc.task}", task=f"x/y_final:{COMMIT}") == (
        f"[`{COMMIT[:8]}`]({url})"
    )


def test_a_value_without_its_hash_is_refused() -> None:
    with pytest.raises(PlaceholderError, match="does not write one hash"):
        _resolved_hash("{commit:commit|o/r|doc.command}", command="the pinned commit")
    with pytest.raises(PlaceholderError, match="does not write one hash"):
        _resolved_hash("{hash:sha256|doc.image}", image=f"commit {COMMIT}")
    with pytest.raises(PlaceholderError, match="does not hold one Git commit"):
        _resolved_hash("{commit:o/r|doc.steps}", steps="nine")
    with pytest.raises(PlaceholderError, match="is not a repository"):
        _resolved_hash(f"{{commit:doc.repo|{COMMIT}}}", repo="none")


def test_a_malformed_hash_or_commit_placeholder_is_refused() -> None:
    with pytest.raises(PlaceholderError, match="unknown or malformed"):
        parse_placeholders("{hash:SHA-256}")
    with pytest.raises(PlaceholderError, match="unknown or malformed"):
        parse_placeholders("{commit:doc.commit}")


def test_an_image_size_placeholder_prints_decimal_units_to_three_digits() -> None:

    documents = {"demo": {"data_science": 7237809664, "standard": 387_666_432}}
    for target, text in (("data_science", "7.24 GB"), ("standard", "388 MB")):
        template = "{imagesize:demo." + target + "}"
        assert resolve_template(template, names={}, documents=documents) == text


@pytest.mark.parametrize("vector", load_conventions()["presentation"]["size_text"]["vectors"])
def test_sizes_follow_the_published_size_text_rule(vector: dict[str, Any]) -> None:
    """Web runs these vectors too : a defined binary size prints one way."""
    assert size_text(vector["bytes"]) == vector["text"]


def test_a_count_word_placeholder_writes_the_count_as_a_word() -> None:

    documents = {
        "demo": {"four": [1, 2, 3, 4], "three": ["a", "b", "c"], "one": [1], "many": [0] * 13}
    }
    for target, text in (("four", "four"), ("three", "three")):
        template = "{countword:demo." + target + "}"
        assert resolve_template(template, names={}, documents=documents) == text
    for target in ("one", "many"):
        with pytest.raises(PlaceholderError, match="write"):
            resolve_template("{countword:demo." + target + "}", names={}, documents=documents)


def test_a_version_placeholder_reads_the_one_version_a_text_holds() -> None:
    documents = {"demo": {"ids": ["previews-app.tar.xz", "vite 8.3.4"], "two": "a 1.2 and 3.4"}}
    assert resolve_template("Vite {version:demo.ids.1}", names={}, documents=documents) == (
        "Vite 8.3.4"
    )
    for target in ("ids.0", "two"):
        with pytest.raises(PlaceholderError, match="does not hold one version"):
            resolve_template("{version:demo." + target + "}", names={}, documents=documents)
