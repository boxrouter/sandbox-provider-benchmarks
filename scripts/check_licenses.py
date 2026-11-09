# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Check SPDX headers and the locked dependency license inventory."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import re
import runpy
import tomllib
from collections.abc import Set as AbstractSet
from pathlib import Path
from typing import cast

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ALLOWED = {
    "apache-2.0",
    "mit",
    "mit-cmu",
    "bsd-2-clause",
    "bsd-3-clause",
    "isc",
    "psf-2.0",
    "mpl-2.0",
    "zlib",
    "cc0-1.0",
    "unlicense",
}
DENIED = {"gpl", "agpl", "sspl", "busl"}
SOURCE_SUFFIXES = {".js", ".py", ".sh", ".ts"}


def project_license(root: Path, project: dict[str, object]) -> set[str]:
    license_value = project.get("license")
    if isinstance(license_value, str):
        return {part.strip().strip("()") for part in license_value.split("AND")}
    metadata = tomllib.loads((root / "REUSE.toml").read_text(encoding="utf-8"))
    return {entry["SPDX-License-Identifier"] for entry in metadata["annotations"]}


component_files = runpy.run_path(str(Path(__file__).with_name("check_reuse.py")))["component_files"]


def check_spdx(root: Path, expected: set[str], *, write: bool = False) -> None:
    if not (root / "LICENSE").is_file():
        raise SystemExit("LICENSE is missing")
    groups = component_files(root)
    components = {root: expected}
    for scope in groups:
        if scope != root:
            project = tomllib.loads((scope / "pyproject.toml").read_text())["project"]
            components[scope] = project_license(scope, project)
    checked = 0
    errors: list[str] = []
    for path, scope in ((path, scope) for scope, files in groups.items() for path in files):
        if path.suffix not in SOURCE_SUFFIXES or not path.is_file():
            continue
        checked += 1
        allowed = components[scope]
        content = path.read_text(encoding="utf-8")
        if write:
            if len(allowed) != 1:
                raise SystemExit("Header generation requires one source licence per project")
            content = re.sub(
                r"(?m)^(?:#|//) SPDX-(?:FileCopyrightText|License-Identifier):[^\n]*\n?",
                "",
                content,
            )
            lines = content.splitlines(keepends=True)
            prefix = "//" if path.suffix in {".js", ".ts"} else "#"
            header = f"{prefix} SPDX-FileCopyrightText: BoxRouter, Inc.\n"
            header += f"{prefix} SPDX-License-" + f"Identifier: {next(iter(allowed))}\n"
            offset = 1 if lines and lines[0].startswith("#!") else 0
            lines.insert(offset, header)
            content = "".join(lines)
            path.write_text(content, encoding="utf-8")
        lines = content.splitlines()[:5]
        if not any("SPDX-FileCopyrightText: BoxRouter, Inc." in line for line in lines):
            errors.append(f"{path.relative_to(root)}: expected the company copyright holder")
        identifier_tag = r"^(?:#|//) SPDX-License-" + "Identifier: (.+)$"
        identifiers = [match[1] for line in lines if (match := re.match(identifier_tag, line))]
        if len(identifiers) != 1:
            errors.append(f"{path.name}: expected exactly one SPDX license identifier")
            continue
        if identifiers[0] not in allowed:
            errors.append(f"{path.name}: identifier does not match the project license")
    if errors:
        raise SystemExit("\n".join(errors))
    print(f"{checked} source files use the project license; LICENSE is present.")


def dependency_license(distribution: importlib.metadata.Distribution) -> str:
    """Read upstream metadata first, then bundled licence text without per-package overrides."""
    expressions = distribution.metadata.get_all("License-Expression") or []
    if expressions:
        return str(expressions[0])
    values = distribution.metadata.get_all("License") or []
    value = values[0] if values else ""
    classifiers = " ".join(distribution.metadata.get_all("Classifier") or []).casefold()
    if "apache software license" in classifiers:
        return "Apache-2.0"
    for file in distribution.files or []:
        if not re.search(r"(?i)(?:license|copying)(?:[.\-/]|$)", str(file)):
            continue
        path = Path(str(distribution.locate_file(file)))
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace").casefold()
        if "apache license" in text and "version 2.0" in text:
            return "Apache-2.0"
        if "permission is hereby granted, free of charge" in text:
            return "MIT"
        if "redistribution and use in source and binary forms" in text:
            return "BSD-3-Clause" if "neither the" in text else "BSD-2-Clause"
    return value


def check_dependencies(project: dict[str, object]) -> None:
    environment = cast(dict[str, str | AbstractSet[str]], default_environment())
    distributions = {
        str(canonicalize_name(distribution.metadata["Name"] or "unknown")): distribution
        for distribution in importlib.metadata.distributions()
    }
    dependencies = project.get("dependencies", [])
    assert isinstance(dependencies, list)
    pending = [Requirement(value) for value in dependencies if isinstance(value, str)]
    groups = project.get("optional-dependencies", {})
    assert isinstance(groups, dict)
    for group in groups.values():
        assert isinstance(group, list)
        pending.extend(Requirement(value) for value in group if isinstance(value, str))
    selected: dict[str, set[str]] = {}
    while pending:
        requirement = pending.pop()
        extras = set(requirement.extras) or {""}
        if requirement.marker and not any(
            requirement.marker.evaluate({**environment, "extra": extra}) for extra in extras
        ):
            continue
        name = str(canonicalize_name(requirement.name))
        if name in selected and extras <= selected[name]:
            continue
        selected.setdefault(name, set()).update(extras)
        distribution = distributions.get(name)
        if distribution is None:
            raise SystemExit(f"Locked dependency is missing from the environment: {name}")
        for raw in distribution.requires or []:
            child = Requirement(raw)
            if child.marker and not any(
                child.marker.evaluate({**environment, "extra": extra})
                for extra in selected[name] or {""}
            ):
                continue
            pending.append(child)
    allowed = 0
    review: list[str] = []
    denied: list[str] = []
    for dependency_name in sorted(selected):
        distribution = distributions[dependency_name]
        value = dependency_license(distribution)
        identifiers = {item.casefold() for item in re.findall(r"[A-Za-z][A-Za-z0-9.+-]*", value)}
        classifiers = distribution.metadata.get_all("Classifier") or []
        normalized = " ".join([value, *classifiers]).casefold().replace(" ", "-")
        if any(item in normalized for item in DENIED):
            denied.append(dependency_name)
        elif identifiers & ALLOWED or any(
            f"license :: osi approved :: {item}" in " ".join(classifiers).casefold()
            for item in ALLOWED
        ):
            allowed += 1
        else:
            review.append(dependency_name)
    print(f"Dependency licenses: {allowed} allowed, {len(review)} review, {len(denied)} denied.")
    if review:
        print("Review metadata for: " + ", ".join(sorted(review)))
    if denied:
        raise SystemExit("Denied dependency licenses: " + ", ".join(sorted(denied)))


def check_inventory(path: Path) -> None:
    """Check the declared licences of distributed inputs; retain external-only identities."""
    inventory = json.loads(path.read_text(encoding="utf-8"))
    errors = []
    for item in inventory:
        name, expression = item["name"], item["license"]
        identifiers = {
            value.casefold() for value in re.findall(r"[A-Za-z][A-Za-z0-9.+-]*", expression)
        }
        identifiers -= {"and", "or"}
        if not item["distributed"]:
            print(f"External input: {name}: {expression}; not distributed")
        elif not identifiers or not identifiers <= ALLOWED:
            errors.append(f"{name}: licence is not established as compatible with Apache-2.0")
        else:
            print(f"Distributed input: {name}: {expression}")
    if errors:
        raise SystemExit("\n".join(errors))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--spdx-only", action="store_true")
    parser.add_argument("--inventory", type=Path)
    args = parser.parse_args()
    if args.inventory is not None:
        check_inventory(args.inventory)
        return
    root = args.root.resolve()
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    check_spdx(root, project_license(root, project), write=args.write)
    if not args.spdx_only:
        check_dependencies(project)


if __name__ == "__main__":
    main()
