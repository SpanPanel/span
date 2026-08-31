"""Direct tests for Span Panel sensor entity classes."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import CONF_HOST, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import State
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from span_panel_api import SpanPVSnapshot

from custom_components.span_panel import SpanPanelRuntimeData
from custom_components.span_panel.const import (
    ENABLE_ENERGY_DIP_COMPENSATION,
    USE_CIRCUIT_NUMBERS,
)
from custom_components.span_panel.curation import CurationOverlay
from custom_components.span_panel.options import ENERGY_REPORTING_GRACE_PERIOD
from custom_components.span_panel.sensor_base import (
    SpanEnergyExtraStoredData,
    _parse_numeric_state,
)
from custom_components.span_panel.sensor_circuit import (
    SpanCircuitEnergySensor,
    SpanCircuitPowerSensor,
    SpanUnmappedCircuitSensor,
    _resolve_circuit_identifier_for_sync,
    _unnamed_circuit_fallback,
)
from custom_components.span_panel.sensor_definitions import (
    BATTERY_SENSOR,
    BESS_METADATA_SENSORS,
    CIRCUIT_BREAKER_RATING_SENSOR,
    CIRCUIT_CURRENT_SENSOR,
    CIRCUIT_SENSORS,
    EVSE_SENSORS,
    PANEL_DATA_STATUS_SENSORS,
    PANEL_ENERGY_SENSORS,
    PANEL_POWER_SENSORS,
    PV_METADATA_SENSORS,
    STATUS_SENSORS,
    UNMAPPED_SENSORS,
    SpanPanelDataSensorEntityDescription,
)
from custom_components.span_panel.sensor_evse import SpanEvseSensor
from custom_components.span_panel.sensor_panel import (
    SpanBessMetadataSensor,
    SpanPanelBattery,
    SpanPanelEnergySensor,
    SpanPanelPanelStatus,
    SpanPanelPowerSensor,
    SpanPanelStatus,
    SpanPVMetadataSensor,
)

from .adapter_fixtures import schema_one_snapshot, schema_one_tree, schema_zero_snapshot
from .factories import (
    SpanBatterySnapshotFactory,
    SpanCircuitSnapshotFactory,
    SpanEvseSnapshotFactory,
    SpanPanelSnapshotFactory,
)


@pytest.fixture(autouse=True)
def _mock_entity_registry():
    """Patch entity registry lookups used during sensor construction."""
    registry = MagicMock()
    registry.async_get_entity_id.return_value = None
    with patch(
        "custom_components.span_panel.sensor_base.er.async_get",
        return_value=registry,
    ):
        yield registry


def _make_coordinator(
    snapshot, *, options: dict | None = None, schema_major: str | None = "schema_1"
) -> MagicMock:
    """Create a coordinator-like mock for direct sensor tests.

    `schema_major` is the running adapter's key, as `SpanMqttClient.schema_major`
    reports it. Defaulted to the parent/child adapter because a `MagicMock`
    attribute answers every comparison with an object that is equal to nothing,
    and a sensor asking which adapter produced a field would then read every
    coordinator in this module as an adapter it has never heard of.
    """
    coordinator = MagicMock()
    coordinator.data = snapshot
    coordinator.client.schema_major = schema_major
    coordinator.hass = MagicMock()
    coordinator.panel_offline = False
    coordinator.transport_dead = False
    coordinator.config_entry = MockConfigEntry(
        domain="span_panel",
        data={CONF_HOST: "192.168.1.50"},
        options=options or {},
        title="SPAN Panel",
        unique_id=snapshot.serial_number,
    )
    coordinator.config_entry.runtime_data = SpanPanelRuntimeData(
        coordinator=coordinator,
        panel_device_id="panel-device-id",
        curation=CurationOverlay.empty(),
    )
    coordinator.request_reload = MagicMock()
    coordinator.register_circuit_energy_sensor = MagicMock()
    coordinator.get_circuit_dip_offset = MagicMock(return_value=0.0)
    return coordinator


def test_panel_power_sensor_extra_state_attributes_include_amperage() -> None:
    """Panel power sensors should expose 240V and derived amperage."""
    snapshot = SpanPanelSnapshotFactory.create(instant_grid_power_w=480.0)
    coordinator = _make_coordinator(snapshot)
    description = next(desc for desc in PANEL_POWER_SENSORS if desc.key == "instantGridPowerW")

    sensor = SpanPanelPowerSensor(coordinator, description, snapshot)

    sensor._update_native_value()

    assert sensor.native_value == 480.0
    assert sensor.extra_state_attributes == {
        "voltage": 240,
        "amperage": 2.0,
        "at_service_entrance": True,
    }


def test_panel_power_sensor_defaults_amperage_when_value_not_numeric() -> None:
    """Panel power attributes should fall back to 0.0 amperage when value is unknown."""
    snapshot = SpanPanelSnapshotFactory.create(instant_grid_power_w=480.0)
    coordinator = _make_coordinator(snapshot)
    description = next(desc for desc in PANEL_POWER_SENSORS if desc.key == "instantGridPowerW")

    sensor = SpanPanelPowerSensor(coordinator, description, snapshot)

    sensor._attr_native_value = STATE_UNKNOWN

    assert sensor.extra_state_attributes == {
        "voltage": 240,
        "amperage": 0.0,
        "at_service_entrance": True,
    }


def test_grid_power_says_when_the_lugs_are_not_the_utility_connection() -> None:
    """The attribute exists for the case where the two grid figures disagree.

    A BESS ahead of the main lugs, or a panel fed by another panel, leaves this
    sensor metering that panel's own feed while `power_flow_grid` stays
    site-level. Both readings are right and they stop being the same number, and
    without this the user cannot tell that from a fault.
    """
    snapshot = SpanPanelSnapshotFactory.create(
        instant_grid_power_w=480.0, lugs_at_service_entrance=False
    )
    coordinator = _make_coordinator(snapshot)
    description = next(desc for desc in PANEL_POWER_SENSORS if desc.key == "instantGridPowerW")

    sensor = SpanPanelPowerSensor(coordinator, description, snapshot)
    sensor._update_native_value()

    assert sensor.extra_state_attributes["at_service_entrance"] is False
    # The label is conditional; the measurement is not.
    assert sensor.native_value == 480.0


def test_only_the_grid_sensor_carries_the_topology_attribute() -> None:
    """The same class backs four sensors and only one reads a topology-dependent meter.

    Feedthrough, battery and PV are what they say they are wherever the panel
    sits, so an attribute qualifying the grid label would be noise on them --
    and worse, would read as qualifying *their* value.
    """
    snapshot = SpanPanelSnapshotFactory.create(
        instant_grid_power_w=480.0, feedthrough_power_w=120.0, lugs_at_service_entrance=False
    )
    coordinator = _make_coordinator(snapshot)
    description = next(desc for desc in PANEL_POWER_SENSORS if desc.key == "feedthroughPowerW")

    sensor = SpanPanelPowerSensor(coordinator, description, snapshot)
    sensor._update_native_value()

    assert "at_service_entrance" not in (sensor.extra_state_attributes or {})


def test_flat_firmware_publishes_no_topology_it_was_never_told() -> None:
    """The flat adapter never writes the field, so its value is a library default.

    `lugs_at_service_entrance` is a plain `bool` defaulting to True and
    `span_panel_api_schema_0` does not reference it, so a flat panel with a BESS
    ahead of its main lugs -- the very topology the attribute exists to report --
    would publish `at_service_entrance: True`. That is not a wrong reading, which
    a user could at least argue with; it is the absence of one wearing a
    reading's clothes.

    Driven through the real flat adapter rather than the snapshot factory: the
    factory takes whatever a test hands it, and the claim under test is about
    what the adapter does.
    """
    flat = schema_zero_snapshot()
    assert flat.lugs_at_service_entrance is True, (
        "the library default moved; this test's premise is stale"
    )

    coordinator = _make_coordinator(
        replace(flat, instant_grid_power_w=480.0), schema_major="schema_0"
    )
    description = next(desc for desc in PANEL_POWER_SENSORS if desc.key == "instantGridPowerW")

    sensor = SpanPanelPowerSensor(coordinator, description, flat)
    sensor._update_native_value()

    assert sensor.native_value == 480.0
    assert "at_service_entrance" not in (sensor.extra_state_attributes or {})


def test_the_topology_attribute_reports_what_the_parent_child_panel_published() -> None:
    """schema_1 resolves the field, so the attribute is a reading and is published.

    The reference capture's upstream lugs carry `connection/fed-by-device-id =
    bess`: a real panel with a battery ahead of the main lugs, which is the case
    the flat adapter cannot see and this one can.
    """
    snapshot = schema_one_snapshot()
    coordinator = _make_coordinator(snapshot, schema_major="schema_1")
    description = next(desc for desc in PANEL_POWER_SENSORS if desc.key == "instantGridPowerW")

    sensor = SpanPanelPowerSensor(coordinator, description, snapshot)
    sensor._update_native_value()

    assert sensor.extra_state_attributes["at_service_entrance"] is False


def test_the_topology_attribute_still_says_true_when_true_is_a_reading() -> None:
    """Omission must turn on where the value came from, never on what it is.

    Republishing the capture without the upstream lugs' `fed-by-device-id` makes
    a panel that *is* at the service entrance, and schema_1 reads True off it.
    Suppressing True as "probably a default" would lose that.
    """
    tree = schema_one_tree()
    del tree["lugs-upstream"]["connection/fed-by-device-id"]
    snapshot = schema_one_snapshot(tree)
    coordinator = _make_coordinator(snapshot, schema_major="schema_1")
    description = next(desc for desc in PANEL_POWER_SENSORS if desc.key == "instantGridPowerW")

    sensor = SpanPanelPowerSensor(coordinator, description, snapshot)
    sensor._update_native_value()

    assert sensor.extra_state_attributes["at_service_entrance"] is True


def test_the_topology_attribute_is_omitted_before_an_adapter_is_known() -> None:
    """`schema_major` is None until the client has dispatched one.

    Unknown is not "flat" and not "at the service entrance"; it is unknown, and
    the attribute's whole job is to be a reading.
    """
    snapshot = SpanPanelSnapshotFactory.create(instant_grid_power_w=480.0)
    coordinator = _make_coordinator(snapshot, schema_major=None)
    description = next(desc for desc in PANEL_POWER_SENSORS if desc.key == "instantGridPowerW")

    sensor = SpanPanelPowerSensor(coordinator, description, snapshot)
    sensor._update_native_value()

    assert "at_service_entrance" not in (sensor.extra_state_attributes or {})


def test_a_panel_sensor_with_a_translation_key_falls_back_to_the_neutral_label() -> None:
    """One shared answer replaces the per-class fallbacks, and it is name-only.

    `_generate_panel_name` returns the description's own label and nothing else;
    the per-class fallbacks that used to answer here ("Battery", "Status", ...)
    are gone. A description that declares a `translation_key` declares no name,
    so the shared answer can only offer the neutral word -- which is why the
    constructor does not ask it for those at all (`sensor_base.py`: `_attr_name`
    is set only `if not ... translation_key`) and their real label reaches the UI
    from `translations/en.json`. This case pins what the layer says when asked,
    not what such a sensor displays.
    """
    battery = SpanBatterySnapshotFactory.create(soe_percentage=77.0)
    snapshot = SpanPanelSnapshotFactory.create(
        battery=battery,
        pv=SpanPVSnapshot(vendor_name="SolarEdge"),
    )
    coordinator = _make_coordinator(snapshot)

    status_desc = next(desc for desc in STATUS_SENSORS if desc.key == "software_version")
    assert not isinstance(status_desc.name, str), (
        "this case is only interesting for a description that declares no name"
    )

    status_sensor = SpanPanelStatus(coordinator, status_desc, snapshot)
    assert status_sensor._generate_panel_name(snapshot, status_desc) == "Sensor"


def test_a_panel_sensor_with_a_name_shows_it() -> None:
    """The other half of the same answer, with the label spelled on the description."""
    snapshot = SpanPanelSnapshotFactory.create()
    coordinator = _make_coordinator(snapshot)
    unnamed = next(desc for desc in PANEL_DATA_STATUS_SENSORS if desc.key == "main_relay_state")
    described = replace(unnamed, name="Main Relay")

    sensor = SpanPanelPanelStatus(coordinator, described, snapshot)
    assert sensor._generate_panel_name(snapshot, described) == "Main Relay"


def test_panel_metadata_sensors_return_expected_data_sources() -> None:
    """BESS and PV metadata sensors should read their expected snapshots."""
    battery = SpanBatterySnapshotFactory.create(vendor_name="Tesla")
    pv_snapshot = SpanPVSnapshot(vendor_name="SolarEdge")
    snapshot = SpanPanelSnapshotFactory.create(battery=battery, pv=pv_snapshot)
    coordinator = _make_coordinator(snapshot)

    bess_sensor = SpanBessMetadataSensor(
        coordinator,
        BESS_METADATA_SENSORS[0],
        snapshot,
        {"identifiers": {("span_panel", "bess")}},
    )
    pv_sensor = SpanPVMetadataSensor(
        coordinator,
        PV_METADATA_SENSORS[0],
        snapshot,
        {"identifiers": {("span_panel", "pv")}},
    )

    assert bess_sensor.get_data_source(snapshot) is battery
    assert pv_sensor.get_data_source(snapshot) is snapshot


def test_panel_energy_sensor_extra_attributes_include_voltage_and_grace() -> None:
    """Panel energy sensors should merge grace-period and voltage attributes."""
    snapshot = SpanPanelSnapshotFactory.create(main_meter_energy_consumed_wh=1250.0)
    coordinator = _make_coordinator(snapshot)
    description = next(
        desc for desc in PANEL_ENERGY_SENSORS if desc.key == "mainMeterEnergyConsumedWh"
    )

    sensor = SpanPanelEnergySensor(coordinator, description, snapshot)

    sensor._last_valid_state = 1250.0
    sensor._last_valid_changed = datetime.now(tz=UTC) - timedelta(minutes=1)

    attrs = sensor.extra_state_attributes
    assert attrs is not None
    assert attrs["voltage"] == 240
    assert attrs["last_valid_state"] == "1250.0"
    assert "grace_period_remaining" in attrs


def test_panel_battery_sensor_uses_battery_snapshot() -> None:
    """Battery sensors should read from the nested battery snapshot."""
    battery = SpanBatterySnapshotFactory.create(soe_percentage=77.0)
    snapshot = SpanPanelSnapshotFactory.create(battery=battery)
    coordinator = _make_coordinator(snapshot)

    sensor = SpanPanelBattery(coordinator, BATTERY_SENSOR, snapshot)

    assert sensor.get_data_source(snapshot) is battery


def test_panel_status_attributes_return_none_without_coordinator_data() -> None:
    """Panel status attributes should disappear when coordinator data is missing."""
    snapshot = SpanPanelSnapshotFactory.create()
    coordinator = _make_coordinator(snapshot)

    sensor = SpanPanelStatus(
        coordinator,
        next(desc for desc in STATUS_SENSORS if desc.key == "software_version"),
        snapshot,
    )

    coordinator.data = None

    assert sensor.extra_state_attributes is None


def test_bess_metadata_sensor_uses_device_override() -> None:
    """BESS metadata sensors should preserve the provided sub-device info."""
    snapshot = SpanPanelSnapshotFactory.create(
        battery=SpanBatterySnapshotFactory.create(vendor_name="Tesla")
    )
    coordinator = _make_coordinator(snapshot)
    device_info = {"identifiers": {("span_panel", "sp3-242424-001_bess")}}

    sensor = SpanBessMetadataSensor(coordinator, BESS_METADATA_SENSORS[0], snapshot, device_info)

    assert sensor.device_info == device_info


def test_circuit_power_sensor_extra_attributes_include_circuit_metadata() -> None:
    """Circuit power/current sensors should expose circuit attributes."""
    circuit = SpanCircuitSnapshotFactory.create(
        circuit_id="c1",
        name="Kitchen",
        tabs=[5, 6],
        always_on=True,
        relay_state="CLOSED",
        relay_requester="USER",
        priority="SOC_THRESHOLD",
        is_sheddable=True,
        current_a=12.5,
    )
    snapshot = SpanPanelSnapshotFactory.create(circuits={"c1": circuit})
    coordinator = _make_coordinator(snapshot)

    sensor = SpanCircuitPowerSensor(coordinator, CIRCUIT_CURRENT_SENSOR, snapshot, "c1")

    assert sensor.extra_state_attributes == {
        "tabs": "tabs [5:6]",
        "voltage": 240,
        "always_on": True,
        "relay_state": "CLOSED",
        "relay_requester": "USER",
        "shed_priority": "SOC_THRESHOLD",
        "is_sheddable": True,
    }


def test_circuit_power_sensor_names_an_unnamed_circuit_after_its_tab() -> None:
    """An unnamed circuit in friendly-name mode falls back to its breaker position.

    Answering `None` here let Home Assistant name every unnamed circuit on the
    panel alike, and the id followed: they collided and were disambiguated with
    `_2`, `_3`, ... in whatever order they happened to be added.
    """
    circuit = SpanCircuitSnapshotFactory.create(circuit_id="c1", name=None, tabs=[7])
    snapshot = SpanPanelSnapshotFactory.create(circuits={"c1": circuit})
    coordinator = _make_coordinator(snapshot, options={"use_circuit_numbers": False})

    sensor = SpanCircuitPowerSensor(coordinator, CIRCUIT_BREAKER_RATING_SENSOR, snapshot, "c1")

    assert sensor.name == "Circuit 7 Breaker Rating"


def test_unnamed_circuit_fallback_uses_solar_and_evse_labels() -> None:
    """Unnamed PV and EVSE circuits should use special fallback labels."""
    solar_circuit = SpanCircuitSnapshotFactory.create(circuit_id="pv1", name="", device_type="pv")
    evse_circuit = SpanCircuitSnapshotFactory.create(circuit_id="ev1", name="", device_type="evse")

    assert _unnamed_circuit_fallback(solar_circuit, "pv1") == "Solar"
    assert _unnamed_circuit_fallback(evse_circuit, "ev1") == "EV Charger"


def test_resolve_circuit_identifier_for_sync_falls_back_when_name_missing() -> None:
    """Panel-name sync should use unnamed fallback labels when needed."""
    evse_circuit = SpanCircuitSnapshotFactory.create(circuit_id="ev1", name="", device_type="evse")

    assert _resolve_circuit_identifier_for_sync(evse_circuit, "ev1") == "EV Charger"


def test_circuit_power_sensor_subdevice_uses_short_name() -> None:
    """EVSE sub-device circuit sensors should omit circuit prefix in the name."""
    circuit = SpanCircuitSnapshotFactory.create(circuit_id="c1", name="Garage")
    snapshot = SpanPanelSnapshotFactory.create(circuits={"c1": circuit})
    coordinator = _make_coordinator(snapshot)

    sensor = SpanCircuitPowerSensor(
        coordinator,
        CIRCUIT_CURRENT_SENSOR,
        snapshot,
        "c1",
        device_info_override={"identifiers": {("span_panel", "evse")}},
    )

    assert sensor._generate_panel_name(snapshot, sensor.entity_description) == "Current"


def test_circuit_power_sensor_missing_circuit_uses_unmapped_fallback_name() -> None:
    """Missing circuits should use the unmapped friendly name fallback."""
    snapshot = SpanPanelSnapshotFactory.create(circuits={})
    coordinator = _make_coordinator(snapshot)

    sensor = SpanCircuitPowerSensor(
        coordinator, CIRCUIT_CURRENT_SENSOR, snapshot, "missing_circuit"
    )

    assert (
        sensor._generate_panel_name(snapshot, sensor.entity_description)
        == "Unmapped Tab missing_circuit Current"
    )


def test_circuit_power_sensor_get_data_source_raises_for_missing_circuit() -> None:
    """Missing circuit data should raise a clear KeyError."""
    snapshot = SpanPanelSnapshotFactory.create(circuits={})
    coordinator = _make_coordinator(snapshot)

    sensor = SpanCircuitPowerSensor(coordinator, CIRCUIT_CURRENT_SENSOR, snapshot, "c1")

    with pytest.raises(KeyError, match="Circuit c1 not found"):
        sensor.get_data_source(snapshot)


def test_circuit_energy_sensor_registers_consumed_sensor_on_add() -> None:
    """Consumed/produced energy sensors should register with the coordinator."""
    circuit = SpanCircuitSnapshotFactory.create(circuit_id="c1", name="Kitchen")
    snapshot = SpanPanelSnapshotFactory.create(circuits={"c1": circuit})
    coordinator = _make_coordinator(snapshot)
    description = next(desc for desc in CIRCUIT_SENSORS if desc.key == "circuit_energy_consumed")

    sensor = SpanCircuitEnergySensor(coordinator, description, snapshot, "c1")

    sensor.async_get_last_extra_data = AsyncMock(return_value=None)
    sensor.async_get_last_state = AsyncMock(return_value=None)
    sensor.hass = MagicMock()
    sensor.entity_id = "sensor.kitchen_consumed_energy"

    with patch(
        "homeassistant.helpers.restore_state.async_get",
        return_value=MagicMock(async_restore_entity_added=MagicMock(return_value=None)),
    ):
        asyncio.run(sensor.async_added_to_hass())

    coordinator.register_circuit_energy_sensor.assert_called_once_with("c1", "consumed", sensor)


def test_circuit_net_energy_sensor_applies_dip_offset_adjustment() -> None:
    """Net energy sensors should add coordinator-provided dip compensation offsets."""
    circuit = SpanCircuitSnapshotFactory.create(
        circuit_id="c1",
        name="Kitchen",
        consumed_energy_wh=10.0,
        produced_energy_wh=2.0,
    )
    snapshot = SpanPanelSnapshotFactory.create(circuits={"c1": circuit})
    coordinator = _make_coordinator(snapshot)
    coordinator.get_circuit_dip_offset.side_effect = [5.0, 2.0]
    description = next(desc for desc in CIRCUIT_SENSORS if desc.key == "circuit_energy_net")

    sensor = SpanCircuitEnergySensor(coordinator, description, snapshot, "c1")

    sensor._process_raw_value(20.0)

    assert sensor.native_value == 23.0


def test_circuit_energy_sensor_missing_circuit_uses_fallback_names() -> None:
    """Circuit energy sensors should format fallback names when the circuit is gone."""
    snapshot = SpanPanelSnapshotFactory.create(circuits={})
    coordinator = _make_coordinator(snapshot)
    description = next(desc for desc in CIRCUIT_SENSORS if desc.key == "circuit_energy_consumed")

    sensor = SpanCircuitEnergySensor(coordinator, description, snapshot, "c9")

    assert (
        sensor._generate_panel_name(snapshot, sensor.entity_description)
        == "Circuit c9 Consumed Energy"
    )


def test_circuit_energy_sensor_subdevice_uses_description_only() -> None:
    """EVSE sub-device energy sensors should use the description name directly."""
    circuit = SpanCircuitSnapshotFactory.create(circuit_id="c1", name="Garage")
    snapshot = SpanPanelSnapshotFactory.create(circuits={"c1": circuit})
    coordinator = _make_coordinator(snapshot)
    description = next(desc for desc in CIRCUIT_SENSORS if desc.key == "circuit_energy_consumed")

    sensor = SpanCircuitEnergySensor(
        coordinator,
        description,
        snapshot,
        "c1",
        device_info_override={"identifiers": {("span_panel", "evse")}},
    )

    assert sensor._generate_panel_name(snapshot, sensor.entity_description) == "Consumed Energy"


def test_circuit_energy_sensor_extra_attributes_only_include_base_when_circuit_missing() -> None:
    """Missing circuit data should still return grace-period attributes without tabs."""
    snapshot = SpanPanelSnapshotFactory.create(circuits={})
    coordinator = _make_coordinator(snapshot)
    description = next(desc for desc in CIRCUIT_SENSORS if desc.key == "circuit_energy_consumed")

    sensor = SpanCircuitEnergySensor(coordinator, description, snapshot, "c1")

    sensor._last_valid_state = 12.0
    sensor._last_valid_changed = datetime.now(tz=UTC) - timedelta(minutes=1)

    attrs = sensor.extra_state_attributes
    assert attrs is not None
    assert attrs["last_valid_state"] == "12.0"
    assert "tabs" not in attrs
    assert "voltage" not in attrs


def test_unmapped_circuit_sensor_generates_unmapped_friendly_name() -> None:
    """Unmapped circuit sensors should use tab-based fallback names."""
    snapshot = SpanPanelSnapshotFactory.create(
        circuits={"unmapped_tab_7": SpanCircuitSnapshotFactory.create(circuit_id="unmapped_tab_7")}
    )
    coordinator = _make_coordinator(snapshot)

    sensor = SpanUnmappedCircuitSensor(coordinator, UNMAPPED_SENSORS[0], snapshot, "unmapped_tab_7")

    assert (
        sensor._generate_panel_name(snapshot, sensor.entity_description)
        == "Unmapped Tab 7 Power"
    )


def test_parse_numeric_state_ignores_unknown_state() -> None:
    """Non-numeric restore states should not seed grace tracking."""
    restored = State("sensor.span_energy", STATE_UNKNOWN)

    assert _parse_numeric_state(restored) == (None, None)


def test_parse_numeric_state_returns_numeric_value_and_timestamp() -> None:
    """Numeric restore states should be parsed to float and naive datetime."""
    restored = State("sensor.span_energy", "42.5")

    value, changed = _parse_numeric_state(restored)

    assert value == 42.5
    assert changed is not None
    assert changed.tzinfo is UTC


def test_parse_numeric_state_ignores_non_numeric_value() -> None:
    """Non-numeric restore states should not seed grace tracking."""
    restored = State("sensor.span_energy", "not-a-number")

    assert _parse_numeric_state(restored) == (None, None)


def test_panel_power_sensor_stays_available_while_panel_offline() -> None:
    """Base sensor availability should stay true during panel-offline handling."""
    snapshot = SpanPanelSnapshotFactory.create(instant_grid_power_w=250.0)
    coordinator = _make_coordinator(snapshot)
    coordinator.panel_offline = True
    description = next(desc for desc in PANEL_POWER_SENSORS if desc.key == "instantGridPowerW")

    sensor = SpanPanelPowerSensor(coordinator, description, snapshot)

    sensor._update_native_value()

    assert sensor.available is True
    assert sensor.native_value == 0.0


def test_panel_status_sensor_reports_unknown_when_offline() -> None:
    """Enum-like sensors should report unknown when offline."""
    snapshot = SpanPanelSnapshotFactory.create(main_relay_state="CLOSED")
    coordinator = _make_coordinator(snapshot)
    coordinator.panel_offline = True
    description = next(desc for desc in PANEL_DATA_STATUS_SENSORS if desc.key == "main_relay_state")

    sensor = SpanPanelPowerSensor(coordinator, description, snapshot)

    sensor._update_native_value()

    assert sensor.native_value == STATE_UNKNOWN


def test_panel_data_sensor_missing_value_function_reports_unknown() -> None:
    """Sensors without a value function should fall back to unknown."""
    snapshot = SpanPanelSnapshotFactory.create()
    coordinator = _make_coordinator(snapshot)
    description = SpanPanelDataSensorEntityDescription(
        key="missing_value",
        value_fn=None,
        device_class=SensorDeviceClass.ENUM,
        translation_key="missing_value",
        options=["unknown"],
        entity_category=None,
    )

    sensor = SpanPanelPowerSensor(coordinator, description, snapshot)

    sensor._handle_online_state()

    assert sensor.native_value == STATE_UNKNOWN


def test_panel_data_sensor_adds_enum_option_for_new_value() -> None:
    """Enum sensors should normalize and append unseen options."""
    snapshot = SpanPanelSnapshotFactory.create()
    coordinator = _make_coordinator(snapshot)
    description = SpanPanelDataSensorEntityDescription(
        key="dynamic_enum",
        value_fn=lambda _: "NEW_STATE",
        device_class=SensorDeviceClass.ENUM,
        translation_key="dynamic_enum",
        options=["unknown"],
        entity_category=None,
    )

    sensor = SpanPanelPowerSensor(coordinator, description, snapshot)

    sensor._handle_online_state()

    assert sensor.native_value == "new_state"
    assert "new_state" in sensor.options


def test_panel_data_sensor_initializes_missing_enum_options() -> None:
    """Enum sensors should initialize the options list when it is missing."""
    snapshot = SpanPanelSnapshotFactory.create()
    coordinator = _make_coordinator(snapshot)
    description = SpanPanelDataSensorEntityDescription(
        key="dynamic_enum_missing_options",
        value_fn=lambda _: "BRAND_NEW",
        device_class=SensorDeviceClass.ENUM,
        translation_key="dynamic_enum_missing_options",
        options=None,
        entity_category=None,
    )

    sensor = SpanPanelPowerSensor(coordinator, description, snapshot)

    sensor._handle_online_state()

    assert sensor.native_value == "brand_new"
    assert sensor.options == ["brand_new"]


def test_energy_extra_stored_data_invalid_input_returns_none() -> None:
    """Malformed extra restore data should be ignored."""
    assert SpanEnergyExtraStoredData.from_dict("invalid") is None


def test_energy_sensor_coerces_invalid_grace_period_value() -> None:
    """Grace period options should be normalized to a safe integer."""
    snapshot = SpanPanelSnapshotFactory.create(main_meter_energy_consumed_wh=1250.0)
    coordinator = _make_coordinator(snapshot)
    coordinator.config_entry = MockConfigEntry(
        domain="span_panel",
        data={CONF_HOST: "192.168.1.50"},
        options={"energy_reporting_grace_period": "abc"},
        title="SPAN Panel",
        unique_id=snapshot.serial_number,
    )
    coordinator.config_entry.runtime_data = SpanPanelRuntimeData(
        coordinator=coordinator,
        panel_device_id="panel-device-id",
        curation=CurationOverlay.empty(),
    )
    description = next(
        desc for desc in PANEL_ENERGY_SENSORS if desc.key == "mainMeterEnergyConsumedWh"
    )

    sensor = SpanPanelEnergySensor(coordinator, description, snapshot)

    sensor._last_valid_state = 1250.0
    sensor._last_valid_changed = datetime.now(tz=UTC) - timedelta(minutes=1)
    sensor._grace_period_minutes = "abc"

    attrs = sensor.extra_state_attributes
    assert attrs is not None
    assert attrs["grace_period_remaining"] == "13"


def test_energy_sensor_offline_without_last_valid_state_reports_none() -> None:
    """Energy sensors without restored state should become unknown when offline."""
    snapshot = SpanPanelSnapshotFactory.create(main_meter_energy_consumed_wh=1250.0)
    coordinator = _make_coordinator(snapshot)
    coordinator.panel_offline = True
    description = next(
        desc for desc in PANEL_ENERGY_SENSORS if desc.key == "mainMeterEnergyConsumedWh"
    )

    sensor = SpanPanelEnergySensor(coordinator, description, snapshot)

    sensor._update_native_value()

    assert sensor.native_value is None
    assert sensor.native_value != STATE_UNAVAILABLE


def test_circuit_power_sensor_logs_debug_info_for_instant_power(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Circuit sensors should emit debug info when power data is available."""
    circuit = SpanCircuitSnapshotFactory.create(
        circuit_id="c1",
        name="Kitchen",
        instant_power_w=150.0,
    )
    snapshot = SpanPanelSnapshotFactory.create(circuits={"c1": circuit})
    coordinator = _make_coordinator(snapshot)

    sensor = SpanCircuitPowerSensor(coordinator, CIRCUIT_CURRENT_SENSOR, snapshot, "c1")

    sensor.id = "c1"
    caplog.set_level("DEBUG")

    sensor._handle_online_state()

    assert "CIRCUIT_POWER_DEBUG: Circuit c1" in caplog.text


def test_evse_sensor_uses_evse_subdevice_info_and_name() -> None:
    """EVSE sensors should attach to the EVSE sub-device and keep static names."""
    evse = SpanEvseSnapshotFactory.create(node_id="evse-0", feed_circuit_id="c1")
    snapshot = SpanPanelSnapshotFactory.create(
        circuits={"c1": SpanCircuitSnapshotFactory.create(circuit_id="c1", name="Garage")},
        evse={"evse-0": evse},
    )
    coordinator = _make_coordinator(snapshot, options={"use_circuit_numbers": False})
    coordinator.config_entry = MockConfigEntry(
        domain="span_panel",
        data={CONF_HOST: "192.168.1.50", "device_name": "Main House"},
        options={"use_circuit_numbers": False},
        title="SPAN Panel",
        unique_id=snapshot.serial_number,
    )
    coordinator.config_entry.runtime_data = SpanPanelRuntimeData(
        coordinator=coordinator,
        panel_device_id="panel-device-id",
        curation=CurationOverlay.empty(),
    )
    description = next(desc for desc in EVSE_SENSORS if desc.key == "evse_status")

    sensor = SpanEvseSensor(coordinator, description, snapshot, "evse-0")

    assert sensor.entity_description.translation_key == "evse_status"
    assert sensor.unique_id.endswith("_evse_evse-0_evse_status")
    assert sensor.device_info["name"] == "Main House SPAN Drive (Garage)"
    assert sensor.get_data_source(snapshot) is evse


def test_evse_sensor_returns_empty_snapshot_when_missing_mid_session() -> None:
    """EVSE sensors should tolerate missing EVSE data after creation."""
    snapshot = SpanPanelSnapshotFactory.create(evse={"evse-0": SpanEvseSnapshotFactory.create()})
    coordinator = _make_coordinator(snapshot)
    description = next(desc for desc in EVSE_SENSORS if desc.key == "evse_status")

    sensor = SpanEvseSensor(coordinator, description, snapshot, "evse-0")

    missing_snapshot = SpanPanelSnapshotFactory.create(evse={})
    data_source = sensor.get_data_source(missing_snapshot)

    assert data_source.node_id == ""
    assert data_source.feed_circuit_id == ""


def test_existing_circuit_entity_uses_panel_name_on_init() -> None:
    """Existing registry entities should initialize with panel-sync naming."""
    circuit = SpanCircuitSnapshotFactory.create(circuit_id="c1", name="Kitchen")
    snapshot = SpanPanelSnapshotFactory.create(circuits={"c1": circuit})
    coordinator = _make_coordinator(snapshot)

    with patch("custom_components.span_panel.sensor_base.er.async_get") as mock_async_get:
        registry = MagicMock()
        registry.async_get_entity_id.return_value = "sensor.kitchen_current"
        mock_async_get.return_value = registry

        sensor = SpanCircuitPowerSensor(coordinator, CIRCUIT_CURRENT_SENSOR, snapshot, "c1")

    assert sensor._attr_name == "Kitchen Current"
    assert sensor._previous_circuit_name == "Kitchen"


def test_circuit_sensor_first_update_requests_reload_for_name_sync() -> None:
    """First coordinator update should request a reload for dynamic name sync."""
    circuit = SpanCircuitSnapshotFactory.create(circuit_id="c1", name="Kitchen")
    snapshot = SpanPanelSnapshotFactory.create(circuits={"c1": circuit})
    coordinator = _make_coordinator(snapshot)

    with patch("custom_components.span_panel.sensor_base.er.async_get") as mock_async_get:
        registry = MagicMock()
        registry.async_get_entity_id.return_value = None
        registry.async_get.return_value = None
        mock_async_get.return_value = registry

        sensor = SpanCircuitPowerSensor(coordinator, CIRCUIT_CURRENT_SENSOR, snapshot, "c1")

    sensor.hass = MagicMock()
    sensor.entity_id = "sensor.kitchen_current"
    sensor.async_write_ha_state = MagicMock()

    with patch("custom_components.span_panel.sensor_base.er.async_get") as mock_async_get:
        runtime_registry = MagicMock()
        runtime_registry.async_get.return_value = None
        mock_async_get.return_value = runtime_registry
        sensor._handle_coordinator_update()

    coordinator.request_reload.assert_called_once()
    assert sensor._previous_circuit_name == "Kitchen"


def test_circuit_sensor_user_override_skips_reload_on_name_change() -> None:
    """Customized entity names should suppress automatic sync reloads."""
    circuit = SpanCircuitSnapshotFactory.create(circuit_id="c1", name="Kitchen")
    snapshot = SpanPanelSnapshotFactory.create(circuits={"c1": circuit})
    coordinator = _make_coordinator(snapshot)

    with patch("custom_components.span_panel.sensor_base.er.async_get") as mock_async_get:
        init_registry = MagicMock()
        init_registry.async_get_entity_id.return_value = "sensor.kitchen_current"
        mock_async_get.return_value = init_registry

        sensor = SpanCircuitPowerSensor(coordinator, CIRCUIT_CURRENT_SENSOR, snapshot, "c1")

    updated_circuit = SpanCircuitSnapshotFactory.create(circuit_id="c1", name="Renamed Kitchen")
    coordinator.data = SpanPanelSnapshotFactory.create(circuits={"c1": updated_circuit})
    sensor.hass = MagicMock()
    sensor.entity_id = "sensor.kitchen_current"
    sensor.async_write_ha_state = MagicMock()

    with patch("custom_components.span_panel.sensor_base.er.async_get") as mock_async_get:
        runtime_registry = MagicMock()
        runtime_registry.async_get.return_value = MagicMock(name="Custom Kitchen Current")
        mock_async_get.return_value = runtime_registry
        sensor._handle_coordinator_update()

    coordinator.request_reload.assert_not_called()
    assert sensor._previous_circuit_name == "Renamed Kitchen"


def test_sensor_online_state_handles_lookup_error_as_unknown() -> None:
    """Lookup errors from the value function path should fall back to unknown."""
    snapshot = SpanPanelSnapshotFactory.create()
    coordinator = _make_coordinator(snapshot)
    description = SpanPanelDataSensorEntityDescription(
        key="lookup_error",
        value_fn=lambda data: data.missing_attribute,
        device_class=SensorDeviceClass.ENUM,
        translation_key="lookup_error",
        options=["unknown"],
        entity_category=None,
    )

    sensor = SpanPanelPowerSensor(coordinator, description, snapshot)

    sensor._handle_online_state()

    assert sensor.native_value == STATE_UNKNOWN


def test_sensor_online_state_handles_unexpected_exception_as_unknown(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Unexpected value function errors should log a warning and fall back."""
    snapshot = SpanPanelSnapshotFactory.create()
    coordinator = _make_coordinator(snapshot)
    description = SpanPanelDataSensorEntityDescription(
        key="boom",
        value_fn=lambda _: (_ for _ in ()).throw(RuntimeError("boom")),
        device_class=SensorDeviceClass.ENUM,
        translation_key="boom",
        options=["unknown"],
        entity_category=None,
    )

    sensor = SpanPanelPowerSensor(coordinator, description, snapshot)

    caplog.set_level("WARNING")
    sensor._handle_online_state()

    assert sensor.native_value == STATE_UNKNOWN
    assert "Value function failed" in caplog.text


def test_energy_sensor_restores_extra_data_on_add() -> None:
    """Energy sensors should restore grace-period and dip state from extra data."""
    snapshot = SpanPanelSnapshotFactory.create(main_meter_energy_consumed_wh=1250.0)
    coordinator = _make_coordinator(
        snapshot,
        options={
            ENABLE_ENERGY_DIP_COMPENSATION: True,
            ENERGY_REPORTING_GRACE_PERIOD: 15,
        },
    )
    description = next(
        desc for desc in PANEL_ENERGY_SENSORS if desc.key == "mainMeterEnergyConsumedWh"
    )

    sensor = SpanPanelEnergySensor(coordinator, description, snapshot)

    sensor.hass = MagicMock()
    sensor.entity_id = "sensor.main_meter_energy_consumed"
    sensor.async_get_last_extra_data = AsyncMock(
        return_value=MagicMock(
            as_dict=MagicMock(
                return_value={
                    "last_valid_state": 33.0,
                    "last_valid_changed": "2024-01-01T12:00:00",
                    "energy_offset": 5.0,
                    "last_panel_reading": 120.0,
                    "last_dip_delta": 2.5,
                }
            )
        )
    )
    sensor.async_get_last_state = AsyncMock(return_value=None)

    with patch(
        "homeassistant.helpers.restore_state.async_get",
        return_value=MagicMock(async_restore_entity_added=MagicMock(return_value=None)),
    ):
        asyncio.run(sensor.async_added_to_hass())

    assert sensor._last_valid_state == 33.0
    assert sensor._restored_from_storage is True
    assert sensor.energy_offset == 5.0
    assert sensor._last_panel_reading == 120.0
    assert sensor._last_dip_delta == 2.5


def test_dip_offset_applied_before_coordinator_listener_fires() -> None:
    """Dip offset must be set before the coordinator listener is registered.

    If an MQTT push arrives right when the listener is registered inside
    super().async_added_to_hass(), async_update_listeners() calls the callback
    synchronously.  The offset must already be in place at that moment or
    _process_raw_value() will apply offset=0 and report the bare panel counter.

    This test captures the value of _energy_offset at the exact instant
    async_add_listener is called and asserts it equals the stored offset, not 0.
    With the original ordering (restore AFTER super) the captured value was 0.0.
    """
    stored_offset = 139355.1

    snapshot = SpanPanelSnapshotFactory.create(main_meter_energy_consumed_wh=753930.6)
    coordinator = _make_coordinator(
        snapshot,
        options={
            ENABLE_ENERGY_DIP_COMPENSATION: True,
            ENERGY_REPORTING_GRACE_PERIOD: 15,
        },
    )
    description = next(
        desc for desc in PANEL_ENERGY_SENSORS if desc.key == "mainMeterEnergyConsumedWh"
    )
    sensor = SpanPanelEnergySensor(coordinator, description, snapshot)

    sensor.hass = MagicMock()
    sensor.entity_id = "sensor.main_meter_energy_consumed"
    sensor.async_get_last_extra_data = AsyncMock(
        return_value=MagicMock(
            as_dict=MagicMock(
                return_value={
                    "last_valid_state": 893285.7,
                    "last_valid_changed": "2026-05-11T17:31:46+00:00",
                    "energy_offset": stored_offset,
                    "last_panel_reading": 753930.6,
                    "last_dip_delta": 65711.9,
                }
            )
        )
    )
    sensor.async_get_last_state = AsyncMock(return_value=None)

    # Capture the entity's _energy_offset at the moment the listener is
    # registered (inside super().async_added_to_hass()).  With the fix, this
    # must already equal the stored offset; before the fix it was 0.0.
    offset_at_registration: list[float] = []

    def capture_offset_on_register(update_callback, context=None):
        offset_at_registration.append(sensor._energy_offset)
        return MagicMock()

    coordinator.async_add_listener = MagicMock(side_effect=capture_offset_on_register)

    with patch(
        "homeassistant.helpers.restore_state.async_get",
        return_value=MagicMock(async_restore_entity_added=MagicMock(return_value=None)),
    ):
        asyncio.run(sensor.async_added_to_hass())

    assert offset_at_registration, "async_add_listener was never called"
    assert offset_at_registration[0] == stored_offset, (
        f"_energy_offset at listener registration was {offset_at_registration[0]}, "
        f"expected {stored_offset} — dip offset not restored before super() call"
    )


def test_energy_sensor_restores_invalid_timestamp_logs_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Invalid restored timestamps should be ignored with a warning."""
    snapshot = SpanPanelSnapshotFactory.create(main_meter_energy_consumed_wh=1250.0)
    coordinator = _make_coordinator(snapshot)
    description = next(
        desc for desc in PANEL_ENERGY_SENSORS if desc.key == "mainMeterEnergyConsumedWh"
    )

    sensor = SpanPanelEnergySensor(coordinator, description, snapshot)

    sensor.hass = MagicMock()
    sensor.entity_id = "sensor.main_meter_energy_consumed"
    sensor.async_get_last_extra_data = AsyncMock(
        return_value=MagicMock(
            as_dict=MagicMock(
                return_value={
                    "last_valid_state": 33.0,
                    "last_valid_changed": "not-a-date",
                }
            )
        )
    )
    sensor.async_get_last_state = AsyncMock(return_value=None)

    caplog.set_level("WARNING")

    with patch(
        "homeassistant.helpers.restore_state.async_get",
        return_value=MagicMock(async_restore_entity_added=MagicMock(return_value=None)),
    ):
        asyncio.run(sensor.async_added_to_hass())

    assert sensor._last_valid_state == 33.0
    assert sensor._last_valid_changed is None
    assert "Failed to parse restored last_valid_changed" in caplog.text


def test_energy_sensor_initializes_grace_period_from_last_state() -> None:
    """Energy sensors should seed grace tracking from the last HA state."""
    snapshot = SpanPanelSnapshotFactory.create(main_meter_energy_consumed_wh=1250.0)
    coordinator = _make_coordinator(snapshot)
    description = next(
        desc for desc in PANEL_ENERGY_SENSORS if desc.key == "mainMeterEnergyConsumedWh"
    )

    sensor = SpanPanelEnergySensor(coordinator, description, snapshot)

    restored_changed = datetime(2024, 1, 2, 3, 4, 5)
    sensor.hass = MagicMock()
    sensor.entity_id = "sensor.main_meter_energy_consumed"
    sensor.async_get_last_extra_data = AsyncMock(return_value=None)
    sensor.async_get_last_state = AsyncMock(
        return_value=State(
            "sensor.main_meter_energy_consumed", "18.5", last_changed=restored_changed
        )
    )

    with patch(
        "homeassistant.helpers.restore_state.async_get",
        return_value=MagicMock(async_restore_entity_added=MagicMock(return_value=None)),
    ):
        asyncio.run(sensor.async_added_to_hass())

    assert sensor._last_valid_state == 18.5
    assert sensor._last_valid_changed == restored_changed.replace(tzinfo=UTC)
    assert sensor._restored_from_storage is True


def test_energy_sensor_extra_restore_state_data_includes_offsets() -> None:
    """Restore payload should include tracked grace and dip data."""
    snapshot = SpanPanelSnapshotFactory.create(main_meter_energy_consumed_wh=1250.0)
    coordinator = _make_coordinator(snapshot, options={ENABLE_ENERGY_DIP_COMPENSATION: True})
    description = next(
        desc for desc in PANEL_ENERGY_SENSORS if desc.key == "mainMeterEnergyConsumedWh"
    )

    sensor = SpanPanelEnergySensor(coordinator, description, snapshot)

    sensor._attr_native_value = 44.0
    sensor._last_valid_state = 40.0
    sensor._last_valid_changed = datetime(2024, 1, 1, 12, 0, 0)
    sensor._energy_offset = 3.0
    sensor._last_panel_reading = 44.0
    sensor._last_dip_delta = 1.5

    data = sensor.extra_restore_state_data.as_dict()

    assert data["native_value"] == 44.0
    assert data["last_valid_state"] == 40.0
    assert data["energy_offset"] == 3.0
    assert data["last_panel_reading"] == 44.0
    assert data["last_dip_delta"] == 1.5


def test_energy_sensor_offline_seeds_last_valid_from_current_native_value() -> None:
    """Offline grace handling should seed tracking from a restored native value."""
    snapshot = SpanPanelSnapshotFactory.create(main_meter_energy_consumed_wh=1250.0)
    coordinator = _make_coordinator(snapshot)
    coordinator.panel_offline = True
    description = next(
        desc for desc in PANEL_ENERGY_SENSORS if desc.key == "mainMeterEnergyConsumedWh"
    )

    sensor = SpanPanelEnergySensor(coordinator, description, snapshot)

    sensor._attr_native_value = 22.0

    sensor._handle_offline_grace_period()

    assert sensor.native_value == 22.0
    assert sensor._last_valid_state == 22.0
    assert sensor._last_valid_changed is not None


def test_energy_sensor_negative_grace_period_is_coerced_to_zero() -> None:
    """Negative grace periods should be normalized to zero."""
    snapshot = SpanPanelSnapshotFactory.create(main_meter_energy_consumed_wh=1250.0)
    coordinator = _make_coordinator(snapshot)
    description = next(
        desc for desc in PANEL_ENERGY_SENSORS if desc.key == "mainMeterEnergyConsumedWh"
    )

    sensor = SpanPanelEnergySensor(coordinator, description, snapshot)

    sensor._last_valid_state = 22.0
    sensor._last_valid_changed = datetime.now(tz=UTC) - timedelta(minutes=1)
    sensor._grace_period_minutes = -5

    attrs = sensor.extra_state_attributes

    assert attrs is not None
    assert "grace_period_remaining" not in attrs
    assert sensor._grace_period_minutes == 0


def test_energy_sensor_extra_attributes_mark_using_grace_period_when_offline() -> None:
    """Grace-period attributes should expose when offline state is being used."""
    snapshot = SpanPanelSnapshotFactory.create(main_meter_energy_consumed_wh=1250.0)
    coordinator = _make_coordinator(snapshot)
    coordinator.panel_offline = True
    description = next(
        desc for desc in PANEL_ENERGY_SENSORS if desc.key == "mainMeterEnergyConsumedWh"
    )

    sensor = SpanPanelEnergySensor(coordinator, description, snapshot)

    sensor._last_valid_state = 22.0
    sensor._last_valid_changed = datetime.now(tz=UTC) - timedelta(minutes=1)

    attrs = sensor.extra_state_attributes

    assert attrs is not None
    assert attrs["using_grace_period"] == "True"


def test_energy_sensor_first_update_requests_reload_for_name_sync() -> None:
    """Energy sensors should request reload on the first synced panel name."""
    circuit = SpanCircuitSnapshotFactory.create(circuit_id="c1", name="Kitchen")
    snapshot = SpanPanelSnapshotFactory.create(circuits={"c1": circuit})
    coordinator = _make_coordinator(snapshot)
    description = next(desc for desc in CIRCUIT_SENSORS if desc.key == "circuit_energy_consumed")

    with patch("custom_components.span_panel.sensor_base.er.async_get") as mock_async_get:
        registry = MagicMock()
        registry.async_get_entity_id.return_value = None
        registry.async_get.return_value = None
        mock_async_get.return_value = registry
        sensor = SpanCircuitEnergySensor(coordinator, description, snapshot, "c1")

    sensor.hass = MagicMock()
    sensor.entity_id = "sensor.kitchen_consumed_energy"
    sensor.async_write_ha_state = MagicMock()

    with patch("custom_components.span_panel.sensor_base.er.async_get") as mock_async_get:
        runtime_registry = MagicMock()
        runtime_registry.async_get.return_value = None
        mock_async_get.return_value = runtime_registry
        sensor._handle_coordinator_update()

    coordinator.request_reload.assert_called_once()
    assert sensor._previous_circuit_name == "Kitchen"


def test_energy_sensor_name_change_requests_reload() -> None:
    """Energy sensors should request reload when the panel circuit name changes."""
    circuit = SpanCircuitSnapshotFactory.create(circuit_id="c1", name="Kitchen")
    snapshot = SpanPanelSnapshotFactory.create(circuits={"c1": circuit})
    coordinator = _make_coordinator(snapshot)
    description = next(desc for desc in CIRCUIT_SENSORS if desc.key == "circuit_energy_consumed")

    with patch("custom_components.span_panel.sensor_base.er.async_get") as mock_async_get:
        init_registry = MagicMock()
        init_registry.async_get_entity_id.return_value = "sensor.kitchen_consumed_energy"
        mock_async_get.return_value = init_registry
        sensor = SpanCircuitEnergySensor(coordinator, description, snapshot, "c1")

    updated_circuit = SpanCircuitSnapshotFactory.create(circuit_id="c1", name="Renamed Kitchen")
    coordinator.data = SpanPanelSnapshotFactory.create(circuits={"c1": updated_circuit})
    sensor.hass = MagicMock()
    sensor.entity_id = "sensor.kitchen_consumed_energy"
    sensor.async_write_ha_state = MagicMock()

    with patch("custom_components.span_panel.sensor_base.er.async_get") as mock_async_get:
        runtime_registry = MagicMock()
        runtime_registry.async_get.return_value = None
        mock_async_get.return_value = runtime_registry
        sensor._handle_coordinator_update()

    coordinator.request_reload.assert_called_once()
    assert sensor._previous_circuit_name == "Renamed Kitchen"


def test_circuit_sensor_takes_the_panel_name_in_circuit_numbers_mode() -> None:
    """The name follows the panel; the entity_id does not follow the name."""
    circuit = SpanCircuitSnapshotFactory.create(circuit_id="c1", name="Kitchen", tabs=[5])
    snapshot = SpanPanelSnapshotFactory.create(circuits={"c1": circuit})
    coordinator = _make_coordinator(snapshot, options={USE_CIRCUIT_NUMBERS: True})

    with patch("custom_components.span_panel.sensor_base.er.async_get") as mock_async_get:
        registry = MagicMock()
        registry.async_get_entity_id.return_value = "sensor.circuit_5_current"
        entity_entry = MagicMock()
        entity_entry.name = None
        registry.async_get.return_value = entity_entry
        mock_async_get.return_value = registry

        sensor = SpanCircuitPowerSensor(coordinator, CIRCUIT_CURRENT_SENSOR, snapshot, "c1")

    assert sensor._attr_name == "Kitchen Current"
    assert sensor.suggested_object_id == "Circuit 5 current"
    assert sensor.entity_id is None  # Core assigns it when the platform adds the entity
    assert sensor._previous_circuit_name == "Kitchen"


def test_circuit_sensor_name_change_requests_reload_in_circuit_numbers_mode() -> None:
    """A renamed circuit reloads, which is what rebuilds original_name."""
    circuit = SpanCircuitSnapshotFactory.create(circuit_id="c1", name="Kitchen", tabs=[5])
    snapshot = SpanPanelSnapshotFactory.create(circuits={"c1": circuit})
    coordinator = _make_coordinator(snapshot, options={USE_CIRCUIT_NUMBERS: True})

    with patch("custom_components.span_panel.sensor_base.er.async_get") as mock_async_get:
        init_registry = MagicMock()
        init_registry.async_get_entity_id.return_value = "sensor.circuit_5_current"
        entity_entry = MagicMock()
        entity_entry.name = None
        init_registry.async_get.return_value = entity_entry
        mock_async_get.return_value = init_registry

        sensor = SpanCircuitPowerSensor(coordinator, CIRCUIT_CURRENT_SENSOR, snapshot, "c1")

    # Simulate coordinator update with a name change
    updated_circuit = SpanCircuitSnapshotFactory.create(
        circuit_id="c1", name="Renamed Kitchen", tabs=[5]
    )
    coordinator.data = SpanPanelSnapshotFactory.create(circuits={"c1": updated_circuit})
    sensor.hass = MagicMock()
    sensor.entity_id = "sensor.circuit_5_current"
    sensor.async_write_ha_state = MagicMock()

    with patch("custom_components.span_panel.sensor_base.er.async_get") as mock_async_get:
        runtime_registry = MagicMock()
        runtime_entry = MagicMock()
        # Released at construction, so nothing occupies the field any more.
        runtime_entry.name = None
        runtime_registry.async_get.return_value = runtime_entry
        mock_async_get.return_value = runtime_registry
        sensor._handle_coordinator_update()

    coordinator.request_reload.assert_called_once()
    runtime_registry.async_update_entity.assert_not_called()
    assert sensor._previous_circuit_name == "Renamed Kitchen"
