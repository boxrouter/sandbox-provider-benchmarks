# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Check the generated Python distributions carry their license files, metadata and assets, and
that the wheel installed on its own builds the same text bundle as the source tree."""

import json
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from email.parser import Parser
from pathlib import Path, PurePosixPath
from typing import Any

from sandbox_benchmark_verifier.text import write_text_bundle

LICENSE_SOURCES = {
    "LICENSE": "LICENSE",
    "LICENSE-CC-BY-4.0": "LICENSE-CC-BY-4.0",
    "NOTICE": "NOTICE",
    "LicenseRef-PolyForm-Shield-1.0.0.txt": ("LICENSES/LicenseRef-PolyForm-Shield-1.0.0.txt"),
}
EXPECTED_FILES = set(LICENSE_SOURCES)
EXPECTED_LICENSE = "Apache-2.0"
# Run in a clean environment that holds only the wheel: the text bundle from its packaged assets.
INSTALLED_TEXT = (
    "import sys; from pathlib import Path; "
    "from sandbox_benchmark_verifier.catalog import _assets; "
    "from sandbox_benchmark_verifier.text import write_text_bundle; "
    "write_text_bundle(_assets(), Path(sys.argv[1]))"
)
# A tree's bundle names its checkout's commit; a wheel's names the commit stamped into it.
SOURCE_STAMPS = {"source_commit", "source_url"}


def packaged_assets() -> dict[str, str]:
    """Each source directory the wheel force-includes, with its path inside the wheel."""
    config = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    assets: dict[str, str] = config["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    return assets


def require_files(names: set[str], distribution: Path) -> None:
    missing = EXPECTED_FILES - names
    if missing:
        raise SystemExit(f"{distribution.name} is missing: {', '.join(sorted(missing))}")


def require_license_contents(contents: dict[str, bytes], distribution: Path) -> None:
    for name in EXPECTED_FILES:
        source = Path(LICENSE_SOURCES[name]).read_bytes()
        if contents[name] != source:
            raise SystemExit(f"{distribution.name} has different {name} contents")


def require_metadata(contents: bytes, distribution: Path) -> None:
    metadata = Parser().parsestr(contents.decode("utf-8"))
    if any(
        classifier.casefold().startswith("private ::")
        for classifier in metadata.get_all("Classifier", [])
    ):
        raise SystemExit(f"{distribution.name} contains a private-only classifier")
    if metadata.get("License-Expression") != EXPECTED_LICENSE:
        raise SystemExit(f"{distribution.name} has no {EXPECTED_LICENSE} license expression")
    declared = {PurePosixPath(name).name for name in metadata.get_all("License-File", [])}
    missing = EXPECTED_FILES - declared
    if missing:
        raise SystemExit(f"{distribution.name} metadata omits: {', '.join(sorted(missing))}")


def check_wheel(distribution: Path) -> None:
    with zipfile.ZipFile(distribution) as archive:
        members = archive.namelist()
        require_files({PurePosixPath(name).name for name in members}, distribution)
        license_members = {PurePosixPath(name).name: name for name in members}
        require_license_contents(
            {name: archive.read(license_members[name]) for name in EXPECTED_FILES}, distribution
        )
        metadata_names = [name for name in members if name.endswith(".dist-info/METADATA")]
        if len(metadata_names) != 1:
            raise SystemExit(f"{distribution.name} must contain one wheel METADATA file")
        require_metadata(archive.read(metadata_names[0]), distribution)
        member_names = set(members)
        for folder, packaged in packaged_assets().items():
            for source in sorted(path for path in Path(folder).rglob("*") if path.is_file()):
                member = f"{packaged}/{source.relative_to(folder).as_posix()}"
                if member not in member_names or archive.read(member) != source.read_bytes():
                    raise SystemExit(f"{distribution.name} has different {source} contents")
        if "sandbox_benchmark_verifier/py.typed" not in members:
            raise SystemExit(f"{distribution.name} is missing py.typed")
        metadata = Parser().parsestr(archive.read(metadata_names[0]).decode("utf-8"))
        namespace = metadata["Name"].replace("-", "_")
        packages = {
            name.split("/", 1)[0] for name in members if "/" in name and ".dist-info/" not in name
        }
        if packages != {namespace}:
            raise SystemExit(f"{distribution.name} includes an unowned package namespace")


def without_source_stamps(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: without_source_stamps(item)
            for key, item in value.items()
            if key not in SOURCE_STAMPS
        }
    if isinstance(value, list):
        return [without_source_stamps(item) for item in value]
    return value


def check_installed_wheel(distribution: Path) -> None:
    """Install the wheel alone in a clean environment, outside the checkout, and build the text
    bundle from its packaged assets: it must equal the bundle the source tree builds."""
    with tempfile.TemporaryDirectory() as scratch:
        installed = Path(scratch) / "installed.json"
        subprocess.run(
            [
                "uv",
                "run",
                "--isolated",
                "--no-project",
                "--quiet",
                "--with",
                str(distribution.resolve()),
                "--directory",
                scratch,
                "python",
                "-I",
                "-c",
                INSTALLED_TEXT,
                str(installed),
            ],
            check=True,
        )
        tree = Path(scratch) / "tree.json"
        write_text_bundle(Path.cwd(), tree)
        built = [
            without_source_stamps(json.loads(path.read_text(encoding="utf-8")))
            for path in (installed, tree)
        ]
    if built[0] != built[1]:
        raise SystemExit(f"{distribution.name} installed builds a different text bundle")


def check_sdist(distribution: Path) -> None:
    with tarfile.open(distribution, "r:gz") as archive:
        members = archive.getmembers()
        require_files({PurePosixPath(item.name).name for item in members}, distribution)
        license_members = {PurePosixPath(item.name).name: item for item in members}
        license_contents = {}
        for name in EXPECTED_FILES:
            archived_file = archive.extractfile(license_members[name])
            if archived_file is None:
                raise SystemExit(f"{distribution.name} {name} is not a file")
            license_contents[name] = archived_file.read()
        require_license_contents(license_contents, distribution)
        metadata = [item for item in members if item.name.endswith("/PKG-INFO")]
        if len(metadata) != 1:
            raise SystemExit(f"{distribution.name} must contain one PKG-INFO file")
        contents = archive.extractfile(metadata[0])
        if contents is None:
            raise SystemExit(f"{distribution.name} PKG-INFO is not a file")
        require_metadata(contents.read(), distribution)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: check_distribution.py <build-directory>")
    output = Path(sys.argv[1])
    wheels = sorted(output.glob("*.whl"))
    sdists = sorted(output.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise SystemExit(f"expected one wheel and one sdist in {output}")
    check_wheel(wheels[0])
    check_sdist(sdists[0])
    check_installed_wheel(wheels[0])
    print(
        f"{wheels[0].name} and {sdists[0].name}: license files and assets present; "
        "the installed wheel builds the tree's text bundle"
    )


if __name__ == "__main__":
    main()
