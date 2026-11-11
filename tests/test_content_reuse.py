# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Keep text verification scoped to one read-only content criteria mapping."""

from collections.abc import Callable, Mapping
from contextlib import nullcontext
from copy import deepcopy
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Any

import pytest

from sandbox_benchmark_verifier import content, text

pytestmark = pytest.mark.unit


def test_literal_template_uses_supplied_criteria(monkeypatch: pytest.MonkeyPatch) -> None:
    criteria = {
        "literal_templates": {
            "summary": {
                "category": "index",
                "reference": "{param:index.summary}",
                "source": "notes.summary",
            }
        },
        "notes": {"summary": "A value for every provider."},
    }

    def unexpected_rebuild() -> dict[str, Any]:
        pytest.fail("the supplied criteria should avoid rebuilding content criteria")

    monkeypatch.setattr(content, "content_criteria", unexpected_rebuild)

    assert content.is_literal_template("A value for every provider.", "index", criteria=criteria)


def test_default_criteria_calls_return_fresh_mutable_mappings() -> None:
    first = content.content_criteria()
    second = content.content_criteria()
    original_enabled = second["slots"]["caption"]["enabled"]

    assert first is not second
    assert first["slots"] is not second["slots"]
    first["slots"]["caption"]["enabled"] = not original_enabled
    assert second["slots"]["caption"]["enabled"] is original_enabled


def test_document_errors_match_default_and_supplied_criteria_without_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = {
        "id": "demo",
        "version": "1.0.0",
        "notes": {
            "summary": "The test runs 10 trials.",
            "introduction": "The test runs 10 trials.",
        },
    }
    criteria = content.content_criteria()
    original = deepcopy(criteria)
    default_errors = content.document_content_errors(document, documents={}, names={})
    assert default_errors
    assert criteria == original

    def unexpected_rebuild() -> dict[str, Any]:
        pytest.fail("the supplied criteria should avoid rebuilding content criteria")

    monkeypatch.setattr(content, "content_criteria", unexpected_rebuild)
    supplied_errors = content.document_content_errors(
        document, documents={}, names={}, criteria=criteria
    )

    assert supplied_errors == default_errors
    assert criteria == original


def test_slot_rule_updates_a_copy_of_the_selected_rule() -> None:
    source_rule = {"form": "sentence", "min_length": 1}
    criteria = {
        "slots": {"notes.summary": source_rule},
        "literal_templates": {
            "summary": {
                "slot": "notes.summary",
                "panel_kinds": ["index"],
                "source": "notes.summary",
            }
        },
        "notes": {"summary": "An owned summary."},
    }

    resolved = content.slot_rule(criteria, "notes.summary", "index")

    assert resolved == {"form": "literal", "min_length": 1, "const": "An owned summary."}
    assert resolved is not source_rule
    assert source_rule == {"form": "sentence", "min_length": 1}


def test_text_verification_reuses_bundle_criteria_for_fields_and_documents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    declare_common: Callable[[Path], dict[str, str]],
) -> None:
    criteria: Mapping[str, Any] = MappingProxyType(
        {
            "literal_templates": MappingProxyType({}),
            "slots": MappingProxyType({"caption": MappingProxyType({"enabled": False})}),
        }
    )
    document = {
        "id": "demo",
        "version": "1.0.0",
        "category": "index",
        "notes": {"introduction": "A short summary."},
    }
    bundle: dict[str, Any] = {
        "content_criteria": criteria,
        "methodologies": {
            "demo": {"1.0.0": {"text": {"notes": {"introduction": "A short summary."}}}}
        },
        "reader": {"methods": {}},
        "metric_labels": {"metrics": {}},
        "names": [],
    }
    bundle_builds: list[Path] = []
    table_loads: list[Path] = []
    literal_criteria: list[Mapping[str, Any] | None] = []
    document_criteria: list[Mapping[str, Any] | None] = []
    original_is_literal_template = content.is_literal_template

    (tmp_path / "names").mkdir()
    (tmp_path / "names/names.yaml").write_text(
        "names:\n  - kind: test\n    id: demo-row\n    name: Demo Row\n    document: demo\n",
        encoding="utf-8",
    )
    declare_common(tmp_path)  # catalog selection's one explicit common (#755)

    def build_bundle(root: Path) -> dict[str, Any]:
        bundle_builds.append(root)
        return bundle

    def literal_template(
        value: Any, category: Any, *, criteria: Mapping[str, Any] | None = None
    ) -> bool:
        literal_criteria.append(criteria)
        return original_is_literal_template(value, category, criteria=criteria)

    def document_errors(
        checked_document: Mapping[str, Any],
        *,
        documents: Mapping[str, Mapping[str, Any]],
        names: Mapping[str, str],
        criteria: Mapping[str, Any] | None = None,
    ) -> list[str]:
        assert checked_document is document
        assert documents["content"] is criteria
        document_criteria.append(criteria)
        return []

    def load_tables(root: Path) -> SimpleNamespace:
        table_loads.append(root)
        return SimpleNamespace(names=[{"kind": "index", "id": "demo", "name": "Demo"}])

    def unexpected_rebuild() -> dict[str, Any]:
        pytest.fail("a text verification must pass its bundle criteria to each validator")

    monkeypatch.setattr(text, "compiler_tables_cache", lambda *_args: nullcontext())
    monkeypatch.setattr(text, "build_text_bundle", build_bundle)
    monkeypatch.setattr(text, "content_criteria", unexpected_rebuild)
    monkeypatch.setattr(content, "content_criteria", unexpected_rebuild)
    monkeypatch.setattr(text, "is_literal_template", literal_template)
    monkeypatch.setattr(text, "document_content_errors", document_errors)
    monkeypatch.setattr(text, "load_tables", load_tables)
    monkeypatch.setattr(text, "_document_rows", lambda _root, _rows: {"test:demo-row": document})
    monkeypatch.setattr(text, "_latest_documents", lambda _root, **_common: {})
    monkeypatch.setattr(text, "criteria_errors", lambda _criteria: [])
    monkeypatch.setattr(text, "raw_token_warnings", lambda _bundle: [])
    monkeypatch.setattr(text, "index_metric_id_warnings", lambda _bundle: [])
    monkeypatch.setattr(text, "_retired_words", lambda *_args: [])
    monkeypatch.setattr(text, "reader_errors", lambda _root: [])
    monkeypatch.setattr(text, "source_name_errors", lambda _root: [])
    monkeypatch.setattr(text, "published_name_errors", lambda _bundle, _root: [])

    text.text_warnings(tmp_path)

    assert bundle_builds == [tmp_path]
    assert literal_criteria
    assert all(used is criteria for used in literal_criteria)
    assert document_criteria == [criteria]
    assert table_loads == [tmp_path]
