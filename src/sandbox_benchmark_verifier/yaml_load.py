# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""The one YAML reader (libyaml's C loader when installed) and the text compiler's dumps.

A text is parsed once per process and its result kept pickled: a compile reads the same files
several times, and a test's copied checkout holds the same files again. Each call unpickles new
objects, equal to a fresh parse (same types, same shared anchors), so callers may change them.
"""

import pickle
from collections.abc import Iterator, Mapping
from functools import lru_cache
from pathlib import Path
from typing import IO, Any

import yaml

_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def load_yaml(source: str | IO[str]) -> Any:
    """Parse one YAML document as ``yaml.safe_load`` does."""
    if isinstance(source, str):
        return pickle.loads(_parsed(source))
    return yaml.load(source, Loader=_LOADER)


def load_yaml_all(source: str | IO[str]) -> Iterator[Any]:
    """Parse every document in a stream as ``yaml.safe_load_all`` does."""
    if isinstance(source, str):
        try:
            documents = _parsed_all(source)
        except Exception:  # pylint: disable=broad-exception-caught
            # The lazy parse yields the documents before the bad one, then raises the same error.
            return yaml.load_all(source, Loader=_LOADER)
        return iter(pickle.loads(documents))
    return yaml.load_all(source, Loader=_LOADER)


def read_mapping(path: Path) -> dict[str, Any]:
    """Parse a YAML file that holds one mapping, such as a names table."""
    document = load_yaml(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"{path.name}: expected a mapping")
    return document


@lru_cache(maxsize=512)
def _parsed(text: str) -> bytes:
    return pickle.dumps(yaml.load(text, Loader=_LOADER))


@lru_cache(maxsize=512)
def _parsed_all(text: str) -> bytes:
    return pickle.dumps(list(yaml.load_all(text, Loader=_LOADER)))


_DUMPS: dict[str, str] = {}


def dump_yaml(data: Mapping[str, Any], *, allow_unicode: bool = False) -> str:
    """``yaml.safe_dump(dict(data), allow_unicode=...)``, dumped once per distinct value.

    A text compile dumps each reader entry about ten times (1,889 dumps of 183 entries), and the
    emitter is pure Python. The key is the value's ``repr``, which tells apart every type
    ``safe_dump`` writes (str, int, float, bool, None, date, list, tuple, dict).
    """
    value = dict(data)
    key = repr((allow_unicode, value))
    text = _DUMPS.get(key)
    if text is None:
        if len(_DUMPS) >= 4096:
            _DUMPS.clear()
        text = _DUMPS[key] = yaml.safe_dump(value, allow_unicode=allow_unicode)
    return text
