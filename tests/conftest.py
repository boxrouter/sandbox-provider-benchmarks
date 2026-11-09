# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""`bundle` copies the real tree; `build_text_bundle` compiles fresh inputs; `successor_round` is
a copy of the tree after `scripts/successors.py` moved documents to a new common."""

import importlib.util
import shutil
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml

from sandbox_benchmark_verifier.text import build_text_bundle as _compile_text_bundle

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def successors_script() -> Any:
    """scripts/successors.py, which is not in a package."""
    spec = importlib.util.spec_from_file_location("successors", ROOT / "scripts/successors.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@dataclass(frozen=True)
class SuccessorRound:
    root: Path
    before: dict[str, dict[str, Any]]
    """The newest version of each document before the round, by id."""
    moved: dict[str, str]
    old_commons: tuple[str, ...]
    new_common: str
    effective: str


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    """A fresh copy of the folders the name, text and label checks read, for a test to edit."""
    root = tmp_path / "tree"
    for folder in (
        "methodology",
        "schemas",
        "names",
        "reader",
        "conventions",
        "parameters",
        "contracts",
    ):
        shutil.copytree(ROOT / folder, root / folder)
    return root


@pytest.fixture(scope="session")
def successor_round(
    tmp_path_factory: pytest.TempPathFactory, successors_script: Any
) -> SuccessorRound:
    """Two rounds on a copy of the tree: a new common with a new image, adopted by the generated
    Exec Latency documents' common and by the composites' inputs' common."""
    successors = successors_script
    root = tmp_path_factory.mktemp("successor-round")
    for name in ("methodology", "parameters", "sources"):
        shutil.copytree(ROOT / name, root / name)
    before = {key: held.document for key, held in successors.newest(root).items()}
    common = before["sbs.common"]
    old_image = str(common["environment"]["image"]["reference"]).rpartition(":")[2]
    new_image = "ab" * 32
    new_common = successors.next_version(str(common["version"]))
    text = (root / f"methodology/00-common-{common['version']}.yaml").read_text(encoding="utf-8")
    text = text.replace(f"version: {common['version']}\n", f"version: {new_common}\n", 1)
    (root / f"methodology/00-common-{new_common}.yaml").write_text(
        text.replace(old_image, new_image), encoding="utf-8"
    )
    composite_input = before["time-index"]["composition"]["inputs"][0]["id"]
    old_commons = tuple(
        dict.fromkeys(
            str(before[identifier]["inherits"]["version"])
            for identifier in ("exec-latency", composite_input)
        )
    )
    effective = max(str(document["effective"]) for document in before.values())
    facts = {"digest": f"sha256:{new_image}", "compressed_bytes": 1, "files": 2, "layers": 3}
    facts |= {"uncompressed_bytes": 4}
    moved: dict[str, str] = {}
    for old in old_commons:
        moved |= successors.run(root, old, new_common, effective, facts=facts)
    return SuccessorRound(root, before, moved, old_commons, new_common, effective)


@pytest.fixture(params=["repository", "after a successor round"])
def methodology_root(request: pytest.FixtureRequest) -> Path:
    """The repository, and a copy of it after a successor round: a rule the repository keeps
    must still hold once scripts/successors.py has run."""
    if request.param == "repository":
        return ROOT
    successor: SuccessorRound = request.getfixturevalue("successor_round")
    return successor.root


@pytest.fixture(scope="session")
def _real_tree_bundle() -> dict[str, Any]:
    return _compile_text_bundle(ROOT)


@pytest.fixture
def _bundle_snapshot_factory(_real_tree_bundle: dict[str, Any]) -> Callable[[], dict[str, Any]]:
    return lambda: deepcopy(_real_tree_bundle)


@pytest.fixture
def bundle(_bundle_snapshot_factory: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    return _bundle_snapshot_factory()


@pytest.fixture
def build_text_bundle() -> Callable[[Path], dict[str, Any]]:
    return _compile_text_bundle


def _declare_common(root: Path) -> dict[str, str]:
    """Give a stand-in tree the one explicit common catalog selection binds to (#755, O12): the
    repository's declared default (names.yaml `catalog_selection.default_common`) and that
    common document itself, copied unchanged. A stand-in document joins the selection by
    inheriting the returned reference."""
    declared = yaml.safe_load((ROOT / "names" / "names.yaml").read_text(encoding="utf-8"))
    reference = dict(declared["catalog_selection"]["default_common"])
    names = root / "names" / "names.yaml"
    names.parent.mkdir(parents=True, exist_ok=True)
    identity, version = reference["id"], reference["version"]
    head = f"catalog_selection:\n  default_common: {{id: {identity}, version: {version}}}\n"
    names.write_text(head + (names.read_text(encoding="utf-8") if names.exists() else ""))
    source = next(
        path
        for path in sorted((ROOT / "methodology").glob("00-common*.yaml"))
        if (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("version")
        == reference["version"]
    )
    (root / "methodology").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, root / "methodology" / source.name)
    return reference


@pytest.fixture
def declare_common() -> Callable[[Path], dict[str, str]]:
    """Write the declared default common into a stand-in tree; see `_declare_common`."""
    return _declare_common
