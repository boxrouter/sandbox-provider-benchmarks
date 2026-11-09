# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""The reader projection keeps scientific inputs immutable and resolves their sources."""

import json
import re
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from sandbox_benchmark_verifier.catalog import load_methodology
from sandbox_benchmark_verifier.content import content_criteria, criteria_errors
from sandbox_benchmark_verifier.methodology_page import reader_definition
from sandbox_benchmark_verifier.names import label_case_errors, load_tables
from sandbox_benchmark_verifier.status_footer import status_label_owner_errors
from sandbox_benchmark_verifier.versions import version_key

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[1]


def test_duration_labels_leave_the_unit_to_the_formatted_value() -> None:
    labels = content_criteria()["methodology_page"]["field_labels"]
    durations = {key: label for key, label in labels.items() if key.endswith("_seconds")}
    assert durations
    assert all("(s)" not in label for label in durations.values())


def test_literal_commands_and_duration_fields_have_owned_formats() -> None:
    criteria = content_criteria()
    policy = criteria["methodology_page"]
    for key in policy["field_labels"]:
        if key.endswith("_seconds"):
            assert policy["field_formats"][key] == "duration_seconds"
    broken = deepcopy(criteria)
    broken["methodology_page"]["field_formats"]["command"] = "unowned-format"
    assert any(
        "unsupported owned methodology value format" in error for error in criteria_errors(broken)
    )


def test_at_a_glance_is_short_and_sections_carry_no_open_flag() -> None:
    page = content_criteria()["methodology_page"]

    # inference in the glance; CPU cores and memory are one Machine Size; every section starts
    # collapsed.
    sources = [path for group in page["at_a_glance"]["groups"] for path in group]
    assert "environment.size" in sources
    assert not {"procedure.sandboxes_per_trial", "procedure.concurrency", "execution"} & set(
        sources
    )
    for policy in [*page["sections"].values(), *page["common_sections"].values()]:
        assert "open" not in policy
    broken = deepcopy(content_criteria())
    broken["methodology_page"]["machine_size"] = "{vcpu} vCPU"
    assert any("machine_size must be a template" in error for error in criteria_errors(broken))


def test_the_image_reference_field_is_prose_where_a_document_writes_a_sentence() -> None:
    """DeepSWE's reference is a sentence about its task images, drawn whole in
    monospace. A pinned image reference is drawn by the standard image's own facts, so the field
    carries no code format."""
    assert (
        "environment.image.reference" not in content_criteria()["methodology_page"]["field_formats"]
    )


def test_metric_reader_names_distinguish_values_without_changing_metric_identity() -> None:
    document: dict[str, Any] = {
        "id": "exec-latency",
        "metrics": [{"name": "total_time"}, {"name": "runtime"}, {"name": "latency"}],
    }
    result = reader_definition(document, {}, content_criteria()["methodology_page"])
    assert [metric["reader_name"] for metric in result["metrics"]] == [
        "Total Time",
        "Command Runtime",
        "Command Round-Trip Time",
    ]
    assert all("reader_name" not in metric for metric in document["metrics"])


def test_taxonomy_status_is_explicit_and_separate_from_reader_names() -> None:
    rows = {row["id"]: row for row in load_tables().names if row["kind"] in {"section", "tab"}}
    for row in rows.values():
        if "status" in row:
            assert row["status"].replace("_", " ").title() not in row["name"]


def test_reader_projection_sources_inherited_facts_and_removes_duplicate_fields() -> None:
    policy = content_criteria()["methodology_page"]
    document: dict[str, Any] = {
        "id": "example",
        "version": "1.0.0",
        "inherits": {"id": "sbs.common", "version": "1.0.0"},
        "statistics": {"primary": "mean"},
        "procedure": {"variants": [{"id": "one"}]},
        "variants": [{"id": "one"}],
    }
    original = deepcopy(document)
    parent = {"statistics": {"primary": "median", "interval": "Bootstrap interval"}}
    result = reader_definition(document, {("sbs.common", "1.0.0"): parent}, policy)
    assert result["statistics"] == {"primary": "mean", "interval": "Bootstrap interval"}
    assert result["reader_sources"] == {"statistics.interval": document["inherits"]}
    assert "variants" not in result
    assert result["procedure"]["variants"] == document["procedure"]["variants"]
    assert document == original


def test_a_composite_page_draws_its_formulas_from_the_owner_policy() -> None:
    policy = content_criteria()["methodology_page"]
    composite = reader_definition({"id": "time-index"}, {}, policy, "time-index")
    assert composite["composition"]["formulas"] == [
        {"id": "time-index", **policy["formulas"]["time-index"]}
    ]


def test_a_formula_listing_tests_reaches_each_of_them_without_the_list() -> None:
    policy = deepcopy(content_criteria()["methodology_page"])
    policy["formulas"]["demo"] = {**policy["formulas"]["time-index"], "tests": ["a", "b"]}
    for test in ("a", "b"):
        formulas = reader_definition({"id": test}, {}, policy, test)["composition"]["formulas"]
        assert formulas == [{"id": "demo", **policy["formulas"]["time-index"]}]
    assert "composition" not in reader_definition({"id": "c"}, {}, policy, "c")


def test_a_document_shows_every_formula_listing_it_in_the_owners_order() -> None:
    policy = deepcopy(content_criteria()["methodology_page"])
    first = {**policy["formulas"]["time-index"], "tests": ["a"]}
    second = {**policy["formulas"]["time-index"], "tests": ["a"]}
    policy["formulas"] = {"demo-first": first, "demo-second": second}
    shown = reader_definition({"id": "a"}, {}, policy, "a")["composition"]["formulas"]
    assert [row["aria_label"] for row in shown] == [first["aria_label"], second["aria_label"]]
    assert all("tests" not in row for row in shown)


def test_a_status_label_is_read_from_its_owner_never_retyped(tmp_path: Path) -> None:
    """Not displayed had three copies in three casings (the status label
    "Not displayed", the page's field label and its footer "Not Displayed:", both from a
    conventions slot name). Status labels are Title Case (founder, 2026-11-08). A label exists
    once, at ranking_statuses; every other place reads it, and a string that is a label in any
    casing, colon or not, fails; a sentence-case label fails the names check.
    """
    criteria = content_criteria()
    page = criteria["methodology_page"]
    statuses = criteria["ranking_statuses"]
    for key, status in (
        ("not_displayed", "not_displayed"),
        ("not_measured", "not_measured"),
        ("not_offered", "not_offered"),
        ("in_development", "in_development"),
        ("too_few_runs", "too_few_runs"),
        ("ranked", "ranked"),
    ):
        assert page["field_labels"][key] == statuses[status]["label"], key
    # The Common Methodology's Not Ranked section and the Not Ranked card read it like any place.
    not_ranked = statuses["too_few_runs"]["label"]
    assert page["field_labels"]["not_ranked"] == page["pages"]["not_ranked"] == not_ranked
    assert "{param:" in Path("contracts/text/methodology-page.json").read_text(encoding="utf-8")
    assert page["enum_labels"]["not_offered"] == statuses["not_offered"]["label"]
    assert "not_displayed" not in page["pages"]  # the footer draws the label and its own colon
    facts = criteria["fact_list"]
    assert facts["fact_failed_units"] == statuses["filled_units"]["label"]
    assert (
        facts["fact_failed_workloads"] == facts["fact_failed_trials"] == statuses["filled"]["label"]
    )
    assert facts["fact_no_result_not_measured"] == statuses["not_measured"]["label"]
    assert label_case_errors(ROOT) == []

    # The sources hold no typed status label, in any casing.
    assert not status_label_owner_errors(ROOT)
    shutil.copytree(ROOT / "contracts", tmp_path / "contracts")
    (tmp_path / "conventions").mkdir()
    shutil.copy(ROOT / "conventions/text-conventions.yaml", tmp_path / "conventions")
    shutil.copytree(ROOT / "schemas", tmp_path / "schemas")
    path = tmp_path / "contracts/text/methodology-page.json"
    contract = json.loads(path.read_text(encoding="utf-8"))
    contract["pages"]["not_displayed"] = "Not displayed:"
    contract["field_labels"]["not_offered"] = "NOT OFFERED"
    contract["field_labels"]["too_few_runs"] = "Not Ranked"
    path.write_text(json.dumps(contract), encoding="utf-8")
    facts_path = tmp_path / "contracts/text/fact-list.json"
    facts = json.loads(facts_path.read_text(encoding="utf-8"))
    facts["fact_failed_units"] = "Provider Failures"
    facts_path.write_text(json.dumps(facts), encoding="utf-8")
    (tmp_path / "contracts/text/prose-note.json").write_text(
        json.dumps({"note": "providers not displayed stay unlisted"}), encoding="utf-8"
    )
    conventions = tmp_path / "conventions/text-conventions.yaml"
    conventions.write_text(
        conventions.read_text(encoding="utf-8") + "extra_label: Not ranked\n", encoding="utf-8"
    )
    errors = status_label_owner_errors(tmp_path)
    # Every re-typed label fails, wherever it sits; the words alone in prose are not the label.
    assert sorted(error.split(":")[0] for error in errors) == [
        "contracts/text/fact-list.json.fact_failed_units",
        "contracts/text/methodology-page.json.field_labels.not_offered",
        "contracts/text/methodology-page.json.field_labels.too_few_runs",
        "contracts/text/methodology-page.json.pages.not_displayed",
        "conventions/text-conventions.yaml.extra_label",
    ]
    owner = tmp_path / "contracts/text/1.0.0.json"
    labels_doc = json.loads(owner.read_text(encoding="utf-8"))
    labels_doc["ranking_statuses"]["not_offered"]["label"] = "Not offered"
    owner.write_text(json.dumps(labels_doc), encoding="utf-8")
    # A sentence-case label fails the one label registry (conventions naming.label_catalogs).
    shutil.copy(ROOT / "conventions/text-conventions.yaml", tmp_path / "conventions")
    assert any(
        "ranking_statuses.not_offered.label" in e and "is not Title Case" in e
        for e in label_case_errors(tmp_path)
    )


def test_reader_projection_omits_unverified_origins_without_altering_scientific_records() -> None:
    policy = content_criteria()["methodology_page"]
    document: dict[str, Any] = {
        "id": "example",
        "source": {"ids": ["private-origin"], "version": "private-revision"},
        "environment": {
            "image": {
                "reference": "private-registry/image@sha256:private",
                "modification": "An owned scientific preparation sentence.",
            }
        },
        "cost": {"price_sheet_version": "public-price-version"},
    }
    original = deepcopy(document)
    result = reader_definition(document, {}, policy)
    assert "source" not in result
    assert result["environment"]["image"] == document["environment"]["image"]
    assert result["cost"] == document["cost"]
    assert document == original


def test_reader_projection_hides_retired_failure_ranking_without_changing_evidence() -> None:
    policy = content_criteria()["methodology_page"]
    document = {
        "id": "example",
        "statistics": {"ranking": {"failing_often": "Historical threshold", "min_successes": 10}},
        "notes": {"not_ranked": {"failing_often": "Historical explanation"}},
    }
    original = deepcopy(document)
    result = reader_definition(document, {}, policy)
    assert result["statistics"]["ranking"] == {"min_successes": 10}
    assert result["notes"]["not_ranked"] == {}
    assert document == original


def test_success_only_public_status_and_selector_contract() -> None:
    criteria = content_criteria()
    assert "failing_often" not in criteria["ranking_statuses"]
    footer = criteria["ranking_status_footer"]
    assert footer["names_reason_template"].format(names="R2E-Gym") == "R2E-Gym"
    assert "failing_often" not in footer["reason_templates"]
    assert "failure_reason_template" not in footer
    assert not {"fact_excluded", "fact_burst_failed"} & criteria["fact_list"].keys()
    assert "github_actions_bar_failures" not in criteria["hover_templates"]
    floor = criteria["fact_list"]["fact_tail_floor"]
    unavailable = criteria["statistics_selector"]["p95_unavailable"]
    assert str(floor) in unavailable
    assert "{" not in unavailable


def test_public_failure_presentation_cannot_be_reintroduced() -> None:
    criteria = content_criteria()
    criteria["ranking_statuses"]["failing_often"] = {"label": "Failing often", "footer": True}
    assert (
        "public trial presentation must contain successful samples and blocked reasons only"
        in criteria_errors(criteria)
    )


def test_the_standard_image_is_public_and_the_page_can_say_how_to_pull_it() -> None:
    criteria = content_criteria()
    policy = criteria["methodology_page"]
    assert not [path for path in policy["private_evidence_paths"] if path.startswith("environment")]
    image = policy["standard_image"]
    common = max(
        (row for row in load_methodology() if row.document["id"] == "sbs.common"),
        key=lambda row: version_key(str(row.document["version"])),
    )
    reference = common.document["environment"]["image"]["reference"]
    name, digest = reference.split("@")
    assert name == "ghcr.io/boxrouter/standard-image"
    assert digest in image["versions"]
    assert image["registry_page"].startswith("https://github.com/orgs/boxrouter/packages/")
    for broken_key in ("registry_page", "labels", "copy"):
        broken = deepcopy(criteria)
        broken["methodology_page"]["standard_image"].pop(broken_key)
        assert any("standard_image" in error for error in criteria_errors(broken))
    broken = deepcopy(criteria)
    broken["methodology_page"]["standard_image"]["versions"]["sha256:short"] = "1"
    assert any("sha256 digest" in error for error in criteria_errors(broken))


def _tests_page() -> tuple[
    dict[str, Any], dict[tuple[str, str], dict[str, Any]], list[dict[str, Any]]
]:
    def document(identifier: str, version: str, effective: str, unit: str) -> dict[str, Any]:
        return {
            "id": identifier,
            "version": version,
            "effective": effective,
            "metrics": [{"name": "total_time", "unit": unit}],
            "inherits": {"id": "sbs.common", "version": "1.0.13"},
        }

    host = document("host-doc", "1.0.1", "2026-10-31", "s")
    documents = {
        ("host-doc", "1.0.1"): host,
        ("member-doc", "1.0.0"): document("member-doc", "1.0.0", "2026-10-30", "old"),
        ("member-doc", "1.0.1"): document("member-doc", "1.0.1", "2026-10-31", "ms"),
        ("member-doc", "1.0.2"): document("member-doc", "1.0.2", "2026-11-01", "later"),
    }
    documents[("sbs.common", "1.0.13")] = {
        "id": "sbs.common",
        "version": "1.0.13",
        "effective": "2026-01-01",
    }
    rows = [
        {"kind": "test", "id": "host", "document": "host-doc"},
        {"kind": "test", "id": "member", "document": "member-doc", "page": "host"},
    ]
    return host, documents, rows


def test_a_page_draws_the_metric_of_each_test_it_holds_under_the_test_name() -> None:
    host, documents, rows = _tests_page()
    policy = content_criteria()["methodology_page"]
    result = reader_definition(host, documents, policy, "host", rows, {})
    assert [(m["test"], m["reader_name"], m["unit"]) for m in result["metrics"]] == [
        ("host", "{name:test.host}", "s"),
        ("member", "{name:test.member}", "ms"),
    ]
    assert host["metrics"] == [{"name": "total_time", "unit": "s"}]


def test_a_page_version_before_every_version_of_a_member_draws_no_member() -> None:
    host, documents, rows = _tests_page()
    host["effective"] = "2026-10-01"
    result = reader_definition(
        host, documents, content_criteria()["methodology_page"], "host", rows, {}
    )
    assert [m["unit"] for m in result["metrics"]] == ["s"]
    assert result["metrics"][0]["reader_name"] == "Total Time"


def test_every_page_lists_the_metrics_of_each_test_of_its_tab_and_their_formulas(
    bundle: dict[str, Any],
) -> None:
    rows = load_tables(ROOT).names
    names = {r["id"]: r["name"] for r in rows if r["kind"] == "test"}
    hosts = {r["page"] for r in rows if r["kind"] == "test" and "page" in r}
    assert hosts == {
        "exec-latency-per-100-calls",
        "read-4kib",
        "sequential-read",
        "7-zip-1-thread",
    }
    for host in hosts:
        parent = next(r["parent"] for r in rows if r["id"] == host and r["kind"] == "test")
        tab = [r["id"] for r in rows if r["kind"] == "test" and r.get("parent") == parent]
        document = bundle["reader"]["methods"][host]["document"]
        version = max(bundle["methodologies"][document], key=version_key)
        definition = bundle["methodologies"][document][version]["reader_definition"]
        headings = [m["reader_name"] for m in definition["metrics"]]
        for test in tab:
            assert any(h.startswith(names[test]) for h in headings), (host, test, headings)
        assert len(set(headings)) == len(headings), headings
        assert {m["test"] for m in definition["metrics"]} == set(tab)
        assert definition["composition"]["formulas"], host


def test_a_document_that_runs_no_trials_has_no_sandbox_and_trials_section(
    bundle: dict[str, Any],
) -> None:
    """The composites showed a sandbox image and `docker pull` for trials they do not run."""
    page = content_criteria()["methodology_page"]
    assert page["sections"]["sandbox"]["requires"] == ["procedure.trials"]
    assert page["common_sections"]["sandbox"]["requires"] == []
    composites = [
        identifier
        for identifier, versions in bundle["methodologies"].items()
        if any(entry["reader_definition"].get("category") == "index" for entry in versions.values())
    ]
    assert composites
    for identifier in composites:
        for version, entry in bundle["methodologies"][identifier].items():
            assert "trials" not in entry["reader_definition"].get("procedure", {}), (
                identifier,
                version,
            )


def test_a_test_page_links_its_parameter_file_instead_of_drawing_a_data_sources_section() -> None:
    page = content_criteria()["methodology_page"]
    assert "sources" not in page["sections"]
    assert "Data Sources" not in json.dumps(page["sections"])
    assert page["actions"]["parameters"] == "View the Parameters"
    assert page["parameters_link"]["href"].endswith("/blob/v{version}/{path}workloads.json")


def test_the_newest_common_states_the_capability_source_without_a_typed_release_or_a_promise() -> (
    None
):
    """the Provider Requirements source line has no pinned SPS release
    (no source in this build for the release the measured data pinned) and no sentence about the
    next SPS release or an unproven custom_images status."""
    commons = [row.document for row in load_methodology() if row.document["id"] == "sbs.common"]
    newest = max(commons, key=lambda document: version_key(document["version"]))
    source = newest["capability_source"]
    assert "release" not in source
    assert source["use"].endswith("when a required capability is absent.")
    assert "next SPS release" not in source["use"]
    assert "custom_images" not in source["use"]


def test_a_common_from_1_0_15_names_no_aws_region() -> None:
    """The job host's region is a fact of each publication (the runner's host regions), so a common
    states none: 1.0.10-1.0.14 typed us-west-2, and the US-East publication contradicted itself."""
    region = re.compile(r"\b(?:us|eu|ap|ca|sa|me|af)-(?:east|west|north|south|central)-\d\b")
    commons = [row.document for row in load_methodology() if row.document["id"] == "sbs.common"]
    named = sorted(
        str(document["version"])
        for document in commons
        if version_key(document["version"]) >= (1, 0, 15) and region.search(json.dumps(document))
    )
    assert named == []
    assert any(version_key(document["version"]) >= (1, 0, 15) for document in commons)


@pytest.mark.parametrize("common_version", ["1.0.10", "1.0.13"])
def test_page_members_keep_the_measured_hosts_common_and_effective_date(
    common_version: str,
) -> None:
    host, documents, rows = _tests_page()
    common = {"id": "sbs.common", "version": common_version}
    documents[("sbs.common", common_version)] = {
        **documents[("sbs.common", "1.0.13")],
        "version": common_version,
    }
    for document in documents.values():
        if document["id"] != "sbs.common":
            document["inherits"] = common
    other = deepcopy(documents[("member-doc", "1.0.1")])
    other.update(version="9.0.0", inherits={"id": "sbs.common", "version": "1.0.999"})
    other["metrics"][0]["unit"] = "wrong-cohort"
    documents[("member-doc", "9.0.0")] = other
    retro = deepcopy(documents[("member-doc", "1.0.1")])
    retro.update(version="9.1.0", composition={"input_selection": "retrospective_exact"})
    retro["metrics"][0]["unit"] = "retrospective"
    documents[("member-doc", "9.1.0")] = retro
    result = reader_definition(
        host, documents, content_criteria()["methodology_page"], "host", rows, {}
    )
    assert [metric["unit"] for metric in result["metrics"]] == ["s", "ms"]


def test_a_page_with_members_requires_its_exact_common() -> None:
    host, documents, rows = _tests_page()
    host.pop("inherits")
    with pytest.raises(ValueError, match="exact inherited common"):
        reader_definition(host, documents, content_criteria()["methodology_page"], "host", rows, {})


def test_page_members_refuse_conflicting_effective_location_policy() -> None:
    host, documents, rows = _tests_page()
    documents[("sbs.common", "1.0.13")]["environment"] = {
        "region_policy": {"selection": "no_portable_region_request"}
    }
    documents[("member-doc", "1.0.1")]["environment"] = {"region_policy": "legacy US request"}
    before = deepcopy(documents)
    with pytest.raises(ValueError, match="leaf region policy conflicts"):
        reader_definition(host, documents, content_criteria()["methodology_page"], "host", rows, {})
    assert documents == before
