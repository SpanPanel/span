from custom_components.span_panel.entity_summary import log_entity_summary
import logging


class DummyCircuit:
    """Dummy circuit class for testing."""

    def __init__(self, name, circuit_id, is_user_controllable):
        """Initialize dummy circuit."""
        self.name = name
        self.circuit_id = circuit_id
        self.is_user_controllable = is_user_controllable


class DummyData:
    """Dummy data class for testing."""

    circuits = {
        "1": DummyCircuit("A", "1", True),
        "2": DummyCircuit("B", "2", False),
    }


class DummyCoordinator:
    """Dummy coordinator class for testing."""

    data = DummyData()


class DummyConfigEntry:
    """Dummy config entry class for testing."""

    options = {}


def test_log_entity_summary_basic(caplog):
    with caplog.at_level(logging.INFO):
        log_entity_summary(DummyCoordinator(), DummyConfigEntry())
        assert "Total circuits" in caplog.text
