"""Schema unit strings must be recognisable HA units."""

from __future__ import annotations

from homeassistant.const import (
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTime,
)

from tests.adapter_fixtures import schema_one_metadata

_KNOWN: set[str] = {
    *(u.value for u in UnitOfPower),
    *(u.value for u in UnitOfEnergy),
    *(u.value for u in UnitOfElectricCurrent),
    *(u.value for u in UnitOfElectricPotential),
    # `shed-forecast` declares its four estimates in `min`, which is
    # `UnitOfTime.MINUTES` — the first non-electrical quantity a SPAN panel
    # publishes, and the reason a time vocabulary belongs here at all.
    *(u.value for u in UnitOfTime),
    PERCENTAGE,
}

_MIN_SAMPLED_UNITS = 5
"""Floor on distinct unit strings the adapter must emit for the check to mean anything."""

UNIT_TRANSLATIONS: dict[str, str] = {}
"""Schema unit string -> HA unit, for units the panel spells differently.

Empty today. An entry here is a deliberate statement that the panel's spelling
differs from HA's, not a licence to paper over a firmware bug.
"""


def test_units_are_recognised() -> None:
    sampled = {e.unit for e in schema_one_metadata().values() if e.resolved and e.unit is not None}

    # The check below is monotone: it passes for any subset, including the empty
    # one. Pin the sample so a fixture path change, a fixture regenerated without
    # units, or an adapter that stops populating `unit` cannot retire it silently.
    # The adapter emits 7 distinct strings over 62 entries, so 5 leaves room for
    # two units to be legitimately retired and still fails on a collapse.
    assert len(sampled) >= _MIN_SAMPLED_UNITS, (
        f"the adapter emitted only {sorted(sampled)} — the vocabulary check has nothing to check"
    )

    unknown = sorted(
        unit for unit in sampled if unit not in _KNOWN and unit not in UNIT_TRANSLATIONS
    )
    assert not unknown, (
        f"the adapter declares unit strings HA does not recognise: {unknown}. "
        "Add a UNIT_TRANSLATIONS entry, or fix the mapping — do not let this "
        "reach the unit check, where it becomes one Repair per sensor."
    )
