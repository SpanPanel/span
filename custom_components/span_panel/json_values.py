"""Read JSON this integration did not type, and answer JSON from shapes it did, with no `Any`.

Two directions, one rule: a value's type is stated, never assumed.

**Reading.** A store's `async_load`, a form's input and a decoded file all
answer `object`, and `isinstance(value, dict)` narrows that to `dict[Any,
Any]`, which carries `Any` into every read of it. `is_mapping` and `is_list`
narrow to the read-only `Mapping[object, object]` and `Sequence[object]`
instead, so a reader built on them states the type of each part it keeps and
cannot write into what it was handed. `is_list` is a `TypeGuard` rather than
a `TypeIs`: narrowed to `list[object]`, a `list[str]` would accept any item;
narrowed by `TypeIs` to `Sequence[object]`, its false branch would claim the
value is no string and no tuple either.

**Answering.** A service response is `JsonObjectType`, and a `TypedDict` is
not one: it is a `Mapping[str, object]`, and `dict` and `list` are invariant,
so `list[int]` is not `list[JsonValueType]` however JSON its contents are.
`json_object` rebuilds a typed shape as JSON, checking every value on the way,
so a shape that grows a field is answered with it rather than through a second,
hand-kept list of fields that could leave it out.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TypeGuard, TypeIs

from homeassistant.util.json import JsonObjectType, JsonValueType


def is_mapping(value: object) -> TypeIs[Mapping[object, object]]:
    """Whether `value` is a mapping, narrowed to one whose keys and values are read as `object`."""
    return isinstance(value, Mapping)


def is_list(value: object) -> TypeGuard[Sequence[object]]:
    """Whether `value` is a list, narrowed to a read-only sequence whose items are read as `object`."""
    return isinstance(value, list)


def json_value(value: object) -> JsonValueType:
    """Return `value` as JSON: a scalar as it is, a mapping or a list rebuilt, item by item.

    Raises `TypeError` for anything else, and for a mapping key that is not a
    string, naming what it found: a value JSON cannot carry is a bug in the
    shape that produced it, and Home Assistant would refuse the response anyway.
    """
    if value is None or isinstance(value, (str, int, float)):
        return value
    if is_mapping(value):
        return json_object(_string_keyed(value))
    if is_list(value):
        return [json_value(item) for item in value]
    raise TypeError(f"{type(value).__name__} is not a JSON value")


def json_object(value: Mapping[str, object]) -> JsonObjectType:
    """Return a string-keyed mapping, a `TypedDict` included, as a JSON object; see `json_value`."""
    return {key: json_value(item) for key, item in value.items()}


def _string_keyed(value: Mapping[object, object]) -> dict[str, object]:
    """Return `value` with each key checked to be a string."""
    keyed: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise TypeError(f"{type(key).__name__} key {key!r} is not a JSON object key")
        keyed[key] = item
    return keyed
