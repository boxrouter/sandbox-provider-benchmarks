# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""The one YAML reader parses a text once and still gives each caller its own objects."""

import uuid

import pytest
import yaml
from pytest_mock import MockerFixture

from sandbox_benchmark_verifier.yaml_load import dump_yaml, load_yaml, load_yaml_all

pytestmark = pytest.mark.unit


def unique(text: str) -> str:
    """A text no other test parsed, so the first call in this test is the parse."""
    return f"# {uuid.uuid4()}\n{text}"


def test_a_text_is_parsed_once_and_each_call_gets_new_objects(mocker: MockerFixture) -> None:
    text = unique("a: [1, 2]\n---\nb: {c: 2026-11-05}\n")
    spy = mocker.spy(yaml, "load_all")
    first = list(load_yaml_all(text))
    second = list(load_yaml_all(text))
    assert first == second == list(yaml.load_all(text, Loader=yaml.SafeLoader))
    assert spy.call_count == 2  # one parse for both calls, one for the comparison
    first[0]["a"].append(3)
    assert list(load_yaml_all(text))[0] == {"a": [1, 2]}

    single = unique("a: [1, 2]\n")
    one = load_yaml(single)
    one["a"].append(3)
    assert load_yaml(single) == {"a": [1, 2]}


def test_anchors_stay_shared_within_a_document() -> None:
    document = load_yaml(unique("a: &x [1]\nb: *x\n"))
    assert document["a"] is document["b"]


def test_a_bad_stream_yields_its_good_documents_then_raises() -> None:
    documents = load_yaml_all(unique("a: 1\n---\nb: [\n"))
    assert next(documents) == {"a": 1}
    with pytest.raises(yaml.YAMLError):
        next(documents)
    with pytest.raises(yaml.YAMLError):
        load_yaml(unique("b: [\n"))


@pytest.mark.parametrize(
    "data",
    [{"a": 1}, {"a": "1"}, {"a": 1.0}, {"a": True}, {"a": None}, {"a": [1]}, {"a": (1,)}],
)
def test_a_dump_is_safe_dump_of_the_same_value(data: dict[str, object]) -> None:
    """The remembered dump tells apart values that print alike (1, "1", 1.0, True)."""
    for allow_unicode in (False, True):
        assert dump_yaml(data, allow_unicode=allow_unicode) == yaml.safe_dump(
            data, allow_unicode=allow_unicode
        )
