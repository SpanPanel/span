"""Tests for select entity functionality."""

from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import pytest
from span_panel_api import PublishOutcome, PublishState
from span_panel_api.exceptions import SpanPanelServerError

from custom_components.span_panel.const import CircuitPriority
from custom_components.span_panel.control_gate import ControlPolicy
from custom_components.span_panel.select import (
    CIRCUIT_PRIORITY_DESCRIPTION,
    SpanPanelCircuitsSelect,
    async_setup_entry,
)

from .factories import SpanCircuitSnapshotFactory, SpanPanelSnapshotFactory


def _make_coordinator_with_circuit(
    hass: HomeAssistant,
    circuit_id: str = "id",
    circuit_name: str = "name",
    priority: str = "SOC_THRESHOLD",
) -> MagicMock:
    """Build a mock coordinator whose .data contains a single circuit."""
    circuit = SpanCircuitSnapshotFactory.create(
        circuit_id=circuit_id,
        name=circuit_name,
        relay_state="CLOSED",
        instant_power_w=100.0,
        produced_energy_wh=0.0,
        consumed_energy_wh=50.0,
        tabs=[1],
        priority=priority,
        is_user_controllable=True,
    )

    snapshot = SpanPanelSnapshotFactory.create(
        circuits={circuit_id: circuit},
    )

    coordinator = MagicMock()
    coordinator.hass = hass
    coordinator.data = snapshot
    coordinator.config_entry = MagicMock()
    coordinator.config_entry.title = "SPAN Panel"
    coordinator.config_entry.data = {}
    coordinator.config_entry.options = {}
    return coordinator


def test_select_init_missing_circuit() -> None:
    """Test that initializing with a missing circuit_id raises ValueError."""
    # Coordinator with no circuits
    snapshot = SpanPanelSnapshotFactory.create(circuits={})
    coordinator = MagicMock()
    coordinator.data = snapshot
    coordinator.config_entry = MagicMock()
    coordinator.config_entry.data = {}
    coordinator.config_entry.options = {}

    with pytest.raises(ValueError):
        SpanPanelCircuitsSelect(coordinator, CIRCUIT_PRIORITY_DESCRIPTION, coordinator.data, "bad_id", "Test Device")


def test_a_new_select_shows_the_priority_the_coordinator_holds_now(hass: HomeAssistant) -> None:
    """The first selection is state, read where every later one is: the coordinator's data.

    The select is built from setup's snapshot, which names its device, and the
    coordinator's data may have moved on by then. The selection it shows comes
    from that data, as the breaker switch's first position does, so a priority
    changed during setup shows at once.
    """
    coordinator = _make_coordinator_with_circuit(hass, priority="NEVER")
    decided = SpanPanelSnapshotFactory.create(
        circuits={
            "id": replace(coordinator.data.circuits["id"], priority="SOC_THRESHOLD")
        },
    )

    select = SpanPanelCircuitsSelect(
        coordinator, CIRCUIT_PRIORITY_DESCRIPTION, decided, "id", "SPAN Panel"
    )

    assert select.current_option == CircuitPriority.NEVER.value
    assert select.options == [
        priority.value for priority in CircuitPriority if priority is not CircuitPriority.UNKNOWN
    ]


def test_a_select_whose_circuit_the_coordinator_lacks_starts_with_nothing_selected(
    hass: HomeAssistant,
) -> None:
    """A circuit gone from the coordinator's data has no selection to show until it returns."""
    coordinator = _make_coordinator_with_circuit(hass)
    decided = coordinator.data
    coordinator.data = SpanPanelSnapshotFactory.create(circuits={})

    select = SpanPanelCircuitsSelect(
        coordinator, CIRCUIT_PRIORITY_DESCRIPTION, decided, "id", "SPAN Panel"
    )

    assert (select.options, select.current_option) == ([], None)


@pytest.mark.asyncio
async def test_async_select_option_refusal_is_raised_at_the_caller(hass: HomeAssistant) -> None:
    """A refused priority reaches the person who chose it, carrying the reason.

    The panel refuses this for two different situations -- the circuit is
    commissioned never-backup, or the panel could not carry the change out --
    and the exception distinguishes them only in its message. So the message is
    passed through rather than diagnosed here, and the entity contributes the
    name the user knows the circuit by.
    """
    coordinator = _make_coordinator_with_circuit(hass)
    circuit = coordinator.data.circuits["id"]

    select = SpanPanelCircuitsSelect(coordinator, CIRCUIT_PRIORITY_DESCRIPTION, coordinator.data, "id", "Test Device")
    select.coordinator = coordinator
    select.hass = MagicMock()

    coordinator.client = AsyncMock()
    coordinator.client.set_circuit_priority = AsyncMock(
        side_effect=SpanPanelServerError("Circuit 'id' declares its shed priority not settable")
    )

    select._get_circuit = MagicMock(return_value=circuit)
    with pytest.raises(HomeAssistantError) as raised:
        await select.async_select_option(CircuitPriority.SOC_THRESHOLD.value)

    assert raised.value.translation_key == "circuit_priority_failed"
    placeholders = raised.value.translation_placeholders
    assert placeholders is not None
    assert placeholders["circuit"] == "name"
    assert placeholders["reason"] == "Circuit 'id' declares its shed priority not settable"
    coordinator.async_request_refresh.assert_not_called()


@pytest.mark.asyncio
async def test_async_select_option_undelivered_is_raised_at_the_caller(hass: HomeAssistant) -> None:
    """A `FAILED` outcome is the promise this change will not arrive later."""
    coordinator = _make_coordinator_with_circuit(hass)
    circuit = coordinator.data.circuits["id"]

    select = SpanPanelCircuitsSelect(coordinator, CIRCUIT_PRIORITY_DESCRIPTION, coordinator.data, "id", "Test Device")
    select.coordinator = coordinator
    select.hass = MagicMock()

    coordinator.client = AsyncMock()
    coordinator.client.set_circuit_priority = AsyncMock(
        return_value=PublishOutcome(
            state=PublishState.FAILED, topic=None, value="NEVER", detail="transport is closed"
        )
    )

    select._get_circuit = MagicMock(return_value=circuit)
    with pytest.raises(HomeAssistantError) as raised:
        await select.async_select_option(CircuitPriority.SOC_THRESHOLD.value)

    assert raised.value.translation_key == "circuit_priority_not_delivered"
    placeholders = raised.value.translation_placeholders
    assert placeholders is not None
    assert placeholders["reason"] == "transport is closed"
    coordinator.async_request_refresh.assert_not_called()


@pytest.mark.asyncio
async def test_async_select_option_success_refreshes_coordinator(hass: HomeAssistant) -> None:
    """Successful priority changes should refresh coordinator data."""
    coordinator = _make_coordinator_with_circuit(hass)
    coordinator.async_request_refresh = AsyncMock()

    with patch(
        "custom_components.span_panel.select.er.async_get"
    ) as mock_async_get:
        registry = MagicMock()
        registry.async_get_entity_id.return_value = None
        mock_async_get.return_value = registry
        select = SpanPanelCircuitsSelect(
            coordinator, CIRCUIT_PRIORITY_DESCRIPTION, coordinator.data, "id", "SPAN Panel"
        )

    coordinator.client = MagicMock()
    coordinator.client.set_circuit_priority = AsyncMock()
    select.hass = MagicMock()

    await select.async_select_option(CircuitPriority.SOC_THRESHOLD.value)

    coordinator.client.set_circuit_priority.assert_awaited_once_with(
        "id", "SOC_THRESHOLD"
    )
    coordinator.async_request_refresh.assert_awaited_once()


def test_select_initial_install_in_circuit_numbers_mode_is_on_the_circuits_device(
    hass: HomeAssistant,
) -> None:
    """The circuit's device is named by the circuit in both modes, and the base is the suffix.

    The option decides only which identifier an existing id is read back
    against; a new select has none to read.
    """
    coordinator = _make_coordinator_with_circuit(hass)
    coordinator.config_entry.options = {"use_circuit_numbers": True}

    with patch(
        "custom_components.span_panel.select.er.async_get"
    ) as mock_async_get:
        registry = MagicMock()
        registry.async_get_entity_id.return_value = None
        mock_async_get.return_value = registry

        select = SpanPanelCircuitsSelect(
            coordinator, CIRCUIT_PRIORITY_DESCRIPTION, coordinator.data, "id", "SPAN Panel"
        )

    assert select.name == "Circuit Priority"
    assert select.device_info is not None
    assert select.device_info.name == "name"
    assert select.suggested_object_id == "circuit priority"


def test_select_unnamed_friendly_mode_falls_back_to_the_tabs(hass: HomeAssistant) -> None:
    """An unnamed circuit's device is named for its breaker position, not left to HA.

    Deferring gave every unnamed circuit on the panel the same name and the same
    id stem, which the registry then disambiguated with `_2`, `_3`, ... in
    whatever order the platform happened to add them.
    """
    coordinator = _make_coordinator_with_circuit(hass, circuit_name="")

    with patch(
        "custom_components.span_panel.select.er.async_get"
    ) as mock_async_get:
        registry = MagicMock()
        registry.async_get_entity_id.return_value = None
        mock_async_get.return_value = registry

        select = SpanPanelCircuitsSelect(
            coordinator, CIRCUIT_PRIORITY_DESCRIPTION, coordinator.data, "id", "SPAN Panel"
        )

    assert select.name == "Circuit Priority"
    assert select.device_info is not None
    assert select.device_info.name == "Circuit 1"
    assert select.suggested_object_id == "circuit priority"


def test_select_existing_entity_uses_solar_fallback_name(hass: HomeAssistant) -> None:
    """An unnamed solar circuit's device is named "Solar", and its select carries the label.

    The id the select already has was built from the same fallback, so the base
    reads it back as "Solar" then `circuit_priority`.
    """
    circuit = replace(
        SpanCircuitSnapshotFactory.create(
            circuit_id="pv-1",
            name=None,
            tabs=[9, 10],
            priority="SOC_THRESHOLD",
        ),
        device_type="pv",
    )
    snapshot = SpanPanelSnapshotFactory.create(circuits={"pv-1": circuit})
    coordinator = MagicMock()
    coordinator.data = snapshot
    coordinator.hass = hass
    coordinator.config_entry = MagicMock()
    coordinator.config_entry.title = "SPAN Panel"
    coordinator.config_entry.data = {}
    coordinator.config_entry.options = {}

    with patch(
        "custom_components.span_panel.select.er.async_get"
    ) as mock_async_get:
        registry = MagicMock()
        registry.async_get_entity_id.return_value = "select.solar_circuit_priority"
        mock_async_get.return_value = registry

        select = SpanPanelCircuitsSelect(
            coordinator, CIRCUIT_PRIORITY_DESCRIPTION, coordinator.data, "pv-1", "SPAN Panel"
        )

    assert select.name == "Circuit Priority"
    assert select.device_info is not None
    assert select.device_info.name == "Solar"
    assert select.suggested_object_id == "circuit priority"


def test_select_available_false_when_panel_offline(hass: HomeAssistant) -> None:
    """Select entities become unavailable when the panel is offline."""
    coordinator = _make_coordinator_with_circuit(hass)
    coordinator.panel_offline = True

    select = SpanPanelCircuitsSelect(coordinator, CIRCUIT_PRIORITY_DESCRIPTION, coordinator.data, "id", "SPAN Panel")

    assert select.available is False


def test_select_extra_state_attributes_include_tabs_and_voltage(hass: HomeAssistant) -> None:
    """Select attributes should expose breaker tabs and circuit voltage."""
    coordinator = _make_coordinator_with_circuit(hass)
    with patch(
        "custom_components.span_panel.select.er.async_get"
    ) as mock_async_get:
        registry = MagicMock()
        registry.async_get_entity_id.return_value = None
        mock_async_get.return_value = registry

        select = SpanPanelCircuitsSelect(
            coordinator, CIRCUIT_PRIORITY_DESCRIPTION, coordinator.data, "id", "SPAN Panel"
        )

    assert select.extra_state_attributes == {"tabs": "tabs [1]", "voltage": 120}


def test_handle_coordinator_update_requests_reload_on_first_sync(hass: HomeAssistant) -> None:
    """First update for an entity not yet in the registry should request reload."""
    coordinator = _make_coordinator_with_circuit(hass, circuit_name="Kitchen")
    with patch(
        "custom_components.span_panel.select.er.async_get"
    ) as mock_async_get:
        registry = MagicMock()
        registry.async_get_entity_id.return_value = None
        registry.async_get.return_value = None
        mock_async_get.return_value = registry

        select = SpanPanelCircuitsSelect(
            coordinator, CIRCUIT_PRIORITY_DESCRIPTION, coordinator.data, "id", "SPAN Panel"
        )
        select.hass = MagicMock()
        select.async_write_ha_state = MagicMock()
        select.entity_id = "select.kitchen_circuit_priority"

        select._handle_coordinator_update()

    coordinator.request_reload.assert_called_once()


def test_handle_coordinator_update_a_user_named_select_still_reloads(hass: HomeAssistant) -> None:
    """A name the user gave the select does not hold its circuit's device on the old name.

    The circuit's name lives on its device, which only the reload renames.
    """
    coordinator = _make_coordinator_with_circuit(hass, circuit_name="Kitchen")
    with patch(
        "custom_components.span_panel.select.er.async_get"
    ) as mock_async_get:
        registry = MagicMock()
        registry.async_get_entity_id.return_value = "select.kitchen_circuit_priority"
        mock_async_get.return_value = registry

        select = SpanPanelCircuitsSelect(
            coordinator, CIRCUIT_PRIORITY_DESCRIPTION, coordinator.data, "id", "SPAN Panel"
        )

    updated_circuit = replace(coordinator.data.circuits["id"], name="Renamed Kitchen")
    coordinator.data = SpanPanelSnapshotFactory.create(circuits={"id": updated_circuit})
    select.hass = MagicMock()
    select.async_write_ha_state = MagicMock()
    select.entity_id = "select.kitchen_circuit_priority"

    with patch(
        "custom_components.span_panel.select.er.async_get"
    ) as mock_async_get:
        runtime_registry = MagicMock()
        runtime_registry.async_get.return_value = MagicMock(
            name="Custom Kitchen Priority"
        )
        mock_async_get.return_value = runtime_registry
        select._handle_coordinator_update()

    coordinator.request_reload.assert_called_once()
    runtime_registry.async_update_entity.assert_not_called()
    assert select._previous_circuit_name == "Renamed Kitchen"


def test_handle_coordinator_update_requests_reload_on_name_change(hass: HomeAssistant) -> None:
    """Later circuit renames should request a select reload."""
    coordinator = _make_coordinator_with_circuit(hass, circuit_name="Kitchen")
    with patch(
        "custom_components.span_panel.select.er.async_get"
    ) as mock_async_get:
        registry = MagicMock()
        registry.async_get_entity_id.return_value = "select.kitchen_circuit_priority"
        mock_async_get.return_value = registry

        select = SpanPanelCircuitsSelect(
            coordinator, CIRCUIT_PRIORITY_DESCRIPTION, coordinator.data, "id", "SPAN Panel"
        )

    updated_circuit = replace(coordinator.data.circuits["id"], name="Renamed Kitchen")
    coordinator.data = SpanPanelSnapshotFactory.create(circuits={"id": updated_circuit})
    select.hass = MagicMock()
    select.async_write_ha_state = MagicMock()
    select.entity_id = "select.kitchen_circuit_priority"

    with patch(
        "custom_components.span_panel.select.er.async_get"
    ) as mock_async_get:
        runtime_registry = MagicMock()
        runtime_registry.async_get.return_value = None
        mock_async_get.return_value = runtime_registry
        select._handle_coordinator_update()

    coordinator.request_reload.assert_called_once()
    assert select._previous_circuit_name == "Renamed Kitchen"


def test_handle_coordinator_update_skips_when_circuit_missing_from_snapshot(
    hass: HomeAssistant,
) -> None:
    """Select entity should not crash when its circuit is temporarily absent from a snapshot."""
    coordinator = _make_coordinator_with_circuit(hass, circuit_name="Kitchen")
    with patch(
        "custom_components.span_panel.select.er.async_get"
    ) as mock_async_get:
        registry = MagicMock()
        registry.async_get_entity_id.return_value = None
        mock_async_get.return_value = registry

        select = SpanPanelCircuitsSelect(
            coordinator, CIRCUIT_PRIORITY_DESCRIPTION, coordinator.data, "id", "SPAN Panel"
        )

    # Simulate a partial snapshot missing this circuit
    coordinator.data = SpanPanelSnapshotFactory.create(circuits={})
    select.hass = MagicMock()
    select.async_write_ha_state = MagicMock()
    select.entity_id = "select.kitchen_circuit_priority"

    # Should not raise KeyError
    select._handle_coordinator_update()

    # async_write_ha_state should NOT be called since we returned early
    select.async_write_ha_state.assert_not_called()


@pytest.mark.asyncio
async def test_async_setup_entry_filters_supported_circuits(hass: HomeAssistant) -> None:
    """Platform setup should only create selects for supported controllable circuits."""
    controllable = SpanCircuitSnapshotFactory.create(
        circuit_id="main-1",
        name="Kitchen",
        is_user_controllable=True,
        tabs=[1],
    )
    not_controllable = SpanCircuitSnapshotFactory.create(
        circuit_id="main-2",
        name="Locked",
        is_user_controllable=False,
        tabs=[2],
    )
    evse_upstream = replace(
        SpanCircuitSnapshotFactory.create(
            circuit_id="evse-1",
            name="EV Upstream",
            is_user_controllable=True,
            tabs=[3, 4],
        ),
        device_type="evse",
        relative_position="UPSTREAM",
    )
    pv_downstream = replace(
        SpanCircuitSnapshotFactory.create(
            circuit_id="pv-1",
            name="Solar",
            is_user_controllable=True,
            tabs=[5, 6],
        ),
        device_type="pv",
        relative_position="DOWNSTREAM",
    )

    coordinator = MagicMock()
    coordinator.hass = hass
    coordinator.data = SpanPanelSnapshotFactory.create(
        circuits={
            "main-1": controllable,
            "main-2": not_controllable,
            "evse-1": evse_upstream,
            "pv-1": pv_downstream,
        }
    )
    config_entry = MagicMock()
    config_entry.title = "SPAN Panel"
    config_entry.data = {}
    config_entry.runtime_data = MagicMock(
        control_policy=ControlPolicy.default(), coordinator=coordinator, setup_snapshot=coordinator.data
    )
    async_add_entities = MagicMock()

    await async_setup_entry(hass, config_entry, async_add_entities)

    entities = async_add_entities.call_args.args[0]
    assert len(entities) == 2
    assert {entity.id for entity in entities} == {"main-1", "pv-1"}


async def test_async_setup_entry_skips_circuits_whose_priority_is_not_settable(
    hass: HomeAssistant,
) -> None:
    """A never-backup circuit gets no priority select, even with a controllable relay.

    Relay controllability and priority settability are two independent
    commissioning flags. Under v1.0 the panel expresses never-backup as the
    absence of `$settable` on `load-shed/priority`, which the adapter carries as
    `is_never_backup` -- a different property from `switch/relay-controllable`,
    which it carries as `is_user_controllable`.

    Gating this platform on the relay flag alone offers a priority control on a
    circuit whose priority the panel will refuse to change. The panels we have
    captures from declare every circuit's priority settable, so hardware does not
    exercise this today; the flag exists because a panel may say otherwise, and
    it is the only thing that says so.
    """
    settable_priority = SpanCircuitSnapshotFactory.create(
        circuit_id="main-1",
        name="Kitchen",
        is_user_controllable=True,
        is_never_backup=False,
        tabs=[1],
    )
    locked_priority = SpanCircuitSnapshotFactory.create(
        circuit_id="main-2",
        name="Water Heater",
        is_user_controllable=True,
        is_never_backup=True,
        tabs=[2],
    )

    coordinator = MagicMock()
    coordinator.hass = hass
    coordinator.data = SpanPanelSnapshotFactory.create(
        circuits={"main-1": settable_priority, "main-2": locked_priority}
    )
    config_entry = MagicMock()
    config_entry.title = "SPAN Panel"
    config_entry.data = {}
    config_entry.runtime_data = MagicMock(
        control_policy=ControlPolicy.default(), coordinator=coordinator, setup_snapshot=coordinator.data
    )
    async_add_entities = MagicMock()

    await async_setup_entry(hass, config_entry, async_add_entities)

    entities = async_add_entities.call_args.args[0]
    assert {entity.id for entity in entities} == {"main-1"}


def test_select_circuit_numbers_entity_id_stable_after_reload(
    hass: HomeAssistant,
) -> None:
    """The base stays the suffix while the circuit's device carries the panel's name."""
    circuit = SpanCircuitSnapshotFactory.create(
        circuit_id="2",
        name="Air Conditioner",
        tabs=[15, 17],
        is_user_controllable=True,
    )
    snapshot = SpanPanelSnapshotFactory.create(circuits={"2": circuit})
    coordinator = MagicMock()
    coordinator.data = snapshot
    coordinator.config_entry = MagicMock()
    coordinator.config_entry.title = "SPAN Panel"
    coordinator.config_entry.data = {}
    coordinator.config_entry.options = {"use_circuit_numbers": True}
    coordinator.hass = hass

    # --- Initial install: entity NOT in registry ---
    with pytest.MonkeyPatch.context() as mp:
        registry = MagicMock()
        registry.async_get_entity_id.return_value = None
        mp.setattr(
            "custom_components.span_panel.select.er.async_get",
            lambda _hass: registry,
        )
        select = SpanPanelCircuitsSelect(
            coordinator,
            CIRCUIT_PRIORITY_DESCRIPTION,
            coordinator.data,
            "2",
            "SPAN Panel",
        )

    assert select.name == "Circuit Priority"
    assert select.suggested_object_id == "circuit priority"

    # --- After reload: entity EXISTS in registry ---
    with pytest.MonkeyPatch.context() as mp:
        registry = MagicMock()
        registry.async_get_entity_id.return_value = (
            "select.span_panel_circuit_15_17_circuit_priority"
        )
        mp.setattr(
            "custom_components.span_panel.select.er.async_get",
            lambda _hass: registry,
        )
        select2 = SpanPanelCircuitsSelect(
            coordinator,
            CIRCUIT_PRIORITY_DESCRIPTION,
            coordinator.data,
            "2",
            "SPAN Panel",
        )

    # The label, carried by original_name rather than the registry's `name`,
    # which would outrank the base; the tabs read the existing id back.
    assert select2.name == "Circuit Priority"
    assert select2.device_info is not None
    assert select2.device_info.name == "Air Conditioner"
    assert select2.suggested_object_id == "circuit priority"


def test_select_circuit_numbers_entity_id_120v_single_tab(
    hass: HomeAssistant,
) -> None:
    """A single-tab circuit's id is built from its tab; the omission is its device's.

    Named so that its device's name ends on the suffix words. The id spells
    `circuit_priority` after the tab, which settles the form, but the id Core
    composes is the device's name and the base, so the base leaves the words
    out as it does for a new entity on that device: Recreate offers
    `..._main_circuit_priority`, the id the circuit has under circuit names.
    """
    circuit = SpanCircuitSnapshotFactory.create(
        circuit_id="5",
        name="Main Circuit Priority",
        tabs=[10],
        is_user_controllable=True,
    )
    snapshot = SpanPanelSnapshotFactory.create(circuits={"5": circuit})
    coordinator = MagicMock()
    coordinator.data = snapshot
    coordinator.config_entry = MagicMock()
    coordinator.config_entry.title = "SPAN Panel"
    coordinator.config_entry.data = {}
    coordinator.config_entry.options = {"use_circuit_numbers": True}
    coordinator.hass = hass

    with pytest.MonkeyPatch.context() as mp:
        registry = MagicMock()
        registry.async_get_entity_id.return_value = (
            "select.span_panel_circuit_10_circuit_priority"
        )
        # The row has no area of its own, so Core composes its device in front.
        registry.async_get.return_value.area_id = None
        mp.setattr(
            "custom_components.span_panel.select.er.async_get",
            lambda _hass: registry,
        )
        select = SpanPanelCircuitsSelect(
            coordinator,
            CIRCUIT_PRIORITY_DESCRIPTION,
            coordinator.data,
            "5",
            "SPAN Panel",
        )

    assert select.name == "Circuit Priority"
    assert select.suggested_object_id == ""


def test_select_coordinator_update_circuit_numbers_requests_reload(
    hass: HomeAssistant,
) -> None:
    """In circuit-numbers mode, a name change should update the registry display name."""
    circuit = SpanCircuitSnapshotFactory.create(
        circuit_id="2",
        name="Air Conditioner",
        tabs=[15, 17],
        is_user_controllable=True,
    )
    snapshot = SpanPanelSnapshotFactory.create(circuits={"2": circuit})
    coordinator = MagicMock()
    coordinator.data = snapshot
    coordinator.config_entry = MagicMock()
    coordinator.config_entry.title = "SPAN Panel"
    coordinator.config_entry.data = {}
    coordinator.config_entry.options = {"use_circuit_numbers": True}
    coordinator.hass = hass

    # Create select with entity already in registry (existing entity)
    with pytest.MonkeyPatch.context() as mp:
        registry = MagicMock()
        registry.async_get_entity_id.return_value = (
            "select.span_panel_circuit_15_17_circuit_priority"
        )
        entity_entry = MagicMock()
        type(entity_entry).name = PropertyMock(
            return_value="Air Conditioner Circuit Priority"
        )
        registry.async_get.return_value = entity_entry
        mp.setattr(
            "custom_components.span_panel.select.er.async_get",
            lambda _hass: registry,
        )
        select = SpanPanelCircuitsSelect(
            coordinator,
            CIRCUIT_PRIORITY_DESCRIPTION,
            coordinator.data,
            "2",
            "SPAN Panel",
        )

    # Simulate a circuit name change from "Air Conditioner" to "Kitchen AC"
    renamed = replace(circuit, name="Kitchen AC")
    coordinator.data = SpanPanelSnapshotFactory.create(circuits={"2": renamed})
    select.hass = hass
    select.entity_id = "select.span_panel_circuit_15_17_circuit_priority"
    select.async_write_ha_state = MagicMock()

    with pytest.MonkeyPatch.context() as mp:
        runtime_registry = MagicMock()
        runtime_entry = MagicMock()
        # Released at construction, so nothing occupies the field any more.
        type(runtime_entry).name = PropertyMock(return_value=None)
        runtime_registry.async_get.return_value = runtime_entry
        mp.setattr(
            "custom_components.span_panel.select.er.async_get",
            lambda _hass: runtime_registry,
        )
        select._handle_coordinator_update()

    coordinator.request_reload.assert_called_once()
    runtime_registry.async_update_entity.assert_not_called()
