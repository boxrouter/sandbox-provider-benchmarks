# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""One form for a machine size and "and" in a name.

The page contract owns the machine size template (``machine_size``): a size is drawn
"2 vCPU / 4 GiB". A label that types another form is a defect; the owner's template is the only
way to write one.
"""

import json
import re
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

from sandbox_benchmark_verifier.yaml_load import load_yaml

_NUMBER = r"\d+(?:\.\d+)?"
# A size written in any form: "2 vCPU / 4 GiB", "2 vCPU, 4 GiB", "2 vCPU and 4 GiB".
_SIZE = re.compile(rf"\b({_NUMBER})\s*vCPUs?\b\s*(?:,|/|and)?\s*(?:and\s+)?({_NUMBER})\s*GiB\b")
_AMPERSAND = re.compile(r"\s&\s")
PAGE = Path("contracts/text/methodology-page.json")


def _labels(rows: Sequence[Mapping[str, Any]]) -> Iterator[tuple[str, str]]:
    """Every label a names row shows: its name, label and selector label."""
    for row in rows:
        where = f"names {row['kind']}.{row['id']}"
        for field in ("name", "label"):
            if isinstance(row.get(field), str):
                yield f"{where}.{field}", row[field]
        selector = row.get("selector")
        if isinstance(selector, Mapping) and isinstance(selector.get("label"), str):
            yield f"{where}.selector.label", selector["label"]


def machine_size_text(template: str, vcpu: str, memory_gib: str) -> str:
    """A size drawn in the owner's template."""
    return template.replace("{vcpu}", vcpu).replace("{memory_gib}", memory_gib)


def machine_size_errors(texts: Mapping[str, str], template: str) -> list[str]:
    """Every size in a text that is not the owner's template filled in."""
    errors = []
    for where, text in texts.items():
        for match in _SIZE.finditer(text):
            expected = machine_size_text(template, match[1], match[2])
            if match[0] != expected:
                errors.append(f"{where}: machine size {match[0]!r} must read {expected!r}")
    return errors


def ampersand_errors(texts: Mapping[str, str]) -> list[str]:
    """A name joins words with "and", never "&"."""
    return [
        f"{where}: write 'and', not '&'" for where, text in texts.items() if _AMPERSAND.search(text)
    ]


def unit_form_errors(root: Path, rows: Sequence[Mapping[str, Any]]) -> list[str]:
    """The two forms over the names table, the page contract and the reader text."""
    page_path = root / PAGE
    if not page_path.is_file():
        return []
    page = json.loads(page_path.read_text(encoding="utf-8"))
    labels = dict(_labels(rows))
    sizes = {**labels, f"{PAGE.name}": json.dumps(page, ensure_ascii=False)}
    aliases = root / "names" / "aliases.yaml"
    if aliases.is_file():
        # A retired document's name is shown as typed where an older score version names it.
        retired = (load_yaml(aliases.read_text(encoding="utf-8")) or {}).get("retired_names", {})
        sizes.update((f"aliases retired_names.{key}", text) for key, text in retired.items())
    reader = root / "reader" / "methods.yaml"
    if reader.is_file():
        sizes[str(reader.relative_to(root))] = reader.read_text(encoding="utf-8")
    return [*machine_size_errors(sizes, str(page["machine_size"])), *ampersand_errors(labels)]
