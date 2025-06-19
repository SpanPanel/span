"""Test to verify panel-level entities are not migrated."""

from unittest.mock import Mock
from custom_components.span_panel.coordinator import SpanPanelCoordinator


def test_panel_level_entity_detection() -> None:
    """Test that panel-level entities are correctly identified."""
    # Create a mock coordinator - we only need the _is_panel_level_entity method
    # so we can instantiate with mocks for the required parameters
    coordinator = SpanPanelCoordinator(
        hass=Mock(), span_panel=Mock(), name="test", update_interval=30, config_entry=Mock()
    )

    # Test panel-level binary sensor entities
    panel_binary_entities = [
        Mock(unique_id="span_12345_doorState", entity_id="binary_sensor.span_panel_door_state"),
        Mock(unique_id="span_12345_eth0Link", entity_id="binary_sensor.span_panel_ethernet_link"),
        Mock(unique_id="span_12345_wlanLink", entity_id="binary_sensor.span_panel_wifi_link"),
        Mock(unique_id="span_12345_wwanLink", entity_id="binary_sensor.span_panel_cellular_link"),
    ]

    # Test panel-level sensor entities
    panel_sensor_entities = [
        Mock(unique_id="span_12345_instantGridPowerW", entity_id="sensor.span_panel_current_power"),
        Mock(unique_id="span_12345_dsmState", entity_id="sensor.span_panel_dsm_state"),
        Mock(unique_id="span_12345_softwareVer", entity_id="sensor.span_panel_software_version"),
    ]

    # Test circuit-based entities (should NOT be identified as panel-level)
    circuit_entities = [
        Mock(unique_id="span_12345_circuits_1", entity_id="sensor.span_panel_circuit_1_power"),
        Mock(unique_id="span_12345_circuits_2", entity_id="switch.span_panel_circuit_2"),
        Mock(
            unique_id="span_12345_circuits_3_power", entity_id="sensor.span_panel_circuit_3_power"
        ),
    ]

    # Verify panel-level entities are identified correctly
    for entity in panel_binary_entities + panel_sensor_entities:
        assert coordinator._is_panel_level_entity(entity), (
            f"Entity {entity.unique_id} should be identified as panel-level"
        )

    # Verify circuit entities are NOT identified as panel-level
    for entity in circuit_entities:
        assert not coordinator._is_panel_level_entity(entity), (
            f"Entity {entity.unique_id} should NOT be identified as panel-level"
        )

    print("✓ Panel-level entity detection working correctly")


if __name__ == "__main__":
    test_panel_level_entity_detection()
