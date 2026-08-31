"""User-supplied metadata for adopted entities.

Adopted entities arrive with deliberately minimal metadata because the
integration refuses to guess. A user who owns the vendor device is not
guessing: this module stores their assertions -- `state_class`,
`device_class`, and prominence -- keyed by a scope-prefixed wire address,
validates them against what the wire actually declares, and hands the
platforms fully-formed entity descriptions at construction.

**This is the one module allowed to spell `state_class`.** `adoption.py` and
`extension.py` carry AST guards asserting the token never appears there; the
description helpers below are how a curated record becomes an entity
description without either module naming the thing it must not set.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING, Final, TypedDict

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntityDescription,
    SensorStateClass,
)

# `homeassistant.components.sensor` re-exports this at runtime but leaves it out
# of its `__all__`, so the package-level import is an `attr-defined` error under
# mypy. `.const` is where it is actually defined and is the path that type-checks.
from homeassistant.components.sensor.const import DEVICE_CLASS_UNITS
from homeassistant.const import EntityCategory, Platform
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .util import NUMERIC_DATATYPES

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

PROMOTED: Final = "none"
"""The one storable `entity_category` value: promoted out of diagnostics."""


@dataclass(frozen=True, slots=True)
class RowContext:
    """What one adopted row declares, as far as validation needs it."""

    platform: Platform
    datatype: str
    unit: str | None


@dataclass(frozen=True, slots=True)
class CurationRecord:
    """One row's user-asserted metadata. Absence of a field means default."""

    state_class: SensorStateClass | None = None
    device_class: str | None = None
    promote: bool = False


class CurationError(Exception):
    """A record that cannot be stored, with a websocket-ready error code."""

    def __init__(self, code: str, message: str) -> None:
        """Carry a stable code beside the message, because the two have different readers.

        The message says what is wrong in English and is for a log; the code is
        what the websocket hands the frontend, so it has to survive rewording.
        """
        super().__init__(message)
        self.code = code


def _validate_state_class(value: object, context: RowContext) -> SensorStateClass:
    if context.platform is not Platform.SENSOR or context.datatype not in NUMERIC_DATATYPES:
        raise CurationError(
            "invalid_state_class",
            f"a state class needs a numeric sensor row, not {context.platform.value}"
            f"/{context.datatype}",
        )
    try:
        return SensorStateClass(str(value))
    except ValueError as err:
        raise CurationError("invalid_state_class", f"unknown state class {value!r}") from err


def _validate_device_class(value: object, context: RowContext) -> str:
    if context.platform is Platform.BINARY_SENSOR:
        try:
            return BinarySensorDeviceClass(str(value)).value
        except ValueError as err:
            raise CurationError(
                "invalid_device_class", f"unknown binary sensor device class {value!r}"
            ) from err
    if context.platform is not Platform.SENSOR:
        raise CurationError(
            "invalid_field_for_platform",
            "a control row accepts prominence only, not a device class",
        )
    try:
        device_class = SensorDeviceClass(str(value))
    except ValueError as err:
        raise CurationError("invalid_device_class", f"unknown device class {value!r}") from err
    constrained = DEVICE_CLASS_UNITS.get(device_class)
    if constrained is not None and context.unit not in constrained:
        raise CurationError(
            "incompatible_device_class",
            f"{device_class.value} does not admit the declared unit {context.unit!r}",
        )
    return device_class.value


def validate_record(raw: Mapping[str, object], context: RowContext) -> CurationRecord:
    """Turn a websocket record payload into a `CurationRecord`, or refuse it.

    Refuse rather than warn: a stored record is applied unattended at every
    future setup. Cross-field checks only -- field shape and enum membership
    are already constrained in the websocket schema, and are re-checked here
    because the same validator also re-runs at construction (see `sanitise`).
    """
    state_class: SensorStateClass | None = None
    device_class: str | None = None
    promote = False
    if "state_class" in raw:
        if context.platform not in (Platform.SENSOR, Platform.BINARY_SENSOR):
            raise CurationError(
                "invalid_field_for_platform", "a control row accepts prominence only"
            )
        state_class = _validate_state_class(raw["state_class"], context)
    if "device_class" in raw:
        device_class = _validate_device_class(raw["device_class"], context)
    if "entity_category" in raw:
        if raw["entity_category"] != PROMOTED:
            raise CurationError("invalid_entity_category", f"only {PROMOTED!r} is storable")
        promote = True
    return CurationRecord(state_class=state_class, device_class=device_class, promote=promote)


def record_as_dict(record: CurationRecord) -> dict[str, str]:
    """Return the storable/wire form of a record: present fields only."""
    raw: dict[str, str] = {}
    if record.state_class is not None:
        raw["state_class"] = record.state_class.value
    if record.device_class is not None:
        raw["device_class"] = record.device_class
    if record.promote:
        raw["entity_category"] = PROMOTED
    return raw


def parse_record(raw: object) -> CurationRecord | None:
    """Read one stored record, or `None` when the disk holds something else.

    Shape-only: staleness against the wire is `sanitise`'s job, because the
    wire is not available at load time and may change between loads.
    """
    if not isinstance(raw, Mapping):
        return None
    known = {"state_class", "device_class", "entity_category"}
    if not raw or not set(raw) <= known:
        return None
    state_class: SensorStateClass | None = None
    if "state_class" in raw:
        try:
            state_class = SensorStateClass(str(raw["state_class"]))
        except ValueError:
            return None
    device_class = str(raw["device_class"]) if "device_class" in raw else None
    if "entity_category" in raw and raw["entity_category"] != PROMOTED:
        return None
    return CurationRecord(
        state_class=state_class, device_class=device_class, promote="entity_category" in raw
    )


def sanitise(record: CurationRecord, context: RowContext) -> tuple[CurationRecord, tuple[str, ...]]:
    """Drop the fields of a record the current declaration no longer supports.

    A record can go stale between save and a later setup -- the vendor may
    change a row's unit or datatype. Each field is re-validated independently
    and a failing one is dropped rather than failing the record: the wire may
    revert, and the user's other assertions are still good.
    """
    kept: dict[str, object] = {}
    dropped: list[str] = []
    for field, value in record_as_dict(record).items():
        try:
            validate_record({field: value}, context)
        except CurationError:
            dropped.append(field)
        else:
            kept[field] = value
    return validate_record(kept, context) if kept else CurationRecord(), tuple(dropped)


def allowed_state_classes(context: RowContext) -> list[str]:
    """Return the state classes a row of this shape may assert. Empty off numeric sensors."""
    if context.platform is not Platform.SENSOR or context.datatype not in NUMERIC_DATATYPES:
        return []
    return [cls.value for cls in SensorStateClass]


def allowed_device_classes(context: RowContext) -> list[str]:
    """Return the device classes compatible with this row's platform and declared unit."""
    if context.platform is Platform.BINARY_SENSOR:
        return [cls.value for cls in BinarySensorDeviceClass]
    if context.platform is not Platform.SENSOR:
        return []
    allowed: list[str] = []
    for device_class in SensorDeviceClass:
        constrained = DEVICE_CLASS_UNITS.get(device_class)
        if constrained is None or context.unit in constrained:
            allowed.append(device_class.value)
    return allowed


_STORE_VERSION: Final = 1


class StoredCuration(TypedDict):
    """The one shape this module writes to disk."""

    records: dict[str, dict[str, str]]


def _store(hass: HomeAssistant, entry: ConfigEntry) -> Store[StoredCuration]:
    """Per entry, exactly as `additions._store` is: two panels curate independently."""
    return Store(hass, _STORE_VERSION, f"{DOMAIN}.curation.{entry.entry_id}")


class CurationOverlay:
    """Every stored record for one entry, resolved once per setup."""

    def __init__(self, records: Mapping[str, CurationRecord]) -> None:
        """Take a copy: the overlay is read once at setup and never re-reads the disk."""
        self._records = dict(records)

    @classmethod
    def empty(cls) -> CurationOverlay:
        """Return the overlay for an entry that has curated nothing."""
        return cls({})

    def record_for(self, key: str) -> CurationRecord | None:
        """Return the record as stored, unmeasured against any row -- see `for_row`."""
        return self._records.get(key)

    def for_row(self, key: str, context: RowContext) -> CurationRecord | None:
        """Return the record as it applies to the row's *current* declaration.

        Curation must never block setup: a field the wire no longer supports
        is dropped with one warning, never raised.
        """
        record = self._records.get(key)
        if record is None:
            return None
        sanitised, dropped = sanitise(record, context)
        if dropped:
            _LOGGER.warning(
                "Curation for %s no longer fits the published declaration; ignoring %s",
                key,
                ", ".join(dropped),
            )
        return sanitised

    def stale_fields(self, key: str, context: RowContext) -> tuple[str, ...]:
        """Name the fields `for_row` would drop, so the editor can say which are stale."""
        record = self._records.get(key)
        if record is None:
            return ()
        return sanitise(record, context)[1]

    def as_dicts(self) -> dict[str, dict[str, str]]:
        """Enum values and keys only -- safe for diagnostics and the list command."""
        return {key: record_as_dict(record) for key, record in sorted(self._records.items())}


async def async_load_curation(hass: HomeAssistant, entry: ConfigEntry) -> CurationOverlay:
    """Read the overlay off disk. A store this module did not write loads as empty.

    Awaited from `async_setup_entry`, so nothing here may raise for bad disk
    content -- see `additions._load` for the incident that rule comes from.
    """
    stored = await _store(hass, entry).async_load()
    records: dict[str, CurationRecord] = {}
    raw_records = stored.get("records") if isinstance(stored, dict) else None
    if isinstance(raw_records, dict):
        for key, raw in raw_records.items():
            record = parse_record(raw)
            if isinstance(key, str) and record is not None:
                records[key] = record
            else:
                _LOGGER.warning("Discarding unreadable curation record under %r", key)
    elif stored is not None:
        _LOGGER.warning("Curation store is not the shape this integration writes; starting empty")
    return CurationOverlay(records)


async def async_save_record(
    hass: HomeAssistant, entry: ConfigEntry, key: str, record: CurationRecord | None
) -> None:
    """Write one record (or clear one) and leave every other key untouched.

    A record asserting nothing clears the key rather than being stored, because
    its stored form is `{}` and `parse_record` refuses that. Writing it would
    leave a record on disk that the next load reports as unreadable -- the
    warning meant for a damaged or hand-edited store -- and the save after that
    would delete, over a value this signature accepts. Save may not write what
    load rejects.
    """
    store = _store(hass, entry)
    stored = await store.async_load()
    raw_records: dict[str, dict[str, str]] = {}
    if isinstance(stored, dict) and isinstance(stored.get("records"), dict):
        for existing_key, raw in stored["records"].items():
            if isinstance(existing_key, str) and parse_record(raw) is not None:
                raw_records[existing_key] = dict(raw)
    fields = record_as_dict(record) if record is not None else {}
    if fields:
        raw_records[key] = fields
    else:
        raw_records.pop(key, None)
    await store.async_save({"records": raw_records})


async def async_forget_curation(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Drop the curated records when the entry is removed.

    The keys are wire addresses, not registry ids, so a store left behind would
    be picked back up by whatever entry is next added for the same panel -- and
    silently re-assert metadata the user removed the panel to be rid of.
    """
    await _store(hass, entry).async_remove()


def sensor_description(
    path: str,
    unit: str | None,
    default_device_class: SensorDeviceClass | None,
    record: CurationRecord | None,
) -> SensorEntityDescription:
    """Return the description a (possibly curated) adopted sensor is built from.

    The one place in the integration where a `state_class` reaches an adopted
    entity. `adoption.py` and `extension.py` call this rather than building
    their own description, which is what keeps their AST guards true.
    """
    device_class = default_device_class
    state_class: SensorStateClass | None = None
    if record is not None:
        if record.device_class is not None:
            device_class = SensorDeviceClass(record.device_class)
        state_class = record.state_class
    return SensorEntityDescription(
        key=path,
        device_class=device_class,
        native_unit_of_measurement=unit,
        state_class=state_class,
    )


def binary_sensor_device_class(record: CurationRecord | None) -> BinarySensorDeviceClass | None:
    """Return a curated binary device class, or none -- there is no unit map to default from."""
    if record is None or record.device_class is None:
        return None
    return BinarySensorDeviceClass(record.device_class)


def entity_category_for(record: CurationRecord | None) -> EntityCategory | None:
    """Return DIAGNOSTIC unless the user explicitly promoted this row."""
    return None if record is not None and record.promote else EntityCategory.DIAGNOSTIC
