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
        SpanPanelCircuitsSelect(coordinator, CIRCUIT_PRIORITY_DESCRIPTION, "bad_id", "Test Device")


@pytest.mark.asyncio
async def test_async_select_option_refusal_is_raised_at_the_caller() -> None:
    """A refused priority reaches the person who chose it, carrying the reason.

    The panel refuses this for two different situations -- the circuit is
    commissioned never-backup, or the panel could not carry the change out --
    and the exception distinguishes them only in its message. So the message is
    passed through rather than diagnosed here, and the entity contributes the
    name the user knows the circuit by.
    """
    coordinator = _make_coordinator_with_circuit()
    circuit = coordinator.data.circuits["id"]

    select = SpanPanelCircuitsSelect(coordinator, CIRCUIT_PRIORITY_DESCRIPTION, "id", "Test Device")
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
async def test_async_select_option_undelivered_is_raised_at_the_caller() -> None:
    """A `FAILED` outcome is the promise this change will not arrive later."""
    coordinator = _make_coordinator_with_circuit()
    circuit = coordinator.data.circuits["id"]

    select = SpanPanelCircuitsSelect(coordinator, CIRCUIT_PRIORITY_DESCRIPTION, "id", "Test Device")
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
async def test_async_select_option_success_refreshes_coordinator() -> None:
    """Successful priority changes should refresh coordinator data."""
    coordinator = _make_coordinator_with_circuit()
    coordinator.hass = MagicMock()
    coordinator.async_request_refresh = AsyncMock()

    with patch(
        "custom_components.span_panel.select.er.async_get"
    ) as mock_async_get:
        registry = MagicMock()
        registry.async_get_entity_id.return_value = None
        mock_async_get.return_value = registry
        select = SpanPanelCircuitsSelect(
            coordinator, CIRCUIT_PRIORITY_DESCRIPTION, "id", "SPAN Panel"
        )

    coordinator.client = MagicMock()
    coordinator.client.set_circuit_priority = AsyncMock()
    select.hass = MagicMock()

    await select.async_select_option(CircuitPriority.SOC_THRESHOLD.value)

    coordinator.client.set_circuit_priority.assert_awaited_once_with(
        "id", "SOC_THRESHOLD"
    )
    coordinator.async_request_refresh.assert_awaited_once()


def test_select_uses_circuit_numbers_for_the_base_when_the_option_is_enabled() -> None:
    """The option decides the id base; the displayed name is the panel's regardless.

    It used to decide the name too, on a first install only, because the name was
    the only thing an id could be composed from. The base is composed from the
    option directly now.
    """
    coordinator = _make_coordinator_with_circuit()
    coordinator.config_entry.options = {"use_circuit_numbers": True}
    coordinator.hass = MagicMock()

    with patch(
        "custom_components.span_panel.select.er.async_get"
    ) as mock_async_get:
        registry = MagicMock()
        registry.async_get_entity_id.return_value = None
        mock_async_get.return_value = registry

        select = SpanPanelCircuitsSelect(
            coordinator, CIRCUIT_PRIORITY_DESCRIPTION, "id", "SPAN Panel"
        )

    assert select.name == "name Circuit Priority"
    assert select.suggested_object_id == "Circuit 1 circuit priority"


def test_select_unnamed_friendly_mode_falls_back_to_the_tabs() -> None:
    """An unnamed circuit is named for its breaker position, not left to HA.

    Deferring gave every unnamed circuit on the panel the same name and the same
    id stem, which the registry then disambiguated with `_2`, `_3`, ... in
    whatever order the platform happened to add them.
    """
    coordinator = _make_coordinator_with_circuit(circuit_name="")
    coordinator.hass = MagicMock()

    with patch(
        "custom_components.span_panel.select.er.async_get"
    ) as mock_async_get:
        registry = MagicMock()
        registry.async_get_entity_id.return_value = None
        mock_async_get.return_value = registry

        select = SpanPanelCircuitsSelect(
            coordinator, CIRCUIT_PRIORITY_DESCRIPTION, "id", "SPAN Panel"
        )

    assert select.name == "Circuit 1 Circuit Priority"
    assert select.suggested_object_id == "Circuit 1 circuit priority"


def test_select_existing_entity_uses_solar_fallback_name() -> None:
    """Existing unnamed PV entities should use the solar fallback name."""
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
    coordinator.hass = MagicMock()
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
            coordinator, CIRCUIT_PRIORITY_DESCRIPTION, "pv-1", "SPAN Panel"
        )

    assert select.name == "Solar Circuit Priority"


def test_select_available_false_when_panel_offline() -> None:
    """Select entities become unavailable when the panel is offline."""
    coordinator = _make_coordinator_with_circuit()
    coordinator.panel_offline = True

    select = SpanPanelCircuitsSelect(coordinator, CIRCUIT_PRIORITY_DESCRIPTION, "id", "SPAN Panel")

    assert select.available is False


def test_select_extra_state_attributes_include_tabs_and_voltage() -> None:
    """Select attributes should expose breaker tabs and circuit voltage."""
    coordinator = _make_coordinator_with_circuit()
    coordinator.hass = MagicMock()
    with patch(
        "custom_components.span_panel.select.er.async_get"
    ) as mock_async_get:
        registry = MagicMock()
        registry.async_get_entity_id.return_value = None
        mock_async_get.return_value = registry

        select = SpanPanelCircuitsSelect(
            coordinator, CIRCUIT_PRIORITY_DESCRIPTION, "id", "SPAN Panel"
        )

    assert select.extra_state_attributes == {"tabs": "tabs [1]", "voltage": 120}


def test_handle_coordinator_update_requests_reload_on_first_sync() -> None:
    """First update for an entity not yet in the registry should request reload."""
    coordinator = _make_coordinator_with_circuit(circuit_name="Kitchen")
    coordinator.hass = MagicMock()
    with patch(
        "custom_components.span_panel.select.er.async_get"
    ) as mock_async_get:
        registry = MagicMock()
        registry.async_get_entity_id.return_value = None
        registry.async_get.return_value = None
        mock_async_get.return_value = registry

        select = SpanPanelCircuitsSelect(
            coordinator, CIRCUIT_PRIORITY_DESCRIPTION, "id", "SPAN Panel"
        )
        select.hass = MagicMock()
        select.async_write_ha_state = MagicMock()
        select.entity_id = "select.kitchen_circuit_priority"

        select._handle_coordinator_update()

    coordinator.request_reload.assert_called_once()


def test_handle_coordinator_update_user_override_skips_reload() -> None:
    """Customized select names should suppress automatic name sync reloads."""
    coordinator = _make_coordinator_with_circuit(circuit_name="Kitchen")
    coordinator.hass = MagicMock()
    with patch(
        "custom_components.span_panel.select.er.async_get"
    ) as mock_async_get:
        registry = MagicMock()
        registry.async_get_entity_id.return_value = "select.kitchen_circuit_priority"
        mock_async_get.return_value = registry

        select = SpanPanelCircuitsSelect(
            coordinator, CIRCUIT_PRIORITY_DESCRIPTION, "id", "SPAN Panel"
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

    coordinator.request_reload.assert_not_called()
    assert select._previous_circuit_name == "Renamed Kitchen"


def test_handle_coordinator_update_requests_reload_on_name_change() -> None:
    """Later circuit renames should request a select reload."""
    coordinator = _make_coordinator_with_circuit(circuit_name="Kitchen")
    coordinator.hass = MagicMock()
    with patch(
        "custom_components.span_panel.select.er.async_get"
    ) as mock_async_get:
        registry = MagicMock()
        registry.async_get_entity_id.return_value = "select.kitchen_circuit_priority"
        mock_async_get.return_value = registry

        select = SpanPanelCircuitsSelect(
            coordinator, CIRCUIT_PRIORITY_DESCRIPTION, "id", "SPAN Panel"
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


def test_handle_coordinator_update_skips_when_circuit_missing_from_snapshot() -> None:
    """Select entity should not crash when its circuit is temporarily absent from a snapshot."""
    coordinator = _make_coordinator_with_circuit(circuit_name="Kitchen")
    coordinator.hass = MagicMock()
    with patch(
        "custom_components.span_panel.select.er.async_get"
    ) as mock_async_get:
        registry = MagicMock()
        registry.async_get_entity_id.return_value = None
        mock_async_get.return_value = registry

        select = SpanPanelCircuitsSelect(
            coordinator, CIRCUIT_PRIORITY_DESCRIPTION, "id", "SPAN Panel"
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
async def test_async_setup_entry_filters_supported_circuits() -> None:
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
    config_entry.runtime_data = MagicMock(control_policy=ControlPolicy.default(), coordinator=coordinator)
    async_add_entities = MagicMock()

    await async_setup_entry(MagicMock(), config_entry, async_add_entities)

    entities = async_add_entities.call_args.args[0]
    assert len(entities) == 2
    assert {entity.id for entity in entities} == {"main-1", "pv-1"}


async def test_async_setup_entry_skips_circuits_whose_priority_is_not_settable() -> None:
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
    coordinator.data = SpanPanelSnapshotFactory.create(
        circuits={"main-1": settable_priority, "main-2": locked_priority}
    )
    config_entry = MagicMock()
    config_entry.title = "SPAN Panel"
    config_entry.data = {}
    config_entry.runtime_data = MagicMock(control_policy=ControlPolicy.default(), coordinator=coordinator)
    async_add_entities = MagicMock()

    await async_setup_entry(MagicMock(), config_entry, async_add_entities)

    entities = async_add_entities.call_args.args[0]
    assert {entity.id for entity in entities} == {"main-1"}


def test_select_circuit_numbers_entity_id_stable_after_reload(
    hass: HomeAssistant,
) -> None:
    """The base must stay circuit-based after name sync sets the friendly display name."""
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
            "2",
            "SPAN Panel",
        )

    assert select.name == "Air Conditioner Circuit Priority"
    assert select.suggested_object_id == "Circuit 15 17 circuit priority"

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
            "2",
            "SPAN Panel",
        )

    # The panel's name, carried by original_name rather than the registry's
    # `name`, which would outrank the base.
    assert select2.name == "Air Conditioner Circuit Priority"
    assert select2.suggested_object_id == "Circuit 15 17 circuit priority"


def test_select_circuit_numbers_entity_id_120v_single_tab(
    hass: HomeAssistant,
) -> None:
    """120V single-tab circuit should produce a base with one tab number."""
    circuit = SpanCircuitSnapshotFactory.create(
        circuit_id="5",
        name="Kitchen Outlets",
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
        registry.async_get_entity_id.return_value = None
        mp.setattr(
            "custom_components.span_panel.select.er.async_get",
            lambda _hass: registry,
        )
        select = SpanPanelCircuitsSelect(
            coordinator,
            CIRCUIT_PRIORITY_DESCRIPTION,
            "5",
            "SPAN Panel",
        )

    assert select.name == "Kitchen Outlets Circuit Priority"
    assert select.suggested_object_id == "Circuit 10 circuit priority"


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
