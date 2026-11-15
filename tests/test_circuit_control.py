"""Tests for Span Panel circuit control functionality (switches, relay operations)."""

from dataclasses import replace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, PropertyMock

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import pytest
from span_panel_api.exceptions import SpanPanelServerError

from custom_components.span_panel.control_gate import ControlPolicy
from custom_components.span_panel.switch import (
    SpanPanelCircuitsSwitch,
    async_setup_entry,
)

from .factories import SpanCircuitSnapshotFactory, SpanPanelSnapshotFactory


@pytest.fixture(autouse=True)
def expected_lingering_timers():
    """Fix expected lingering timers for tests."""
    return True


def _make_coordinator(
    hass: HomeAssistant,
    circuits: dict[str, Any],
    *,
    panel_offline: bool = False,
    options: dict[str, Any] | None = None,
) -> MagicMock:
    """Create a coordinator mock with a real snapshot from factories."""
    snapshot = SpanPanelSnapshotFactory.create(circuits=circuits)
    coordinator = MagicMock()
    coordinator.hass = hass
    coordinator.data = snapshot
    coordinator.panel_offline = panel_offline
    coordinator.config_entry = MagicMock()
    coordinator.config_entry.title = "SPAN Panel"
    coordinator.config_entry.data = {}
    coordinator.config_entry.options = options or {}
    coordinator.request_reload = MagicMock()
    coordinator.async_request_refresh = AsyncMock()
    coordinator.client = AsyncMock()
    return coordinator


@pytest.mark.asyncio
async def test_switch_creation_for_controllable_circuit(hass: HomeAssistant) -> None:
    """Switches are created only for user-controllable circuits."""
    controllable = SpanCircuitSnapshotFactory.create(
        circuit_id="1",
        name="Kitchen Outlets",
        is_user_controllable=True,
    )
    non_controllable = SpanCircuitSnapshotFactory.create(
        circuit_id="2",
        name="Main Feed",
        is_user_controllable=False,
    )
    coordinator = _make_coordinator(hass, {"1": controllable, "2": non_controllable})

    entities: list[Any] = []
    mock_entry = MagicMock()
    mock_entry.title = "SPAN Panel"
    mock_entry.data = {}
    mock_entry.runtime_data = MagicMock(
        control_policy=ControlPolicy.default(), coordinator=coordinator, setup_snapshot=coordinator.data
    )

    await async_setup_entry(hass, mock_entry, lambda e, **kw: entities.extend(e))

    assert len(entities) == 1
    assert entities[0]._circuit_id == "1"


@pytest.mark.asyncio
async def test_switch_turn_on_operation(hass: HomeAssistant) -> None:
    """Turning on a switch sends CLOSED to the client."""
    circuit = SpanCircuitSnapshotFactory.create(
        circuit_id="1",
        name="Kitchen Outlets",
        relay_state="OPEN",
    )
    coordinator = _make_coordinator(hass, {"1": circuit})

    switch = SpanPanelCircuitsSwitch(coordinator, "1", "SPAN Panel")
    switch.async_write_ha_state = MagicMock()
    await switch.async_turn_on()

    coordinator.client.set_circuit_relay.assert_called_once_with("1", "CLOSED")
    assert switch.is_on is True


@pytest.mark.asyncio
async def test_switch_turn_off_operation(hass: HomeAssistant) -> None:
    """Turning off a switch sends OPEN to the client."""
    circuit = SpanCircuitSnapshotFactory.create(
        circuit_id="1",
        name="Kitchen Outlets",
        relay_state="CLOSED",
    )
    coordinator = _make_coordinator(hass, {"1": circuit})

    switch = SpanPanelCircuitsSwitch(coordinator, "1", "SPAN Panel")
    switch.async_write_ha_state = MagicMock()
    await switch.async_turn_off()

    coordinator.client.set_circuit_relay.assert_called_once_with("1", "OPEN")
    assert switch.is_on is False


@pytest.mark.asyncio
async def test_a_refused_relay_command_is_raised_and_leaves_the_switch_alone(
    hass: HomeAssistant,
) -> None:
    """The panel declares this circuit's relay non-commandable.

    Nothing is published, so no coordinator update is coming to correct an
    optimistic write -- which is why `_async_set_relay` writes its state only
    after the publish, and why the refusal has to reach the caller instead of
    being logged and swallowed.
    """
    circuit = SpanCircuitSnapshotFactory.create(
        circuit_id="1",
        name="Kitchen Outlets",
        relay_state="CLOSED",
    )
    coordinator = _make_coordinator(hass, {"1": circuit})
    coordinator.client.set_circuit_relay = AsyncMock(
        side_effect=SpanPanelServerError("Circuit '1' declares its relay non-commandable")
    )

    switch = SpanPanelCircuitsSwitch(coordinator, "1", "SPAN Panel")
    switch.async_write_ha_state = MagicMock()

    with pytest.raises(HomeAssistantError) as raised:
        await switch.async_turn_off()

    assert raised.value.translation_key == "circuit_relay_failed"
    placeholders = raised.value.translation_placeholders
    assert placeholders is not None
    assert placeholders["circuit"] == "Kitchen Outlets"
    assert placeholders["reason"] == "Circuit '1' declares its relay non-commandable"
    # The relay never moved, so neither did the switch.
    assert switch.is_on is True
    switch.async_write_ha_state.assert_not_called()


@pytest.mark.asyncio
async def test_a_refusal_names_an_unnamed_circuit_by_its_panel_positions(
    hass: HomeAssistant,
) -> None:
    """The identifier the entity was named from, not the wire id."""
    circuit = SpanCircuitSnapshotFactory.create(
        circuit_id="abc123",
        name="",
        relay_state="OPEN",
        tabs=[30, 32],
    )
    coordinator = _make_coordinator(hass, {"abc123": circuit})
    coordinator.client.set_circuit_relay = AsyncMock(
        side_effect=SpanPanelServerError("Circuit 'abc123' declares its relay non-commandable")
    )

    switch = SpanPanelCircuitsSwitch(coordinator, "abc123", "SPAN Panel")
    switch.async_write_ha_state = MagicMock()

    with pytest.raises(HomeAssistantError) as raised:
        await switch.async_turn_on()

    placeholders = raised.value.translation_placeholders
    assert placeholders is not None
    assert placeholders["circuit"] == "Circuit 30 32"


def test_switch_state_reflects_relay_state(hass: HomeAssistant) -> None:
    """Switch is_on mirrors the circuit relay state."""
    closed = SpanCircuitSnapshotFactory.create(
        circuit_id="1", name="Kitchen", relay_state="CLOSED"
    )
    coordinator_closed = _make_coordinator(hass, {"1": closed})
    assert (
        SpanPanelCircuitsSwitch(coordinator_closed, "1", "SPAN Panel").is_on
        is True
    )

    opened = SpanCircuitSnapshotFactory.create(
        circuit_id="1", name="Kitchen", relay_state="OPEN"
    )
    coordinator_open = _make_coordinator(hass, {"1": opened})
    assert (
        SpanPanelCircuitsSwitch(coordinator_open, "1", "SPAN Panel").is_on
        is False
    )


def test_switch_handles_missing_circuit(hass: HomeAssistant) -> None:
    """Constructing a switch for a missing circuit raises ValueError."""
    coordinator = _make_coordinator(hass, {})

    with pytest.raises(ValueError, match="Circuit 1 not found"):
        SpanPanelCircuitsSwitch(coordinator, "1", "SPAN Panel")


def test_switch_coordinator_update_handling(hass: HomeAssistant) -> None:
    """Switch reflects new relay state after coordinator data changes."""
    circuit = SpanCircuitSnapshotFactory.create(
        circuit_id="1",
        name="Kitchen Outlets",
        relay_state="CLOSED",
    )
    coordinator = _make_coordinator(hass, {"1": circuit})

    switch = SpanPanelCircuitsSwitch(coordinator, "1", "SPAN Panel")
    switch.hass = hass
    switch.entity_id = "switch.span_panel_kitchen_outlets_breaker"
    switch.registry_entry = MagicMock()
    switch.platform = MagicMock(platform_name="switch")

    assert switch.is_on is True

    # Replace snapshot with an OPEN relay
    updated = replace(circuit, relay_state="OPEN")
    coordinator.data = SpanPanelSnapshotFactory.create(circuits={"1": updated})
    switch._handle_coordinator_update()

    assert switch.is_on is False


def test_circuit_name_change_triggers_reload_request(hass: HomeAssistant) -> None:
    """Changing a circuit name triggers an integration reload."""
    circuit = SpanCircuitSnapshotFactory.create(
        circuit_id="1",
        name="Kitchen Outlets",
        relay_state="CLOSED",
    )
    coordinator = _make_coordinator(hass, {"1": circuit})

    switch = SpanPanelCircuitsSwitch(coordinator, "1", "SPAN Panel")
    switch.hass = hass
    switch.entity_id = "switch.span_panel_kitchen_outlets_breaker"
    switch.registry_entry = MagicMock()
    switch.platform = MagicMock(platform_name="switch")

    # Replace snapshot with a renamed circuit
    renamed = replace(circuit, name="New Kitchen Outlets")
    coordinator.data = SpanPanelSnapshotFactory.create(circuits={"1": renamed})
    switch._handle_coordinator_update()

    coordinator.request_reload.assert_called_once()


def test_switch_unavailable_when_panel_offline(hass: HomeAssistant) -> None:
    """Switches become unavailable when the panel is offline."""
    circuit = SpanCircuitSnapshotFactory.create(circuit_id="1")
    coordinator = _make_coordinator(hass, {"1": circuit}, panel_offline=True)

    switch = SpanPanelCircuitsSwitch(coordinator, "1", "SPAN Panel")
    assert switch.available is False


def test_switch_extra_state_attributes_include_tabs_and_voltage(hass: HomeAssistant) -> None:
    """Switches expose circuit tab and voltage metadata."""
    circuit = SpanCircuitSnapshotFactory.create(circuit_id="1", tabs=[1])
    coordinator = _make_coordinator(hass, {"1": circuit})

    switch = SpanPanelCircuitsSwitch(coordinator, "1", "SPAN Panel")
    assert switch.extra_state_attributes == {"tabs": "tabs [1]", "voltage": 120}


def test_switch_relay_state_target_shows_pending_state(hass: HomeAssistant) -> None:
    """Switch should show the target state while a relay command is pending."""
    # Panel reports OPEN but has a pending target of CLOSED
    circuit = SpanCircuitSnapshotFactory.create(
        circuit_id="1", relay_state="OPEN", relay_state_target="CLOSED"
    )
    coordinator = _make_coordinator(hass, {"1": circuit})

    switch = SpanPanelCircuitsSwitch(coordinator, "1", "SPAN Panel")
    # Target differs from actual — switch should show target (CLOSED = on)
    assert switch.is_on is True

    # Once the panel confirms the relay change, target matches actual
    confirmed = SpanCircuitSnapshotFactory.create(
        circuit_id="1", relay_state="CLOSED", relay_state_target="CLOSED"
    )
    coordinator.data = SpanPanelSnapshotFactory.create(circuits={"1": confirmed})
    switch._update_is_on()
    assert switch.is_on is True


def test_switch_relay_state_target_none_uses_actual_state(hass: HomeAssistant) -> None:
    """Switch should use actual relay state when no target is published."""
    circuit = SpanCircuitSnapshotFactory.create(
        circuit_id="1", relay_state="OPEN", relay_state_target=None
    )
    coordinator = _make_coordinator(hass, {"1": circuit})

    switch = SpanPanelCircuitsSwitch(coordinator, "1", "SPAN Panel")
    assert switch.is_on is False


@pytest.mark.asyncio
async def test_switch_async_setup_entry_filters_supported_circuits(
    hass: HomeAssistant,
) -> None:
    """Setup should only create switches for supported controllable circuits."""
    controllable = SpanCircuitSnapshotFactory.create(
        circuit_id="1",
        is_user_controllable=True,
    )
    locked = SpanCircuitSnapshotFactory.create(
        circuit_id="2",
        is_user_controllable=False,
    )
    evse_upstream = replace(
        SpanCircuitSnapshotFactory.create(
            circuit_id="3",
            name="EV Upstream",
            is_user_controllable=True,
            tabs=[3, 4],
        ),
        device_type="evse",
        relative_position="UPSTREAM",
    )
    pv_downstream = replace(
        SpanCircuitSnapshotFactory.create(
            circuit_id="4",
            name="Solar",
            is_user_controllable=True,
            tabs=[5, 6],
        ),
        device_type="pv",
        relative_position="DOWNSTREAM",
    )

    coordinator = _make_coordinator(
        hass,
        {
            "1": controllable,
            "2": locked,
            "3": evse_upstream,
            "4": pv_downstream,
        }
    )

    entities: list[Any] = []
    mock_entry = MagicMock()
    mock_entry.title = "SPAN Panel"
    mock_entry.data = {}
    mock_entry.runtime_data = MagicMock(
        control_policy=ControlPolicy.default(), coordinator=coordinator, setup_snapshot=coordinator.data
    )

    await async_setup_entry(hass, mock_entry, lambda e, **kw: entities.extend(e))

    assert len(entities) == 2
    assert {entity._circuit_id for entity in entities} == {"1", "4"}


def test_switch_existing_entity_uses_solar_fallback_name(hass: HomeAssistant) -> None:
    """An unnamed solar circuit's device is named "Solar", and its switch carries the label.

    The id the switch already has was built from the same fallback, so the base
    reads it back as "Solar" then `breaker`.
    """
    solar = replace(
        SpanCircuitSnapshotFactory.create(
            circuit_id="15",
            name="",
            tabs=[15],
            is_user_controllable=True,
        ),
        device_type="pv",
    )
    coordinator = _make_coordinator(hass, {"15": solar})

    with pytest.MonkeyPatch.context() as mp:
        registry = MagicMock()
        registry.async_get_entity_id.return_value = "switch.solar_breaker"
        mp.setattr(
            "custom_components.span_panel.switch.er.async_get",
            lambda _hass: registry,
        )
        switch = SpanPanelCircuitsSwitch(coordinator, "15", "SPAN Panel")

    assert switch.name == "Breaker"
    assert switch.device_info is not None
    assert switch.device_info.get("name") == "Solar"
    assert switch.suggested_object_id == "breaker"


def test_switch_initial_install_in_circuit_numbers_mode_is_on_the_circuits_device(
    hass: HomeAssistant,
) -> None:
    """The circuit's device is named by the circuit in both modes, and the base is the suffix.

    The option decides only which identifier an existing id is read back
    against; a new switch has none to read.
    """
    circuit = SpanCircuitSnapshotFactory.create(
        circuit_id="2",
        name="Kitchen",
        tabs=[2, 3],
        is_user_controllable=True,
    )
    coordinator = _make_coordinator(hass, {"2": circuit}, options={"use_circuit_numbers": True})

    with pytest.MonkeyPatch.context() as mp:
        registry = MagicMock()
        registry.async_get_entity_id.return_value = None
        mp.setattr(
            "custom_components.span_panel.switch.er.async_get",
            lambda _hass: registry,
        )
        switch = SpanPanelCircuitsSwitch(coordinator, "2", "SPAN Panel")

    assert switch.name == "Breaker"
    assert switch.device_info is not None
    assert switch.device_info.get("name") == "Kitchen"
    assert switch.suggested_object_id == "breaker"


def test_switch_initial_install_without_name_falls_back_to_the_tabs(
    hass: HomeAssistant,
) -> None:
    """An unnamed circuit's device is named for its breaker position, not left to HA.

    Letting HA default it gave every unnamed circuit on the panel the same name
    and the same id stem, which the registry then disambiguated with `_2`, `_3`,
    ... in whatever order the platform happened to add them.
    """
    circuit = SpanCircuitSnapshotFactory.create(
        circuit_id="3",
        name="",
        tabs=[3],
        is_user_controllable=True,
    )
    coordinator = _make_coordinator(hass, {"3": circuit})

    with pytest.MonkeyPatch.context() as mp:
        registry = MagicMock()
        registry.async_get_entity_id.return_value = None
        mp.setattr(
            "custom_components.span_panel.switch.er.async_get",
            lambda _hass: registry,
        )
        switch = SpanPanelCircuitsSwitch(coordinator, "3", "SPAN Panel")

    assert switch.name == "Breaker"
    assert switch.device_info is not None
    assert switch.device_info.get("name") == "Circuit 3"
    assert switch.suggested_object_id == "breaker"
    assert switch._previous_circuit_name is not None


def test_switch_first_update_requests_reload_without_user_override(
    hass: HomeAssistant,
) -> None:
    """First update should request a reload when no user override exists."""
    circuit = SpanCircuitSnapshotFactory.create(
        circuit_id="1", name="Kitchen", is_user_controllable=True
    )
    coordinator = _make_coordinator(hass, {"1": circuit})

    with pytest.MonkeyPatch.context() as mp:
        init_registry = MagicMock()
        init_registry.async_get_entity_id.return_value = None
        mp.setattr(
            "custom_components.span_panel.switch.er.async_get",
            lambda _hass: init_registry,
        )
        switch = SpanPanelCircuitsSwitch(coordinator, "1", "SPAN Panel")

    switch.hass = hass
    switch.entity_id = "switch.kitchen_breaker"
    switch.async_write_ha_state = MagicMock()

    with pytest.MonkeyPatch.context() as mp:
        runtime_registry = MagicMock()
        runtime_registry.async_get.return_value = None
        mp.setattr(
            "custom_components.span_panel.switch.er.async_get",
            lambda _hass: runtime_registry,
        )
        switch._handle_coordinator_update()

    coordinator.request_reload.assert_called_once()


def test_switch_a_user_named_switch_still_reloads_on_a_rename(hass: HomeAssistant) -> None:
    """A name the user gave the switch does not hold its circuit's device on the old name.

    The circuit's name lives on its device, which only the reload renames.
    """
    circuit = SpanCircuitSnapshotFactory.create(
        circuit_id="1", name="Kitchen", is_user_controllable=True
    )
    coordinator = _make_coordinator(hass, {"1": circuit})

    with pytest.MonkeyPatch.context() as mp:
        init_registry = MagicMock()
        init_registry.async_get_entity_id.return_value = "switch.kitchen_breaker"
        mp.setattr(
            "custom_components.span_panel.switch.er.async_get",
            lambda _hass: init_registry,
        )
        switch = SpanPanelCircuitsSwitch(coordinator, "1", "SPAN Panel")

    renamed = replace(circuit, name="Renamed Kitchen")
    coordinator.data = SpanPanelSnapshotFactory.create(circuits={"1": renamed})
    switch.hass = hass
    switch.entity_id = "switch.kitchen_breaker"
    switch.async_write_ha_state = MagicMock()

    with pytest.MonkeyPatch.context() as mp:
        runtime_registry = MagicMock()
        runtime_registry.async_get.return_value = MagicMock(
            name="Custom Kitchen Breaker"
        )
        mp.setattr(
            "custom_components.span_panel.switch.er.async_get",
            lambda _hass: runtime_registry,
        )
        switch._handle_coordinator_update()

    coordinator.request_reload.assert_called_once()
    runtime_registry.async_update_entity.assert_not_called()
    assert switch._previous_circuit_name == "Renamed Kitchen"


def test_switch_update_is_on_clears_state_when_circuit_disappears(
    hass: HomeAssistant,
) -> None:
    """Switch state should clear when its circuit disappears from coordinator data."""
    circuit = SpanCircuitSnapshotFactory.create(
        circuit_id="1", is_user_controllable=True
    )
    coordinator = _make_coordinator(hass, {"1": circuit})

    with pytest.MonkeyPatch.context() as mp:
        registry = MagicMock()
        registry.async_get_entity_id.return_value = None
        mp.setattr(
            "custom_components.span_panel.switch.er.async_get",
            lambda _hass: registry,
        )
        switch = SpanPanelCircuitsSwitch(coordinator, "1", "SPAN Panel")

    coordinator.data = SpanPanelSnapshotFactory.create(circuits={})
    switch._update_is_on()

    assert switch.is_on is None


def test_switch_turn_on_off_schedule_async_tasks(hass: HomeAssistant) -> None:
    """Sync turn_on/turn_off wrappers should schedule async tasks."""
    circuit = SpanCircuitSnapshotFactory.create(
        circuit_id="1", is_user_controllable=True
    )
    coordinator = _make_coordinator(hass, {"1": circuit})

    with pytest.MonkeyPatch.context() as mp:
        registry = MagicMock()
        registry.async_get_entity_id.return_value = None
        mp.setattr(
            "custom_components.span_panel.switch.er.async_get",
            lambda _hass: registry,
        )
        switch = SpanPanelCircuitsSwitch(coordinator, "1", "SPAN Panel")

    switch.hass = MagicMock()
    on_task = object()
    off_task = object()
    switch.async_turn_on = MagicMock(return_value=on_task)
    switch.async_turn_off = MagicMock(return_value=off_task)

    switch.turn_on()
    switch.turn_off()

    assert switch.hass.create_task.call_count == 2
    switch.hass.create_task.assert_any_call(on_task)
    switch.hass.create_task.assert_any_call(off_task)


def test_switch_relay_state_target_exposed_in_attributes(hass: HomeAssistant) -> None:
    """relay_state_target should appear in extra attributes when set."""
    circuit = SpanCircuitSnapshotFactory.create(
        circuit_id="1",
        relay_state="OPEN",
        relay_state_target="CLOSED",
        tabs=[1],
    )
    coordinator = _make_coordinator(hass, {"1": circuit})

    switch = SpanPanelCircuitsSwitch(coordinator, "1", "SPAN Panel")
    attrs = switch.extra_state_attributes
    assert attrs is not None
    assert attrs["relay_state_target"] == "CLOSED"


def test_switch_relay_state_target_absent_when_none(hass: HomeAssistant) -> None:
    """relay_state_target should not appear in attributes when None."""
    circuit = SpanCircuitSnapshotFactory.create(
        circuit_id="1",
        relay_state="CLOSED",
        relay_state_target=None,
        tabs=[1],
    )
    coordinator = _make_coordinator(hass, {"1": circuit})

    switch = SpanPanelCircuitsSwitch(coordinator, "1", "SPAN Panel")
    attrs = switch.extra_state_attributes
    assert attrs is not None
    assert "relay_state_target" not in attrs


def test_switch_circuit_numbers_entity_id_stable_after_reload(
    hass: HomeAssistant,
) -> None:
    """The base stays the suffix while the circuit's device carries the panel's name.

    One name path in both modes -- the label, on a device named by the panel --
    and one id path: the base handed to Core, read back from the id the switch
    has. The two are decoupled, so the display name cannot move an id.
    """
    circuit = SpanCircuitSnapshotFactory.create(
        circuit_id="2",
        name="Air Conditioner",
        tabs=[15, 17],
        is_user_controllable=True,
    )
    coordinator = _make_coordinator(hass, {"2": circuit}, options={"use_circuit_numbers": True})

    # --- Initial install: entity NOT in registry ---
    with pytest.MonkeyPatch.context() as mp:
        registry = MagicMock()
        registry.async_get_entity_id.return_value = None
        mp.setattr(
            "custom_components.span_panel.switch.er.async_get",
            lambda _hass: registry,
        )
        switch = SpanPanelCircuitsSwitch(coordinator, "2", "SPAN Panel")

    assert switch.name == "Breaker"
    assert switch.suggested_object_id == "breaker"

    # --- After reload: entity EXISTS in registry ---
    with pytest.MonkeyPatch.context() as mp:
        registry = MagicMock()
        registry.async_get_entity_id.return_value = (
            "switch.span_panel_circuit_15_17_breaker"
        )
        mp.setattr(
            "custom_components.span_panel.switch.er.async_get",
            lambda _hass: registry,
        )
        switch2 = SpanPanelCircuitsSwitch(coordinator, "2", "SPAN Panel")

    # The label, carried by original_name rather than the registry's `name`,
    # which would outrank the base; the tabs read the existing id back.
    assert switch2.name == "Breaker"
    assert switch2.device_info is not None
    assert switch2.device_info.get("name") == "Air Conditioner"
    assert switch2.suggested_object_id == "breaker"


def test_switch_circuit_numbers_entity_id_120v_single_tab(
    hass: HomeAssistant,
) -> None:
    """A single-tab circuit's id is built from its tab; the omission is its device's.

    Named so that its device's name ends on the suffix word. The id spells
    `breaker` after the tab, which settles the form, but the id Core composes
    is the device's name and the base, so the base leaves the word out as it
    does for a new entity on that device: Recreate offers `..._main_breaker`,
    the id the circuit has under circuit names, not `..._main_breaker_breaker`.
    """
    circuit = SpanCircuitSnapshotFactory.create(
        circuit_id="5",
        name="Main Breaker",
        tabs=[10],
        is_user_controllable=True,
    )
    coordinator = _make_coordinator(hass, {"5": circuit}, options={"use_circuit_numbers": True})

    with pytest.MonkeyPatch.context() as mp:
        registry = MagicMock()
        registry.async_get_entity_id.return_value = "switch.span_panel_circuit_10_breaker"
        # The row has no area of its own, so Core composes its device in front.
        registry.async_get.return_value.area_id = None
        mp.setattr(
            "custom_components.span_panel.switch.er.async_get",
            lambda _hass: registry,
        )
        switch = SpanPanelCircuitsSwitch(coordinator, "5", "SPAN Panel")

    assert switch.name == "Breaker"
    assert switch.suggested_object_id == ""


def test_switch_circuit_numbers_releases_the_synced_registry_name(
    hass: HomeAssistant,
) -> None:
    """A name an older release wrote is handed back, so it stops deciding the id."""
    circuit = SpanCircuitSnapshotFactory.create(
        circuit_id="2",
        name="Air Conditioner",
        tabs=[15, 17],
        is_user_controllable=True,
    )
    coordinator = _make_coordinator(hass, {"2": circuit}, options={"use_circuit_numbers": True})

    with pytest.MonkeyPatch.context() as mp:
        registry = MagicMock()
        registry.async_get_entity_id.return_value = (
            "switch.span_panel_circuit_15_17_breaker"
        )
        # Simulate registry entry with no user-set name.
        # Use PropertyMock because MagicMock(name=...) sets the mock's
        # internal label rather than the .name attribute.
        entity_entry = MagicMock()
        type(entity_entry).name = PropertyMock(return_value="Air Conditioner Breaker")
        registry.async_get.return_value = entity_entry
        mp.setattr(
            "custom_components.span_panel.switch.er.async_get",
            lambda _hass: registry,
        )
        SpanPanelCircuitsSwitch(coordinator, "2", "SPAN Panel")

    registry.async_update_entity.assert_called_once_with(
        "switch.span_panel_circuit_15_17_breaker", name=None
    )


def test_switch_circuit_numbers_preserves_user_custom_name(
    hass: HomeAssistant,
) -> None:
    """User-customized registry names must not be overwritten by sync."""
    circuit = SpanCircuitSnapshotFactory.create(
        circuit_id="2",
        name="Air Conditioner",
        tabs=[15, 17],
        is_user_controllable=True,
    )
    coordinator = _make_coordinator(hass, {"2": circuit}, options={"use_circuit_numbers": True})

    with pytest.MonkeyPatch.context() as mp:
        registry = MagicMock()
        registry.async_get_entity_id.return_value = (
            "switch.span_panel_circuit_15_17_breaker"
        )
        # Simulate registry entry with a user-customized name.
        # Use PropertyMock because MagicMock(name=...) sets the mock's
        # internal label rather than the .name attribute.
        entity_entry = MagicMock()
        type(entity_entry).name = PropertyMock(return_value="My AC Unit")
        registry.async_get.return_value = entity_entry
        mp.setattr(
            "custom_components.span_panel.switch.er.async_get",
            lambda _hass: registry,
        )
        SpanPanelCircuitsSwitch(coordinator, "2", "SPAN Panel")

    registry.async_update_entity.assert_not_called()


def test_switch_coordinator_update_circuit_numbers_requests_reload(
    hass: HomeAssistant,
) -> None:
    """A renamed circuit reloads, which is what rebuilds original_name."""
    circuit = SpanCircuitSnapshotFactory.create(
        circuit_id="2",
        name="Air Conditioner",
        tabs=[15, 17],
        is_user_controllable=True,
    )
    coordinator = _make_coordinator(hass, {"2": circuit}, options={"use_circuit_numbers": True})

    # Create switch with entity already in registry (existing entity)
    with pytest.MonkeyPatch.context() as mp:
        registry = MagicMock()
        registry.async_get_entity_id.return_value = (
            "switch.span_panel_circuit_15_17_breaker"
        )
        entity_entry = MagicMock()
        type(entity_entry).name = PropertyMock(return_value="Air Conditioner Breaker")
        registry.async_get.return_value = entity_entry
        mp.setattr(
            "custom_components.span_panel.switch.er.async_get",
            lambda _hass: registry,
        )
        switch = SpanPanelCircuitsSwitch(coordinator, "2", "SPAN Panel")

    # Simulate a circuit name change from "Air Conditioner" to "Kitchen AC"
    renamed = replace(circuit, name="Kitchen AC")
    coordinator.data = SpanPanelSnapshotFactory.create(circuits={"2": renamed})
    switch.hass = hass
    switch.entity_id = "switch.span_panel_circuit_15_17_breaker"
    switch.async_write_ha_state = MagicMock()

    with pytest.MonkeyPatch.context() as mp:
        runtime_registry = MagicMock()
        runtime_entry = MagicMock()
        # Released at construction, so nothing occupies the field any more.
        type(runtime_entry).name = PropertyMock(return_value=None)
        runtime_registry.async_get.return_value = runtime_entry
        mp.setattr(
            "custom_components.span_panel.switch.er.async_get",
            lambda _hass: runtime_registry,
        )
        switch._handle_coordinator_update()

    coordinator.request_reload.assert_called_once()
    runtime_registry.async_update_entity.assert_not_called()


def test_switch_coordinator_update_circuit_numbers_preserves_user_override(
    hass: HomeAssistant,
) -> None:
    """In circuit-numbers mode a user-set name is never written, and the rename still reloads.

    The reload renames the circuit's device; the user's name is theirs (R4).
    """
    circuit = SpanCircuitSnapshotFactory.create(
        circuit_id="2",
        name="Air Conditioner",
        tabs=[15, 17],
        is_user_controllable=True,
    )
    coordinator = _make_coordinator(hass, {"2": circuit}, options={"use_circuit_numbers": True})

    # Create switch with entity already in registry
    with pytest.MonkeyPatch.context() as mp:
        registry = MagicMock()
        registry.async_get_entity_id.return_value = (
            "switch.span_panel_circuit_15_17_breaker"
        )
        entity_entry = MagicMock()
        type(entity_entry).name = PropertyMock(return_value="Air Conditioner Breaker")
        registry.async_get.return_value = entity_entry
        mp.setattr(
            "custom_components.span_panel.switch.er.async_get",
            lambda _hass: registry,
        )
        switch = SpanPanelCircuitsSwitch(coordinator, "2", "SPAN Panel")

    # Simulate a circuit name change; user has set "My AC Unit" in the registry
    renamed = replace(circuit, name="Kitchen AC")
    coordinator.data = SpanPanelSnapshotFactory.create(circuits={"2": renamed})
    switch.hass = hass
    switch.entity_id = "switch.span_panel_circuit_15_17_breaker"
    switch.async_write_ha_state = MagicMock()

    with pytest.MonkeyPatch.context() as mp:
        runtime_registry = MagicMock()
        runtime_entry = MagicMock()
        type(runtime_entry).name = PropertyMock(return_value="My AC Unit")
        runtime_registry.async_get.return_value = runtime_entry
        mp.setattr(
            "custom_components.span_panel.switch.er.async_get",
            lambda _hass: runtime_registry,
        )
        switch._handle_coordinator_update()

    runtime_registry.async_update_entity.assert_not_called()
    coordinator.request_reload.assert_called_once()


def test_switch_coordinator_update_friendly_mode_still_reloads(
    hass: HomeAssistant,
) -> None:
    """In friendly-names mode, name changes should still trigger a reload."""
    circuit = SpanCircuitSnapshotFactory.create(
        circuit_id="1",
        name="Kitchen Outlets",
        is_user_controllable=True,
    )
    coordinator = _make_coordinator(hass, {"1": circuit}, options={"use_circuit_numbers": False})

    # Create switch with entity already in registry (previous name known)
    with pytest.MonkeyPatch.context() as mp:
        registry = MagicMock()
        registry.async_get_entity_id.return_value = (
            "switch.span_panel_kitchen_outlets_breaker"
        )
        entity_entry = MagicMock()
        type(entity_entry).name = PropertyMock(return_value=None)
        registry.async_get.return_value = entity_entry
        mp.setattr(
            "custom_components.span_panel.switch.er.async_get",
            lambda _hass: registry,
        )
        switch = SpanPanelCircuitsSwitch(coordinator, "1", "SPAN Panel")

    assert switch._previous_circuit_name == "Kitchen Outlets"

    # Simulate a circuit name change
    renamed = replace(circuit, name="New Kitchen Outlets")
    coordinator.data = SpanPanelSnapshotFactory.create(circuits={"1": renamed})
    switch.hass = hass
    switch.entity_id = "switch.span_panel_kitchen_outlets_breaker"
    switch.async_write_ha_state = MagicMock()

    with pytest.MonkeyPatch.context() as mp:
        runtime_registry = MagicMock()
        runtime_entry = MagicMock()
        type(runtime_entry).name = PropertyMock(return_value=None)
        runtime_registry.async_get.return_value = runtime_entry
        mp.setattr(
            "custom_components.span_panel.switch.er.async_get",
            lambda _hass: runtime_registry,
        )
        switch._handle_coordinator_update()

    coordinator.request_reload.assert_called_once()
