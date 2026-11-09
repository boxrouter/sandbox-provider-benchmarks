# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Closed component revisions are rejected without changing public upstream version metadata."""

from copy import deepcopy

from sandbox_benchmark_verifier.content import content_criteria, criteria_errors
from sandbox_benchmark_verifier.public_provenance import RULE_ID, closed_revision_errors


def test_current_document_closed_revision_check_preserves_public_sources() -> None:
    for text in (
        "Modal adapter 0.4.115",
        "Core workspace-v0.1.17",
        "adapter for Blaxel (version 0.5.48)",
        "A provider adapter 9.8.7 records exact hourly cost.",
        "Core workspace-v9.8.7",
        "Core version: 9.8.7",
        "runner `v9.8.7`",
        "The adapter revision abcdef0123456789 records this value.",
        "Adopt workspace-v9.8.7.",
        "adapter-kit==9.8.7",
    ):
        errors = closed_revision_errors(text)
        assert errors and all(RULE_ID in error for error in errors)
        assert all("9.8.7" not in error and "abcdef" not in error for error in errors)
    for text in (
        "Methodology 1.2.3",
        "Provider spec 1.2.3",
        "Pricing 1.2.3",
        "The provider's public SDK 1.2.3 exposes this report.",
        "Public native Modal SDK 1.6.0 exposes this report.",
        "Core records the provider's data; the public price sheet is 1.2.3.",
    ):
        assert not closed_revision_errors(text)


def test_public_provenance_rule_is_exported_from_its_owner() -> None:
    criteria = content_criteria()
    rule = next(rule for rule in criteria["conventions"]["rules"] if rule["id"] == RULE_ID)
    assert all(
        criteria["public_source_provenance"][key] == value
        for key, value in rule["predicate"].items()
    )
    assert rule["predicate"]["allowed_version_sources"] == [
        "methodology",
        "provider_spec",
        "pricing",
    ]


def test_public_registry_projection_keeps_upstream_references_and_code_versions() -> None:
    criteria = content_criteria()
    view = criteria["public_source_provenance"]["registry_view"]
    fields = {field["path"]: field for field in view["fields"]}
    assert view["source"] == "public_sources" and view["entry_key"] == "id"
    assert fields["version"]["format"] == "code"
    assert fields["licence.spdx"]["omit_if_null"] is True
    assert fields["licence.status"]["values"]["not-stated"]
    assert fields["licence.reference_only"]["values"]["true"]
    assert not criteria_errors(criteria)


def test_public_registry_projection_rejects_generic_metadata_omission_and_date_coercion() -> None:
    criteria = deepcopy(content_criteria())
    view = criteria["public_source_provenance"]["registry_view"]
    view["fields"] = [field for field in view["fields"] if field["path"] != "licence.status"]
    next(field for field in view["fields"] if field["path"] == "version")["format"] = "date"
    errors = criteria_errors(criteria)
    assert any("licence.status" in error for error in errors)
    assert any("version" in error and "without date inference" in error for error in errors)
