"""Schema unit strings must be recognisable HA units."""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.const import (
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
)
import pytest
from span_panel_api.models import FieldMetadata

from tests.adapter_fixtures import schema_one_metadata, schema_zero_metadata

MetadataFn = Callable[[], dict[str, FieldMetadata]]

_KNOWN: set[str] = {
    *(u.value for u in UnitOfPower),
    *(u.value for u in UnitOfEnergy),
    *(u.value for u in UnitOfElectricCurrent),
    *(u.value for u in UnitOfElectricPotential),
    PERCENTAGE,
}

UNIT_TRANSLATIONS: dict[str, str] = {}
"""Schema unit string -> HA unit, for units the panel spells differently.

Empty today. An entry here is a deliberate statement that the panel's spelling
differs from HA's, not a licence to paper over a firmware bug.
"""


@pytest.mark.parametrize(
    ("adapter", "metadata_fn"),
    [("schema_0", schema_zero_metadata), ("schema_1", schema_one_metadata)],
)
def test_units_are_recognised(adapter: str, metadata_fn: MetadataFn) -> None:
    unknown = sorted(
        {
            entry.unit
            for entry in metadata_fn().values()
            if entry.resolved
            and entry.unit is not None
            and entry.unit not in _KNOWN
            and entry.unit not in UNIT_TRANSLATIONS
        }
    )
    assert not unknown, (
        f"{adapter} declares unit strings HA does not recognise: {unknown}. "
        "Add a UNIT_TRANSLATIONS entry, or fix the mapping — do not let this "
        "reach the unit check, where it becomes one Repair per sensor."
    )
