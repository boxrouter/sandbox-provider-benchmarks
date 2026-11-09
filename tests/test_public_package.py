# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import importlib
import json
import runpy
import subprocess
import tomllib
from importlib.metadata import PathDistribution
from pathlib import Path

import jsonschema
import pytest
import yaml


def test_verifier_imports() -> None:
    assert (
        importlib.import_module("sandbox_benchmark_verifier").__name__
        == "sandbox_benchmark_verifier"
    )
    assert jsonschema.__name__ == "jsonschema"
    assert yaml.__name__ == "yaml"


def test_header_generation_preserves_shebang_and_source_literals(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    (tmp_path / "LICENSE").write_text("test licence\n")
    source = tmp_path / "example.py"
    original = '#!/usr/bin/env python3\nvalue = "SPDX-License-' + 'Identifier: example"\n'
    source.write_text(original)
    subprocess.run(["git", "add", "example.py"], cwd=tmp_path, check=True)
    checker = runpy.run_path(str(Path(__file__).parents[1] / "scripts/check_licenses.py"))
    check = checker["check_spdx"]
    check(tmp_path, {"Apache-2.0"}, write=True)
    generated = source.read_text()
    assert generated.startswith(original.splitlines()[0] + "\n# SPDX-FileCopyrightText:")
    assert original.splitlines()[1] in generated
    check(tmp_path, {"Apache-2.0"}, write=True)
    assert source.read_text() == generated
    check(tmp_path, {"Apache-2.0"})


def test_inventory_rejects_incompatible_distribution_but_records_external_inputs(
    tmp_path: Path,
) -> None:
    checker = runpy.run_path(str(Path(__file__).parents[1] / "scripts/check_licenses.py"))
    check = checker["check_inventory"]
    inventory = tmp_path / "inventory.json"
    item = {"name": "example-input", "license": "GPL-2.0-only", "distributed": True}
    inventory.write_text(json.dumps([item]))
    with pytest.raises(SystemExit, match="not established as compatible"):
        check(inventory)
    inventory.write_text(json.dumps([{**item, "distributed": False}]))
    check(inventory)
    inventory.write_text(json.dumps([{**item, "license": "MIT OR Apache-2.0"}]))
    check(inventory)


def test_dependency_licence_uses_bundled_text_and_preserves_explicit_expressions(
    tmp_path: Path,
) -> None:
    metadata = tmp_path / "example.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text("Name: example\nVersion: 1\n")
    (metadata / "LICENSE").write_text("Permission is hereby granted, free of charge")
    (metadata / "RECORD").write_text("example.dist-info/LICENSE,,\n")
    checker = runpy.run_path(str(Path(__file__).parents[1] / "scripts/check_licenses.py"))
    check = checker["dependency_license"]
    assert check(PathDistribution(metadata)) == "MIT"
    (metadata / "METADATA").write_text(
        "Name: example\nVersion: 1\nLicense-Expression: GPL-2.0-only\n"
    )
    assert check(PathDistribution(metadata)) == "GPL-2.0-only"


def test_distribution_metadata_rejects_private_only_classifiers() -> None:
    checker = runpy.run_path(str(Path(__file__).parents[1] / "tests/check_distribution.py"))
    metadata = b"Name: example\nVersion: 1\nClassifier: private :: sample\n"
    with pytest.raises(SystemExit, match="private-only classifier"):
        checker["require_metadata"](metadata, Path("example.whl"))


def test_nested_components_keep_their_own_source_licences(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    (tmp_path / "LICENSE").write_text("test licence\n")
    (tmp_path / "root.py").write_text("value = 1\n")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "pyproject.toml").write_text('[project]\nlicense = "MIT"\n')
    (nested / "child.py").write_text("value = 2\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    checker = runpy.run_path(str(Path(__file__).parents[1] / "scripts/check_licenses.py"))
    groups = checker["component_files"](tmp_path)
    assert nested / "child.py" in groups[nested]
    assert nested / "child.py" not in groups[tmp_path]
    check = checker["check_spdx"]
    check(tmp_path, {"Apache-2.0"}, write=True)
    assert "Identifier: MIT" in (nested / "child.py").read_text()
    assert "Identifier: Apache-2.0" in (tmp_path / "root.py").read_text()
    (nested / "pyproject.toml").write_text('[project]\nlicense = "MIT AND CC-BY-4.0"\n')
    (nested / "data.json").write_text("{}")
    check(tmp_path, {"Apache-2.0"})
    (nested / "child.py").write_text((tmp_path / "root.py").read_text())
    with pytest.raises(SystemExit, match="identifier does not match the project license"):
        check(tmp_path, {"Apache-2.0"})


def test_procedure_license_is_exact_and_scoped_to_its_component(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    (tmp_path / "LICENSE").write_text("test licence\n")
    procedures = tmp_path / "procedures"
    procedures.mkdir()
    (procedures / "pyproject.toml").write_text(
        '[project]\nlicense = "LicenseRef-PolyForm-Shield-1.0.0"\n'
    )
    procedure = procedures / "cpu.py"
    identifier = "SPDX-License-" + "Identifier: "
    procedure.write_text(
        "# SPDX-FileCopyrightText: BoxRouter, Inc.\n"
        "# " + identifier + "LicenseRef-PolyForm-Shield-1.0.0\n"
        "value = 1\n"
    )
    source = tmp_path / "main.py"
    source.write_text(
        "# SPDX-FileCopyrightText: BoxRouter, Inc.\n# " + identifier + "Apache-2.0\nvalue = 1\n"
    )
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    checker = runpy.run_path(str(Path(__file__).parents[1] / "scripts/check_licenses.py"))
    check = checker["check_spdx"]
    check(tmp_path, {"Apache-2.0"})

    source.write_text(source.read_text().replace("Apache-2.0", "LicenseRef-PolyForm-Shield-1.0.0"))
    with pytest.raises(SystemExit, match="identifier does not match the project license"):
        check(tmp_path, {"Apache-2.0"})
    source.write_text(source.read_text().replace("LicenseRef-PolyForm-Shield-1.0.0", "Apache-2.0"))
    procedure.write_text(
        procedure.read_text().replace("LicenseRef-PolyForm-Shield-1.0.0", "Apache-2.0")
    )
    with pytest.raises(SystemExit, match="identifier does not match the project license"):
        check(tmp_path, {"Apache-2.0"})


def test_procedure_license_text_and_reuse_scope_are_export_root_visible() -> None:
    root = Path(__file__).parents[1]
    license_name = "LicenseRef-PolyForm-Shield-1.0.0.txt"
    assert (root / "LICENSES" / license_name).is_file()
    assert not (root / "procedures" / "LICENSES" / license_name).exists()

    reuse = tomllib.loads((root / "REUSE.toml").read_text(encoding="utf-8"))
    annotations = [
        row
        for row in reuse["annotations"]
        if row.get("SPDX-License-Identifier") == "LicenseRef-PolyForm-Shield-1.0.0"
    ]
    assert annotations == [
        {
            "path": "procedures/**",
            "SPDX-FileCopyrightText": "BoxRouter, Inc.",
            "SPDX-License-Identifier": "LicenseRef-PolyForm-Shield-1.0.0",
        }
    ]
