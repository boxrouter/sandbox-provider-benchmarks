# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Small reader defects found in the methodology review (2026-11-03), each kept from returning."""

from pathlib import Path
from typing import Any

import pytest

from sandbox_benchmark_verifier.name_refs import reader_strings
from sandbox_benchmark_verifier.names import load_tables
from sandbox_benchmark_verifier.versions import version_key

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[1]


def _sentences(bundle: dict[str, Any], method: str) -> list[str]:
    entry = bundle["reader"]["methods"][method]
    texts = [entry["text"], *entry["by_version"].values()]
    return [text for tree in texts for _, text in reader_strings(tree)]


def test_the_outside_product_keeps_its_name_in_the_ci_actions_opening(
    bundle: dict[str, Any],
) -> None:
    """The retired name "GitHub Actions" is also the product the jobs run on, so no rewrite may
    turn it into "CI Actions"."""
    opening = " ".join(_sentences(bundle, "ci-jobs"))
    assert "real GitHub Actions jobs" in opening
    assert "CI Actions jobs" not in opening


def test_every_sentence_of_the_common_default_sandbox_is_a_sentence(
    bundle: dict[str, Any],
) -> None:
    """The region fragment ("The provider's default US region.") stood alone in Default Sandbox;
    the Introduction already says the region."""
    regions = {
        row["reader_definition"]["environment"]["region_policy"]
        for versions in bundle["methodologies"].values()
        for row in versions.values()
        if "region_policy" in row["reader_definition"].get("environment", {})
    }
    notes = bundle["reader"]["methods"]["common"]["text"]["notes"]
    for region in regions:
        assert region not in notes["environment"]


def test_the_newest_scores_name_no_retired_input(bundle: dict[str, Any]) -> None:
    """An input with no page (Image Build 4 vCPU / 8 GiB) stays only in the older score versions
    that were published with it."""
    retired = set(load_tables(ROOT).aliases.get("retired_names", {}))
    for identifier, versions in bundle["methodologies"].items():
        if not identifier.startswith("index."):
            continue
        newest = max(versions, key=version_key)
        inputs = versions[newest]["reader_definition"].get("composition", {}).get("inputs", [])
        assert not {row["id"] for row in inputs} & retired, identifier


def test_two_tests_on_one_page_are_two_names_in_a_score(bundle: dict[str, Any]) -> None:
    """Provider Scores listed "Files, Files": a test id drawn through its page's name. The owner
    names the tests by their own registry rows."""
    for versions in (bundle["methodologies"]["index.provider_score"],):
        for row in versions.values():
            leaves = row["reader_definition"]["composition"]["leaves"]
            assert leaves["summed"][0]["tests"] == ["Read 4 KiB", "Write 4 KiB"]
            assert "CPU" in leaves["first_test_only"]


def test_pause_and_resume_states_which_providers_it_tests_and_delayed_resume_does_not(
    bundle: dict[str, Any],
) -> None:
    """only providers with an explicit pause command are tested; Delayed Resume
    does measure auto-idle providers, so the limit is on Pause and Resume alone."""
    limits = (
        "Evaluates resume time on a single sandbox, sent immediately after the previous pause "
        "returns.",
        "Only providers with an explicit `pause` command or equivalent are tested.",
        "This test does not capture providers' ability to scale down or auto-idle/auto-pause.",
    )
    for limit in limits:
        assert limit in _sentences(bundle, "pause-resume")
        assert limit not in _sentences(bundle, "delayed-resume")
    assert not any(
        sentence.startswith("Evaluates immediate resume")
        for sentence in _sentences(bundle, "pause-resume")
    )


def test_the_r2e_gym_setup_step_lists_two_actions_without_a_comma(bundle: dict[str, Any]) -> None:
    """Snapshotting and destroying sandbox 1 reads as one setup step."""
    steps = " ".join(_sentences(bundle, "r2e-gym"))
    assert "snapshot and destroy sandbox 1" in steps
    assert "snapshot, and destroy" not in steps


def test_deepswe_setup_types_no_image_size(bundle: dict[str, Any]) -> None:
    """DeepSWE setup avoids an unsupported image-size claim."""
    setup = " ".join(_sentences(bundle, "deepswe"))
    assert "pinned by SHA-256 digest" in setup
    assert "1 to 2 GiB" not in setup


def test_every_catalog_document_has_a_current_documents_entry(bundle: dict[str, Any]) -> None:
    """web-chain, 2026-11-05: web draws no page for a document current_documents omits, and
    Reliability (planned, outside the default context) had left the catalog."""
    canonical = load_tables(ROOT).aliases.get("document", {})
    listed = {canonical.get(identity, identity) for identity in bundle["current_documents"]}
    documents = {canonical.get(identity, identity) for identity in bundle["methodologies"]}
    assert documents - listed == set()
    assert bundle["current_documents"]["reliability.provider_failures"]["version"] == "0.1.6"
