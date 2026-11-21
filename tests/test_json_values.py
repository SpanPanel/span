"""`json_values`: narrowing what was never typed, and answering typed shapes as JSON."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from typing import TypedDict

from homeassistant.helpers.json import JSONEncoder
import pytest

from custom_components.span_panel.json_values import (
    is_list,
    is_mapping,
    json_object,
    json_value,
)


class _Inner(TypedDict):
    ids: list[str]
    rating: float | None


class _Outer(TypedDict):
    name: str
    count: int
    enabled: bool
    inner: dict[str, _Inner]
    ratings: list[int] | None


def test_a_typed_shape_is_answered_as_the_json_it_holds() -> None:
    """Every field of every nested shape, as it is, with nothing left out."""
    shape: _Outer = {
        "name": "Kitchen",
        "count": 3,
        "enabled": False,
        "inner": {"circuit-1": {"ids": ["a", "b"], "rating": None}},
        "ratings": [20, 30],
    }

    assert json_object(shape) == {
        "name": "Kitchen",
        "count": 3,
        "enabled": False,
        "inner": {"circuit-1": {"ids": ["a", "b"], "rating": None}},
        "ratings": [20, 30],
    }


@pytest.mark.parametrize(
    "value",
    [datetime(2026, 1, 1, tzinfo=UTC), {"a", "b"}, b"bytes"],
    ids=["a-datetime", "a-set", "bytes"],
)
def test_a_value_json_cannot_carry_is_refused(value: object) -> None:
    """Named rather than passed on, at any depth: the shape that produced it is wrong."""
    with pytest.raises(TypeError, match=type(value).__name__):
        json_value({"outer": [{"inner": value}]})


def test_a_tuple_is_answered_as_an_array_as_home_assistants_encoder_writes_it() -> None:
    """A field typed with a tuple answers as an array, at any depth, as the service's encoder would.

    Fails when a tuple is refused, which mypy would let through and the
    service would only find at runtime.
    """
    shape: dict[str, tuple[int, ...] | tuple[tuple[str, int], ...]] = {
        "ratings": (20, 30),
        "pairs": (("a", 1), ("b", 2)),
    }

    answered = json_object(shape)

    assert answered == {"ratings": [20, 30], "pairs": [["a", 1], ["b", 2]]}
    assert answered == json.loads(JSONEncoder().encode(shape))


def test_a_key_that_is_not_a_string_is_refused() -> None:
    """A JSON object's keys are strings."""
    with pytest.raises(TypeError, match="key 1"):
        json_value({"outer": {1: "one"}})


def test_the_narrowing_answers_by_kind_alone() -> None:
    """A mapping and a list are recognised whatever they hold; a string is neither."""
    assert is_mapping({"a": 1}) and is_mapping({})
    assert is_list([1, "a"]) and is_list([])
    for value in ("text", 1, None, ("a",), {"a"}):
        assert not is_mapping(value)
        assert not is_list(value)
