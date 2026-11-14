"""The EVSE charge-current limit, as the integration's first number entity.

The only settable property the v1.0 catch-up surfaces, so this is the one place
in the catch-up where being wrong reaches the panel rather than the dashboard.
The tests are shaped around that.

**Nothing here names an amperage.** Every expectation is computed from the
capture, and where the capture publishes the same value on both chargers — the
same ceiling on each — the test republishes differing values first, because an
assertion satisfied by reading one charger twice proves nothing about two.

**The capture sets no user limit.** From firmware r202639 a charger publishes
its user limit only once a user sets one, and `ebus-panel-sim` 0.9.0, which the
reference tree was captured with, publishes that behaviour: each charger
declares the limit settable and publishes no value for it. The control then
shows the commissioned ceiling, the limit the charger is enforcing. A test about
a valued limit publishes one (`_user_limit`) the way a charger would after a
user set it.

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
from span_panel_api.protocol import SchemaAdapter

from custom_components.span_panel import PLATFORMS, SpanPanelRuntimeData
from custom_components.span_panel.const import DOMAIN
from custom_components.span_panel.curation import CurationOverlay
from custom_components.span_panel.field_paths import (
    RESIDUAL_EXEMPT_PATHS,
    Producibility,
    declared_field_paths,
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
from .captures_replay import HOMIE_PREFIX, replay
from .factories import pv_binding_for

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


def _user_limit(tree: dict[str, dict[str, str]], device_id: str) -> int:
    """A limit a user would set on this charger: inside its ceiling, and not equal to it.

    Below the ceiling so it cannot be mistaken for the ceiling standing in for
    an absent limit, which is what the control shows when none is set.
    """
    return _published(tree, device_id, CEILING_TOPIC) - 8


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


def _declared_range(device_id: str, range_format: str | None) -> dict[str, dict[str, str]]:
    """The capture with one charger's user limit declaring `range_format`, or none.

    `None` removes the `$format`, the charger that declares no range; anything
    else replaces it, as a charger commissioned with another range would.
    """
    tree = schema_one_tree()

    def declare(description: dict[str, dict[str, dict[str, dict[str, dict[str, object]]]]]) -> None:
        declared = description["nodes"][CONFIG_NODE]["properties"]["user-max-charge-current"]
        if range_format is None:
            declared.pop("format")
        else:
            declared["format"] = range_format

    return _mutated_description(tree, device_id, declare)


def _renamed_to_catalog(tree: dict[str, dict[str, str]], device_id: str) -> dict[str, dict[str, str]]:
    """`tree` with one charger publishing the eBus `charge-limit` spelling, in place.

    The naming the catalog specifies and no producer we have publishes. The
    entity must not be able to tell: the library resolves the node from the
    `$description`, so the rename is a rename and nothing else. The limit's
    value moves only where the charger publishes one: unset, it is unpublished
    under either spelling. The ceiling is always published, so its absence is a
    fixture fault and fails here.
    """
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
    if LIMIT_TOPIC in tree[device_id]:
        tree[device_id]["charge-limit/owner-limit"] = tree[device_id].pop(LIMIT_TOPIC)
    return tree


def _fed_adapter(*extra: tuple[str, str]) -> SchemaAdapter:
    """A real schema_1 adapter fed the capture the way the broker replays it.

    Through `captures_replay.replay`, the suite's one replay of a tree, so the
    capture reaches the adapter the library dispatches to and under the rule
    that replay applies to every value.

    `extra` then delivers messages as the broker would, each payload exactly as
    given: a value a charger publishes after the capture was taken, a Homie
    `$target`, published on `<node>/<property>/$target`, which the fixture's
    flat `{topic: value}` shape has no room for, or a zero-length payload,
    which is a clear.
    """
    adapter = replay(schema_one_tree())
    for topic, value in extra:
        adapter.handle_message(topic, value)
    return adapter


def _wire(device_id: str, topic: str) -> str:
    """The broker topic a charger publishes one of its properties on."""
    return f"{HOMIE_PREFIX}/{device_id}/{topic}"


# ---------------------------------------------------------------------------
# Building the entities
# ---------------------------------------------------------------------------


def _coordinator(snapshot: SpanPanelSnapshot, client: object | None = None) -> MagicMock:
    coordinator = MagicMock()
    coordinator.data = snapshot
    coordinator.panel_offline = False
    coordinator.transport_dead = False
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
        coordinator=coordinator,
        panel_device_id="panel-device-id",
        curation=CurationOverlay.empty(),
        pv_binding=pv_binding_for(snapshot),
        setup_snapshot=snapshot,
    )
    coordinator.async_request_refresh = AsyncMock()
    return coordinator


async def _created(
    hass: HomeAssistant, snapshot: SpanPanelSnapshot, client: object | None = None
) -> list[SpanEvseNumber]:
    """Everything `number.async_setup_entry` creates for one snapshot.

    Every call, not the last one: the platform adds the curated numbers in their
    own call before it attempts the adopted ones, so that a device this
    integration models nothing for cannot cost the user a control it does model.
    """
    coordinator = _coordinator(snapshot, client)
    async_add_entities = MagicMock()

    await async_setup_entry(hass, coordinator.config_entry, async_add_entities)

    added: Sequence[SpanEvseNumber] = [
        entity for call in async_add_entities.call_args_list for entity in call.args[0]
    ]
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
    # Whatever the charger published under the node goes with its declaration:
    # the ceiling, and a user limit wherever one had been set.
    for topic in [topic for topic in tree[EVSE] if topic.startswith(f"{CONFIG_NODE}/")]:
        del tree[EVSE][topic]
    assert CEILING_TOPIC not in tree[EVSE] and LIMIT_TOPIC not in tree[EVSE]

    created = await _created(hass, schema_one_snapshot(tree))

    assert [entity._evse_id for entity in created] == [_serial(tree, EVSE_2)]


# ---------------------------------------------------------------------------
# Reading — per charger, from the wire
# ---------------------------------------------------------------------------


async def test_each_number_reads_its_own_charger(hass: HomeAssistant) -> None:
    """Read each charger's own limit, from values published and made to differ first.

    The capture sets no limit on either charger and publishes the same ceiling
    on both, so the state each shows as captured is satisfied by a platform that
    reads one charger twice.
    """
    tree = schema_one_tree()
    first = _user_limit(tree, EVSE)
    second = _user_limit(tree, EVSE_2) - 8
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
    """A limit a user lowers and a ceiling an installer recommissions, on one entity.

    Starts from a limit the charger publishes, since the capture sets none.
    """
    tree = schema_one_tree()
    first = _user_limit(tree, EVSE)
    created = await _created(hass, _snapshot(evse={LIMIT_TOPIC: first}))
    entity = _for(created, tree, EVSE)
    assert entity.native_value == first

    lowered = first - 8
    recommissioned = _published(tree, EVSE, CEILING_TOPIC) - 8
    _refreshed(entity, _snapshot(evse={LIMIT_TOPIC: lowered, CEILING_TOPIC: recommissioned}))

    assert entity.native_value == lowered
    assert entity.native_max_value == recommissioned


async def test_the_capture_sets_no_user_limit_and_each_number_shows_its_ceiling(
    hass: HomeAssistant,
) -> None:
    """The capture's own state, end to end, on both chargers.

    Each charger declares the limit settable and publishes no value for it, which
    is r202639 with no user limit set. The control is still offered, shows the
    ceiling as the limit in force, is bounded by it, and has nothing pending.
    """
    tree = schema_one_tree()
    created = await _created(hass, schema_one_snapshot(tree))

    for device_id in (EVSE, EVSE_2):
        assert _declaration(tree, device_id, "user-max-charge-current")["settable"] is True
        assert LIMIT_TOPIC not in tree[device_id]
        ceiling = _published(tree, device_id, CEILING_TOPIC)

        entity = _for(created, tree, device_id)

        assert entity.native_value == ceiling
        assert entity.native_max_value == ceiling
        assert entity.available is True
        assert entity.extra_state_attributes is None


async def test_a_limit_set_and_cleared_moves_the_state_off_the_ceiling_and_back(
    hass: HomeAssistant,
) -> None:
    """The user limit appearing and going away on a live entity, through the transport.

    A user setting a limit makes the charger publish one; removing it clears the
    retained value with a zero-length payload, which the library reads as
    unpublished. The control follows both, back to the ceiling rather than
    holding the last limit it saw.
    """
    tree = schema_one_tree()
    ceiling = _published(tree, EVSE, CEILING_TOPIC)
    limit = _user_limit(tree, EVSE)
    adapter = _fed_adapter()
    entity = _for(await _created(hass, adapter.build_snapshot()), tree, EVSE)
    assert entity.native_value == ceiling

    adapter.handle_message(_wire(EVSE, LIMIT_TOPIC), str(limit))
    _refreshed(entity, adapter.build_snapshot())
    assert entity.native_value == limit

    adapter.handle_message(_wire(EVSE, LIMIT_TOPIC), "")
    _refreshed(entity, adapter.build_snapshot())
    assert entity.native_value == ceiling
    assert entity.available is True


async def test_an_unpublished_limit_shows_the_ceiling(hass: HomeAssistant) -> None:
    """No user limit means the commissioned ceiling is the limit in force.

    From r202639 a charger nobody has limited publishes no user limit at all.
    The control is still offered, and shows what the charger is enforcing.
    """
    tree = schema_one_tree()
    recommissioned = _published(tree, EVSE, CEILING_TOPIC) - 8
    created = await _created(hass, _snapshot(evse={LIMIT_TOPIC: None, CEILING_TOPIC: recommissioned}))
    entity = _for(created, tree, EVSE)

    assert entity.native_value == recommissioned
    assert entity.available is True


async def test_a_retained_limit_equal_to_the_ceiling_reads_the_same(hass: HomeAssistant) -> None:
    """A stale retained limit at the ceiling and an absent one are the same setting."""
    tree = schema_one_tree()
    ceiling = _published(tree, EVSE, CEILING_TOPIC)
    retained = _for(await _created(hass, _snapshot(evse={LIMIT_TOPIC: ceiling})), tree, EVSE)
    absent = _for(await _created(hass, _snapshot(evse={LIMIT_TOPIC: None})), tree, EVSE)

    assert retained.native_value == absent.native_value == ceiling
    assert retained.available is absent.available is True


async def test_neither_limit_nor_ceiling_is_unknown_rather_than_zero(hass: HomeAssistant) -> None:
    """With nothing published there is no reading, and no bound to offer either."""
    tree = schema_one_tree()
    created = await _created(hass, _snapshot(evse={LIMIT_TOPIC: None, CEILING_TOPIC: None}))
    entity = _for(created, tree, EVSE)

    assert entity.native_value is None
    assert entity.available is False


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
    entity = _for(
        [entity for call in async_add_entities.call_args_list for entity in call.args[0]], tree, EVSE
    )
    assert entity.available is True

    coordinator.panel_offline = True

    assert entity.available is False


# ---------------------------------------------------------------------------
# The pending write
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("limit_set", [False, True], ids=["no-user-limit", "user-limit-set"])
async def test_a_pending_write_is_an_attribute_and_not_the_state(
    hass: HomeAssistant, limit_set: bool
) -> None:
    """The `$target` echo, rendered the way the priority select renders its own.

    Reporting the requested value as the state would show a limit the charger
    may never have accepted. Asserted with no user limit set, as captured, where
    the state is the ceiling, and with one set, where it is that limit: a write
    in flight replaces neither.
    """
    tree = schema_one_tree()
    enforced = _user_limit(tree, EVSE) if limit_set else _published(tree, EVSE, CEILING_TOPIC)
    pending = enforced - 8
    published = [(_wire(EVSE, LIMIT_TOPIC), str(enforced))] if limit_set else []
    adapter = _fed_adapter(*published, (_wire(EVSE, f"{LIMIT_TOPIC}/$target"), str(pending)))
    snapshot = adapter.build_snapshot()

    created = await _created(hass, snapshot)
    entity = _for(created, tree, EVSE)

    assert entity.extra_state_attributes == {"charge_current_limit_target": pending}
    assert entity.native_value == enforced
    assert _for(created, tree, EVSE_2).extra_state_attributes is None


# ---------------------------------------------------------------------------
# The other spelling
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("limit_set", [False, True], ids=["no-user-limit", "user-limit-set"])
async def test_the_catalogued_spelling_produces_the_same_control(
    hass: HomeAssistant, limit_set: bool
) -> None:
    """`charge-limit/{installer-max,owner-limit}` — the eBus 0.1 naming.

    Nothing in this integration names either spelling, so a charger publishing
    the specified one has to produce an identical entity. Asserted against the
    unrenamed capture rather than against literals, so the two are held to each
    other: as captured, where the state comes from the ceiling, and with a user
    limit published, where it comes from the limit, so both renamed properties
    are read.
    """

    def tree_as_published() -> dict[str, dict[str, str]]:
        tree = schema_one_tree()
        if limit_set:
            tree[EVSE][LIMIT_TOPIC] = str(_user_limit(tree, EVSE))
        return tree

    tree = tree_as_published()
    published = _for(await _created(hass, schema_one_snapshot(tree)), tree, EVSE)
    catalogued = _for(
        await _created(hass, schema_one_snapshot(_renamed_to_catalog(tree_as_published(), EVSE))),
        tree,
        EVSE,
    )

    expected = _user_limit(tree, EVSE) if limit_set else _published(tree, EVSE, CEILING_TOPIC)
    assert published.native_value == expected

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


def test_the_description_minimum_is_only_the_fallback_for_an_undeclared_range() -> None:
    """Zero is what a charger that declares no minimum is offered, not the rule.

    The entity's minimum is the one the charger declares; see the range tests
    below, which read it off the entity rather than the description.
    """
    assert EVSE_CHARGE_CURRENT_LIMIT.native_min_value == 0


# ---------------------------------------------------------------------------
# The range the charger declares
# ---------------------------------------------------------------------------


async def test_the_declared_range_bounds_the_control(hass: HomeAssistant) -> None:
    """The capture declares `6:32`: the library refuses 0-5 A, so the control does not offer it."""
    tree = schema_one_tree()
    assert _declaration(tree, EVSE, "user-max-charge-current")["format"] == "6:32"
    ceiling = _published(tree, EVSE, CEILING_TOPIC)
    entity = _for(await _created(hass, schema_one_snapshot(tree)), tree, EVSE)

    assert entity.native_min_value == 6
    assert entity.native_max_value == min(32, ceiling)


async def test_a_charger_declaring_no_range_keeps_the_old_bounds(hass: HomeAssistant) -> None:
    tree = _declared_range(EVSE, None)
    ceiling = _published(tree, EVSE, CEILING_TOPIC)
    entity = _for(await _created(hass, schema_one_snapshot(tree)), tree, EVSE)

    assert entity.native_min_value == 0
    assert entity.native_max_value == ceiling


async def test_a_declared_maximum_below_the_ceiling_bounds_the_maximum(hass: HomeAssistant) -> None:
    tree = _declared_range(EVSE, "6:24")
    ceiling = _published(tree, EVSE, CEILING_TOPIC)
    assert ceiling > 24
    entity = _for(await _created(hass, schema_one_snapshot(tree)), tree, EVSE)

    assert entity.native_min_value == 6
    assert entity.native_max_value == 24


async def test_a_ceiling_below_the_declared_maximum_still_bounds_the_maximum(
    hass: HomeAssistant,
) -> None:
    tree = _declared_range(EVSE, "6:40")
    ceiling = _published(tree, EVSE, CEILING_TOPIC)
    assert ceiling < 40
    entity = _for(await _created(hass, schema_one_snapshot(tree)), tree, EVSE)

    assert entity.native_max_value == ceiling


@pytest.mark.parametrize(
    ("range_format", "declared_minimum", "declared_maximum", "minimum"),
    [("6:", 6, None, 6), (":24", None, 24, 0)],
    ids=["open-maximum", "open-minimum"],
)
async def test_an_open_side_falls_back_for_that_side_only(
    hass: HomeAssistant,
    range_format: str,
    declared_minimum: int | None,
    declared_maximum: int | None,
    minimum: int,
) -> None:
    tree = _declared_range(EVSE, range_format)
    snapshot = schema_one_snapshot(tree)
    declared = snapshot.evse[_serial(tree, EVSE)]
    assert (declared.charge_current_limit_minimum_a, declared.charge_current_limit_maximum_a) == (
        declared_minimum,
        declared_maximum,
    )
    ceiling = _published(tree, EVSE, CEILING_TOPIC)
    entity = _for(await _created(hass, snapshot), tree, EVSE)

    assert entity.native_min_value == minimum
    assert entity.native_max_value == (ceiling if declared_maximum is None else declared_maximum)


async def test_the_range_follows_the_charger_when_it_is_recommissioned(hass: HomeAssistant) -> None:
    """The declared bounds are read on every update, as the ceiling is."""
    tree = schema_one_tree()
    entity = _for(await _created(hass, schema_one_snapshot(tree)), tree, EVSE)
    assert (entity.native_min_value, entity.native_max_value) == (6, 32)

    _refreshed(entity, schema_one_snapshot(_declared_range(EVSE, "10:20")))
    assert (entity.native_min_value, entity.native_max_value) == (10, 20)

    _refreshed(entity, schema_one_snapshot(_declared_range(EVSE, None)))
    assert (entity.native_min_value, entity.native_max_value) == (0, _published(tree, EVSE, CEILING_TOPIC))


async def test_the_range_is_dropped_when_the_limit_stops_being_settable(
    hass: HomeAssistant,
) -> None:
    """An entity created while settable then receives a snapshot where it is not.

    The platform gates creation on `$settable` only at setup; the entity lives
    on and `_apply` runs on every later snapshot. The declared range is applied
    only where the limit is settable, so the bounds go back to the fallback.
    """
    tree = schema_one_tree()
    ceiling = _published(tree, EVSE, CEILING_TOPIC)
    entity = _for(await _created(hass, schema_one_snapshot(tree)), tree, EVSE)
    assert (entity.native_min_value, entity.native_max_value) == (6, 32)

    refused = _not_settable(EVSE)
    assert refused.evse[_serial(tree, EVSE)].charge_current_limit_settable is False
    _refreshed(entity, refused)

    assert (entity.native_min_value, entity.native_max_value) == (0, ceiling)


async def test_a_declared_maximum_bounds_the_control_while_the_ceiling_is_unpublished(
    hass: HomeAssistant,
) -> None:
    """The lower of each bound present: with no ceiling, the declared maximum alone."""
    tree = _declared_range(EVSE, "6:24")
    del tree[EVSE][CEILING_TOPIC]
    entity = _for(await _created(hass, schema_one_snapshot(tree)), tree, EVSE)

    assert entity.native_max_value == 24
    assert entity.available is False


async def test_a_declared_minimum_above_the_maximum_is_clamped_to_it(hass: HomeAssistant) -> None:
    """A charger commissioned below its declared minimum must not report min > max."""
    tree = _declared_range(EVSE, "20:32")
    tree[EVSE][CEILING_TOPIC] = "16"
    entity = _for(await _created(hass, schema_one_snapshot(tree)), tree, EVSE)

    assert entity.native_max_value == 16
    assert entity.native_min_value == 16


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


async def test_a_failure_names_the_charger_the_user_sees(hass: HomeAssistant) -> None:
    """`charger` reaches a person, so it carries the name on the device card.

    The wire addresses a charger by node id and the snapshot keys it by a
    serial-harmonised id. Neither string appears anywhere the user looks, so a
    notification carrying one names a charger they cannot find.
    """
    tree = schema_one_tree()
    client, bridge = _live_client()
    bridge.publish.return_value = None
    created = await _created(hass, schema_one_snapshot(tree), client)
    entity = _for(created, tree, EVSE)
    asked = _published(tree, EVSE, CEILING_TOPIC) - 8

    with pytest.raises(HomeAssistantError) as raised:
        await entity.async_set_native_value(float(asked))

    placeholders = raised.value.translation_placeholders
    assert placeholders is not None
    device_info = entity._attr_device_info
    assert device_info is not None
    assert placeholders["charger"] == device_info["name"]
    assert placeholders["charger"] != entity._evse_id


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
    assert entity.native_min_value == 6


# ---------------------------------------------------------------------------
# The declarations this entity makes about itself
# ---------------------------------------------------------------------------


def test_the_description_declares_its_source_field() -> None:
    """A plain declaration, which the producible gate covers.

    The Repair naming a dead limit has to be able to name this entity, and the
    entity has to go unavailable with it.
    """
    assert EVSE_CHARGE_CURRENT_LIMIT.field_path == FIELD_LIMIT
    assert EVSE_CHARGE_CURRENT_LIMIT.derived is None
    assert EVSE_CHARGE_CURRENT_LIMIT in platform_descriptions()
    assert FIELD_LIMIT in declared_field_paths()


def test_every_field_this_control_reads_is_accounted_for() -> None:
    """Four reads: one declaration and three annotations, each checked against the adapter elsewhere.

    The limit is the entity's reading and is declared. The ceiling is mapped but
    is a bound rather than the reading, so validation is kept from it. The pair
    that describes a command carries no row at all.
    """
    assert FIELD_LIMIT not in RESIDUAL_EXEMPT_PATHS
    assert RESIDUAL_EXEMPT_PATHS[FIELD_CEILING] is Producibility.MAPPED_UNVALIDATED
    assert RESIDUAL_EXEMPT_PATHS[FIELD_TARGET] is Producibility.UNMAPPED
    assert RESIDUAL_EXEMPT_PATHS[FIELD_SETTABLE] is Producibility.UNMAPPED
