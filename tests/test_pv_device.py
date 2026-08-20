"""The solar inverter gets a device of its own, and takes its entities with it.

PV was the last DER on a v1.0 panel with no card. Its vendor, model and
nameplate capacity were rendered as three diagnostic sensors on the *panel's*
device — beside the panel's own manufacturer and model, so a panel card read as
if the enclosure were an Enphase inverter — and the firmware version the library
has always read reached nothing at all, because a version has nowhere to go but
a card. `pv/info/firmware-version` was baselined naming exactly that.

**The identifier is the decision worth writing down.** `{panel serial}_pv`, and
deliberately not the inverter's serial. Every PV `$description` declares
`info/serial-number` and no producer publishes one, so an identifier that
preferred a serial would be `<panel>_pv` on every panel today and
`<panel>_<serial>` on the first panel whose firmware starts publishing one — and
a device identifier is what a consumer keys its registry on, so that day would
read as the inverter being replaced. `test_a_serial_arriving_on_the_wire_does_not_move_the_device`
produces that day and asserts nothing moves.

**The migration is the risk.** Existing installations have these entities on the
panel device with panel-scoped `unique_id`s and panel-scoped `entity_id`s. Moving
an entity to another device must change *only* the device: a changed `entity_id`
breaks a dashboard, a changed `unique_id` orphans the entity and mints a
duplicate. Home Assistant re-homes an entity by itself when it re-registers with
new `device_info`, which is the mechanism used here; what it does *not* do is
re-derive an `entity_id`, so a fresh installation would otherwise get
`sensor.span_panel_solar_pv_vendor` where every existing one has
`sensor.span_panel_pv_vendor`. `construct_panel_scoped_entity_id` pins that shut
and `test_a_fresh_install_and_a_migrated_one_are_the_same_shape` is the proof.

Every expectation about what the card shows is read out of the vendored capture
and proved by republishing or unpublishing the property, never by a literal. The
registry-shape expectations *are* literals, on purpose: they record what a
released installation carries, and deriving them from the code under test would
make the migration assertions vacuous.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
import json
import logging
import pathlib
from typing import Final
from unittest.mock import AsyncMock, MagicMock

from span_panel_api import SpanPanelSnapshot

from custom_components.span_panel import SpanPanelRuntimeData, ensure_device_registered
from custom_components.span_panel.binary_sensor import (
    PV_PANEL_LINK_SENSOR,
    async_setup_entry as binary_sensor_setup_entry,
)
from custom_components.span_panel.const import DOMAIN
from custom_components.span_panel.field_paths import (
    RESIDUAL_EXEMPT_PATHS,
    Producibility,
    declared_field_paths,
)
from custom_components.span_panel.sensor import async_setup_entry as sensor_setup_entry
from custom_components.span_panel.sensor_definitions import PV_METADATA_SENSORS, PV_POWER_SENSOR
from custom_components.span_panel.util import SUB_DEVICE_PV, classify_sub_device_identifier
from custom_components.span_panel.websocket import _classify_sub_device
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import slugify

from .adapter_fixtures import schema_one_snapshot, schema_one_tree

from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockEntityPlatform,
    async_test_home_assistant,
)

PV_DEVICE: Final = "pv"
"""The inverter's Homie device id in the capture."""

VENDOR_TOPIC: Final = "info/vendor-name"
MODEL_TOPIC: Final = "info/model"
FIRMWARE_TOPIC: Final = "info/firmware-version"
SERIAL_TOPIC: Final = "info/serial-number"

PANEL_NAME: Final = "SPAN Panel"

FALLBACK_MANUFACTURER: Final = "Unknown"
FALLBACK_MODEL: Final = "Solar Inverter"

BASELINE: Final = pathlib.Path(__file__).parent / "fixtures" / "unread_declarations_baseline.json"

_LEGACY_PV_ENTITIES: Final[tuple[tuple[str, str, str], ...]] = (
    ("sensor", "pv_power", "sensor.span_panel_pv_power"),
    ("sensor", "pv_vendor", "sensor.span_panel_pv_vendor"),
    ("sensor", "pv_product", "sensor.span_panel_pv_product"),
    ("sensor", "pv_nameplate_capacity", "sensor.span_panel_pv_nameplate_capacity"),
    ("binary_sensor", "pv_panel_link", "binary_sensor.span_panel_pv_panel_link"),
)
"""``(platform, unique_id suffix, entity_id)`` as a released installation holds them.

Literals, deliberately. This is the registry a user upgrading already has, which
is a historical fact rather than something the current code gets to decide —
deriving it from the builders under test would make every assertion below agree
with itself. `test_a_fresh_install_still_builds_the_unique_ids_users_already_have`
is what holds the literals to the code.
"""


# ---------------------------------------------------------------------------
# Reading the capture
# ---------------------------------------------------------------------------


def _published(topic: str) -> str:
    """What the capture publishes on one PV topic, or fail saying it does not."""
    value = schema_one_tree()[PV_DEVICE].get(topic)
    assert value is not None, f"the capture publishes no {topic} on the inverter"
    return value


def _declared(topic: str) -> bool:
    """Whether the inverter's `$description` declares one `node/property`."""
    description = json.loads(schema_one_tree()[PV_DEVICE]["$description"])
    node, _, prop = topic.partition("/")
    return prop in description["nodes"].get(node, {}).get("properties", {})


def _pv_snapshot(**rewrites: str | None) -> SpanPanelSnapshot:
    """A snapshot from the capture with the inverter's topics rewritten or removed.

    Keyword spelling is `node__property_name`. `None` removes the topic, which is
    what firmware omitting a property looks like — a different event from
    publishing an empty string, and the one the card's fallbacks exist for.
    """
    tree = schema_one_tree()
    for path, value in rewrites.items():
        node, _, prop = path.partition("__")
        topic = f"{node.replace('_', '-')}/{prop.replace('_', '-')}"
        if value is None:
            tree[PV_DEVICE].pop(topic, None)
        else:
            tree[PV_DEVICE][topic] = value
    return schema_one_snapshot(tree)


# ---------------------------------------------------------------------------
# Installing for real
# ---------------------------------------------------------------------------


def _entry(hass: HomeAssistant, entry_id: str, serial: str) -> MockConfigEntry:
    """A config entry keyed on the panel's serial, as the config flow makes one."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "192.168.1.40", "device_name": PANEL_NAME},
        options={},
        title=PANEL_NAME,
        entry_id=entry_id,
        unique_id=serial,
    )
    entry.add_to_hass(hass)
    return entry


def _coordinator(
    hass: HomeAssistant, entry: MockConfigEntry, snapshot: SpanPanelSnapshot
) -> MagicMock:
    coordinator = MagicMock()
    coordinator.data = snapshot
    coordinator.hass = hass
    coordinator.panel_offline = False
    coordinator.last_update_success = True
    coordinator.unresolved_paths = frozenset()
    coordinator.config_entry = entry
    coordinator.async_request_refresh = AsyncMock()
    return coordinator


async def _register(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    platform: str,
    setup: Callable[..., object],
) -> None:
    """Run one platform's `async_setup_entry` through a real `EntityPlatform`.

    Through the platform rather than by inspecting the entities the setup
    function returns, because everything under test here happens *in* the
    registry: which device an entity is filed under, and which `entity_id` it
    keeps. Neither is observable on an entity object.
    """
    added: list[object] = []
    await setup(hass, entry, lambda entities, **_: added.extend(entities))

    entity_platform = MockEntityPlatform(
        hass, domain=platform, platform_name=DOMAIN, logger=logging.getLogger(__name__)
    )
    entity_platform.config_entry = entry
    # The translations decide the object id Home Assistant derives, so a harness
    # that skipped them would generate `sensor.span_panel_2` and prove nothing
    # about the ids a user sees.
    await entity_platform.platform_data.async_load_translations()
    await entity_platform.async_add_entities(added)


async def _install(
    hass: HomeAssistant,
    snapshot: SpanPanelSnapshot,
    entry_id: str,
    *,
    seed: Callable[[HomeAssistant, MockConfigEntry, str], None] | None = None,
) -> MockConfigEntry:
    """Set up both platforms the way the integration does, optionally over a seeded registry."""
    entry = _entry(hass, entry_id, snapshot.serial_number)
    panel_device_id = await ensure_device_registered(hass, entry, snapshot, PANEL_NAME)
    if seed is not None:
        seed(hass, entry, panel_device_id)

    coordinator = _coordinator(hass, entry, snapshot)
    entry.runtime_data = SpanPanelRuntimeData(
        coordinator=coordinator, panel_device_id=panel_device_id
    )
    await _register(hass, entry, "sensor", sensor_setup_entry)
    await _register(hass, entry, "binary_sensor", binary_sensor_setup_entry)
    return entry


@asynccontextmanager
async def _a_second_home_assistant() -> AsyncIterator[HomeAssistant]:
    """A second, empty Home Assistant, for comparing two installations.

    Both halves of the fresh-versus-migrated comparison have to be *the* install
    on their instance. Running them into one registry makes the second collide
    with the first and land on `..._pv_vendor_2`, which is an artefact of the
    harness and would mask or invent a divergence either way.
    """
    async with async_test_home_assistant() as second:
        try:
            yield second
        finally:
            await second.async_stop(force=True)


def _seed_the_old_shape(hass: HomeAssistant, entry: MockConfigEntry, panel_device_id: str) -> None:
    """Write the PV entities onto the panel device, as a released install holds them."""
    registry = er.async_get(hass)
    for platform, suffix, entity_id in _LEGACY_PV_ENTITIES:
        created = registry.async_get_or_create(
            platform,
            DOMAIN,
            f"span_{entry.unique_id}_{suffix}",
            config_entry=entry,
            device_id=panel_device_id,
            suggested_object_id=entity_id.split(".", 1)[1],
        )
        assert created.entity_id == entity_id, (
            f"the seed could not reproduce {entity_id}; it landed on {created.entity_id}"
        )


def _pv_device(hass: HomeAssistant, entry: MockConfigEntry) -> dr.DeviceEntry | None:
    return dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, f"{entry.unique_id}_{SUB_DEVICE_PV}"), entry.entry_id
    )


def _registry_shape(hass: HomeAssistant, entry: MockConfigEntry) -> dict[str, tuple[str, ...]]:
    """``{entity_id: (unique_id, *device identifiers)}`` for every entity of one entry.

    Devices by identifier rather than by registry id, because two installations
    mint different registry ids for the same device and the identifier is the
    stable name for "which card is this on".
    """
    devices = dr.async_get(hass)
    shape: dict[str, tuple[str, ...]] = {}
    for entity in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id):
        device = devices.async_get(entity.device_id) if entity.device_id else None
        identifiers = sorted(name for _domain, name in device.identifiers) if device else []
        shape[entity.entity_id] = (entity.unique_id, *identifiers)
    return shape


# ---------------------------------------------------------------------------
# The premise
# ---------------------------------------------------------------------------


def test_the_capture_publishes_the_identity_the_card_shows() -> None:
    """Guard the premise: every card expectation below is read from these topics.

    A test whose expected value comes from an unpublished topic does not fail, it
    stops asserting anything — which is how the fixture drifted eight identity
    properties behind the producer without a single red test.
    """
    assert _published(VENDOR_TOPIC)
    assert _published(MODEL_TOPIC)
    assert _published(FIRMWARE_TOPIC)


def test_the_capture_declares_a_serial_and_publishes_none() -> None:
    """The premise of the identifier decision, held to the capture.

    If a producer ever values this, the choice of identifier stops being
    hypothetical and `test_a_serial_arriving_on_the_wire_does_not_move_the_device`
    stops being a simulation. Either way the identifier must not move, which is
    what the two together assert.
    """
    assert _declared(SERIAL_TOPIC), "the inverter no longer declares a serial number"
    assert schema_one_tree()[PV_DEVICE].get(SERIAL_TOPIC) is None, (
        "the capture now values PV info/serial-number; decision 3 says it stays unvalued "
        "until the flat side's PV device id is confirmed"
    )


# ---------------------------------------------------------------------------
# The card
# ---------------------------------------------------------------------------


async def test_the_inverter_gets_a_card_showing_what_it_publishes(
    hass: HomeAssistant,
) -> None:
    """Manufacturer, model and firmware read off the wire, not out of a constant."""
    entry = await _install(hass, _pv_snapshot(), "entry-pv-card")

    device = _pv_device(hass, entry)
    assert device is not None
    assert device.manufacturer == _published(VENDOR_TOPIC)
    assert device.model == _published(MODEL_TOPIC)
    assert device.sw_version == _published(FIRMWARE_TOPIC)


async def test_the_card_follows_a_republished_identity(hass: HomeAssistant) -> None:
    """The card tracks the wire, so nothing above is passing on a coincidence."""
    rewritten = _pv_snapshot(
        info__vendor_name="Another Vendor",
        info__model="ANOTHER-MODEL-1",
        info__firmware_version="example-pv/v9.9.9",
    )

    entry = await _install(hass, rewritten, "entry-pv-card-rewritten")

    device = _pv_device(hass, entry)
    assert device is not None
    assert device.manufacturer == "Another Vendor"
    assert device.model == "ANOTHER-MODEL-1"
    assert device.sw_version == "example-pv/v9.9.9"


async def test_an_inverter_publishing_no_identity_gets_a_card_with_no_blank_rows(
    hass: HomeAssistant,
) -> None:
    """The fallbacks, and the difference between an absent row and a blank one.

    Vendor and model fall back to strings because a card with no name at all is
    worse than a generic one. The firmware version has no string to fall back to
    and must be *absent* rather than empty: `DeviceInfo` omits a `None` field and
    renders `""` as a present-but-blank row, which reads as an inverter reporting
    a blank version rather than one reporting none.
    """
    bare = _pv_snapshot(
        info__vendor_name=None,
        info__model=None,
        info__firmware_version=None,
    )

    entry = await _install(hass, bare, "entry-pv-card-bare")

    device = _pv_device(hass, entry)
    assert device is not None
    assert device.manufacturer == FALLBACK_MANUFACTURER
    assert device.model == FALLBACK_MODEL
    assert device.sw_version is None


async def test_the_card_hangs_off_the_panel_like_every_other_sub_device(
    hass: HomeAssistant,
) -> None:
    """By registry id, which is the link Home Assistant stops dropping in 2027.8."""
    snapshot = _pv_snapshot()
    entry = await _install(hass, snapshot, "entry-pv-link")

    device = _pv_device(hass, entry)
    panel = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, snapshot.serial_number), entry.entry_id
    )
    assert device is not None
    assert panel is not None
    assert device.via_device_id == panel.id


async def test_a_panel_with_no_inverter_gets_no_card(hass: HomeAssistant) -> None:
    """Absence is a reading. No PV node, no device, and no entities to re-home."""
    tree = schema_one_tree()
    del tree[PV_DEVICE]
    for topics in tree.values():
        topics.pop("power-flows/pv", None)
    for circuit, topics in tree.items():
        if topics.get("connection/feeds-device-id") == PV_DEVICE:
            topics.pop("connection/feeds-device-id", None)
            topics.pop("connection/feeds-device-status", None)
            topics.pop("connection/feeds-device-type", None)

    entry = await _install(hass, schema_one_snapshot(tree), "entry-pv-absent")

    assert _pv_device(hass, entry) is None
    shape = _registry_shape(hass, entry)
    assert not [entity_id for entity_id in shape if "_pv_" in entity_id]


# ---------------------------------------------------------------------------
# The identifier
# ---------------------------------------------------------------------------


async def test_a_serial_arriving_on_the_wire_does_not_move_the_device(
    hass: HomeAssistant,
) -> None:
    """The whole reason the identifier does not mention the inverter's serial.

    `_der_identifier` on the producer side prefers a serial over an instance id,
    so the day firmware starts publishing `info/serial-number` the inverter's
    *Homie* device id changes. A Home Assistant identifier derived from it would
    change with it, and a changed identifier is a new device: the card empties,
    the entities orphan, and an upgrade rehearsal becomes a device-replacement
    rehearsal. Keyed on the panel's serial and the kind instead, so this test
    publishes a serial and watches nothing move.
    """
    before = await _install(hass, _pv_snapshot(), "entry-pv-noserial")
    identifier_before = _pv_device(hass, before)
    shape_before = _registry_shape(hass, before)

    async with _a_second_home_assistant() as second:
        after = await _install(
            second,
            _pv_snapshot(info__serial_number="INVERTER-SERIAL-0001"),
            "entry-pv-serial",
        )
        identifier_after = _pv_device(second, after)
        shape_after = _registry_shape(second, after)

        assert identifier_before is not None
        assert identifier_after is not None
        assert identifier_before.identifiers == identifier_after.identifiers
        assert shape_before == shape_after


async def test_the_topology_reader_calls_the_new_card_a_pv(hass: HomeAssistant) -> None:
    """The writing end and the reading end of the identifier grammar agree.

    The MID shipped classifying as `unknown` because a kind was added to the
    builders and not to the reader, and a card rendered a device with a name and
    no type. Asserted against the device as registered rather than against the
    builder's dict, because the reader is handed a `DeviceEntry`.
    """
    entry = await _install(hass, _pv_snapshot(), "entry-pv-classify")

    device = _pv_device(hass, entry)
    assert device is not None
    assert _classify_sub_device(device) == SUB_DEVICE_PV
    assert classify_sub_device_identifier(f"{entry.unique_id}_{SUB_DEVICE_PV}") == SUB_DEVICE_PV


# ---------------------------------------------------------------------------
# Where the entities land, and what they keep
# ---------------------------------------------------------------------------


def _pv_entities(hass: HomeAssistant, entry: MockConfigEntry) -> dict[str, er.RegistryEntry]:
    """The five PV entities, by `entity_id`, or fail naming the ones missing."""
    registry = er.async_get(hass)
    found: dict[str, er.RegistryEntry] = {}
    missing: list[str] = []
    for platform, suffix, _entity_id in _LEGACY_PV_ENTITIES:
        entity_id = registry.async_get_entity_id(
            platform, DOMAIN, f"span_{entry.unique_id}_{suffix}"
        )
        if entity_id is None:
            missing.append(suffix)
            continue
        entity = registry.async_get(entity_id)
        assert entity is not None
        found[entity_id] = entity
    assert not missing, f"the platform created no entity for {missing}"
    return found


async def test_a_fresh_install_still_builds_the_unique_ids_users_already_have(
    hass: HomeAssistant,
) -> None:
    """The literals above are the current code's output, so the migration tests bite.

    A `unique_id` is an identity: changing one does not rename an entity, it
    orphans the old one and mints a second. This holds the recorded shape to the
    builders, so a change to either fails here rather than in an upgrade.
    """
    entry = await _install(hass, _pv_snapshot(), "entry-pv-uids")

    assert set(_pv_entities(hass, entry)) == {
        entity_id for _platform, _suffix, entity_id in _LEGACY_PV_ENTITIES
    }


async def test_every_pv_entity_lands_on_the_inverters_card(hass: HomeAssistant) -> None:
    """All five, and none of them left behind on the panel."""
    entry = await _install(hass, _pv_snapshot(), "entry-pv-home")

    device = _pv_device(hass, entry)
    assert device is not None
    for entity_id, entity in _pv_entities(hass, entry).items():
        assert entity.device_id == device.id, f"{entity_id} is not on the inverter's card"


def test_the_pinned_ids_are_what_the_panel_card_would_have_generated() -> None:
    """The pin reproduces Home Assistant's own derivation, rather than guessing it.

    `construct_panel_scoped_entity_id` builds `{panel slug}_{translation key}`,
    while Home Assistant builds `{device name} {entity name}` and slugifies it.
    The two agree only because every PV entity's translated name slugifies to its
    translation key — true today, and silently untrue the day somebody renames
    one, which would make a *new* installation's entity_id differ from every
    existing one. Read out of `strings.json`, which is the file that would change.
    """
    strings = json.loads(
        (
            pathlib.Path(__file__).parent.parent
            / "custom_components"
            / "span_panel"
            / "strings.json"
        ).read_text(encoding="utf-8")
    )
    for platform, suffix, entity_id in _LEGACY_PV_ENTITIES:
        name = strings["entity"][platform][suffix]["name"]
        assert slugify(name) == suffix, (
            f"{platform}.{suffix} is named {name!r}, which slugifies to "
            f"{slugify(name)!r}; the pinned entity_id would stop matching what "
            "Home Assistant derives"
        )
        assert entity_id == f"{platform}.{slugify(PANEL_NAME)}_{slugify(name)}"


def test_the_descriptions_carry_the_translation_keys_the_pin_reads() -> None:
    """The pin reads `translation_key`, so a description without one would pin to ``.

    An empty key would build `sensor.span_panel_` for every PV entity and Home
    Assistant would resolve the collision by appending numbers — a silent,
    permanent scrambling of five entity ids on new installations only.
    """
    keys = {PV_POWER_SENSOR.translation_key} | {
        description.translation_key for description in PV_METADATA_SENSORS
    }
    keys.add(PV_PANEL_LINK_SENSOR.translation_key)
    assert keys == {suffix for _platform, suffix, _entity_id in _LEGACY_PV_ENTITIES}


# ---------------------------------------------------------------------------
# The migration
# ---------------------------------------------------------------------------


async def test_the_migration_moves_the_device_and_nothing_else(
    hass: HomeAssistant,
) -> None:
    """Seed the released shape, set up, and read the registry back.

    Home Assistant re-homes an entity when it re-registers with different
    `device_info` — `async_get_or_create` updates `device_id` on an existing
    entry — so there is no bespoke migration to write and deliberately none
    written. What that mechanism does *not* touch is the `entity_id` or the
    `unique_id`, and those are what a user's dashboards and automations name, so
    they are asserted one by one rather than in aggregate.
    """
    entry = await _install(hass, _pv_snapshot(), "entry-pv-migrate", seed=_seed_the_old_shape)

    device = _pv_device(hass, entry)
    assert device is not None
    registry = er.async_get(hass)
    for platform, suffix, entity_id in _LEGACY_PV_ENTITIES:
        unique_id = f"span_{entry.unique_id}_{suffix}"
        entity = registry.async_get(entity_id)
        assert entity is not None, f"{entity_id} no longer exists after setup"
        assert entity.unique_id == unique_id, f"{entity_id} changed unique_id"
        assert entity.platform == DOMAIN
        assert entity.domain == platform
        assert entity.device_id == device.id, f"{entity_id} did not move to the inverter"


async def test_the_migration_leaves_no_duplicate_and_no_orphan(
    hass: HomeAssistant,
) -> None:
    """The two failure modes a re-home has, made observable.

    A duplicate: the entity re-registers under a new `unique_id`, so the old
    registry entry survives beside a new one and a user sees each reading twice.
    An orphan: the panel device keeps an entity nothing writes to any more. Both
    are counted rather than spot-checked, because either would otherwise hide
    among forty circuit entities.
    """
    entry = await _install(hass, _pv_snapshot(), "entry-pv-migrate-clean", seed=_seed_the_old_shape)

    device = _pv_device(hass, entry)
    panel = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, entry.unique_id or ""), entry.entry_id
    )
    assert device is not None
    assert panel is not None

    entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    unique_ids = [entity.unique_id for entity in entities]
    assert len(unique_ids) == len(set(unique_ids)), "an entity re-registered under a second id"

    pv_unique_ids = {f"span_{entry.unique_id}_{suffix}" for _p, suffix, _e in _LEGACY_PV_ENTITIES}
    left_behind = [
        entity.entity_id
        for entity in entities
        if entity.unique_id in pv_unique_ids and entity.device_id != device.id
    ]
    assert not left_behind, f"still on the panel card: {left_behind}"

    pv_devices = [
        candidate
        for candidate in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
        if any(
            classify_sub_device_identifier(name) == SUB_DEVICE_PV
            for _domain, name in candidate.identifiers
        )
    ]
    assert len(pv_devices) == 1, f"{len(pv_devices)} PV devices registered, expected 1"


async def test_a_fresh_install_and_a_migrated_one_are_the_same_shape(
    hass: HomeAssistant,
) -> None:
    """Two installations of the same panel, one upgraded and one new, must agree.

    Divergence here is the long-tail bug: a documentation example, a blueprint or
    a support answer that is right for exactly half of the installed base. The
    comparison is over `{entity_id: (unique_id, device identifiers)}` — every fact
    a user or a dashboard can name — rather than over counts, so a single entity
    filed under a different card fails with the entity_id in the message.
    """
    snapshot = _pv_snapshot()
    migrated = await _install(hass, snapshot, "entry-pv-shape-old", seed=_seed_the_old_shape)
    migrated_shape = _registry_shape(hass, migrated)

    async with _a_second_home_assistant() as second:
        fresh = await _install(second, snapshot, "entry-pv-shape-new")
        assert migrated_shape == _registry_shape(second, fresh)


async def test_a_user_renamed_entity_id_survives_the_move(hass: HomeAssistant) -> None:
    """The pin is a suggestion, and must lose to a choice the user already made.

    Home Assistant only honours a suggested `entity_id` for an entity it is
    registering for the first time. Asserted rather than assumed, because the pin
    is the one new thing in this change that could plausibly overwrite something
    of the user's.
    """

    def seed(hass_: HomeAssistant, entry_: MockConfigEntry, panel_device_id: str) -> None:
        _seed_the_old_shape(hass_, entry_, panel_device_id)
        er.async_get(hass_).async_update_entity(
            "sensor.span_panel_pv_vendor", new_entity_id="sensor.my_solar_brand"
        )

    entry = await _install(hass, _pv_snapshot(), "entry-pv-renamed", seed=seed)

    registry = er.async_get(hass)
    assert registry.async_get("sensor.span_panel_pv_vendor") is None
    renamed = registry.async_get("sensor.my_solar_brand")
    assert renamed is not None
    device = _pv_device(hass, entry)
    assert device is not None
    assert renamed.device_id == device.id


# ---------------------------------------------------------------------------
# The inventories
# ---------------------------------------------------------------------------


def test_the_firmware_version_is_no_longer_an_unread_declaration() -> None:
    """The line this task exists to delete, and the line that stays.

    `pv/info/firmware-version` was baselined saying the inverter had no card to
    carry a version; it has one now, so the line goes. `pv/info/serial-number`
    stays, and its reason has to name the identifier decision rather than only
    the producer-side one, because the Home Assistant identifier is now a second
    thing that would have moved.
    """
    baseline: dict[str, str] = json.loads(BASELINE.read_text(encoding="utf-8"))

    assert "pv/info/firmware-version" not in baseline
    reason = baseline.get("pv/info/serial-number")
    assert reason is not None, "the serial must stay baselined; see decision 3"
    assert "identifier" in reason


def test_the_cards_firmware_read_is_enumerated_as_a_residual() -> None:
    """`pv_device_info` is not an entity, so its read is an exempt residual.

    `NEITHER` beside the `mid.*` and `panel.*` card reads: flat's `pv` device
    class declares no firmware version, and a schema_1 metadata row carries a
    unit and a datatype for a reading, which a version string is not.
    """
    assert RESIDUAL_EXEMPT_PATHS["pv.software_version"] is Producibility.NEITHER
    assert "pv.software_version" not in declared_field_paths()


def test_nothing_reads_a_pv_serial_anywhere() -> None:
    """The negative half of the identifier decision, asserted rather than assumed.

    Adding the field to the snapshot would be harmless; reading it here would
    not, because every read is a place a future change could route into the
    identifier. There is no such field and no such path, and this fails the day
    one arrives without the decision being revisited.
    """
    assert "pv.serial_number" not in RESIDUAL_EXEMPT_PATHS
    assert "pv.serial_number" not in declared_field_paths()
    snapshot = _pv_snapshot(info__serial_number="INVERTER-SERIAL-0001")
    assert not hasattr(snapshot.pv, "serial_number")


def test_the_kind_vocabulary_is_closed() -> None:
    """Every kind the classifier answers with, in one place.

    Not a restatement of the classifier: `test_device_links` parametrises over
    the *builders*, so a fifth sub-device whose identifier nothing classifies
    fails there. This is the other direction — the reader answering a kind the
    writers never mint — and it is why the list is spelled out rather than
    derived from the constants.
    """
    assert [
        classify_sub_device_identifier("panel-serial_bess"),
        classify_sub_device_identifier("panel-serial_mid"),
        classify_sub_device_identifier("panel-serial_evse_node"),
        classify_sub_device_identifier("panel-serial_pv"),
        classify_sub_device_identifier("panel-serial"),
    ] == ["bess", "mid", "evse", SUB_DEVICE_PV, None]
