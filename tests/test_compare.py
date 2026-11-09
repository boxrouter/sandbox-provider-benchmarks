# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Compare a third party's trials with a synthetic publication."""

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
from pytest_mock import MockerFixture

from sandbox_benchmark_verifier.catalog import load_methodology
from sandbox_benchmark_verifier.cli import main
from sandbox_benchmark_verifier.compare import (
    CompareError,
    Selection,
    compare,
    load_summary,
    load_trials,
    render,
    summary_source,
)
from sandbox_benchmark_verifier.stats import bootstrap_ci, nearest_rank
from sandbox_benchmark_verifier.versions import newest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
PUBLICATION = "20260101T000000Z"


def _current_version(identifier: str) -> str:
    return str(newest(item.document for item in load_methodology(ROOT))[identifier]["version"])


PUBLISHED = [120.0 + step for step in range(40)]
SETTINGS = (0, 10_000, 0.95)  # the common methodology's seed, resamples and confidence


def result(entry: str, values: Sequence[float], variant: str = "fio-1g") -> dict[str, Any]:
    low, high = bootstrap_ci(values, *SETTINGS) or (0.0, 0.0)
    return {
        "provider_id": entry.split("#")[0],
        "entry_id": entry,
        "variant": variant,
        "trials": {"succeeded": len(values)},
        "total": {"median": nearest_rank(values, 0.5), "ci": [low, high]},
        "success_values": sorted(values),
    }


def publication(*results: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "boxrouter.bench.page/5#summary",
        "publication_id": PUBLICATION,
        "tests": [
            {
                "id": "disk-rand-read-4k",
                "name": "Random Read",
                "unit": "IOPS",
                "methodology": {"id": "random-read", "version": _current_version("random-read")},
                "variants": [{"id": "fio-1g", "trials_per_result": 40}],
                "default_variant": "fio-1g",
                "results": list(results) or [result("alpha#one", PUBLISHED)],
            },
            {
                "id": "hardlink",
                "name": "Hard Links",
                "unit": "links/s",
                "methodology": {"id": "hard-links", "version": _current_version("hard-links")},
                "variants": [{"id": "link-30s", "trials_per_result": 40}],
                "results": [],
            },
        ],
    }


def trial(value: float | None, **fields: Any) -> dict[str, Any]:
    return {
        "test_id": "disk-rand-read-4k",
        "variant": "fio-1g",
        "measurement": "random_read_iops",
        "value": value,
        "unit": "IOPS",
        "success": value is not None,
        "exclusion_reason": None,
    } | fields


def theirs(values: Sequence[float]) -> list[dict[str, Any]]:
    return [trial(value) for value in values]


SELECT = Selection("alpha", "disk-rand-read-4k")


def test_the_published_trials_agree_with_themselves() -> None:
    comparison = compare(theirs(PUBLISHED), publication(), SELECT)
    assert comparison.agree
    assert comparison.theirs == comparison.ours
    assert comparison.difference_percent == 0
    assert comparison.ours_reproduced


def test_a_different_machine_disagrees() -> None:
    comparison = compare(theirs([value * 0.5 for value in PUBLISHED]), publication(), SELECT)
    assert not comparison.agree
    assert comparison.theirs.median == nearest_rank([value * 0.5 for value in PUBLISHED], 0.5)
    assert comparison.difference_percent == pytest.approx(-50, abs=0.1)


def test_a_few_trials_with_a_wide_interval_overlap_the_published_one() -> None:
    assert compare(theirs([100.0, 140.0, 160.0, 180.0]), publication(), SELECT).agree


def test_the_interval_is_the_methodologys_seeded_bootstrap_of_your_median() -> None:
    values = [90.0, 100.0, 110.0, 120.0, 130.0]
    comparison = compare(theirs(values), publication(), SELECT)
    low, high = bootstrap_ci(values, *SETTINGS) or (0.0, 0.0)
    assert (comparison.theirs.median, comparison.theirs.low, comparison.theirs.high) == (
        110.0,
        low,
        high,
    )


def test_failed_excluded_and_other_records_are_left_out_and_counted() -> None:
    records = [
        *theirs(PUBLISHED),
        trial(None),
        trial(1.0, exclusion_reason="benchmark_error"),
        trial(1.0, test_id="hardlink"),
        trial(1.0, variant="other"),
        trial(1.0, measurement="random_write_iops"),
    ]
    comparison = compare(records, publication(), Selection("alpha", SELECT.test, "fio-1g"))
    assert (comparison.theirs.trials, comparison.other_records, comparison.uncounted_records) == (
        40,
        3,
        2,
    )
    assert comparison.agree


def test_the_variant_comes_from_your_records_when_not_given() -> None:
    published = publication(result("alpha#one", PUBLISHED, "other"))
    records = [trial(value, variant="other") for value in PUBLISHED]
    assert compare(records, published, SELECT).subject.variant == "other"
    assert compare(theirs(PUBLISHED), publication(), SELECT).subject.variant == "fio-1g"


def test_the_provider_may_be_named_by_entry_or_provider_id() -> None:
    published = publication(result("alpha#one", PUBLISHED), result("beta#two", PUBLISHED))
    for name in ("alpha", "alpha#one"):
        chosen = compare(theirs(PUBLISHED), published, Selection(name, SELECT.test))
        assert chosen.subject.entry == "alpha#one"


@pytest.mark.parametrize(
    ("selection", "message"),
    [
        (Selection("gamma", "disk-rand-read-4k"), "0 published results for provider 'gamma'"),
        (Selection("alpha", "nothing"), "no test 'nothing' in the publication; it has: disk-rand"),
        (Selection("alpha", "hardlink"), "0 published results for provider 'alpha'"),
    ],
)
def test_unknown_provider_and_test_list_what_the_data_has(
    selection: Selection, message: str
) -> None:
    with pytest.raises(CompareError, match=message):
        compare(theirs(PUBLISHED), publication(), selection)


def test_a_provider_with_two_entries_must_be_named_by_entry() -> None:
    published = publication(result("alpha#one", PUBLISHED), result("alpha#two", PUBLISHED))
    with pytest.raises(CompareError, match="2 published results for provider 'alpha'"):
        compare(theirs(PUBLISHED), published, SELECT)


def test_a_wrong_unit_is_refused_not_converted() -> None:
    with pytest.raises(CompareError, match="your unit is 'MiB/s'; disk-rand-read-4k is in IOPS"):
        compare([trial(1.0, unit="MiB/s")], publication(), SELECT)


def test_one_usable_record_cannot_form_an_interval() -> None:
    with pytest.raises(CompareError, match="1 of your records are usable"):
        compare([trial(1.0), trial(None)], publication(), SELECT)


def test_records_that_mix_variants_need_a_variant_choice() -> None:
    records = [trial(1.0), trial(2.0, variant="other")]
    with pytest.raises(CompareError, match="mix several variants"):
        compare(records, publication(), SELECT)
    assert compare(records * 3, publication(), Selection("alpha", SELECT.test, "fio-1g"))


def test_a_result_that_pools_calls_per_trial_is_refused() -> None:
    published = publication(result("alpha#one", PUBLISHED))
    published["tests"][0]["results"][0]["pooled_calls_per_trial"] = 100
    with pytest.raises(CompareError, match="pools 100 calls per trial"):
        compare(theirs(PUBLISHED), published, SELECT)


def test_a_stated_interval_that_does_not_follow_from_its_values_is_noted() -> None:
    published = publication(result("alpha#one", PUBLISHED))
    published["tests"][0]["results"][0]["total"]["ci"] = [100.0, 200.0]
    comparison = compare(theirs(PUBLISHED), published, SELECT)
    assert not comparison.ours_reproduced
    assert "do not follow from its listed trial values" in render(comparison)


def test_the_report_shows_both_medians_and_intervals() -> None:
    comparison = compare(theirs([value * 0.5 for value in PUBLISHED]), publication(), SELECT)
    lines = render(comparison).splitlines()
    assert lines[0] == "disk-rand-read-4k (Random Read), variant fio-1g, random_read_iops in IOPS"
    assert lines[1] == f"provider alpha#one, publication {PUBLICATION}"
    assert lines[3].split() == ["trials", "median", "95%", "interval"]
    assert lines[4].split()[:3] == ["yours", "40", f"{comparison.theirs.median:,.3f}"]
    assert lines[5].split()[:3] == ["published", "40", f"{comparison.ours.median:,.3f}"]
    assert lines[-1] == "agree: no, the intervals do not overlap"


def test_trials_load_from_json_lines_or_an_array(tmp_path: Path) -> None:
    records = theirs([1.0, 2.0])
    lines = tmp_path / "lines.jsonl"
    lines.write_text("\n".join(json.dumps(record) for record in records) + "\n\n")
    array = tmp_path / "array.json"
    array.write_text(json.dumps(records))
    assert load_trials(lines) == load_trials(array) == records


@pytest.mark.parametrize("text", ["", "not json", "[1, 2]", '{"a": 1}\nnot json'])
def test_unreadable_trials_are_refused(tmp_path: Path, text: str) -> None:
    path = tmp_path / "trials.jsonl"
    path.write_text(text)
    with pytest.raises(CompareError, match="cannot read|must hold measurement records"):
        load_trials(path)


def test_the_summary_must_be_the_named_publication(tmp_path: Path) -> None:
    path = tmp_path / "summary.json"
    path.write_text(json.dumps(publication()))
    assert load_summary(str(path), PUBLICATION)["publication_id"] == PUBLICATION
    with pytest.raises(CompareError, match=f"is publication {PUBLICATION}, not 2026"):
        load_summary(str(path), "2026")
    path.write_text(json.dumps({"publication_id": PUBLICATION}))
    with pytest.raises(CompareError, match="is not a publication summary"):
        load_summary(str(path), PUBLICATION)
    with pytest.raises(CompareError, match="cannot read the publication summary"):
        load_summary(str(tmp_path / "missing.json"), PUBLICATION)


def test_the_default_summary_is_the_publications_public_address() -> None:
    assert summary_source(PUBLICATION, None) == (
        f"https://boxrouter.ai/benchmarks/data/publications/{PUBLICATION}/summary.json"
    )
    assert summary_source(PUBLICATION, "summary.json") == "summary.json"


def run(
    mocker: MockerFixture,
    capsys: pytest.CaptureFixture[str],
    arguments: Mapping[str, str],
    results: Path,
) -> tuple[int, str, str]:
    options = [part for name, value in arguments.items() for part in (f"--{name}", value)]
    mocker.patch("sys.argv", ["sandbox-benchmark-verify", "compare", str(results), *options])
    code = main()
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_the_command_exits_0_when_they_agree_1_when_they_differ_and_2_when_it_cannot_compare(
    tmp_path: Path, mocker: MockerFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    summary = tmp_path / "summary.json"
    summary.write_text(json.dumps(publication()))
    arguments = {
        "publication": PUBLICATION,
        "provider": "alpha",
        "test": "disk-rand-read-4k",
        "summary": str(summary),
    }
    results = tmp_path / "results.jsonl"

    results.write_text("\n".join(json.dumps(record) for record in theirs(PUBLISHED)))
    code, out, _ = run(mocker, capsys, arguments, results)
    assert (code, out.splitlines()[-1]) == (0, "agree: yes, the intervals overlap")

    results.write_text("\n".join(json.dumps(record) for record in theirs([1.0, 2.0, 3.0])))
    code, out, _ = run(mocker, capsys, arguments, results)
    assert (code, out.splitlines()[-1]) == (1, "agree: no, the intervals do not overlap")

    code, out, err = run(mocker, capsys, arguments | {"provider": "gamma"}, results)
    assert (code, out) == (2, "")
    assert err.startswith("compare: 0 published results for provider 'gamma'")


def test_the_command_requires_its_options(
    tmp_path: Path, mocker: MockerFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    mocker.patch("sys.argv", ["sandbox-benchmark-verify", "compare", str(tmp_path / "r.jsonl")])
    with pytest.raises(SystemExit) as exit_info:
        main()
    assert exit_info.value.code == 2
    assert "compare requires <results>, --publication, --provider and --test" in (
        capsys.readouterr().err
    )
