# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""One form for machine sizes and compound names."""

import json
from pathlib import Path
from typing import Any

from sandbox_benchmark_verifier.names import load_tables, name_errors
from sandbox_benchmark_verifier.placeholders import resolve_template
from sandbox_benchmark_verifier.unit_forms import (
    ampersand_errors,
    machine_size_errors,
    unit_form_errors,
)

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = json.loads((ROOT / "contracts/text/methodology-page.json").read_text())["machine_size"]


def test_the_template_is_the_one_slash_form() -> None:
    assert TEMPLATE == "{vcpu} vCPU / {memory_gib} GiB"


def test_a_size_in_any_other_form_is_refused() -> None:
    assert machine_size_errors({"a": "Image Build (2 vCPU / 4 GiB)"}, TEMPLATE) == []
    for text in ("4 vCPU, 8 GiB", "4 vCPU and 8 GiB", "4 vCPU 8 GiB", "4 vCPUs, 8 GiB"):
        assert len(machine_size_errors({"a": text}, TEMPLATE)) == 1, text


def test_an_ampersand_between_words_is_refused() -> None:
    assert ampersand_errors({"a": "Pause and Resume"}) == []
    assert len(ampersand_errors({"a": "Pause & Resume"})) == 1


def test_the_repository_holds_none_of_them() -> None:
    assert [e for e in name_errors(ROOT) if "machine size" in e or "'and'" in e] == []


def test_pause_and_resume_is_the_name_and_the_old_name_is_history() -> None:
    names = load_tables(ROOT).names
    assert {r["name"] for r in names if r["id"] in ("pause-resume", "pause_resume")} == {
        "Pause and Resume"
    }
    history = (
        (ROOT / "names/names.yaml").read_text(encoding="utf-8").split("display_name_history:")[1]
    )
    assert history.count("  - Pause & Resume") >= 3


def _documents() -> dict[str, Any]:
    page = json.loads((ROOT / "contracts/text/methodology-page.json").read_text())
    return {
        "content": {"methodology_page": page},
        "demo.1.0.0": {"environment": {"size": {"vcpu": 4, "memory_gib": 8}}},
    }


def test_the_machine_placeholder_reads_the_template() -> None:
    text = resolve_template(
        "Runs on {machine:demo.1.0.0.environment.size}.", names={}, documents=_documents()
    )
    assert text == "Runs on 4 vCPU / 8 GiB."


def test_a_retired_name_is_in_the_one_size_form_too(tmp_path: Path) -> None:
    """The retired Image Build 4x8 name was typed "(4 vCPU, 8 GiB)" in names/aliases.yaml, the one
    place the form check did not read, so the Provider Scores inputs showed two forms."""
    (tmp_path / "names").mkdir()
    (tmp_path / "names/aliases.yaml").write_text(
        "retired_names:\n  sandbox.x: Image Build (4 vCPU, 8 GiB)\n", encoding="utf-8"
    )
    (tmp_path / "contracts/text").mkdir(parents=True)
    (tmp_path / "contracts/text/methodology-page.json").write_text(
        json.dumps({"machine_size": TEMPLATE}), encoding="utf-8"
    )
    assert any("retired_names.sandbox.x" in e for e in unit_form_errors(tmp_path, []))
    aliases = load_tables(ROOT).aliases["retired_names"]
    assert machine_size_errors(dict(aliases), TEMPLATE) == []
