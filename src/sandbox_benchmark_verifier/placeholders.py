# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Parse and resolve methodology text placeholders against canonical source data."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sandbox_benchmark_verifier.conventions import load_conventions
from sandbox_benchmark_verifier.image_sizes import byte_size_text

_PLACEHOLDER = re.compile(r"\{([a-z]+):([^{}]*)\}")
# A reference to a shared reader block: {param:reader.common.blocks.<scope>.<name>}.
BLOCK = re.compile(r"\{param:reader\.common\.blocks\.([a-z0-9-]+)\.([a-z0-9_]+)\}")
_NAME_TARGET = re.compile(r"[a-z0-9][a-z0-9._-]*\Z")
_METRIC_TARGET = re.compile(r"[a-z][a-z0-9_-]*\Z")
_PARAM_PATH = r"[a-z0-9][a-z0-9._-]*\.[A-Za-z_][A-Za-z0-9_.-]*"
_PARAM_TARGET = re.compile(_PARAM_PATH + r"\Z")
_SHA256 = r"[0-9a-f]{64}"
_GIT_COMMIT = r"[0-9a-f]{40}"
_REPOSITORY = r"(?:[a-z]+:)?[\w.-]+/[\w.-]+"
_TARGETS = {
    "name": _NAME_TARGET,
    "workloads": _NAME_TARGET,
    "units": _NAME_TARGET,
    "quantity": _METRIC_TARGET,
    "param": _PARAM_TARGET,
    "commit": re.compile(
        rf"(?:[A-Za-z0-9-]+\|)?(?:{_REPOSITORY}|{_PARAM_PATH})\|(?:{_PARAM_PATH}|{_GIT_COMMIT})\Z"
    ),
    "hash": re.compile(rf"[A-Za-z0-9-]+\|{_PARAM_PATH}\Z"),
    "count": _PARAM_TARGET,
    "countword": _PARAM_TARGET,
    "version": _PARAM_TARGET,
    "duration": _PARAM_TARGET,
    "percent": _PARAM_TARGET,
    "size": _PARAM_TARGET,
    "imagesize": _PARAM_TARGET,
    "machine": _PARAM_TARGET,
    "product": re.compile(rf"{_PARAM_PATH}(?:\*{_PARAM_PATH})+\Z"),
    "unit": _METRIC_TARGET,
    "direction": _METRIC_TARGET,
    "axis": _NAME_TARGET,
}
PLACEHOLDER_OPENERS = tuple(f"{{{kind}:" for kind in _TARGETS)


class PlaceholderError(ValueError):
    """A placeholder is malformed, unknown, or has no unique source value."""


@dataclass(frozen=True)
class Placeholder:
    """One parsed placeholder in a text template."""

    kind: str
    target: str
    start: int
    end: int


def parse_placeholders(
    template: str, *, allow_literal_braces: bool = False
) -> tuple[Placeholder, ...]:
    """Parse placeholders, optionally preserving unrelated literal braces."""
    found: list[Placeholder] = []
    cursor = 0
    for match in _PLACEHOLDER.finditer(template):
        kind, target = match.groups()
        if allow_literal_braces and kind not in _TARGETS:
            continue
        _check_literal(template[cursor : match.start()], cursor, allow_literal_braces)
        if kind not in _TARGETS or _TARGETS[kind].fullmatch(target) is None:
            raise PlaceholderError(f"unknown or malformed placeholder {{{kind}:{target}}}")
        found.append(Placeholder(kind, target, match.start(), match.end()))
        cursor = match.end()
    _check_literal(template[cursor:], cursor, allow_literal_braces)
    return tuple(found)


def _check_literal(text: str, offset: int, allow_literal_braces: bool) -> None:
    # Every opener starts with "{", so text without one holds none (this runs for every literal).
    if (not allow_literal_braces and ("{" in text or "}" in text)) or (
        "{" in text and any(opener in text for opener in PLACEHOLDER_OPENERS)
    ):
        raise PlaceholderError(f"malformed placeholder at character {offset}")


def _path_value(document: Mapping[str, Any], path: str) -> Any:
    value: Any = document
    for part in path.split("."):
        if isinstance(value, Mapping) and part in value:
            value = value[part]
        elif isinstance(value, Sequence) and not isinstance(value, str) and part.isdecimal():
            index = int(part)
            if index >= len(value):
                raise KeyError(path)
            value = value[index]
        else:
            raise KeyError(path)
    return value


def _param_raw(target: str, documents: Mapping[str, Mapping[str, Any]]) -> Any:
    # The owner is the longest document id the target continues with a ".": each "." from the right.
    dot = target.rfind(".")
    while dot >= 0 and target[:dot] not in documents:
        dot = target.rfind(".", 0, dot)
    if dot < 0:
        raise PlaceholderError(f"unknown document in parameter placeholder {target!r}")
    owner = target[:dot]
    path = target[dot + 1 :]
    try:
        return _path_value(documents[owner], path)
    except KeyError as error:
        raise PlaceholderError(f"unknown parameter path {target!r}") from error


def _param_value(target: str, documents: Mapping[str, Mapping[str, Any]]) -> Any:
    value = _param_raw(target, documents)
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise PlaceholderError(f"parameter {target!r} is not a scalar text value")
    return value


_SHORT_COMMIT = 8
# A link as reader text carries it: the word a page draws, then where it goes, a SHA-256 to copy
# (`hash:<hex>`) or a commit's page. Every reader of the marks reads this one pattern.
LINK_MARK = re.compile(r"\[([^\]]*)\]\((?:hash:[0-9a-f]+|https://[^\s)]+)\)")


def _marked(label: str, value: str, written: str, target: Callable[[str], str]) -> str:
    """``value`` with its hash drawn as a link on ``label``: the value is the hash, or a text that
    writes it after the label ("commit <hash>", "image@sha256:<hash>")."""
    if re.fullmatch(written, value):
        return f"[{label}]({target(value)})"
    marked, found = re.subn(
        rf"(?<![\w-]){re.escape(label)}[ :]({written})(?![\w-])",
        lambda match: f"[{label}]({target(match[1])})",
        value,
    )
    if found != 1:
        raise PlaceholderError(f"{value!r} does not write one hash after {label!r}")
    return marked


def mark_sha256(label: str, value: str) -> str:
    """A SHA-256 as reader text carries it: the page draws ``label`` as the link that copies it."""
    return _marked(label, value, _SHA256, lambda digest: f"hash:{digest}")


def commit_url(repository: str, commit: str, urls: Mapping[str, str]) -> str:
    """A commit's page: ``urls`` holds each host's pattern; a repository names a host other than
    the default as a prefix ("huggingface:owner/name")."""
    host, _, name = repository.rpartition(":")
    return urls[host or "github"].replace("{repository}", name).replace("{commit}", commit)


def _text_param(source: str, documents: Mapping[str, Mapping[str, Any]]) -> str:
    value = _param_raw(source, documents)
    if not isinstance(value, str):
        raise PlaceholderError(f"parameter {source!r} is not a text value")
    return value


def _hash_text(target: str, documents: Mapping[str, Mapping[str, Any]]) -> str:
    """{hash:<word>|<source>}: a SHA-256 parameter, or a text writing one, linked on its word."""
    label, source = target.split("|")
    return mark_sha256(label, _text_param(source, documents))


def _commit_text(target: str, documents: Mapping[str, Mapping[str, Any]]) -> str:
    """{commit:<word>|<repository>|<source>}: a Git commit, or a text that writes one after the
    word, linked on the word to the commit's page. {commit:<repository>|<source>}: the commit in a
    value that holds one ("image:<commit>"), linked on its short form ("d7a5583c")."""
    *words, repository, source = target.split("|")
    name = repository if "/" in repository else _text_param(repository, documents)
    if not re.fullmatch(_REPOSITORY, name):
        raise PlaceholderError(f"{name!r} is not a repository")
    value = source if re.fullmatch(_GIT_COMMIT, source) else _text_param(source, documents)
    urls = _methodology_page(documents)["hash_link"]["commit_urls"]
    if words:
        return _marked(words[0], value, _GIT_COMMIT, lambda commit: commit_url(name, commit, urls))
    found = re.findall(rf"(?<![0-9a-f]){_GIT_COMMIT}(?![0-9a-f])", value)
    if len(found) != 1:
        raise PlaceholderError(f"{value!r} does not hold one Git commit")
    return f"[`{found[0][:_SHORT_COMMIT]}`]({commit_url(name, found[0], urls)})"


_FIO_SIZE = re.compile(r"(\d+)([kmgt])", re.IGNORECASE)


def fio_size_bytes(value: Any) -> int | None:
    """The bytes of a size written as fio writes it ("4k", "1m"; binary multiples), else None."""
    if not isinstance(value, str) or not (match := _FIO_SIZE.fullmatch(value.strip())):
        return None
    return int(match.group(1)) << (10 * ("kmgt".index(match.group(2).lower()) + 1))


def _number(target: str, documents: Mapping[str, Mapping[str, Any]]) -> int | float:
    value = _param_raw(target, documents)
    if (bytes_ := fio_size_bytes(value)) is not None:
        return bytes_
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PlaceholderError(f"parameter {target!r} is not a number")
    return value


def seconds_text(seconds: int | float) -> str:
    """The one rule for a duration in reader text: up to 60 s in seconds, then whole hours or
    minutes when the value is a multiple of them ("60 s", "15 min", "2 h"), else seconds."""
    whole = int(seconds)
    if whole != seconds or whole <= 60 or whole % 60:
        return f"{seconds:g} s"
    return f"{whole // 3600} h" if whole % 3600 == 0 else f"{whole // 60} min"


def percent_text(fraction: int | float) -> str:
    """A fraction as a percentage in reader text ("0.2" is "20%", "0.075" is "7.5%")."""
    return f"{round(fraction * 100, 6):g}%"


def size_text(size: int | float) -> str:
    """The one rule for a size in reader text, read from the conventions' presentation.size_text:
    binary units, the largest that divides the byte count exactly ("4 KiB", "1 GiB"); a count no
    unit divides stays in bytes ("1,000 bytes")."""
    rule = load_conventions()["presentation"]["size_text"]
    whole = int(size)
    if whole != size or whole < 0:
        raise PlaceholderError(f"{size!r} is not a whole number of bytes")
    unit, divisor = rule["below_unit"]["one" if whole == 1 else "many"], 1
    for name in rule["units"]:
        if whole and whole % (divisor * rule["base"]) == 0:
            divisor *= rule["base"]
            unit = name
    return f"{whole // divisor:,} {unit}"


def _methodology_page(documents: Mapping[str, Mapping[str, Any]]) -> Mapping[str, Any]:
    """The methodology page contract, which owns the machine size template."""
    try:
        page: Mapping[str, Any] = documents["content"]["methodology_page"]
    except KeyError as error:
        raise PlaceholderError(
            "the methodology page contract is not among the documents"
        ) from error
    return page


def machine_text(target: str, documents: Mapping[str, Mapping[str, Any]]) -> str:
    """A machine size ("2 vCPU / 4 GiB") drawn in the one template the page contract owns."""
    size = _param_raw(target, documents)
    if not isinstance(size, Mapping) or not {"vcpu", "memory_gib"} <= size.keys():
        raise PlaceholderError(f"parameter {target!r} is not a machine size")
    template = str(_methodology_page(documents)["machine_size"])
    return template.replace("{vcpu}", f"{size['vcpu']:g}").replace(
        "{memory_gib}", f"{size['memory_gib']:g}"
    )


def _count(target: str, documents: Mapping[str, Mapping[str, Any]]) -> int:
    """The number of items in a list (or entries in a table) parameter."""
    value = _param_raw(target, documents)
    if isinstance(value, str) or not isinstance(value, (Sequence, Mapping)):
        raise PlaceholderError(f"parameter {target!r} is not a list")
    return len(value)


# The number words a count is written with in a sentence ("the four variants"); a larger count is
# written in digits through {count:...}. "one" is not here: a count of one is not a plural.
NUMBER_WORDS = (
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
)


def count_word(target: str, documents: Mapping[str, Mapping[str, Any]]) -> str:
    """{countword:<list>}: the number of items in a list as a word ("four")."""
    count = _count(target, documents)
    if not 2 <= count < 2 + len(NUMBER_WORDS):
        raise PlaceholderError(f"{target!r} holds {count} items; write {{count:...}} in digits")
    return NUMBER_WORDS[count - 2]


_VERSION = re.compile(r"(?<![\w.])\d+\.\d+(?:\.\d+)?(?![\w.])")


def version_text(target: str, documents: Mapping[str, Mapping[str, Any]]) -> str:
    """{version:<text>}: the one version a text parameter holds (8.3.4 in "vite 8.3.4")."""
    found = _VERSION.findall(_text_param(target, documents))
    if len(found) != 1:
        raise PlaceholderError(f"{target!r} does not hold one version")
    return str(found[0])


def _product(target: str, documents: Mapping[str, Mapping[str, Any]]) -> str:
    """The product of whole-number parameters, with thousands separators ("10,000")."""
    result: int | float = 1
    for factor in target.split("*"):
        result *= _number(factor, documents)
    return f"{result:,}" if float(result).is_integer() else str(result)


def _composite_inputs(target: str, documents: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """The workload ids of a composite or one of its scopes, in the composite's member order."""
    owners = [
        identifier
        for identifier in documents
        if target == identifier or target.startswith(identifier + ".")
    ]
    if not owners:
        raise PlaceholderError(f"unknown composite {target!r}")
    owner = max(owners, key=len)
    composition = documents[owner].get("composition", {})
    scope = target[len(owner) :].removeprefix(".")
    if scope:
        selected = [item for item in composition.get("scopes", []) if item["id"] == scope]
        if len(selected) != 1:
            raise PlaceholderError(f"unknown composite scope {target!r}")
        inputs = selected[0]["inputs"]
    else:
        inputs = composition.get("inputs", [])
    if not inputs:
        raise PlaceholderError(f"empty composite scope {target!r}")
    return [str(item["id"] if isinstance(item, Mapping) else item) for item in inputs]


def _list_text(labels: Sequence[str], conjunction: str = "and") -> str:
    """The list rule of reader text: "a", "a and b", "a, b and c"; "or" for a choice."""
    return (
        labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + f" {conjunction} " + labels[-1]
    )


def _workload_names(
    target: str, names: Mapping[str, str], documents: Mapping[str, Mapping[str, Any]]
) -> str:
    labels = []
    for identifier in _composite_inputs(target, documents):
        key = f"document.{identifier}"
        if key not in names:
            raise PlaceholderError(f"unknown workload name {identifier!r}")
        labels.append(names[key])
    return _list_text(labels)


def _workload_units(
    target: str, names: Mapping[str, str], documents: Mapping[str, Mapping[str, Any]]
) -> str:
    """A composite's distinct per-unit names in member order ("trace, task or job")."""
    labels = []
    for identifier in _composite_inputs(target, documents):
        key = f"unit.{identifier}"
        if key not in names:
            raise PlaceholderError(f"the names table gives workload {identifier!r} no unit")
        labels.append(names[key])
    return _list_text(list(dict.fromkeys(labels)), "or")


def _metric_value(metric_id: str, field: str, documents: Mapping[str, Mapping[str, Any]]) -> str:
    values: set[str] = set()
    for document in documents.values():
        metrics = document.get("metrics", [])
        if not isinstance(metrics, list):
            continue
        for metric in metrics:
            if isinstance(metric, Mapping) and metric.get("name") == metric_id:
                value = metric.get(field)
                if isinstance(value, str) and value:
                    values.add(value)
    if not values:
        raise PlaceholderError(f"unknown {field} for metric {metric_id!r}")
    if len(values) != 1:
        raise PlaceholderError(f"ambiguous {field} for metric {metric_id!r}")
    return next(iter(values))


def _axis_name(
    index_id: str, names: Mapping[str, str], documents: Mapping[str, Mapping[str, Any]]
) -> str:
    """The name of the chart axis that plots an index; the document's `plots` owns which one."""
    sides: set[str] = set()
    for document in documents.values():
        composition = document.get("composition")
        plots = composition.get("plots") if isinstance(composition, Mapping) else None
        if not isinstance(plots, Mapping):
            continue
        sides.update(
            side
            for side in ("x", "y")
            if isinstance(plots.get(side), Mapping) and plots[side].get("id") == index_id
        )
    if len(sides) != 1:
        raise PlaceholderError(f"no single chart axis plots {index_id!r}")
    return names[f"axis.{next(iter(sides))}"].lower()


def resolve_template(
    template: str,
    *,
    names: Mapping[str, str],
    documents: Mapping[str, Mapping[str, Any]],
    directions: Mapping[str, str] | None = None,
) -> str:
    """Recursively resolve source references; reject unknown targets and cycles.

    Consumers render the resolved release value without re-resolving its template.
    """
    return _resolve_template(template, names, documents, (), directions or {})


def _placeholder_value(
    placeholder: Placeholder,
    names: Mapping[str, str],
    documents: Mapping[str, Mapping[str, Any]],
    directions: Mapping[str, str],
) -> Any:
    """The text one placeholder stands for, before its own references are resolved."""
    kind, target = placeholder.kind, placeholder.target
    if kind == "name":
        if target not in names:
            raise PlaceholderError(f"unknown canonical name {target!r}")
        return names[target]
    simple: dict[str, Callable[[], Any]] = {
        "workloads": lambda: _workload_names(target, names, documents),
        "units": lambda: _workload_units(target, names, documents),
        "count": lambda: _count(target, documents),
        "countword": lambda: count_word(target, documents),
        "version": lambda: version_text(target, documents),
        "duration": lambda: seconds_text(_number(target, documents)),
        "percent": lambda: percent_text(_number(target, documents)),
        "size": lambda: size_text(_number(target, documents)),
        "imagesize": lambda: byte_size_text(int(_number(target, documents))),
        "machine": lambda: machine_text(target, documents),
        "commit": lambda: _commit_text(target, documents),
        "hash": lambda: _hash_text(target, documents),
        "product": lambda: _product(target, documents),
    }
    if kind in simple:
        return simple[kind]()
    if kind == "axis":
        return _axis_name(target, names, documents)
    if kind == "param":
        value = _param_value(target, documents)
        # A statistic is read by its name, never by its id.
        if target.endswith(".statistics.primary") and f"statistic.{value}" in names:
            return names[f"statistic.{value}"].lower()
        return value
    value = _metric_value(target, kind, documents)
    return directions.get(value, value) if kind == "direction" else value


def _resolve_template(
    template: str,
    names: Mapping[str, str],
    documents: Mapping[str, Mapping[str, Any]],
    active: tuple[tuple[str, str], ...],
    directions: Mapping[str, str],
) -> str:
    if "{" not in template:  # no placeholder: the template is its own text
        return template
    output: list[str] = []
    cursor = 0
    for placeholder in parse_placeholders(template, allow_literal_braces=True):
        output.append(template[cursor : placeholder.start])
        reference = (placeholder.kind, placeholder.target)
        if reference in active:
            chain = " -> ".join(f"{kind}:{target}" for kind, target in (*active, reference))
            raise PlaceholderError(f"cyclic placeholder reference: {chain}")
        value = _placeholder_value(placeholder, names, documents, directions)
        output.append(
            _resolve_template(str(value), names, documents, (*active, reference), directions)
        )
        cursor = placeholder.end
    output.append(template[cursor:])
    return "".join(output)


def with_blocks(entry: Any, blocks: Mapping[str, Mapping[str, str]]) -> Any:
    """The entry with each reference to a shared block replaced by the block's text.

    A block's ``{param:measured.<path>}`` then reads the definition of the test that references
    it, once per measured version, as the test's own wording does.
    """
    if isinstance(entry, str):
        return BLOCK.sub(lambda match: blocks.get(match[1], {}).get(match[2], match[0]), entry)
    if isinstance(entry, Mapping):
        return {key: with_blocks(item, blocks) for key, item in entry.items()}
    if isinstance(entry, list):
        return [with_blocks(item, blocks) for item in entry]
    return entry
