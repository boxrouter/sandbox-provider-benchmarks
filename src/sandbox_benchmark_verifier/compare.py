# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Compare a third party's per-trial results with one published result."""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.request import urlopen

from sandbox_benchmark_verifier.catalog import MethodologyDocument, load_methodology
from sandbox_benchmark_verifier.stats import bootstrap_ci, nearest_rank, numeric

SUMMARY_URL = "https://boxrouter.ai/benchmarks/data/publications/{publication}/summary.json"
SUMMARY_SCHEMA_PREFIX = "boxrouter.bench.page/"
FETCH_TIMEOUT_SECONDS = 60


class CompareError(Exception):
    """The inputs cannot be compared; the message says what to change."""


@dataclass(frozen=True)
class Side:
    """One median with its interval, over `trials` successful trials."""

    trials: int
    median: float
    low: float
    high: float


@dataclass(frozen=True)
class Subject:
    """What was compared: one published result of one test."""

    publication: str
    test: str
    test_name: str
    variant: str
    entry: str
    measurement: str
    unit: str


@dataclass(frozen=True)
class Comparison:
    """Their result against the published one."""

    subject: Subject
    confidence: float
    theirs: Side
    ours: Side
    ours_reproduced: bool
    other_records: int
    uncounted_records: int

    @property
    def agree(self) -> bool:
        """The two stated intervals overlap."""
        return self.theirs.low <= self.ours.high and self.ours.low <= self.theirs.high

    @property
    def difference_percent(self) -> float:
        """Their median relative to the published one."""
        return (self.theirs.median - self.ours.median) / self.ours.median * 100


def load_summary(source: str, publication: str) -> Mapping[str, Any]:
    """Read the publication summary from a file, an https URL or the default public address."""
    try:
        if source.startswith("https://"):
            with urlopen(source, timeout=FETCH_TIMEOUT_SECONDS) as response:
                loaded = json.load(response)
        else:
            loaded = json.loads(Path(source).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CompareError(f"cannot read the publication summary {source}: {exc}") from exc
    schema = loaded.get("schema") if isinstance(loaded, Mapping) else None
    if not isinstance(schema, str) or not schema.startswith(SUMMARY_SCHEMA_PREFIX):
        raise CompareError(f"{source} is not a publication summary")
    if loaded.get("publication_id") != publication:
        raise CompareError(
            f"{source} is publication {loaded.get('publication_id')}, not {publication}"
        )
    return loaded  # type: ignore[no-any-return]


def summary_source(publication: str, summary: str | None) -> str:
    """Where to read a publication's summary: the given file or URL, else the public address."""
    return summary or SUMMARY_URL.format(publication=publication)


def load_trials(path: Path) -> list[Mapping[str, Any]]:
    """Read measurement records from JSON Lines or one JSON array."""
    try:
        text = path.read_text(encoding="utf-8").strip()
        lines = [json.loads(x) for x in text.splitlines() if x.strip()]
        loaded: list[Any] = json.loads(text) if text.startswith("[") else lines
    except (OSError, ValueError) as exc:
        raise CompareError(f"cannot read {path}: {exc}") from exc
    if not loaded or not all(isinstance(record, Mapping) for record in loaded):
        raise CompareError(f"{path} must hold measurement records, one JSON object per trial")
    return loaded


def _choose(kind: str, wanted: str, found: Sequence[str]) -> str:
    if wanted not in found:
        raise CompareError(f"no {kind} {wanted!r} in the publication; it has: {', '.join(found)}")
    return wanted


def _find_methodology(documents: Sequence[MethodologyDocument], citation: Any) -> Mapping[str, Any]:
    if isinstance(citation, Mapping):
        for item in documents:
            if (item.document.get("id"), str(item.document.get("version"))) == (
                citation.get("id"),
                str(citation.get("version")),
            ):
                return item.document
    raise CompareError(f"methodology {citation} is not in this installed verifier; update it")


def interval_settings(
    document: Mapping[str, Any], documents: Sequence[MethodologyDocument]
) -> tuple[int, int, float]:
    """Seed, resamples and confidence of the methodology's own median interval."""
    inherited = document.get("inherits")
    for candidate in (document, _find_methodology(documents, inherited) if inherited else None):
        statistics = candidate.get("statistics") if candidate else None
        interval = statistics.get("interval") if isinstance(statistics, Mapping) else None
        if isinstance(interval, Mapping) and interval.get("type") == "bootstrap_percentile":
            confidence = numeric(interval.get("confidence"))
            if (
                isinstance(interval.get("seed"), int)
                and isinstance(interval.get("resamples"), int)
                and confidence is not None
            ):
                return interval["seed"], interval["resamples"], confidence
    raise CompareError("the methodology states no bootstrap interval for its median")


def _single(values: set[str], kind: str) -> str | None:
    if len(values) > 1:
        raise CompareError(f"your results mix several {kind}s ({', '.join(sorted(values))})")
    return next(iter(values), None)


@dataclass(frozen=True)
class Selection:
    """Which published result to compare with; `variant` is read from the records when unset."""

    provider: str
    test: str
    variant: str | None = None


@dataclass(frozen=True)
class Counted:
    """Your records reduced to the values the headline statistic is taken over."""

    values: list[float]
    other: int
    uncounted: int


def _published_result(
    published: Mapping[str, Any], selection: Selection, variant: str
) -> Mapping[str, Any]:
    results = [
        item
        for item in published["results"]
        if item["variant"] == variant
        and selection.provider in (item["entry_id"], item["provider_id"])
    ]
    if len(results) != 1:
        found = sorted({f"{r['entry_id']} ({r['variant']})" for r in published["results"]})
        raise CompareError(
            f"{len(results)} published results for provider {selection.provider!r}, "
            f"variant {variant!r}; the publication has: {', '.join(found)}"
        )
    chosen: Mapping[str, Any] = results[0]
    return chosen


def _count(
    trials: Sequence[Mapping[str, Any]],
    selection: Selection,
    variant: str,
    metric: Mapping[str, Any],
) -> Counted:
    values: list[float] = []
    other = uncounted = 0
    for trial in trials:
        if (
            trial.get("test_id", selection.test) != selection.test
            or trial.get("variant", variant) != variant
            or trial.get("measurement") != metric["name"]
        ):
            other += 1
            continue
        if trial.get("unit") != metric["unit"]:
            raise CompareError(
                f"your unit is {trial.get('unit')!r}; {selection.test} is in {metric['unit']}"
            )
        value = numeric(trial.get("value"))
        if value is None or trial.get("success") is not True or trial.get("exclusion_reason"):
            uncounted += 1
        else:
            values.append(value)
    return Counted(values, other, uncounted)


def _rounded(interval: tuple[float, float] | None) -> list[float]:
    return [round(x, 3) for x in interval or ()]


def _reproduced(
    result: Mapping[str, Any], stated: Mapping[str, Any], settings: tuple[int, int, float]
) -> bool:
    """Whether the stated median and interval follow from the published trial values."""
    values = result.get("success_values") or []
    return (
        len(values) >= 2
        and _rounded(bootstrap_ci(values, *settings)) == _rounded(stated["ci"])
        and round(nearest_rank(values, 0.5), 3) == round(stated["median"], 3)
    )


def compare(
    trials: Sequence[Mapping[str, Any]],
    summary: Mapping[str, Any],
    selection: Selection,
    documents: Sequence[MethodologyDocument] | None = None,
) -> Comparison:
    """Compare per-trial records with a published result, using the methodology's statistics."""
    documents = load_methodology() if documents is None else documents
    tests = {item["id"]: item for item in summary.get("tests", [])}
    published = tests[_choose("test", selection.test, sorted(tests))]
    methodology = _find_methodology(documents, published.get("methodology"))
    metric = methodology["metrics"][0]
    if metric["unit"] != published["unit"]:
        raise CompareError(
            f"{selection.test} is published in {published['unit']}, not {metric['unit']}"
        )
    variant = str(
        selection.variant
        or _single(
            {
                str(t["variant"])
                for t in trials
                if "variant" in t
                and t.get("test_id", selection.test) == selection.test
                and t.get("measurement") == metric["name"]
            },
            "variant",
        )
        or published.get("default_variant")
        or published["variants"][0]["id"]
    )
    ours = _published_result(published, selection, variant)
    if ours.get("pooled_calls_per_trial"):
        raise CompareError(
            f"{selection.test} pools {ours['pooled_calls_per_trial']} calls per trial and "
            "resamples whole trials for its interval; compare takes one value per trial"
        )
    stated = ours["total"]
    if not stated or not stated.get("ci"):
        raise CompareError("the publication states no median and interval for this result")
    counted = _count(trials, selection, variant, metric)
    settings = interval_settings(methodology, documents)
    interval = bootstrap_ci(counted.values, *settings)
    if interval is None:
        raise CompareError(
            f"{len(counted.values)} of your records are usable for {selection.test} "
            f"({metric['name']}, {variant}); an interval needs at least 2, and the methodology "
            f"asks for {published['variants'][0].get('trials_per_result', 'more')}"
        )
    return Comparison(
        subject=Subject(
            publication=str(summary["publication_id"]),
            test=selection.test,
            test_name=str(published["name"]),
            variant=variant,
            entry=str(ours["entry_id"]),
            measurement=str(metric["name"]),
            unit=str(metric["unit"]),
        ),
        confidence=settings[2],
        theirs=Side(len(counted.values), nearest_rank(counted.values, 0.5), *interval),
        ours=Side(int(ours["trials"]["succeeded"]), stated["median"], *stated["ci"]),
        ours_reproduced=_reproduced(ours, stated, settings),
        other_records=counted.other,
        uncounted_records=counted.uncounted,
    )


def render(result: Comparison) -> str:
    """The comparison as text."""
    level = f"{result.confidence:.0%} interval"
    subject = result.subject
    lines = [
        f"{subject.test} ({subject.test_name}), variant {subject.variant}, {subject.measurement} "
        f"in {subject.unit}",
        f"provider {subject.entry}, publication {subject.publication}",
        "",
        f"{'':10}{'trials':>7}{'median':>14}  {level}",
    ]
    for label, side in (("yours", result.theirs), ("published", result.ours)):
        lines.append(
            f"{label:10}{side.trials:>7}{side.median:>14,.3f}  {side.low:,.3f} to {side.high:,.3f}"
        )
    lines.append("")
    lines.append(f"your median is {result.difference_percent:+.1f}% against the published one")
    lines.append(
        "agree: yes, the intervals overlap"
        if result.agree
        else "agree: no, the intervals do not overlap"
    )
    if not result.ours_reproduced:
        lines.append(
            "note: the published median and interval do not follow from its listed trial values "
            "by the methodology's statistics; they are compared as stated"
        )
    if result.other_records or result.uncounted_records:
        lines.append(
            f"left out of yours: {result.other_records} records for other tests, variants or "
            f"measurements; {result.uncounted_records} failed or excluded"
        )
    return "\n".join(lines)
