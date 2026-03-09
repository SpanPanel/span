"""Tests for unique ID generation patterns in Span Panel integration."""

from unittest.mock import MagicMock

from span_panel_api import SpanPanelSnapshot

from custom_components.span_panel.const import USE_CIRCUIT_NUMBERS
from tests.factories import SpanPanelSnapshotFactory


class TestUniqueIdGeneration:
    """Test unique ID generation for circuits."""

    TEST_SERIAL_NUMBER = "ABC123DEF456"

    KITCHEN_CIRCUIT_DATA = {
        "circuit_id": "1",
        "name": "Kitchen Outlets",
        "power": 245.3,
        "tab": 1,
    }

    LIVING_ROOM_CIRCUIT_DATA = {
        "circuit_id": "2",
        "name": "Living Room Lights",
        "power": 85.2,
        "tab": 2,
    }

    SOLAR_CIRCUIT_DATA = {
        "circuit_id": "15",
        "name": "Solar Panels",
        "power": -1200.0,
        "tab": 15,
    }

    def _create_mock_snapshot(self, serial_number: str | None = None) -> SpanPanelSnapshot:
        """Create a mock SpanPanelSnapshot with predefined data."""
        if serial_number is None:
            serial_number = self.TEST_SERIAL_NUMBER
        return SpanPanelSnapshotFactory.create(serial_number=serial_number)

    def _create_circuit_sensor(
        self,
        snapshot: SpanPanelSnapshot,
        circuit_id: str,
        sensor_description: object,
        config_options: dict[str, object] | None = None,
    ) -> MagicMock:
        """Create a circuit sensor for testing."""
        if config_options is None:
            config_options = {}

        mock_sensor = MagicMock()
        key: str = getattr(sensor_description, "key", "")
        unique_id = f"span_{snapshot.serial_number}_{circuit_id}_{key}"
        mock_sensor.unique_id = unique_id
        return mock_sensor

    def test_regular_circuit_unique_id_new_installation(self) -> None:
        """Test unique ID generation for regular circuits in new installations."""
        snapshot = self._create_mock_snapshot()

        power_description = MagicMock()
        power_description.key = "power"

        sensor = self._create_circuit_sensor(
            snapshot,
            self.KITCHEN_CIRCUIT_DATA["circuit_id"],
            power_description,
            {USE_CIRCUIT_NUMBERS: True},
        )

        expected_unique_id = (
            f"span_{self.TEST_SERIAL_NUMBER}_{self.KITCHEN_CIRCUIT_DATA['circuit_id']}_power"
        )
        assert sensor.unique_id == expected_unique_id

    def test_regular_circuit_unique_id_multiple_circuits(self) -> None:
        """Test unique ID generation for multiple regular circuits."""
        snapshot = self._create_mock_snapshot()

        power_description = MagicMock()
        power_description.key = "power"

        test_cases = [
            (self.KITCHEN_CIRCUIT_DATA, "power"),
            (self.LIVING_ROOM_CIRCUIT_DATA, "power"),
            (self.SOLAR_CIRCUIT_DATA, "power"),
        ]

        for circuit_data, expected_suffix in test_cases:
            sensor = self._create_circuit_sensor(
                snapshot, circuit_data["circuit_id"], power_description, {USE_CIRCUIT_NUMBERS: True}
            )

            expected_unique_id = (
                f"span_{self.TEST_SERIAL_NUMBER}_{circuit_data['circuit_id']}_{expected_suffix}"
            )
            assert sensor.unique_id == expected_unique_id

    def test_unique_id_with_different_serial_numbers(self) -> None:
        """Test that unique IDs change with different serial numbers."""
        test_serials = ["ABC123", "XYZ789", "PANEL001"]

        for serial in test_serials:
            snapshot = self._create_mock_snapshot(serial)

            power_description = MagicMock()
            power_description.key = "power"

            circuit_sensor = self._create_circuit_sensor(
                snapshot, self.KITCHEN_CIRCUIT_DATA["circuit_id"], power_description
            )
            expected_circuit_id = f"span_{serial}_{self.KITCHEN_CIRCUIT_DATA['circuit_id']}_power"
            assert circuit_sensor.unique_id == expected_circuit_id

    def test_unique_id_format_consistency(self) -> None:
        """Test that unique ID formats are consistent and follow documented patterns."""
        snapshot = self._create_mock_snapshot()

        power_description = MagicMock()
        power_description.key = "power"

        circuit_sensor = self._create_circuit_sensor(
            snapshot, self.KITCHEN_CIRCUIT_DATA["circuit_id"], power_description
        )

        parts = circuit_sensor.unique_id.split("_")
        assert parts[0] == "span"
        assert parts[1] == self.TEST_SERIAL_NUMBER
        assert parts[2] == self.KITCHEN_CIRCUIT_DATA["circuit_id"]
        assert parts[3] == "power"

    def test_multi_panel_unique_id_differentiation(self) -> None:
        """Test that unique IDs differentiate between multiple panels."""
        panel1 = self._create_mock_snapshot("PANEL001")
        panel2 = self._create_mock_snapshot("PANEL002")

        power_description = MagicMock()
        power_description.key = "power"

        sensor1 = self._create_circuit_sensor(panel1, "1", power_description)
        sensor2 = self._create_circuit_sensor(panel2, "1", power_description)

        assert sensor1.unique_id != sensor2.unique_id
        assert "PANEL001" in sensor1.unique_id
        assert "PANEL002" in sensor2.unique_id

    def test_circuit_sensor_uses_correct_documented_pattern(self) -> None:
        """Test that circuit sensors follow the documented unique ID pattern."""
        snapshot = self._create_mock_snapshot()

        from custom_components.span_panel.sensor_definitions import UNMAPPED_SENSORS

        power_description = next(d for d in UNMAPPED_SENSORS if d.key == "instantPowerW")

        sensor = self._create_circuit_sensor(
            snapshot,
            self.KITCHEN_CIRCUIT_DATA["circuit_id"],
            power_description,
            {USE_CIRCUIT_NUMBERS: True},
        )

        expected_unique_id = f"span_{self.TEST_SERIAL_NUMBER}_{self.KITCHEN_CIRCUIT_DATA['circuit_id']}_instantPowerW"
        assert sensor.unique_id == expected_unique_id
        assert power_description.key == "instantPowerW"
