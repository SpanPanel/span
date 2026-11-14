"""What schema validation finds on every capture, pinned so a reclassification cannot move it.

`evaluate_field_metadata` turns the adapter's metadata into the findings that
Repairs are raised from: an unresolved field becomes a `schema_field_unresolved`
Repair, and a unit mismatch a `schema_unit_mismatch` one. Which fields it asks
about is decided by the classification in `field_paths`, so a change to that
classification is a runtime change on a panel exactly when it moves one of
these findings.

The record below was taken from every replay before the classification
collapsed onto the parent/child adapter alone, and the collapse left it
unchanged. A diff to it is a change to what a supported panel is told, so it
needs a reason of its own rather than an update to match.

`discovered` is not recorded entry by entry. It is the panel's own report,
split off by `partition` before any question of classification is asked, so the
only way a classification change could move it is by filtering it, and that is
what is pinned instead.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Final

import pytest
from span_panel_api.models import FieldMetadata

from custom_components.span_panel.field_paths import RESIDUAL_EXEMPT_PATHS, Producibility
from custom_components.span_panel.schema_validation import (
    SchemaFindings,
    UnitMismatch,
    evaluate_field_metadata,
    partition,
)
from custom_components.span_panel.sensor_definitions import sensor_descriptions_by_field_path

from .captures_replay import replay
from .test_expected_entities import REPLAYS, Replay, _ids


@dataclass(frozen=True, slots=True)
class _Recorded:
    """The classification-dependent part of one replay's findings."""

    unresolved: frozenset[str] = frozenset()
    unit_mismatches: tuple[UnitMismatch, ...] = ()
    unread: frozenset[str] = frozenset()


_EVERY_FIELD_RESOLVED: Final = _Recorded(unread=frozenset({"circuit.is_240v"}))
"""Every current capture: nothing unresolved, no unit disagreement, one field produced and unread."""

_RECORDED: Final[Mapping[str, _Recorded]] = {
    "main32_r202633": _EVERY_FIELD_RESOLVED,
    "main32_r202639": _EVERY_FIELD_RESOLVED,
    "r202639-a": _EVERY_FIELD_RESOLVED,
    "r202639-b": _EVERY_FIELD_RESOLVED,
    "r202639-c": _EVERY_FIELD_RESOLVED,
    "r202639-d": _EVERY_FIELD_RESOLVED,
    "r202639-e": _EVERY_FIELD_RESOLVED,
    "main32_r202639-no-battery": _EVERY_FIELD_RESOLVED,
    "main32_r202639-battery-unvalued": _EVERY_FIELD_RESOLVED,
    "main32_r202639-unpublished-readings": _EVERY_FIELD_RESOLVED,
}
"""What each replay's validation found, by the stem its expected-entity fixture is filed under."""

KEPT_OUT_OF_VALIDATION: Final = (
    "circuit.is_user_controllable",
    "evse.charge_current_ceiling_a",
)
"""Fields the adapter carries a row for, which validation deliberately never asks about.

Written out rather than read from `RESIDUAL_EXEMPT_PATHS`, because a path
promoted out of that map would leave the list along with it, and the check
below would pass for the very change it exists to catch.

`circuit.is_user_controllable` gates a circuit's controls, and
`evse.charge_current_ceiling_a` bounds the charge-limit number. Neither is an
entity's reading. Validating either would turn a dropped property into a Repair
on a panel that is otherwise healthy.
"""


def _metadata(item: Replay) -> dict[str, FieldMetadata]:
    return replay(item.tree()).build_field_metadata()


def _evaluated(metadata: dict[str, FieldMetadata]) -> SchemaFindings:
    """Validate the way the coordinator does, with the sensors' declared units."""
    return evaluate_field_metadata(metadata, sensor_descriptions_by_field_path())


def test_every_replay_has_a_record() -> None:
    """A replay without a record would be validated against nothing."""
    assert set(_RECORDED) == set(_ids(REPLAYS))


@pytest.mark.parametrize("item", REPLAYS, ids=_ids(REPLAYS))
def test_no_new_schema_repair_on_parent_child_captures(item: Replay) -> None:
    """Each replay's validation finds exactly what it found before the collapse."""
    metadata = _metadata(item)
    findings = _evaluated(metadata)

    assert (
        _Recorded(
            unresolved=findings.unresolved,
            unit_mismatches=findings.unit_mismatches,
            unread=findings.unread,
        )
        == _RECORDED[item.stem]
    )
    assert findings.discovered == partition(metadata)[1]


@pytest.mark.parametrize("item", REPLAYS, ids=_ids(REPLAYS))
def test_a_field_kept_out_of_validation_raises_no_repair_when_its_property_goes(
    item: Replay,
) -> None:
    """A panel that stops declaring one of these properties is told nothing new.

    Each row the capture carries is turned into the adapter's "node present,
    property missing" answer. If validation asked about any of them, that row
    would become a `schema_field_unresolved` Repair here.
    """
    metadata = _metadata(item)
    for path in KEPT_OUT_OF_VALIDATION:
        if (entry := metadata.get(path)) is not None:
            metadata[path] = replace(entry, resolved=False)

    assert _evaluated(metadata).unresolved == _RECORDED[item.stem].unresolved


def test_the_fields_kept_out_of_validation_are_the_ones_annotated_so() -> None:
    """`MAPPED_UNVALIDATED` cannot grow without a reason here, nor lose a member unnoticed.

    A path annotated so and missing from the list above would be kept from
    validation with no reason written down; a path on the list and annotated
    otherwise has been promoted, or demoted, past this check.
    """
    annotated = {
        path
        for path, producibility in RESIDUAL_EXEMPT_PATHS.items()
        if producibility is Producibility.MAPPED_UNVALIDATED
    }

    assert annotated == set(KEPT_OUT_OF_VALIDATION)


def test_every_field_kept_out_of_validation_is_on_some_capture() -> None:
    """Without a row on some capture, the check above would hold for a field it never tested."""
    carried = {path for item in REPLAYS for path in _metadata(item)}

    assert set(KEPT_OUT_OF_VALIDATION) <= carried
