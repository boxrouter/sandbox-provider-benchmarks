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
    data = workload["data"]
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
