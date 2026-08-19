"""An entity whose declared field could not be resolved reports unavailable.

The Repairs from `schema_repairs` explain and aggregate; this is the same fact
told where a user actually looks. It also mitigates the user-visible symptom of
the "absent property parses to 0.0 rather than None" defect *for the
resolution-failure case*: a field the adapter cannot resolve at all would
otherwise render as a live zero, which for a TOTAL_INCREASING energy sensor
reads as a counter reset. It does nothing for a field that resolves but stops
being published at runtime -- that needs the snapshot model to admit None.
"""

from __future__ import annotations

from typing import cast
from unittest.mock import MagicMock

from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from span_panel_api import SpanMqttClient, SpanPanelSnapshot

from custom_components.span_panel import (
    SpanPanelRuntimeData,
    binary_sensor,
    button,
    select,
    sensor_base,
    switch,
)
from custom_components.span_panel.binary_sensor import (
    BESS_CONNECTED_SENSOR,
    BINARY_SENSORS,
    EVSE_BINARY_SENSORS,
    SpanEvseBinarySensor,
    SpanPanelBinarySensor,
)
from custom_components.span_panel.const import (
    CONF_DEVICE_NAME,
    PANEL_STATUS,
    SYSTEM_ETHERNET_LINK,
)
from custom_components.span_panel.coordinator import SpanPanelCoordinator
from custom_components.span_panel.entity import SpanPanelEntity
from custom_components.span_panel.schema_validation import SchemaFindings
from custom_components.span_panel.sensor_circuit import SpanCircuitPowerSensor
from custom_components.span_panel.sensor_definitions import CIRCUIT_SENSORS

from .factories import (
    SpanCircuitSnapshotFactory,
    SpanEvseSnapshotFactory,
    SpanPanelSnapshotFactory,
)

_CIRCUIT_POWER_PATH = "circuit.instant_power_w"
_ETHERNET_LINK_PATH = "panel.eth0_link"


def _make_coordinator(hass: HomeAssistant) -> SpanPanelCoordinator:
    """Return a real coordinator, so `unresolved_paths` is the real property.

    A MagicMock would answer `_findings` with a mock and `unresolved_paths` with
    a mock whose `__contains__` is False -- the probe would look correct while
    never firing.
    """
    snapshot = SpanPanelSnapshotFactory.create(
        circuits={"c1": SpanCircuitSnapshotFactory.create(circuit_id="c1", name="Kitchen")}
    )
    entry = MockConfigEntry(
        domain="span_panel",
        data={CONF_HOST: "192.168.1.50", CONF_DEVICE_NAME: "SPAN Panel"},
        options={},
        title="SPAN Panel",
        unique_id=snapshot.serial_number,
    )
    entry.add_to_hass(hass)
    coordinator = SpanPanelCoordinator(hass, cast(SpanMqttClient, MagicMock()), entry)
    coordinator.data = snapshot
    entry.runtime_data = SpanPanelRuntimeData(
        coordinator=coordinator, panel_device_id="panel-device-id"
    )
    return coordinator


def _snapshot(coordinator: SpanPanelCoordinator) -> SpanPanelSnapshot:
    return coordinator.data


def _circuit_power_entity(coordinator: SpanPanelCoordinator) -> SpanCircuitPowerSensor:
    """Build a sensor reading `circuit.instant_power_w` -- the `SpanSensorBase` branch."""
    description = next(desc for desc in CIRCUIT_SENSORS if desc.key == "circuit_power")
    assert description.field_path == _CIRCUIT_POWER_PATH
    return SpanCircuitPowerSensor(coordinator, description, _snapshot(coordinator), "c1")


def _ethernet_link_entity(coordinator: SpanPanelCoordinator) -> SpanPanelBinarySensor:
    """Build a binary sensor reading `panel.eth0_link` -- the `SpanPanelEntity` branch.

    Binary sensors do not inherit `SpanSensorBase`, so this is the second base
    class the probe has to live on.
    """
    description = next(desc for desc in BINARY_SENSORS if desc.key == SYSTEM_ETHERNET_LINK)
    assert description.field_path == _ETHERNET_LINK_PATH
    return SpanPanelBinarySensor(coordinator, description)


async def test_sensor_unavailable_when_its_field_is_unresolved(hass: HomeAssistant) -> None:
    """A dead field must not render as a live 0.0."""
    coordinator = _make_coordinator(hass)
    coordinator._findings = SchemaFindings(frozenset({_CIRCUIT_POWER_PATH}), (), frozenset())

    assert _circuit_power_entity(coordinator).available is False


async def test_sensor_available_when_findings_are_clean(hass: HomeAssistant) -> None:
    coordinator = _make_coordinator(hass)
    coordinator._findings = SchemaFindings(frozenset(), (), frozenset())

    assert _circuit_power_entity(coordinator).available is True


async def test_sensor_available_while_findings_are_unknown(hass: HomeAssistant) -> None:
    """No validation pass has completed yet; that is not a reason to go dark."""
    coordinator = _make_coordinator(hass)
    assert coordinator.schema_findings is None

    assert _circuit_power_entity(coordinator).available is True


async def test_sensor_unavailable_when_unresolved_and_panel_offline(
    hass: HomeAssistant,
) -> None:
    """The probe must precede the grace-period branch.

    `SpanSensorBase.available` returns True while `panel_offline` so the sensor
    can show its grace-period state. Probing after that check would let every
    offline sensor report a resolved-looking value for a field that is gone.
    """
    coordinator = _make_coordinator(hass)
    coordinator._findings = SchemaFindings(frozenset({_CIRCUIT_POWER_PATH}), (), frozenset())
    coordinator._panel_offline = True

    assert coordinator.panel_offline is True
    assert _circuit_power_entity(coordinator).available is False


async def test_sensor_unaffected_by_an_unrelated_unresolved_field(
    hass: HomeAssistant,
) -> None:
    """Only the entity's own field counts; a neighbour's failure is not its own."""
    coordinator = _make_coordinator(hass)
    coordinator._findings = SchemaFindings(frozenset({_ETHERNET_LINK_PATH}), (), frozenset())

    assert _circuit_power_entity(coordinator).available is True


async def test_binary_sensor_unavailable_when_its_field_is_unresolved(
    hass: HomeAssistant,
) -> None:
    """`SpanPanelBinarySensor` extends `SpanPanelEntity`, not `SpanSensorBase`."""
    coordinator = _make_coordinator(hass)
    coordinator._findings = SchemaFindings(frozenset({_ETHERNET_LINK_PATH}), (), frozenset())

    assert _ethernet_link_entity(coordinator).available is False


async def test_binary_sensor_available_when_findings_are_clean(hass: HomeAssistant) -> None:
    coordinator = _make_coordinator(hass)
    coordinator._findings = SchemaFindings(frozenset(), (), frozenset())

    assert _ethernet_link_entity(coordinator).available is True


async def test_binary_sensor_unavailable_when_unresolved_and_panel_offline(
    hass: HomeAssistant,
) -> None:
    """`SpanPanelBinarySensor` has its own offline branch that returns True.

    The hardware-status sensors stay available while the panel is offline so
    they can show Unknown. That branch bypasses `super().available`, so the
    probe has to run ahead of it here too -- the same ordering `SpanSensorBase`
    needs.
    """
    coordinator = _make_coordinator(hass)
    coordinator._findings = SchemaFindings(frozenset({_ETHERNET_LINK_PATH}), (), frozenset())
    coordinator._panel_offline = True

    assert _ethernet_link_entity(coordinator).available is False


async def test_panel_status_binary_sensor_is_never_probed(hass: HomeAssistant) -> None:
    """It reports coordinator reachability, not a snapshot field."""
    coordinator = _make_coordinator(hass)
    coordinator._findings = SchemaFindings(frozenset({_ETHERNET_LINK_PATH}), (), frozenset())
    description = next(desc for desc in BINARY_SENSORS if desc.key == PANEL_STATUS)
    assert description.derived is True

    entity = SpanPanelBinarySensor(coordinator, description)

    assert entity.available is True


async def test_derived_entity_is_never_probed(hass: HomeAssistant) -> None:
    """A derived entity declares no source field, so nothing can unresolve it.

    `bess_connected` reads `battery.connected`, which only one adapter
    publishes -- exactly why the description is `derived`. Probing a derived
    description would make availability depend on a path it never declared.
    """
    coordinator = _make_coordinator(hass)
    coordinator._findings = SchemaFindings(frozenset({"battery.connected"}), (), frozenset())
    assert BESS_CONNECTED_SENSOR.derived is True

    entity = SpanPanelBinarySensor(coordinator, BESS_CONNECTED_SENSOR)

    assert entity.available is True


@pytest.mark.parametrize("key", ["evse_charging", "evse_ev_connected"])
async def test_evse_binary_sensor_is_covered_by_the_base_class(
    hass: HomeAssistant, key: str
) -> None:
    """`SpanEvseBinarySensor` defines no `available`, so it exercises `entity.py`.

    The circuit sensor goes through `SpanSensorBase.available` and the panel
    binary sensor through `SpanPanelBinarySensor.available`; neither reaches the
    override on `SpanPanelEntity` itself. This one does.

    Both EVSE binary sensors read `evse.status`, so both must answer the same
    way. `evse_ev_connected` used to be `derived=True` and so stayed available
    while its sibling went dark on the very same dead field.
    """
    coordinator = _make_coordinator(hass)
    coordinator.data = SpanPanelSnapshotFactory.create(
        evse={"evse-0": SpanEvseSnapshotFactory.create()}
    )
    coordinator._findings = SchemaFindings(frozenset({"evse.status"}), (), frozenset())
    description = next(desc for desc in EVSE_BINARY_SENSORS if desc.key == key)
    assert description.field_path == "evse.status"
    assert "available" not in vars(SpanEvseBinarySensor)

    entity = SpanEvseBinarySensor(coordinator, description, "evse-0")

    assert entity.available is False


def test_every_available_override_is_accounted_for() -> None:
    """A new `available` override must decide where the probe sits.

    `SpanSensorBase` and `SpanPanelBinarySensor` return True on their own
    before delegating, so each carries the probe ahead of that branch. The
    switch, select and button return False when the panel is offline and
    otherwise delegate, so the override on `SpanPanelEntity` covers them.
    Anything new in this list has to answer the same question, and this
    assertion is what forces it to be asked.
    """
    overriders = {
        cls.__name__
        for module in (binary_sensor, button, select, sensor_base, switch)
        for cls in vars(module).values()
        if isinstance(cls, type)
        and issubclass(cls, SpanPanelEntity)
        and cls is not SpanPanelEntity  # the base itself, imported into each module
        and "available" in vars(cls)
    }

    assert overriders == {
        "SpanSensorBase",
        "SpanPanelBinarySensor",
        "SpanPanelCircuitsSwitch",
        "SpanPanelGFEOverrideButton",
        "SpanPanelCircuitsSelect",
    }
