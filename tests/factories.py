"""Factories for creating test objects with defaults for Span Panel integration.

All factories return library types (SpanPanelSnapshot, SpanCircuitSnapshot,
SpanBatterySnapshot) — frozen dataclasses from span_panel_api.
"""

from typing import Any

from span_panel_api import (
    SpanBatterySnapshot,
    SpanCircuitSnapshot,
    SpanPanelSnapshot,
    SpanPVSnapshot,
)

from custom_components.span_panel.const import (
    DSM_OFF_GRID,
    DSM_ON_GRID,
    PANEL_BACKUP,
    PANEL_ON_GRID,
    SYSTEM_DOOR_STATE_CLOSED,
    CircuitPriority,
    CircuitRelayState,
)


class SpanCircuitSnapshotFactory:
    """Factory for creating SpanCircuitSnapshot test objects."""

    @staticmethod
    def create(
        circuit_id: str = "1",
        name: str = "Test Circuit",
        relay_state: str = CircuitRelayState.CLOSED.name,
        instant_power_w: float = 150.5,
        consumed_energy_wh: float = 1500.0,
        produced_energy_wh: float = 0.0,
        tabs: list[int] | None = None,
        priority: str = CircuitPriority.SOC_THRESHOLD.name,
        is_user_controllable: bool = True,
        is_sheddable: bool = True,
        is_never_backup: bool = False,
        is_240v: bool = False,
        current_a: float | None = None,
        breaker_rating_a: float | None = None,
        always_on: bool = False,
        relay_requester: str = "UNKNOWN",
        energy_accum_update_time_s: int = 0,
        instant_power_update_time_s: int = 0,
    ) -> SpanCircuitSnapshot:
        """Create a SpanCircuitSnapshot with reasonable defaults."""
        if tabs is None:
            tabs = [int(circuit_id)] if circuit_id.isdigit() else [1]
        return SpanCircuitSnapshot(
            circuit_id=circuit_id,
            name=name,
            relay_state=relay_state,
            instant_power_w=instant_power_w,
            produced_energy_wh=produced_energy_wh,
            consumed_energy_wh=consumed_energy_wh,
            tabs=tabs,
            priority=priority,
            is_user_controllable=is_user_controllable,
            is_sheddable=is_sheddable,
            is_never_backup=is_never_backup,
            is_240v=is_240v,
            current_a=current_a,
            breaker_rating_a=breaker_rating_a,
            always_on=always_on,
            relay_requester=relay_requester,
            energy_accum_update_time_s=energy_accum_update_time_s,
            instant_power_update_time_s=instant_power_update_time_s,
        )

    @staticmethod
    def create_kitchen_outlet() -> SpanCircuitSnapshot:
        """Create a kitchen outlet circuit."""
        return SpanCircuitSnapshotFactory.create(
            circuit_id="1",
            name="Kitchen Outlets",
            instant_power_w=245.3,
            consumed_energy_wh=2450.8,
            tabs=[1],
        )

    @staticmethod
    def create_living_room_lights() -> SpanCircuitSnapshot:
        """Create a living room lights circuit."""
        return SpanCircuitSnapshotFactory.create(
            circuit_id="2",
            name="Living Room Lights",
            instant_power_w=85.2,
            consumed_energy_wh=850.5,
            tabs=[2],
        )

    @staticmethod
    def create_solar_panel() -> SpanCircuitSnapshot:
        """Create a solar panel circuit (producing energy)."""
        return SpanCircuitSnapshotFactory.create(
            circuit_id="15",
            name="Solar Panels",
            instant_power_w=-1200.0,
            consumed_energy_wh=0.0,
            produced_energy_wh=12000.5,
            tabs=[15],
            priority=CircuitPriority.NEVER.name,
            is_user_controllable=False,
        )

    @staticmethod
    def create_non_controllable() -> SpanCircuitSnapshot:
        """Create a non-user-controllable circuit."""
        return SpanCircuitSnapshotFactory.create(
            circuit_id="30",
            name="Main Panel Feed",
            is_user_controllable=False,
            priority=CircuitPriority.NEVER.name,
            tabs=[30],
        )


class SpanBatterySnapshotFactory:
    """Factory for creating SpanBatterySnapshot test objects."""

    @staticmethod
    def create(
        soe_percentage: float | None = 85.0,
        soe_kwh: float | None = None,
    ) -> SpanBatterySnapshot:
        """Create a SpanBatterySnapshot with reasonable defaults."""
        return SpanBatterySnapshot(
            soe_percentage=soe_percentage,
            soe_kwh=soe_kwh,
        )


class SpanPanelSnapshotFactory:
    """Factory for creating SpanPanelSnapshot test objects."""

    @staticmethod
    def create(
        serial_number: str = "sp3-242424-001",
        firmware_version: str = "1.2.3",
        main_relay_state: str = "CLOSED",
        instant_grid_power_w: float = 2500.75,
        feedthrough_power_w: float = 0.0,
        main_meter_energy_consumed_wh: float = 2500.0,
        main_meter_energy_produced_wh: float = 0.0,
        feedthrough_energy_consumed_wh: float = 0.0,
        feedthrough_energy_produced_wh: float = 0.0,
        dsm_grid_state: str = DSM_ON_GRID,
        current_run_config: str = PANEL_ON_GRID,
        door_state: str = SYSTEM_DOOR_STATE_CLOSED,
        proximity_proven: bool = False,
        uptime_s: int = 86400,
        eth0_link: bool = True,
        wlan_link: bool = True,
        wwan_link: bool = False,
        circuits: dict[str, SpanCircuitSnapshot] | None = None,
        battery: SpanBatterySnapshot | None = None,
        dominant_power_source: str | None = None,
        grid_state: str | None = None,
        grid_islandable: bool | None = None,
        l1_voltage: float | None = None,
        l2_voltage: float | None = None,
        main_breaker_rating_a: int | None = None,
        wifi_ssid: str | None = None,
        vendor_cloud: str | None = None,
        power_flow_battery: float | None = None,
        power_flow_site: float | None = None,
        power_flow_pv: float | None = None,
        pv: SpanPVSnapshot | None = None,
    ) -> SpanPanelSnapshot:
        """Create a SpanPanelSnapshot with reasonable defaults."""
        if circuits is None:
            circuits = {}
        if battery is None:
            battery = SpanBatterySnapshot()
        if pv is None:
            pv = SpanPVSnapshot()
        return SpanPanelSnapshot(
            serial_number=serial_number,
            firmware_version=firmware_version,
            main_relay_state=main_relay_state,
            instant_grid_power_w=instant_grid_power_w,
            feedthrough_power_w=feedthrough_power_w,
            main_meter_energy_consumed_wh=main_meter_energy_consumed_wh,
            main_meter_energy_produced_wh=main_meter_energy_produced_wh,
            feedthrough_energy_consumed_wh=feedthrough_energy_consumed_wh,
            feedthrough_energy_produced_wh=feedthrough_energy_produced_wh,
            dsm_grid_state=dsm_grid_state,
            current_run_config=current_run_config,
            door_state=door_state,
            proximity_proven=proximity_proven,
            uptime_s=uptime_s,
            eth0_link=eth0_link,
            wlan_link=wlan_link,
            wwan_link=wwan_link,
            circuits=circuits,
            battery=battery,
            dominant_power_source=dominant_power_source,
            grid_state=grid_state,
            grid_islandable=grid_islandable,
            l1_voltage=l1_voltage,
            l2_voltage=l2_voltage,
            main_breaker_rating_a=main_breaker_rating_a,
            wifi_ssid=wifi_ssid,
            vendor_cloud=vendor_cloud,
            power_flow_battery=power_flow_battery,
            power_flow_site=power_flow_site,
            power_flow_pv=power_flow_pv,
            pv=pv,
        )

    @staticmethod
    def create_complete(
        serial_number: str = "sp3-242424-001",
        circuits: list[SpanCircuitSnapshot] | None = None,
        battery: SpanBatterySnapshot | None = None,
        **kwargs: Any,
    ) -> SpanPanelSnapshot:
        """Create a complete panel snapshot with default circuits and battery."""
        if circuits is None:
            circuits_list = [
                SpanCircuitSnapshotFactory.create_kitchen_outlet(),
                SpanCircuitSnapshotFactory.create_living_room_lights(),
                SpanCircuitSnapshotFactory.create_solar_panel(),
            ]
        else:
            circuits_list = circuits

        circuits_dict = {c.circuit_id: c for c in circuits_list}

        if battery is None:
            battery = SpanBatterySnapshotFactory.create()

        return SpanPanelSnapshotFactory.create(
            serial_number=serial_number,
            circuits=circuits_dict,
            battery=battery,
            **kwargs,
        )

    @staticmethod
    def create_on_grid(serial_number: str = "sp3-242424-001") -> SpanPanelSnapshot:
        """Create a panel snapshot in on-grid state."""
        return SpanPanelSnapshotFactory.create_complete(
            serial_number=serial_number,
            instant_grid_power_w=1850.5,
            dsm_grid_state=DSM_ON_GRID,
            current_run_config=PANEL_ON_GRID,
        )

    @staticmethod
    def create_backup(serial_number: str = "sp3-242424-001") -> SpanPanelSnapshot:
        """Create a panel snapshot in backup state."""
        return SpanPanelSnapshotFactory.create_complete(
            serial_number=serial_number,
            instant_grid_power_w=0.0,
            dsm_grid_state=DSM_OFF_GRID,
            current_run_config=PANEL_BACKUP,
        )

    @staticmethod
    def create_minimal(serial_number: str = "sp3-242424-001") -> SpanPanelSnapshot:
        """Create a minimal panel snapshot with a single circuit."""
        return SpanPanelSnapshotFactory.create_complete(
            serial_number=serial_number,
            circuits=[SpanCircuitSnapshotFactory.create_kitchen_outlet()],
        )
