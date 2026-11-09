# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""The workload a measured version pinned, read from its parameter file.

Reader pages and the workload subtitles read a parameter of the pinned workload
(``<document>.parameters.workload.<key>``) through this one function.
"""

import json
import shlex
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from sandbox_benchmark_verifier.versions import document_version

# The one parameter that differs between versions whose other parameters are the same.
JOB_HASH = "job_file_sha256"


def pinned_workload(
    root: Path, measured: Mapping[str, Any], documents: Mapping[tuple[str, str], Mapping[str, Any]]
) -> dict[str, Any]:
    """The experiment arguments a measured version pinned: the parameter file's data and options.

    A test sharing another test's trial reads that owner's pinned parameters. ``options`` holds the
    ``--name value`` pairs of the pinned command line.
    """
    shared = measured.get("procedure", {}).get("shared_trial")
    owner = documents[(shared["with"], shared["version"])] if shared else measured
    workload = _workload(root, owner)
    data = {**_added_later(root, owner, workload, documents), **workload["data"]}
    tokens = shlex.split(data["command"]) if isinstance(data.get("command"), str) else []
    options = {
        token[2:]: following if not following.startswith("-") else ""
        for token, following in zip(tokens, [*tokens[1:], ""], strict=False)
        if token.startswith("--")
    }
    return {**data, "options": options}


def _workload(root: Path, document: Mapping[str, Any]) -> dict[str, Any]:
    """The first workload of a document's parameter file."""
    path = root / str(document["parameters"]["path"]) / "workloads.json"
    return dict(json.loads(path.read_text(encoding="utf-8"))[0])


def _added_later(
    root: Path,
    owner: Mapping[str, Any],
    workload: Mapping[str, Any],
    documents: Mapping[tuple[str, str], Mapping[str, Any]],
) -> dict[str, Any]:
    """The parameters the owner's version does not have, from the oldest later version of the
    document that has each one.

    A key the owner has always keeps its own value. A later version supplies a key only when its
    workload is the owner's apart from added keys and the job file's hash, so the reader never
    shows a value the owner's version did not run with.
    """
    owned = workload["data"]
    later = sorted(
        (
            document
            for (identifier, _), document in documents.items()
            if identifier == owner["id"]
            and "parameters" in document
            and document_version(document) > document_version(owner)
        ),
        key=document_version,
    )
    supplied: dict[str, Any] = {}
    for document in later:
        candidate = _workload(root, document)
        added = {key: value for key, value in candidate["data"].items() if key not in owned}
        if not set(added) - set(supplied):
            continue
        same = all(
            candidate["data"].get(key) == value for key, value in owned.items() if key != JOB_HASH
        ) and {
            key: value for key, value in candidate.items() if key not in ("data", "version")
        } == {key: value for key, value in workload.items() if key not in ("data", "version")}
        if not same:
            raise ValueError(
                f"{owner['id']}@{owner['version']} and {document['id']}@{document['version']} "
                f"differ in more than added parameters and {JOB_HASH}"
            )
        supplied |= {key: value for key, value in added.items() if key not in supplied}
    return supplied
