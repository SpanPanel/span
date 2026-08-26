"""The EVSE charge-current limit, as the integration's first number entity.

The only settable property the v1.0 catch-up surfaces, so this is the one place
in the catch-up where being wrong reaches the panel rather than the dashboard.
The tests are shaped around that.

**Nothing here names an amperage.** Every expectation is computed from the
capture, and where the capture publishes the same value on both chargers — it
publishes 32 on each — the test republishes differing values first, because an
assertion satisfied by reading one charger twice proves nothing about two.

**The write is proved to the wire, not to a mock.** `test_the_write_reaches_the
_wire_as_one_publish` drives the entity through the real client and the real
schema_1 adapter and asserts the exact topic and payload the transport hands the
broker — including that the topic is addressed by the charger's *device id*
while the entity holds its serial-harmonised snapshot key, which are different
strings and would both look plausible in a log.

**The control exists only where the panel declares it.** `$settable` is the
gate, so a charger whose limit is not declared settable gets no entity at all —
asserted through `async_setup_entry`, because an entity that must not exist
cannot be observed by asking an entity for its state.
"""

from __future__ import annotations

from collections.abc import Sequence
import json
from unittest.mock import AsyncMock, MagicMock

from homeassistant.components.number import NumberDeviceClass, NumberMode
from homeassistant.const import Platform, UnitOfElectricCurrent
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from span_panel_api import SpanPanelSnapshot

from custom_components.span_panel import PLATFORMS, SpanPanelRuntimeData
from custom_components.span_panel.const import DOMAIN
from custom_components.span_panel.field_paths import (
    RESIDUAL_EXEMPT_PATHS,
    DerivedReason,
    Producibility,
    platform_descriptions,
)
from custom_components.span_panel.number import (
    EVSE_CHARGE_CURRENT_LIMIT,
    SpanEvseNumber,
    async_setup_entry,
)

from .adapter_fixtures import (
    SCHEMA_ONE_PANEL,
    schema_one_metadata,
    schema_one_snapshot,
    schema_one_tree,
)

EVSE = "evse"
EVSE_2 = "evse-2"
"""The two chargers the capture commissions. Two is what makes cross-wiring falsifiable."""

CONFIG_NODE = "config"
CEILING_TOPIC = f"{CONFIG_NODE}/max-charge-current"
LIMIT_TOPIC = f"{CONFIG_NODE}/user-max-charge-current"

FIELD_LIMIT = "evse.charge_current_limit_a"
FIELD_CEILING = "evse.charge_current_ceiling_a"
FIELD_TARGET = "evse.charge_current_limit_target_a"
FIELD_SETTABLE = "evse.charge_current_limit_settable"


# ---------------------------------------------------------------------------
# Reading the capture
# ---------------------------------------------------------------------------


def _published(tree: dict[str, dict[str, str]], device_id: str, topic: str) -> int:
    """What the capture publishes on this topic, or fail saying it does not."""
    value = tree[device_id].get(topic)
    assert value is not None, f"{device_id} publishes no {topic} in the capture"
    return int(value)


def _declaration(tree: dict[str, dict[str, str]], device_id: str, property_id: str) -> dict[str, object]:
    """One property's declaration out of the charger's own `$description`."""
    description = json.loads(tree[device_id]["$description"])
    declared = description["nodes"][CONFIG_NODE]["properties"][property_id]
    assert isinstance(declared, dict)
    return declared


def _snapshot(**overrides: dict[str, int | None]) -> SpanPanelSnapshot:
    """A snapshot from the capture with each charger's topics rewritten or removed."""
    tree = schema_one_tree()
    for device_id, topics in overrides.items():
        for topic, value in topics.items():
            if value is None:
                tree[device_id].pop(topic, None)
            else:
                tree[device_id][topic] = str(value)
    return schema_one_snapshot(tree)


def _mutated_description(
    tree: dict[str, dict[str, str]], device_id: str, mutate: object
) -> dict[str, dict[str, str]]:
    """Rewrite one charger's `$description` through `mutate`, in place on `tree`."""
    description = json.loads(tree[device_id]["$description"])
    assert callable(mutate)
    mutate(description)
    tree[device_id]["$description"] = json.dumps(description)
    return tree


def _not_settable(device_id: str) -> SpanPanelSnapshot:
    """A charger that publishes a limit and does not declare it writable."""

    def drop(description: dict[str, dict[str, dict[str, dict[str, dict[str, object]]]]]) -> None:
        description["nodes"][CONFIG_NODE]["properties"]["user-max-charge-current"].pop("settable")

    return schema_one_snapshot(_mutated_description(schema_one_tree(), device_id, drop))


def _renamed_to_catalog(device_id: str) -> dict[str, dict[str, str]]:
    """The capture with one charger publishing the eBus `charge-limit` spelling.

    The naming the catalog specifies and no producer we have publishes. The
    entity must not be able to tell: the library resolves the node from the
    `$description`, so the rename is a rename and nothing else.
    """
    tree = schema_one_tree()
    description = json.loads(tree[device_id]["$description"])
    properties = description["nodes"].pop(CONFIG_NODE)["properties"]
    description["nodes"]["charge-limit"] = {
        "name": "charge-limit",
        "type": "energy.ebus.capability.charge-limit",
        "properties": {
            "installer-max": properties["max-charge-current"],
            "owner-limit": properties["user-max-charge-current"],
        },
    }
    tree[device_id]["$description"] = json.dumps(description)
    tree[device_id]["charge-limit/installer-max"] = tree[device_id].pop(CEILING_TOPIC)
    tree[device_id]["charge-limit/owner-limit"] = tree[device_id].pop(LIMIT_TOPIC)
    return tree


def _fed_adapter(*extra: tuple[str, str]) -> object:
    """A real schema_1 adapter fed the capture the way the broker replays it.

    `extra` appends messages the retained-topic fixture cannot express — a
    Homie `$target` is published on `<node>/<property>/$target`, which the
    fixture's flat `{topic: value}` shape has no room for.
    """
    from span_panel_api.models import V2HomieSchema
    from span_panel_api_schema_1 import SchemaOneAdapter

    tree = schema_one_tree()
    adapter = SchemaOneAdapter(
        SCHEMA_ONE_PANEL,
        V2HomieSchema(
            firmware_version="spanos2/r202633/01",
            types_schema_hash="sha256:test",
            types={},
            data_model_version="1.0",
        ),
    )
    for device_id in [SCHEMA_ONE_PANEL, *[d for d in tree if d != SCHEMA_ONE_PANEL]]:
        topics = tree[device_id]
        prefix = f"ebus/5/{device_id}"
        adapter.handle_message(f"{prefix}/$description", topics["$description"])
        adapter.handle_message(f"{prefix}/$state", topics["$state"])
        for topic, value in topics.items():
            if not topic.startswith("$"):
                adapter.handle_message(f"{prefix}/{topic}", value)
    for topic, value in extra:
        adapter.handle_message(topic, value)
    return adapter


# ---------------------------------------------------------------------------
# Building the entities
# ---------------------------------------------------------------------------


def _coordinator(snapshot: SpanPanelSnapshot, client: object | None = None) -> MagicMock:
    coordinator = MagicMock()
    coordinator.data = snapshot
    coordinator.panel_offline = False
    coordinator.last_update_success = True
    coordinator.unresolved_paths = frozenset()
    coordinator.client = MagicMock() if client is None else client
    coordinator.config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={},
        title="SPAN Panel",
        unique_id=snapshot.serial_number,
    )
    coordinator.config_entry.runtime_data = SpanPanelRuntimeData(
        coordinator=coordinator, panel_device_id="panel-device-id"
    )
    coordinator.async_request_refresh = AsyncMock()
    return coordinator


async def _created(
    hass: HomeAssistant, snapshot: SpanPanelSnapshot, client: object | None = None
) -> list[SpanEvseNumber]:
    """Everything `number.async_setup_entry` creates for one snapshot."""
    coordinator = _coordinator(snapshot, client)
    async_add_entities = MagicMock()

    await async_setup_entry(hass, coordinator.config_entry, async_add_entities)

    added: Sequence[SpanEvseNumber] = async_add_entities.call_args.args[0]
    for entity in added:
        assert isinstance(entity, SpanEvseNumber)
    return list(added)


def _serial(tree: dict[str, dict[str, str]], device_id: str) -> str:
    serial = tree[device_id].get("info/serial-number")
    assert serial, f"{device_id} publishes no serial in the capture"
    return serial


def _for(created: Sequence[SpanEvseNumber], tree: dict[str, dict[str, str]], device_id: str) -> SpanEvseNumber:
    """The number belonging to one charger, found by the serial the snapshot keys it on."""
    serial = _serial(tree, device_id)
    matches = [entity for entity in created if entity._evse_id == serial]
    assert len(matches) == 1, f"{len(matches)} numbers created for {device_id}, expected 1"
    return matches[0]


def _refreshed(entity: SpanEvseNumber, snapshot: SpanPanelSnapshot | None) -> SpanEvseNumber:
    """Push a new snapshot through the coordinator update the way HA does."""
    entity.coordinator.data = snapshot
    entity.async_write_ha_state = MagicMock()
    entity._handle_coordinator_update()
    return entity


# ---------------------------------------------------------------------------
# The platform exists
# ---------------------------------------------------------------------------


def test_the_number_platform_is_forwarded() -> None:
    """A platform module nothing forwards creates nothing, silently."""
    assert Platform.NUMBER in PLATFORMS


async def test_one_number_per_charger_that_declares_a_settable_limit(hass: HomeAssistant) -> None:
    tree = schema_one_tree()
    created = await _created(hass, schema_one_snapshot(tree))

    assert [entity._evse_id for entity in created] == [_serial(tree, EVSE), _serial(tree, EVSE_2)]
    assert {entity.entity_description.key for entity in created} == {EVSE_CHARGE_CURRENT_LIMIT.key}
    assert len({entity.unique_id for entity in created}) == 2


async def test_no_number_where_the_limit_is_not_declared_settable(hass: HomeAssistant) -> None:
    """The refusal, at the point where it costs a user nothing.

    A charger publishes a perfectly readable limit and does not declare it
    writable. Offering the control anyway would put a write on the wire the
    panel never offered, and the user would find out by it not working.
    """
    tree = schema_one_tree()
    created = await _created(hass, _not_settable(EVSE))

    assert [entity._evse_id for entity in created] == [_serial(tree, EVSE_2)]


async def test_no_number_where_the_charger_declares_no_limit_at_all(hass: HomeAssistant) -> None:
    """`charge-limit.md`: absence means the charger charges at a fixed rate."""
    tree = schema_one_tree()

    def drop(description: dict[str, dict[str, object]]) -> None:
        description["nodes"].pop(CONFIG_NODE)

    _mutated_description(tree, EVSE, drop)
    del tree[EVSE][CEILING_TOPIC]
    del tree[EVSE][LIMIT_TOPIC]

    created = await _created(hass, schema_one_snapshot(tree))

    assert [entity._evse_id for entity in created] == [_serial(tree, EVSE_2)]


# ---------------------------------------------------------------------------
# Reading — per charger, from the wire
# ---------------------------------------------------------------------------


async def test_each_number_reads_its_own_charger(hass: HomeAssistant) -> None:
    """Read each charger's own limit, from values made to differ first.

    The capture publishes 32 on both, so an assertion against it as-published is
    satisfied by a platform that reads one charger twice.
    """
    tree = schema_one_tree()
    first = _published(tree, EVSE, LIMIT_TOPIC) - 8
    second = _published(tree, EVSE_2, LIMIT_TOPIC) - 16
    assert first != second

    created = await _created(hass, _snapshot(evse={LIMIT_TOPIC: first}, **{"evse-2": {LIMIT_TOPIC: second}}))

    assert _for(created, tree, EVSE).native_value == first
    assert _for(created, tree, EVSE_2).native_value == second


async def test_each_number_is_bounded_by_its_own_ceiling(hass: HomeAssistant) -> None:
    """`native_max_value` is the commissioned ceiling, and per charger."""
    tree = schema_one_tree()
    first = _published(tree, EVSE, CEILING_TOPIC) - 8
    second = _published(tree, EVSE_2, CEILING_TOPIC) - 16
    assert first != second

    created = await _created(hass, _snapshot(evse={CEILING_TOPIC: first}, **{"evse-2": {CEILING_TOPIC: second}}))

    assert _for(created, tree, EVSE).native_max_value == first
    assert _for(created, tree, EVSE_2).native_max_value == second


async def test_republishing_moves_the_state_and_the_bound(hass: HomeAssistant) -> None:
    tree = schema_one_tree()
    created = await _created(hass, schema_one_snapshot(tree))
    entity = _for(created, tree, EVSE)
    assert entity.native_value == _published(tree, EVSE, LIMIT_TOPIC)

    lowered = _published(tree, EVSE, LIMIT_TOPIC) - 16
    recommissioned = _published(tree, EVSE, CEILING_TOPIC) - 8
    _refreshed(entity, _snapshot(evse={LIMIT_TOPIC: lowered, CEILING_TOPIC: recommissioned}))

    assert entity.native_value == lowered
    assert entity.native_max_value == recommissioned


async def test_an_unpublished_limit_is_unknown_rather_than_zero(hass: HomeAssistant) -> None:
    """The control is still offered — the property is declared, the value is late."""
    tree = schema_one_tree()
    created = await _created(hass, _snapshot(evse={LIMIT_TOPIC: None}))
    entity = _for(created, tree, EVSE)

    assert entity.native_value is None
    assert entity.available is True


async def test_an_unpublished_ceiling_makes_the_control_unavailable(hass: HomeAssistant) -> None:
    """A number must report some maximum, and Home Assistant's default is 100.

    Rendering an uncommissioned charger as a 0-100 A control would put a
    plausible-looking amperage in front of a user that no installer ever set.
    Unavailable says the panel has not told us what the charger is rated for.
    """
    tree = schema_one_tree()
    created = await _created(hass, _snapshot(evse={CEILING_TOPIC: None}))

    assert _for(created, tree, EVSE).available is False
    assert _for(created, tree, EVSE_2).available is True


async def test_a_coordinator_with_no_snapshot_yet_takes_the_control_down(hass: HomeAssistant) -> None:
    """A failed first refresh leaves `coordinator.data` unset.

    Every other read here goes through the snapshot, so the entity has to answer
    without one rather than raising inside a property Home Assistant polls.
    """
    tree = schema_one_tree()
    created = await _created(hass, schema_one_snapshot(tree))
    entity = _for(created, tree, EVSE)

    _refreshed(entity, None)

    assert entity.available is False
    assert entity.native_value is None
    assert entity.extra_state_attributes is None


async def test_the_control_is_unavailable_while_the_panel_is_offline(hass: HomeAssistant) -> None:
    tree = schema_one_tree()
    coordinator = _coordinator(schema_one_snapshot(tree))
    async_add_entities = MagicMock()
    await async_setup_entry(hass, coordinator.config_entry, async_add_entities)
    entity = _for(async_add_entities.call_args.args[0], tree, EVSE)
    assert entity.available is True

    coordinator.panel_offline = True

    assert entity.available is False


# ---------------------------------------------------------------------------
# The pending write
# ---------------------------------------------------------------------------


async def test_a_pending_write_is_an_attribute_and_not_the_state(hass: HomeAssistant) -> None:
    """The `$target` echo, rendered the way the priority select renders its own.

    Reporting the requested value as the state would show a limit the charger
    may never have accepted.
    """
    tree = schema_one_tree()
    pending = _published(tree, EVSE, LIMIT_TOPIC) - 8
    adapter = _fed_adapter((f"ebus/5/{EVSE}/{LIMIT_TOPIC}/$target", str(pending)))
    snapshot = adapter.build_snapshot()

    created = await _created(hass, snapshot)
    entity = _for(created, tree, EVSE)

    assert entity.extra_state_attributes == {"charge_current_limit_target": pending}
    assert entity.native_value == _published(tree, EVSE, LIMIT_TOPIC)
    assert _for(created, tree, EVSE_2).extra_state_attributes is None


# ---------------------------------------------------------------------------
# The other spelling
# ---------------------------------------------------------------------------


async def test_the_catalogued_spelling_produces_the_same_control(hass: HomeAssistant) -> None:
    """`charge-limit/{installer-max,owner-limit}` — the eBus 0.1 naming.

    Nothing in this integration names either spelling, so a charger publishing
    the specified one has to produce an identical entity. Asserted against the
    unrenamed capture rather than against literals, so the two are held to each
    other.
    """
    tree = schema_one_tree()
    published = _for(await _created(hass, schema_one_snapshot(tree)), tree, EVSE)
    catalogued = _for(await _created(hass, schema_one_snapshot(_renamed_to_catalog(EVSE))), tree, EVSE)

    assert catalogued.unique_id == published.unique_id
    assert catalogued.native_value == published.native_value
    assert catalogued.native_max_value == published.native_max_value
    assert catalogued.available == published.available


# ---------------------------------------------------------------------------
# The control's shape, taken from the declaration
# ---------------------------------------------------------------------------


def test_the_unit_is_the_one_the_charger_declares() -> None:
    """The entity's unit against the panel's, through the adapter's metadata row.

    The same check `evaluate_field_metadata` makes for every sensor, made here
    because a number carries a unit and this platform is not a sensor platform.
    """
    declared = schema_one_metadata()[FIELD_LIMIT]

    assert declared.resolved is True
    assert declared.unit == EVSE_CHARGE_CURRENT_LIMIT.native_unit_of_measurement
    assert EVSE_CHARGE_CURRENT_LIMIT.native_unit_of_measurement == UnitOfElectricCurrent.AMPERE


def test_the_step_is_the_granularity_the_charger_declares() -> None:
    """A step of 1 is a claim about the datatype, so it is checked against it."""
    tree = schema_one_tree()

    assert _declaration(tree, EVSE, "user-max-charge-current")["datatype"] == "integer"
    assert schema_one_metadata()[FIELD_LIMIT].datatype == "integer"
    assert EVSE_CHARGE_CURRENT_LIMIT.native_step == 1


def test_the_control_is_configuration_rather_than_measurement() -> None:
    assert EVSE_CHARGE_CURRENT_LIMIT.entity_category is EntityCategory.CONFIG
    assert EVSE_CHARGE_CURRENT_LIMIT.device_class is NumberDeviceClass.CURRENT
    assert EVSE_CHARGE_CURRENT_LIMIT.mode is NumberMode.BOX
    assert EVSE_CHARGE_CURRENT_LIMIT.native_min_value == 0


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def _live_client() -> tuple[object, MagicMock]:
    """A real transport over a real schema_1 adapter fed the capture.

    Not a mock: the point of the write tests is the topic and the payload, and a
    mocked client asserts only that the integration called the method it was
    written to call.
    """
    from span_panel_api.mqtt.client import MqttClientConfig, SpanMqttClient

    client = SpanMqttClient(
        host="192.168.1.1",
        serial_number=SCHEMA_ONE_PANEL,
        broker_config=MqttClientConfig(broker_host="h", username="u", password="p"),
    )
    client._adapter = _fed_adapter()
    bridge = MagicMock()
    client._bridge = bridge
    return client, bridge


async def test_the_write_reaches_the_wire_as_one_publish(hass: HomeAssistant) -> None:
    """Entity to broker, with the exact topic and payload asserted.

    The topic is addressed by the charger's **device id** while the entity holds
    its serial-harmonised snapshot key — two different strings, both plausible
    in a log, and only one of which any panel subscribes to.
    """
    tree = schema_one_tree()
    client, bridge = _live_client()
    created = await _created(hass, schema_one_snapshot(tree), client)
    entity = _for(created, tree, EVSE)
    asked = _published(tree, EVSE, CEILING_TOPIC) - 8

    await entity.async_set_native_value(float(asked))

    assert entity._evse_id != EVSE
    bridge.publish.assert_called_once_with(
        f"ebus/5/{EVSE}/config/user-max-charge-current/set", str(asked)
    )
    entity.coordinator.async_request_refresh.assert_awaited_once()


async def test_the_write_goes_to_the_charger_the_entity_belongs_to(hass: HomeAssistant) -> None:
    tree = schema_one_tree()
    client, bridge = _live_client()
    created = await _created(hass, schema_one_snapshot(tree), client)
    asked = _published(tree, EVSE_2, CEILING_TOPIC) - 8

    await _for(created, tree, EVSE_2).async_set_native_value(float(asked))

    bridge.publish.assert_called_once_with(
        f"ebus/5/{EVSE_2}/config/user-max-charge-current/set", str(asked)
    )


async def test_a_fractional_request_truncates_downward(hass: HomeAssistant) -> None:
    """The property is declared `integer`, so some whole number has to be chosen.

    Down, because this is a ceiling: asking for a fraction and getting the lower
    whole number is a slower charge, getting the higher one is a current the
    user did not request.
    """
    tree = schema_one_tree()
    client, bridge = _live_client()
    created = await _created(hass, schema_one_snapshot(tree), client)
    whole = _published(tree, EVSE, CEILING_TOPIC) - 8

    await _for(created, tree, EVSE).async_set_native_value(whole + 0.7)

    bridge.publish.assert_called_once_with(
        f"ebus/5/{EVSE}/config/user-max-charge-current/set", str(whole)
    )


async def test_a_value_above_the_ceiling_is_refused_before_it_reaches_the_wire(
    hass: HomeAssistant,
) -> None:
    """The second of the two range checks, and the one that matters.

    Home Assistant rejects a service call outside the range this entity last
    reported. This is the library rejecting it against what the panel is
    publishing now — which is the same check only until an installer
    recommissions the charger between the two.
    """
    tree = schema_one_tree()
    client, bridge = _live_client()
    created = await _created(hass, schema_one_snapshot(tree), client)
    over = _published(tree, EVSE, CEILING_TOPIC) + 1

    with pytest.raises(HomeAssistantError) as raised:
        await _for(created, tree, EVSE).async_set_native_value(float(over))

    assert raised.value.translation_key == "evse_charge_limit_failed"
    bridge.publish.assert_not_called()


async def test_a_command_the_transport_never_handed_over_is_reported(
    hass: HomeAssistant,
) -> None:
    """`FAILED`, produced by the real transport declining while disconnected.

    The bridge refuses rather than letting paho queue the publish across a
    reconnect, which is what makes `FAILED` a promise: nothing fires later
    against a panel nobody is watching. A promise that specific is worth telling
    the user, and it is a different fact from a refusal -- this one resolved an
    address and never handed it over.
    """
    tree = schema_one_tree()
    client, bridge = _live_client()
    bridge.publish.return_value = None
    created = await _created(hass, schema_one_snapshot(tree), client)
    entity = _for(created, tree, EVSE)
    asked = _published(tree, EVSE, CEILING_TOPIC) - 8

    with pytest.raises(HomeAssistantError) as raised:
        await entity.async_set_native_value(float(asked))

    assert raised.value.translation_key == "evse_charge_limit_not_delivered"
    placeholders = raised.value.translation_placeholders
    assert placeholders is not None
    assert placeholders["reason"] == "broker not connected; refused rather than queued"
    entity.coordinator.async_request_refresh.assert_not_awaited()


async def test_the_range_home_assistant_checks_is_the_commissioned_one(hass: HomeAssistant) -> None:
    """The first of the two checks, asserted through the range the entity reports.

    `number.async_set_value` refuses a service call outside `min_value` /
    `max_value` before it ever reaches this platform, and for a non-temperature
    device class those are `native_min_value` / `native_max_value` unconverted.
    So the range the entity reports has to be the panel's, which is what reading
    `native_max_value` off the wire buys — the state-side properties are not
    read here because they raise until the entity is attached to a platform.
    """
    tree = schema_one_tree()
    created = await _created(hass, schema_one_snapshot(tree))
    entity = _for(created, tree, EVSE)

    assert entity.native_max_value == _published(tree, EVSE, CEILING_TOPIC)
    assert entity.native_min_value == 0


async def test_a_client_with_no_evse_control_is_reported_rather_than_ignored(
    hass: HomeAssistant,
) -> None:
    """A transport that does not implement the control at all — a flat panel's."""

    class _NoEvseControl:
        """Everything but `set_evse_charge_limit`."""

    tree = schema_one_tree()
    created = await _created(hass, schema_one_snapshot(tree), _NoEvseControl())

    with pytest.raises(HomeAssistantError) as raised:
        await _for(created, tree, EVSE).async_set_native_value(16.0)

    assert raised.value.translation_key == "evse_charge_limit_unsupported"


# ---------------------------------------------------------------------------
# The declarations this entity makes about itself
# ---------------------------------------------------------------------------


def test_the_description_names_its_source_field_and_why_it_is_exempt() -> None:
    """Both `field_path` and `derived`, as a schema-conditional description must.

    The only settable entity to carry the pair, and the Repair naming a dead
    field has to be able to name this entity too.
    """
    assert EVSE_CHARGE_CURRENT_LIMIT.field_path == FIELD_LIMIT
    assert EVSE_CHARGE_CURRENT_LIMIT.derived is DerivedReason.SCHEMA_CONDITIONAL_FIELD
    assert EVSE_CHARGE_CURRENT_LIMIT in platform_descriptions()


def test_every_field_this_control_reads_is_enumerated() -> None:
    """Four reads, four annotations, each checked against the adapters elsewhere.

    The pair the panel publishes as readings carries a schema_1 metadata row; the
    pair that describes a command carries none on either adapter.
    """
    assert RESIDUAL_EXEMPT_PATHS[FIELD_LIMIT] is Producibility.SCHEMA_1_ONLY
    assert RESIDUAL_EXEMPT_PATHS[FIELD_CEILING] is Producibility.SCHEMA_1_ONLY
    assert RESIDUAL_EXEMPT_PATHS[FIELD_TARGET] is Producibility.NEITHER
    assert RESIDUAL_EXEMPT_PATHS[FIELD_SETTABLE] is Producibility.NEITHER
