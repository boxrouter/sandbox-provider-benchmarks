# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""scripts/successors.py writes a successor round: each chosen document's next version on the new
common, its parameter tree, the generated Exec Latency file through its source, the composites
and the lock. The generated-file and composite rules are checked on the round's tree by
test_exec_views.py and test_composite_inputs.py (the `methodology_root` fixture)."""

import copy
import json
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from sandbox_benchmark_verifier import lock
from sandbox_benchmark_verifier.names import load_tables, resolve
from sandbox_benchmark_verifier.parameters import tree_digest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[1]


def _newest(successors: Any, root: Path) -> dict[str, dict[str, Any]]:
    return {key: held.document for key, held in successors.newest(root).items()}


def _restored(successor: dict[str, Any], previous: dict[str, Any], images: Any) -> Any:
    """The successor with the round's changes undone: it must equal its predecessor."""
    text = json.dumps(successor)
    assert not any(before in text for before, _ in images), successor["id"]
    for before, after in images:
        text = text.replace(after, before)
    restored = json.loads(text)
    restored["version"], restored["effective"] = previous["version"], previous["effective"]
    restored["inherits"]["version"] = previous["inherits"]["version"]
    if "parameters" in previous:
        restored["parameters"] = previous["parameters"]
    shared = restored.get("procedure", {}).get("shared_trial")
    if shared:
        shared["version"] = previous["procedure"]["shared_trial"]["version"]
    for item, old in zip(
        restored.get("composition", {}).get("inputs", []),
        previous.get("composition", {}).get("inputs", []),
        strict=True,
    ):
        item["id"] = old["id"]
        item["version"] = old["version"]
    if "scopes" in restored.get("composition", {}):
        restored["composition"]["scopes"] = copy.deepcopy(previous["composition"]["scopes"])
    if "plots" in restored.get("composition", {}):
        restored["composition"]["plots"] = copy.deepcopy(previous["composition"]["plots"])
    return restored


def test_each_successor_is_its_predecessor_on_the_new_common(
    successor_round: Any, successors_script: Any
) -> None:
    r = successor_round
    after = _newest(successors_script, r.root)
    commons = {
        version: successors_script.common(r.root, version)
        for version in (*r.old_commons, r.new_common)
    }
    image_changes = [
        replacement
        for old in r.old_commons
        for replacement in successors_script.image_replacements(commons[old], commons[r.new_common])
    ]
    assert r.moved and {"exec-latency", "time-index", "time-vs-cost"} <= set(r.moved)
    owner_tables = load_tables(ROOT)
    for identifier, version in r.moved.items():
        successor, previous = after[identifier], r.before[identifier]
        assert successor["version"] == version
        assert version == successors_script.next_version(previous["version"])
        assert successor["effective"] == r.effective
        assert successor["inherits"]["version"] == r.new_common
        before_text = json.dumps(previous)
        images = [replacement for replacement in image_changes if replacement[0] in before_text]
        restored = _restored(successor, previous, images)
        assert json.dumps(restored, sort_keys=True) == json.dumps(previous, sort_keys=True)
        previous_composition = previous.get("composition") or {}
        successor_composition = successor.get("composition") or {}
        if previous_composition:
            for field in ("aggregation", "weighting", "inclusion"):
                assert successor_composition[field] == previous_composition[field]
            old_inputs = previous_composition["inputs"]
            new_inputs = successor_composition["inputs"]
            assert [item["quantity"] for item in new_inputs] == [
                item["quantity"] for item in old_inputs
            ]
            identity_map = {
                old["id"]: new["id"] for old, new in zip(old_inputs, new_inputs, strict=True)
            }
            canonical_inputs = {
                resolve("document", old["id"], tables=owner_tables): new["id"]
                for old, new in zip(old_inputs, new_inputs, strict=True)
            }
            for old_scope, new_scope in zip(
                previous_composition.get("scopes", []),
                successor_composition.get("scopes", []),
                strict=True,
            ):
                assert (new_scope["id"], new_scope["label"]) == (
                    old_scope["id"],
                    old_scope["label"],
                )
                assert new_scope["inputs"] == [
                    identity_map.get(item, item) for item in old_scope["inputs"]
                ]
            old_plots = previous_composition.get("plots") or {}
            new_plots = successor_composition.get("plots") or {}
            if old_plots:
                assert new_plots.get("note") == old_plots.get("note")
                for axis in ("x", "y"):
                    if axis not in old_plots:
                        continue
                    new_axis = new_plots[axis]
                    assert new_axis["quantity"] == old_plots[axis]["quantity"]
                    axis_identity = resolve("document", old_plots[axis]["id"], tables=owner_tables)
                    assert new_axis["id"] == canonical_inputs.get(axis_identity)
                    assert [
                        item
                        for item in new_inputs
                        if item["id"] == new_axis["id"] and item["version"] == new_axis["version"]
                    ] == [item for item in new_inputs if item["id"] == new_axis["id"]]
    unmoved = {
        identifier
        for identifier, document in r.before.items()
        if document.get("inherits", {}).get("version") in r.old_commons
        and not document.get("composition")
    } - set(r.moved)
    assert unmoved == set()


def test_successor_parameter_trees_match_their_digests_and_the_lock_is_complete(
    successor_round: Any, successors_script: Any
) -> None:
    r = successor_round
    after = _newest(successors_script, r.root)
    for identifier in r.moved:
        parameters = after[identifier].get("parameters")
        if parameters:
            assert parameters["path"] == f"parameters/{identifier}/{r.moved[identifier]}/"
            assert tree_digest(r.root / parameters["path"]) == parameters["sha256"], identifier
    assert lock.check(r.root, require_complete=True) == []


def test_a_round_that_changes_the_image_refuses_to_guess_its_facts(
    tmp_path: Path, successors_script: Any
) -> None:
    """Image Build's `standard` row states facts read from the image; without --facts the
    command stops before it writes anything."""
    for name in ("methodology", "parameters", "sources"):
        shutil.copytree(ROOT / name, tmp_path / name)
    successors = successors_script
    before = _newest(successors, tmp_path)
    common = copy.deepcopy(before["sbs.common"])
    image = str(common["environment"]["image"]["reference"]).rpartition(":")[2]
    new = successors.next_version(str(common["version"]))
    path = tmp_path / f"methodology/00-common-{common['version']}.yaml"
    text = path.read_text(encoding="utf-8").replace(
        f"version: {common['version']}\n", f"version: {new}\n", 1
    )
    (tmp_path / f"methodology/00-common-{new}.yaml").write_text(
        text.replace(image, "cd" * 32), "utf-8"
    )
    image_build = next(i for i in before if i.startswith("image-build-2vcpu"))
    lock_before = (tmp_path / "methodology/released.lock").read_bytes()
    with pytest.raises(SystemExit, match="pass --facts"):
        successors.run(tmp_path, str(before[image_build]["inherits"]["version"]), new, "2026-11-06")
    assert (tmp_path / "methodology/released.lock").read_bytes() == lock_before
    assert _newest(successors, tmp_path)[image_build]["version"] == before[image_build]["version"]


def test_preserve_explicit_overrides_moves_identity_without_touching_values_or_parameters(
    tmp_path: Path, successors_script: Any
) -> None:
    successors = successors_script
    methodology = tmp_path / "methodology"
    methodology.mkdir()
    old_image = "registry.example.test/sandbox:old-digest"
    new_image = "registry.example.test/sandbox:new-digest"
    (methodology / "00-common-1.0.0.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "sbs.common",
                "version": "1.0.0",
                "effective": "2025-01-01",
                "environment": {"image": {"reference": old_image}},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    new_common = yaml.safe_load((methodology / "00-common-1.0.0.yaml").read_text())
    new_common["version"] = "1.0.1"
    new_common["environment"]["image"]["reference"] = new_image
    (methodology / "00-common-1.0.1.yaml").write_text(
        yaml.safe_dump(new_common, sort_keys=False), encoding="utf-8"
    )

    docs: list[dict[str, Any]] = []
    parameter_bytes: dict[str, bytes] = {}
    for identifier, image, size in (
        ("synthetic.equal-image", old_image, 4),
        ("synthetic.custom-image", "registry.example.test/task:custom", 8),
    ):
        directory = tmp_path / f"parameters/{identifier}/1.0.0"
        directory.mkdir(parents=True)
        parameter_bytes[identifier] = (
            '[{"id":"task","version":"kept","data":{"image":"'
            + image
            + '","facts":{"files":3}}}]\n'
        ).encode()
        (directory / "workloads.json").write_bytes(parameter_bytes[identifier])
        docs.append(
            {
                "id": identifier,
                "version": "1.0.0",
                "effective": "2025-01-01",
                "category": "sandbox_workload",
                "inherits": {"id": "sbs.common", "version": "1.0.0"},
                "environment": {
                    "image": {"reference": image},
                    "size": f"{size}x{size * 2}",
                    "network": {"mode": "private", "egress": False},
                    "lifetime": {"seconds": 90 + size},
                },
                "parameters": {
                    "path": f"parameters/{identifier}/1.0.0/",
                    "sha256": tree_digest(directory),
                },
            }
        )
    method_path = methodology / "synthetic.yaml"
    method_path.write_text(yaml.safe_dump_all(docs, sort_keys=False), encoding="utf-8")
    previous_method_bytes = method_path.read_bytes()
    lock.update(tmp_path)
    previous_lock = lock.read_lock(tmp_path)

    moved = successors.run(
        tmp_path,
        "1.0.0",
        "1.0.1",
        "2026-11-06",
        only=[document["id"] for document in docs],
        preserve_explicit_overrides=True,
    )
    after = _newest(successors, tmp_path)

    assert moved == {"synthetic.equal-image": "1.0.1", "synthetic.custom-image": "1.0.1"}
    for previous in docs:
        identifier = previous["id"]
        successor = after[identifier]
        assert successor["environment"] == previous["environment"]
        assert (
            successor["environment"]["image"]["reference"]
            == previous["environment"]["image"]["reference"]
        )
        assert successor["parameters"]["path"] == f"parameters/{identifier}/1.0.1/"
        assert successor["parameters"]["sha256"] == previous["parameters"]["sha256"]
        assert (
            tmp_path / successor["parameters"]["path"] / "workloads.json"
        ).read_bytes() == parameter_bytes[identifier]
        assert (
            tmp_path / f"parameters/{identifier}/1.0.0/workloads.json"
        ).read_bytes() == parameter_bytes[identifier]
    assert method_path.read_bytes().startswith(previous_method_bytes)
    assert lock.read_lock(tmp_path).items() >= previous_lock.items()
    assert lock.check(tmp_path) == []


@pytest.mark.parametrize("enabled", [False, True])
def test_cli_passes_the_preserve_explicit_overrides_option(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    successors_script: Any,
    enabled: bool,
) -> None:
    captured: dict[str, Any] = {}

    def fake_run(
        root: Path,
        old: str,
        new: str,
        effective: str,
        only: list[str] | None = None,
        facts: dict[str, Any] | None = None,
        preserve_explicit_overrides: bool = False,
    ) -> dict[str, str]:
        captured.update(
            root=root,
            old=old,
            new=new,
            effective=effective,
            preserve_explicit_overrides=preserve_explicit_overrides,
        )
        return {}

    monkeypatch.setattr(successors_script, "ROOT", tmp_path)
    monkeypatch.setattr(successors_script, "run", fake_run)
    argv = ["--from", "1.0.0", "--to", "1.0.1", "--effective", "2026-11-06"]
    if enabled:
        argv.append("--preserve-explicit-overrides")

    successors_script.main(argv)

    assert captured["root"] == tmp_path
    assert captured["old"] == "1.0.0"
    assert captured["new"] == "1.0.1"
    assert captured["preserve_explicit_overrides"] is enabled


def _write_owner_names(
    root: Path, *, aliases: dict[str, str] | None = None, retired: list[str] | None = None
) -> None:
    names = root / "names"
    names.mkdir(parents=True, exist_ok=True)
    ids = ("a", "b", "time", "cost", "frontier", "unrelated", "ghost")
    rows = [
        {
            "row": row,
            "kind": "index" if identifier in {"time", "cost", "frontier", "unrelated"} else "test",
            "id": identifier,
            "name": identifier,
        }
        for row, identifier in enumerate(ids, 1)
    ]
    (names / "names.yaml").write_text(
        yaml.safe_dump(
            {
                "names": rows,
                "metric_label_grammar": {},
                "catalog_selection": {"default_common": {"id": "sbs.common", "version": "1.0.10"}},
            },
            sort_keys=False,
        )
    )
    alias_table: dict[str, Any] = {"document": aliases or {}}
    if retired:
        alias_table["retired"] = retired
    (names / "aliases.yaml").write_text(yaml.safe_dump(alias_table, sort_keys=False))


def _refresh_tree(
    root: Path,
    *,
    mixed: bool = False,
    alias_input: bool = False,
    ambiguous: bool = False,
    missing: bool = False,
    retired: bool = False,
    cycle: bool = False,
) -> None:
    """A derived graph with already released leaf successors, an unrelated index and future data."""
    directory = root / "methodology"
    directory.mkdir()
    for version in ("1.0.10", "1.0.13", "1.0.15"):
        (directory / f"00-common-{version}.yaml").write_text(
            yaml.safe_dump({"id": "sbs.common", "version": version, "effective": "2024-01-01"})
        )

    def leaf(identifier: str, version: str, effective: str, common: str) -> dict[str, Any]:
        return {
            "id": identifier,
            "version": version,
            "effective": effective,
            "category": "sandbox_operation",
            "inherits": {"id": "sbs.common", "version": common},
        }

    def index(identifier: str, inputs: list[str]) -> dict[str, Any]:
        document: dict[str, Any] = {
            "id": identifier,
            "version": "1.0.0",
            "effective": "2024-01-01",
            "category": "index",
            "inherits": {"id": "sbs.common", "version": "1.0.10"},
            "composition": {
                "inputs": [
                    {"id": item, "version": "1.0.0", "quantity": "Value"} for item in inputs
                ],
                "aggregation": "geometric_mean",
                "weighting": "Equal",
                "inclusion": "All",
            },
        }
        if identifier == "time":
            document["composition"]["scopes"] = [
                {"id": "all", "label": "All", "inputs": list(inputs)}
            ]
        if identifier == "frontier":
            document["composition"]["plots"] = {
                "x": {"id": inputs[0], "version": "1.0.0", "quantity": "Time"},
                "y": {"id": inputs[1], "version": "1.0.0", "quantity": "Cost"},
            }
        return document

    leaf_ids: tuple[str, ...] = ("a", "b")
    if ambiguous:
        leaf_ids = ("b",)
    if alias_input and not ambiguous:
        leaf_ids = ("a", "b")
    for identifier in leaf_ids:
        docs = [
            leaf(identifier, "1.0.0", "2024-01-01", "1.0.10"),
            leaf(
                identifier,
                "1.1.0",
                "2025-01-01",
                "1.0.13" if mixed and identifier == "b" else "1.0.10",
            ),
            leaf(identifier, "9.0.0", "2030-01-01", "1.0.15"),
        ]
        if mixed and identifier == "b":
            docs = [document for document in docs if document["inherits"]["version"] != "1.0.10"]
        (directory / f"{identifier}.yaml").write_text(yaml.safe_dump_all(docs, sort_keys=False))
    first_input = (
        "retired-a"
        if retired
        else "ghost"
        if missing
        else "legacy-a"
        if alias_input or ambiguous
        else "a"
    )
    time_inputs = [first_input, "b"]
    cost_inputs = ["a", "b"]
    if cycle:
        time_inputs, cost_inputs = ["cost", "b"], ["time", "a"]
    for identifier, inputs in (
        ("time", time_inputs),
        ("cost", cost_inputs),
        ("frontier", ["time", "cost"]),
        ("unrelated", ["a"]),
    ):
        (directory / f"{identifier}.yaml").write_text(
            yaml.safe_dump(index(identifier, inputs), sort_keys=False)
        )
    aliases = {"legacy-a": "a", "legacy-a-two": "a"} if alias_input or ambiguous else {}
    if ambiguous:
        for version in ("1.0.0", "1.1.0"):
            document = leaf(
                "legacy-a-two",
                version,
                "2024-01-01" if version == "1.0.0" else "2025-01-01",
                "1.0.10",
            )
            path = directory / "legacy-a-two.yaml"
            with path.open("a", encoding="utf-8") as stream:
                yaml.safe_dump(document, stream, sort_keys=False)
        for version in ("1.0.0", "1.1.0"):
            document = leaf(
                "legacy-a", version, "2024-01-01" if version == "1.0.0" else "2025-01-01", "1.0.10"
            )
            path = directory / "legacy-a.yaml"
            with path.open("a", encoding="utf-8") as stream:
                yaml.safe_dump(document, stream, sort_keys=False)
    elif alias_input:
        for version in ("9.0.0",):
            document = leaf("legacy-a", version, "2025-01-01", "1.0.10")
            (directory / "legacy-a.yaml").write_text(yaml.safe_dump(document, sort_keys=False))
    _write_owner_names(root, aliases=aliases, retired=["retired-a"] if retired else None)
    lock.update(root)


def test_refresh_indices_keeps_leaf_bytes_and_algorithms_and_is_idempotent(
    tmp_path: Path, successors_script: Any
) -> None:
    s = successors_script
    _refresh_tree(tmp_path)
    before = _newest(s, tmp_path)
    leaf_bytes = {
        name: (tmp_path / f"methodology/{name}.yaml").read_bytes()
        for name in ("a", "b", "unrelated")
    }
    moved = s.refresh_composites(tmp_path, "2026-11-05", ["frontier", "time", "cost"])
    assert moved == {"cost": "1.0.1", "time": "1.0.1", "frontier": "1.0.1"}
    after = _newest(s, tmp_path)
    for name in moved:
        assert _restored(after[name], before[name], []) == before[name]
        assert after[name]["inherits"]["version"] == "1.0.10"
    assert [x["version"] for x in after["time"]["composition"]["inputs"]] == ["1.1.0", "1.1.0"]
    assert [x["version"] for x in after["frontier"]["composition"]["inputs"]] == ["1.0.1", "1.0.1"]
    for name, contents in leaf_bytes.items():
        assert (tmp_path / f"methodology/{name}.yaml").read_bytes() == contents
    assert s.refresh_composites(tmp_path, "2026-11-05", ["frontier", "time", "cost"]) == {}
    assert lock.check(tmp_path, require_complete=True) == []


def test_refresh_refuses_mixed_common_without_writing(
    tmp_path: Path, successors_script: Any
) -> None:
    _refresh_tree(tmp_path, mixed=True)
    before = {p: p.read_bytes() for p in (tmp_path / "methodology").iterdir()}
    with pytest.raises(SystemExit, match="missing actual document for b"):
        successors_script.refresh_composites(tmp_path, "2026-11-05", ["time", "cost", "frontier"])
    assert all(p.read_bytes() == contents for p, contents in before.items())


def test_refresh_resolves_owner_aliases_and_reuses_one_input_vector_for_scopes_and_axes(
    tmp_path: Path, successors_script: Any
) -> None:
    _refresh_tree(tmp_path, alias_input=True)
    legacy_before = (tmp_path / "methodology/legacy-a.yaml").read_bytes()
    moved = successors_script.refresh_composites(
        tmp_path, "2026-11-05", ["frontier", "time", "cost"]
    )
    after = _newest(successors_script, tmp_path)

    assert moved == {"cost": "1.0.1", "frontier": "1.0.1", "time": "1.0.1"}
    assert [item["id"] for item in after["time"]["composition"]["inputs"]] == ["a", "b"]
    assert after["time"]["composition"]["scopes"][0]["inputs"] == ["a", "b"]
    assert after["time"]["composition"]["inputs"][0]["version"] == "1.1.0"
    assert after["frontier"]["composition"]["plots"]["x"]["id"] == "time"
    assert after["frontier"]["composition"]["plots"]["x"]["version"] == "1.0.1"
    assert after["frontier"]["composition"]["plots"]["y"]["id"] == "cost"
    assert after["frontier"]["composition"]["plots"]["y"]["version"] == "1.0.1"
    assert (tmp_path / "methodology/legacy-a.yaml").read_bytes() == legacy_before
    assert (
        successors_script.refresh_composites(tmp_path, "2026-11-05", ["frontier", "time", "cost"])
        == {}
    )


@pytest.mark.parametrize(
    ("options", "error"),
    [
        ({"missing": True}, "missing actual document"),
        ({"ambiguous": True}, "ambiguous actual documents"),
        ({"retired": True}, "retired document reference"),
        ({"cycle": True}, "graph has a cycle"),
    ],
)
def test_refresh_refuses_unresolvable_composite_graph_before_writing(
    tmp_path: Path,
    successors_script: Any,
    options: dict[str, bool],
    error: str,
) -> None:
    _refresh_tree(tmp_path, **options)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}

    with pytest.raises(SystemExit, match=error):
        successors_script.refresh_composites(tmp_path, "2026-11-05", ["time"])

    assert {path: path.read_bytes() for path in before} == before


def test_refresh_refuses_duplicate_input_identity_after_owner_resolution(
    tmp_path: Path, successors_script: Any
) -> None:
    _refresh_tree(tmp_path, alias_input=True)
    path = tmp_path / "methodology/time.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["composition"]["inputs"][1]["id"] = "legacy-a"
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    before = {item: item.read_bytes() for item in tmp_path.rglob("*") if item.is_file()}

    with pytest.raises(SystemExit, match="duplicate input identity a after resolution"):
        successors_script.refresh_composites(tmp_path, "2026-11-05", ["time"])

    assert {item: item.read_bytes() for item in before} == before


def test_refresh_requires_explicit_indices(tmp_path: Path, successors_script: Any) -> None:
    _refresh_tree(tmp_path)
    with pytest.raises(SystemExit, match="requires --only"):
        successors_script.refresh_composites(tmp_path, "2026-11-05", [])
    with pytest.raises(SystemExit, match="not an index"):
        successors_script.refresh_composites(tmp_path, "2026-11-05", ["a"])


def test_refresh_context_is_selected_before_higher_other_cohort_and_retrospective_versions(
    tmp_path: Path, successors_script: Any
) -> None:
    s = successors_script
    _refresh_tree(tmp_path)
    for identity in ("a", "b", "time", "cost", "frontier"):
        path = tmp_path / f"methodology/{identity}.yaml"
        rows = list(yaml.safe_load_all(path.read_text()))
        higher = copy.deepcopy(rows[0])
        higher.update(
            version="2.0.0",
            effective="2025-01-01",
            inherits={"id": "sbs.common", "version": "1.0.13"},
        )
        if "composition" in higher:
            for ref in higher["composition"]["inputs"]:
                ref["version"] = "2.0.0"
            for axis in higher["composition"].get("plots", {}).values():
                axis["version"] = "2.0.0"
        with path.open("a") as stream:
            yaml.safe_dump(higher, stream, explicit_start=True, sort_keys=False)
    path = tmp_path / "methodology/time.yaml"
    retrospective = copy.deepcopy(list(yaml.safe_load_all(path.read_text()))[0])
    retrospective.update(version="5.0.0", effective="2025-01-01")
    retrospective["composition"]["input_selection"] = "retrospective_exact"
    with path.open("a") as stream:
        yaml.safe_dump(retrospective, stream, explicit_start=True, sort_keys=False)
    assert s.selected_context(tmp_path, "1.0.10")["time"].version == "1.0.0"
    assert s.selected_context(tmp_path, "1.0.13")["a"].version == "2.0.0"
    assert s.refresh_composites(tmp_path, "2026-11-05", ["time"]) == {"time": "5.0.1"}
    selected = s.selected_context(tmp_path, "1.0.10")["time"].document
    assert [ref["version"] for ref in selected["composition"]["inputs"]] == ["1.1.0", "1.1.0"]
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert (
        s.refresh_composites(
            tmp_path, "2026-11-05", ["time", "cost", "frontier"], common_version="1.0.13"
        )
        == {}
    )
    assert {p: p.read_bytes() for p in before} == before
    assert s.selected_context(tmp_path, "1.0.13")["time"].version == "2.0.0"


def test_generator_refuses_effective_policy_mixing_before_any_write(
    tmp_path: Path, successors_script: Any
) -> None:
    _refresh_tree(tmp_path)
    parent = tmp_path / "methodology/00-common-1.0.10.yaml"
    document = yaml.safe_load(parent.read_text())
    document["environment"] = {"region_policy": {"selection": "no_portable_region_request"}}
    parent.write_text(yaml.safe_dump(document))
    leaf = tmp_path / "methodology/a.yaml"
    rows = list(yaml.safe_load_all(leaf.read_text()))
    rows[1]["environment"] = {"region_policy": "Existing US leaf override."}
    leaf.write_text(yaml.safe_dump_all(rows))
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    with pytest.raises(SystemExit, match="leaf region policy conflicts"):
        successors_script.refresh_composites(tmp_path, "2026-11-05", ["time"])
    assert {p: p.read_bytes() for p in before} == before


def test_round_binds_source_common_before_max_and_allocates_above_all_history(
    tmp_path: Path, successors_script: Any
) -> None:
    s = successors_script
    _refresh_tree(tmp_path)
    target = {
        "id": "sbs.common",
        "version": "1.0.11",
        "effective": "2026-01-01",
        "environment": {"region_policy": {"selection": "no_portable_region_request"}},
    }
    (tmp_path / "methodology/00-common-1.0.11.yaml").write_text(yaml.safe_dump(target))
    source = s.selected_context(tmp_path, "1.0.10")["a"].document
    assert source["version"] == "1.1.0"
    moved = s.run(
        tmp_path, "1.0.10", "1.0.11", "2026-11-05", only=["a"], preserve_explicit_overrides=True
    )
    assert moved["a"] == "9.0.1"  # History includes9.0.0 in another common/future context.
    assert s.selected_context(tmp_path, "1.0.10")["a"].version == "1.1.0"
    after = s.selected_context(tmp_path, "1.0.11")["a"].document
    assert after["version"] == "9.0.1"
    assert {k: v for k, v in after.items() if k not in {"version", "effective", "inherits"}} == {
        k: v for k, v in source.items() if k not in {"version", "effective", "inherits"}
    }


def test_round_preserves_explicit_policy_and_refuses_conflicting_target_before_write(
    tmp_path: Path, successors_script: Any
) -> None:
    _refresh_tree(tmp_path)
    target = {
        "id": "sbs.common",
        "version": "1.0.11",
        "effective": "2026-01-01",
        "environment": {"region_policy": {"selection": "no_portable_region_request"}},
    }
    (tmp_path / "methodology/00-common-1.0.11.yaml").write_text(yaml.safe_dump(target))
    path = tmp_path / "methodology/a.yaml"
    rows = list(yaml.safe_load_all(path.read_text()))
    rows[1]["environment"] = {"region_policy": "Explicit existing US policy."}
    path.write_text(yaml.safe_dump_all(rows))
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    with pytest.raises(SystemExit, match="leaf region policy conflicts"):
        successors_script.run(
            tmp_path, "1.0.10", "1.0.11", "2026-11-05", only=["a"], preserve_explicit_overrides=True
        )
    assert {p: p.read_bytes() for p in before} == before


def test_an_appended_version_does_not_repeat_the_schema_header(
    successor_round: Any, successors_script: Any, tmp_path: Path
) -> None:
    header = (
        "# SPDX-"
        "License-Identifier: Apache-2.0\n"  # split: REUSE reads a whole one as this file's tag
        "# yaml-language-server: $schema=../../schemas/methodology.schema.json\n"
    )
    path = tmp_path / "demo.yaml"
    path.write_text(f"{header}id: demo\nversion: 1.0.0\n")
    successors_script.append(path, f"{header}# its own note\nid: demo\nversion: 1.0.1\n")
    assert path.read_text().count(header) == 1
    assert "---\n# its own note\nid: demo\nversion: 1.0.1\n" in path.read_text()
    # A whole round adds no header line to any methodology file.
    for moved in (successor_round.root / "methodology").rglob("*.yaml"):
        original = ROOT / moved.relative_to(successor_round.root)
        before = original.read_text().count("# yaml-language-server:") if original.is_file() else 1
        assert moved.read_text().count("# yaml-language-server:") <= before, moved.name
