# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Reader text never shows an id: scans of the compiled bundle for raw and composite metric ids."""

import re
from collections.abc import Mapping
from typing import Any

from sandbox_benchmark_verifier.name_refs import reader_strings

_CODE_SPAN = re.compile(r"`[^`]*`")
_CODE_CONTENT = re.compile(r"`([^`]*)`")
_RAW_TOKEN = re.compile(r"(?<![\w./-])[a-z][a-z0-9]*(?:_[a-z0-9]+)+(?![\w./-])")


def raw_token_warnings(bundle: Mapping[str, Any]) -> list[str]:
    """Reader prose never shows an id: a direction id, a statistic id or any snake_case token."""
    return [
        f"raw id in reader text {where}: {token}"
        for identifier, versions in bundle.get("methodologies", {}).items()
        for version, row in versions.items()
        for field in ("reader_definition", "text")
        for key in ("headline", "notes", "interpretation", "panel")
        for where, text in reader_strings(
            (row.get(field) or {}).get(key), f"{identifier}@{version}.{field}.{key}"
        )
        for token in _RAW_TOKEN.findall(_CODE_SPAN.sub("", text))
    ]


def index_metric_id_warnings(bundle: Mapping[str, Any]) -> list[str]:
    """A composite metric is named by its display name; its id never reaches the page, even as code.

    Other metric ids may appear in code where prose names the recorded field (``burst_time``).
    """
    methodologies = bundle.get("methodologies", {})
    ids = {
        metric["name"]
        for versions in methodologies.values()
        for row in versions.values()
        if row.get("reader_definition", {}).get("category") == "index"
        for metric in row["reader_definition"].get("metrics", [])
        if metric.get("name")
    }
    sources = [
        ("content", {k: v for k, v in bundle["content_criteria"].items() if k != "conventions"}),
        ("reader", bundle["reader"]),
        *(
            (f"{identifier}@{version}.{field}", row.get(field))
            for identifier, versions in methodologies.items()
            for version, row in versions.items()
            for field in ("reader_definition", "text")
        ),
    ]
    return [
        f"index metric id in reader text {where}: {span}"
        for prefix, source in sources
        for where, text in reader_strings(source, prefix)
        for span in _CODE_CONTENT.findall(text)
        if span in ids
    ]
