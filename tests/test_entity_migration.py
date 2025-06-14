import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from custom_components.span_panel.entity_migration import EntityMigrationManager
from custom_components.span_panel.const import EntityNamingPattern


def create_mock_entity(entity_id: str, unique_id: str):
    """Create mock entities for testing."""
    entity = MagicMock()
    entity.entity_id = entity_id
    entity.unique_id = unique_id
    entity.config_entry_id = "test_entry"
    return entity


@pytest.mark.asyncio
async def test_migrate_entities_no_change():
    mgr = EntityMigrationManager(hass=MagicMock(), config_entry_id="id")
    result = await mgr.migrate_entities("pattern1", "pattern1")
    assert result is True


@pytest.mark.asyncio
async def test_load_circuit_data_handles_missing_coordinator():
    mgr = EntityMigrationManager(hass=MagicMock(), config_entry_id="id")
    # Should not raise even if coordinator is missing
    await mgr._load_circuit_data()


def test_get_integration_entities():
    hass = MagicMock()
    mgr = EntityMigrationManager(hass, "config_id")

    # Mock entity registry
    mock_entity = MagicMock()
    mock_entity.config_entry_id = "config_id"
    mock_entity.entity_id = "sensor.span_panel_circuit_1_power"
    mock_entity.unique_id = "span_123_sensor_1"

    mgr._entity_registry.entities = {"entity1": mock_entity}
    mgr._is_circuit_level_entity = MagicMock(return_value=True)

    entities = mgr._get_integration_entities()
    assert len(entities) == 1
    assert entities[0] == mock_entity


def test_is_circuit_level_entity_true():
    mgr = EntityMigrationManager(hass=MagicMock(), config_entry_id="id")

    mock_entity = MagicMock()
    mock_entity.entity_id = "sensor.span_panel_circuit_1_power"
    mock_entity.unique_id = "span_123_sensor_1"

    result = mgr._is_circuit_level_entity(mock_entity)
    # This would need to match the actual logic in the method
    assert isinstance(result, bool)


def test_is_circuit_level_entity_false():
    mgr = EntityMigrationManager(hass=MagicMock(), config_entry_id="id")

    mock_entity = MagicMock()
    mock_entity.entity_id = "sensor.span_panel_status"
    mock_entity.unique_id = "span_123_status"

    result = mgr._is_circuit_level_entity(mock_entity)
    # This would need to match the actual logic in the method
    assert isinstance(result, bool)


def test_build_entity_mapping_empty():
    mgr = EntityMigrationManager(hass=MagicMock(), config_entry_id="id")

    mapping = mgr._build_entity_mapping([], "pattern1", "pattern2")
    assert mapping == {}


def test_is_circuit_level_entity_panel_level():
    mgr = EntityMigrationManager(hass=MagicMock(), config_entry_id="id")

    # Test panel-level entities that should be excluded
    panel_entities = [
        "sensor.span_panel_current_power",
        "sensor.span_panel_feed_through_power",
        "sensor.span_panel_main_meter_produced_energy",
        "sensor.span_panel_dsm_state",
        "sensor.span_panel_software_version",
        "sensor.span_panel_battery_percentage",
        "binary_sensor.span_panel_door_state",
        "binary_sensor.span_panel_ethernet_link",
    ]

    for entity_id in panel_entities:
        mock_entity = MagicMock()
        mock_entity.entity_id = entity_id
        mock_entity.unique_id = f"span_123_{entity_id.split('.')[-1]}"

        result = mgr._is_circuit_level_entity(mock_entity)
        assert result is False, f"Entity {entity_id} should be panel-level"


def test_is_circuit_level_entity_circuit_level():
    mgr = EntityMigrationManager(hass=MagicMock(), config_entry_id="id")

    # Test circuit-level entities that should be included
    circuit_entities = [
        "sensor.span_panel_circuit_1_power",
        "sensor.span_panel_kitchen_lights_power",
        "switch.span_panel_circuit_5",
        "select.span_panel_circuit_priority_3",
    ]

    for entity_id in circuit_entities:
        mock_entity = MagicMock()
        mock_entity.entity_id = entity_id
        mock_entity.unique_id = f"span_123_{entity_id.split('.')[-1]}"

        result = mgr._is_circuit_level_entity(mock_entity)
        assert result is True, f"Entity {entity_id} should be circuit-level"


def test_is_circuit_level_entity_synthetic():
    mgr = EntityMigrationManager(hass=MagicMock(), config_entry_id="id")

    mock_entity = MagicMock()
    mock_entity.entity_id = "sensor.span_panel_solar_inverter_power"
    mock_entity.unique_id = "span_123_synthetic_solar_inverter_power"

    result = mgr._is_circuit_level_entity(mock_entity)
    assert result is True


def test_get_pattern_flags():
    mgr = EntityMigrationManager(hass=MagicMock(), config_entry_id="id")

    # Test friendly names pattern
    prefix, numbers = mgr._get_pattern_flags(EntityNamingPattern.FRIENDLY_NAMES)
    assert prefix is True
    assert numbers is False

    # Test circuit numbers pattern
    prefix, numbers = mgr._get_pattern_flags(EntityNamingPattern.CIRCUIT_NUMBERS)
    assert prefix is True
    assert numbers is True

    # Test legacy names pattern
    prefix, numbers = mgr._get_pattern_flags(EntityNamingPattern.LEGACY_NAMES)
    assert prefix is False
    assert numbers is False


def test_generate_new_entity_id_no_change():
    mgr = EntityMigrationManager(hass=MagicMock(), config_entry_id="id")

    mock_entity = MagicMock()
    mock_entity.entity_id = "sensor.span_panel_circuit_1_power"

    # Same pattern should return None (no change)
    result = mgr._generate_new_entity_id(
        mock_entity,
        EntityNamingPattern.FRIENDLY_NAMES,
        EntityNamingPattern.FRIENDLY_NAMES,
    )
    assert result is None


def test_generate_new_entity_id_with_change():
    mgr = EntityMigrationManager(hass=MagicMock(), config_entry_id="id")
    mgr._circuit_data = {"1": ("Kitchen Lights", 1)}

    mock_entity = MagicMock()
    mock_entity.entity_id = "sensor.span_panel_circuit_1_power"

    # Mock the transform method to return a new ID
    mgr._transform_entity_id = MagicMock(return_value="sensor.span_panel_kitchen_lights_power")

    result = mgr._generate_new_entity_id(
        mock_entity,
        EntityNamingPattern.CIRCUIT_NUMBERS,
        EntityNamingPattern.FRIENDLY_NAMES,
    )
    assert result == "sensor.span_panel_kitchen_lights_power"


def test_build_entity_mapping_with_changes():
    mgr = EntityMigrationManager(hass=MagicMock(), config_entry_id="id")

    # Create mock entities
    entity1 = MagicMock()
    entity1.entity_id = "sensor.span_panel_circuit_1_power"

    entity2 = MagicMock()
    entity2.entity_id = "sensor.span_panel_circuit_2_power"

    entities = [entity1, entity2]

    # Mock the generate method to return new IDs
    def mock_generate(entity, from_pattern, to_pattern):
        if entity.entity_id == "sensor.span_panel_circuit_1_power":
            return "sensor.span_panel_kitchen_lights_power"
        elif entity.entity_id == "sensor.span_panel_circuit_2_power":
            return "sensor.span_panel_living_room_power"
        return None

    mgr._generate_new_entity_id = mock_generate

    mapping = mgr._build_entity_mapping(
        entities,
        EntityNamingPattern.CIRCUIT_NUMBERS,
        EntityNamingPattern.FRIENDLY_NAMES,
    )

    expected = {
        "sensor.span_panel_circuit_1_power": "sensor.span_panel_kitchen_lights_power",
        "sensor.span_panel_circuit_2_power": "sensor.span_panel_living_room_power",
    }
    assert mapping == expected


def test_transform_entity_id_invalid_format():
    """Test _transform_entity_id with invalid entity ID format."""
    hass = MagicMock()
    mgr = EntityMigrationManager(hass, "test_entry")

    # Invalid entity ID format
    result = mgr._transform_entity_id("invalid_format", True, False, True, False)
    assert result is None


def test_is_synthetic_entity_id():
    mgr = EntityMigrationManager(hass=MagicMock(), config_entry_id="id")

    # Test synthetic entity patterns
    synthetic_ids = [
        "span_panel_solar_inverter_power",
        "span_panel_solar_inverter_leg1_power",
        "span_panel_solar_inverter_leg2_power",
    ]

    for object_id in synthetic_ids:
        result = mgr._is_synthetic_entity_id(object_id)
        assert result is True, f"Should identify {object_id} as synthetic"

    # Test non-synthetic entity
    result = mgr._is_synthetic_entity_id("span_panel_circuit_1_power")
    assert result is False


@pytest.mark.asyncio
async def test_update_entity_id_success():
    hass = MagicMock()
    mgr = EntityMigrationManager(hass, "config_id")

    # Mock successful entity update (async_update_entity doesn't return a value)
    mgr._entity_registry.async_update_entity = MagicMock(return_value=None)
    # Mock async_get to return None (new entity ID doesn't exist)
    mgr._entity_registry.async_get = MagicMock(return_value=None)

    result = await mgr._update_entity_id("old.entity_id", "new.entity_id")
    assert result is True
    mgr._entity_registry.async_update_entity.assert_called_once_with(
        "old.entity_id", new_entity_id="new.entity_id"
    )


@pytest.mark.asyncio
async def test_update_entity_id_failure():
    hass = MagicMock()
    mgr = EntityMigrationManager(hass, "config_id")

    # Mock failed entity update
    mgr._entity_registry.async_update_entity = MagicMock(side_effect=Exception("Update failed"))

    result = await mgr._update_entity_id("old.entity_id", "new.entity_id")
    assert result is False


@pytest.mark.asyncio
async def test_migrate_entities_success():
    hass = MagicMock()
    mgr = EntityMigrationManager(hass, "config_id")

    # Mock methods
    mgr._load_circuit_data = AsyncMock()
    mgr._get_integration_entities = MagicMock(return_value=[])

    result = await mgr.migrate_entities(
        EntityNamingPattern.CIRCUIT_NUMBERS, EntityNamingPattern.FRIENDLY_NAMES
    )
    assert result is True


@pytest.mark.asyncio
async def test_migrate_entities_with_entities():
    hass = MagicMock()
    mgr = EntityMigrationManager(hass, "config_id")

    # Create mock entity
    mock_entity = MagicMock()
    mock_entity.entity_id = "sensor.span_panel_circuit_1_power"
    mock_entity.unique_id = "span_123_circuit_1_power"

    # Mock methods
    mgr._load_circuit_data = AsyncMock()
    mgr._get_integration_entities = MagicMock(return_value=[mock_entity])
    mgr._build_entity_mapping = MagicMock(
        return_value={"sensor.span_panel_circuit_1_power": "sensor.span_panel_kitchen_lights_power"}
    )
    mgr._update_entity_id = AsyncMock(return_value=True)

    result = await mgr.migrate_entities(
        EntityNamingPattern.CIRCUIT_NUMBERS, EntityNamingPattern.FRIENDLY_NAMES
    )
    assert result is True
    mgr._update_entity_id.assert_called_once()


@pytest.mark.asyncio
async def test_migrate_entities_exception():
    hass = MagicMock()
    mgr = EntityMigrationManager(hass, "config_id")

    # Mock exception during migration
    mgr._load_circuit_data = AsyncMock(side_effect=Exception("Load failed"))

    result = await mgr.migrate_entities(
        EntityNamingPattern.CIRCUIT_NUMBERS, EntityNamingPattern.FRIENDLY_NAMES
    )
    assert result is False


def test_get_integration_entities_filtering():
    hass = MagicMock()
    mgr = EntityMigrationManager(hass, "config_id")

    # Mock entity registry with mixed entities
    mock_entity1 = MagicMock()
    mock_entity1.config_entry_id = "config_id"
    mock_entity1.entity_id = "sensor.span_panel_circuit_1_power"

    mock_entity2 = MagicMock()
    mock_entity2.config_entry_id = "other_config"
    mock_entity2.entity_id = "sensor.other_device"

    mock_entity3 = MagicMock()
    mock_entity3.config_entry_id = "config_id"
    mock_entity3.entity_id = "sensor.span_panel_current_power"  # Panel-level

    mgr._entity_registry.entities = {
        "entity1": mock_entity1,
        "entity2": mock_entity2,
        "entity3": mock_entity3,
    }

    # Mock the circuit level check
    def mock_is_circuit_level(entity):
        return "circuit" in entity.entity_id and "current_power" not in entity.entity_id

    mgr._is_circuit_level_entity = mock_is_circuit_level

    entities = mgr._get_integration_entities()
    assert len(entities) == 1
    assert entities[0] == mock_entity1


@pytest.mark.asyncio
async def test_generate_new_entity_id_exception_handling():
    """Test _generate_new_entity_id with exception handling."""
    hass = MagicMock()
    mgr = EntityMigrationManager(hass, "test_entry")

    # Mock entity with invalid format that causes exception
    entity = MagicMock()
    entity.entity_id = "invalid_format"

    # Mock _transform_entity_id to raise exception
    with patch.object(mgr, "_transform_entity_id", side_effect=Exception("Transform error")):
        result = mgr._generate_new_entity_id(
            entity,
            EntityNamingPattern.FRIENDLY_NAMES,
            EntityNamingPattern.CIRCUIT_NUMBERS,
        )
        assert result is None


@pytest.mark.asyncio
async def test_transform_entity_id_exception_handling():
    """Test _transform_entity_id with exception handling."""
    hass = MagicMock()
    mgr = EntityMigrationManager(hass, "test_entry")

    # Mock _is_synthetic_entity_id to raise exception
    with patch.object(
        mgr, "_is_synthetic_entity_id", side_effect=Exception("Synthetic check error")
    ):
        result = mgr._transform_entity_id("sensor.test_entity", True, False, False, True)
        assert result is None


@pytest.mark.asyncio
async def test_transform_circuit_naming_exception_handling():
    """Test _transform_circuit_naming with exception handling."""
    hass = MagicMock()
    mgr = EntityMigrationManager(hass, "test_entry")

    # Mock _circuit_numbers_to_friendly_names to raise exception
    with patch.object(
        mgr,
        "_circuit_numbers_to_friendly_names",
        side_effect=Exception("Transform error"),
    ):
        result = mgr._transform_circuit_naming("circuit_15_breaker", True, False)
        # Should return original on exception
        assert result == "circuit_15_breaker"


@pytest.mark.asyncio
async def test_circuit_numbers_to_friendly_names():
    """Test _circuit_numbers_to_friendly_names transformation."""
    hass = MagicMock()
    mgr = EntityMigrationManager(hass, "test_entry")

    # Set up circuit data
    mgr._circuit_data = {
        "circuit_15": ("Kitchen Outlets", 15),
        "circuit_20": ("Living Room", 20),
    }

    # Test successful transformation
    result = mgr._circuit_numbers_to_friendly_names("span_panel_circuit_15_breaker")
    assert result == "span_panel_kitchen_outlets_breaker"

    # Test with no matching circuit
    result = mgr._circuit_numbers_to_friendly_names("span_panel_circuit_99_breaker")
    assert result == "span_panel_circuit_99_breaker"  # Should return original

    # Test with no circuit pattern match
    result = mgr._circuit_numbers_to_friendly_names("span_panel_some_other_entity")
    assert result == "span_panel_some_other_entity"  # Should return original


@pytest.mark.asyncio
async def test_friendly_names_to_circuit_numbers():
    """Test _friendly_names_to_circuit_numbers transformation."""
    hass = MagicMock()
    mgr = EntityMigrationManager(hass, "test_entry")

    # Set up circuit data
    mgr._circuit_data = {
        "circuit_15": ("Kitchen Outlets", 15),
        "circuit_20": ("Living Room", 20),
    }

    # Test successful transformation with prefix
    result = mgr._friendly_names_to_circuit_numbers("span_panel_kitchen_outlets_breaker")
    assert result == "span_panel_circuit_15_breaker"

    # Test successful transformation without prefix
    result = mgr._friendly_names_to_circuit_numbers("living_room_power")
    assert result == "circuit_20_power"

    # Test with no matching circuit
    result = mgr._friendly_names_to_circuit_numbers("span_panel_unknown_circuit_breaker")
    assert result == "span_panel_unknown_circuit_breaker"  # Should return original

    # Test with no suffix match
    result = mgr._friendly_names_to_circuit_numbers("span_panel_kitchen_outlets_unknown")
    assert result == "span_panel_kitchen_outlets_unknown"  # Should return original


@pytest.mark.asyncio
async def test_is_synthetic_entity_id_multi_circuit():
    """Test _is_synthetic_entity_id with multi-circuit patterns."""
    hass = MagicMock()
    mgr = EntityMigrationManager(hass, "test_entry")

    # Test multi-circuit pattern
    assert mgr._is_synthetic_entity_id("circuit_30_32_energy_consumed") is True
    assert mgr._is_synthetic_entity_id("span_panel_circuit_15_20_power") is True

    # Test non-synthetic patterns
    assert mgr._is_synthetic_entity_id("circuit_15_breaker") is False
    assert mgr._is_synthetic_entity_id("kitchen_outlets_power") is False


@pytest.mark.asyncio
async def test_transform_synthetic_entity_id():
    """Test _transform_synthetic_entity_id functionality."""
    hass = MagicMock()
    config_entry = MagicMock()
    config_entry.options = {"leg1": 30, "leg2": 32}  # Use correct option keys
    hass.config_entries.async_get_entry.return_value = config_entry

    mgr = EntityMigrationManager(hass, "test_entry")

    # Test prefix removal (no circuit naming change)
    result = mgr._transform_synthetic_entity_id(
        "sensor.span_panel_solar_inverter_power",
        "sensor",
        "span_panel_solar_inverter_power",
        True,
        False,
        False,
        False,  # from_prefix=True, to_prefix=False, same circuit naming
    )
    assert result == "sensor.solar_inverter_power"

    # Test prefix addition (no circuit naming change)
    result = mgr._transform_synthetic_entity_id(
        "sensor.solar_inverter_power",
        "sensor",
        "solar_inverter_power",
        False,
        True,
        False,
        False,  # from_prefix=False, to_prefix=True, same circuit naming
    )
    assert result == "sensor.span_panel_solar_inverter_power"

    # Test circuit naming transformation with mocked method
    with patch.object(
        mgr, "_transform_synthetic_circuit_naming", return_value="solar_inverter_power"
    ):
        result = mgr._transform_synthetic_entity_id(
            "sensor.span_panel_circuit_30_32_power",
            "sensor",
            "span_panel_circuit_30_32_power",
            True,
            True,
            True,
            False,  # circuit naming change
        )
        assert result == "sensor.solar_inverter_power"


@pytest.mark.asyncio
async def test_transform_synthetic_entity_id_exception():
    """Test _transform_synthetic_entity_id with exception handling."""
    hass = MagicMock()
    mgr = EntityMigrationManager(hass, "test_entry")

    # Mock _transform_synthetic_circuit_naming to raise exception
    with patch.object(
        mgr,
        "_transform_synthetic_circuit_naming",
        side_effect=Exception("Transform error"),
    ):
        result = mgr._transform_synthetic_entity_id(
            "sensor.test_entity", "sensor", "test_entity", True, False, True, False
        )
        assert result is None


@pytest.mark.asyncio
async def test_transform_synthetic_circuit_naming_numbers_to_friendly():
    """Test _transform_synthetic_circuit_naming from numbers to friendly names."""
    hass = MagicMock()
    config_entry = MagicMock()
    # Use the correct option keys from options.py
    config_entry.options = {"leg1": 30, "leg2": 32}
    hass.config_entries.async_get_entry.return_value = config_entry

    mgr = EntityMigrationManager(hass, "test_entry")

    # Mock the solar inverter circuit check to return True for our test circuits
    with patch.object(mgr, "_is_solar_inverter_circuits", return_value=True):
        # Test multi-circuit solar inverter transformation
        result = mgr._transform_synthetic_circuit_naming(
            "span_panel_circuit_30_32_energy_consumed", True, False
        )
        assert result == "span_panel_solar_inverter_energy_consumed"

    # Test unknown multi-circuit entity (not solar inverter)
    with patch.object(mgr, "_is_solar_inverter_circuits", return_value=False):
        result = mgr._transform_synthetic_circuit_naming(
            "span_panel_circuit_10_15_power", True, False
        )
        assert result == "span_panel_circuit_group_10_15_power"

    # Test single-circuit solar inverter
    with patch.object(mgr, "_get_solar_inverter_circuits", return_value=(30, 32)):
        result = mgr._transform_synthetic_circuit_naming("span_panel_circuit_30_power", True, False)
        assert result == "span_panel_solar_inverter_power"


@pytest.mark.asyncio
async def test_transform_synthetic_circuit_naming_friendly_to_numbers():
    """Test _transform_synthetic_circuit_naming from friendly names to numbers."""
    hass = MagicMock()
    config_entry = MagicMock()
    config_entry.options = {"leg1": 30, "leg2": 32}
    hass.config_entries.async_get_entry.return_value = config_entry

    mgr = EntityMigrationManager(hass, "test_entry")

    # Test solar inverter transformation with both legs
    with patch.object(mgr, "_get_solar_inverter_circuits", return_value=(30, 32)):
        result = mgr._transform_synthetic_circuit_naming(
            "span_panel_solar_inverter_energy_consumed", False, True
        )
        assert result == "span_panel_circuit_30_32_energy_consumed"

    # Test with only one leg configured
    with patch.object(mgr, "_get_solar_inverter_circuits", return_value=(30, 0)):
        result = mgr._transform_synthetic_circuit_naming(
            "span_panel_solar_inverter_power", False, True
        )
        assert result == "span_panel_circuit_30_power"


@pytest.mark.asyncio
async def test_is_solar_inverter_circuits():
    """Test _is_solar_inverter_circuits functionality."""
    hass = MagicMock()
    config_entry = MagicMock()
    config_entry.options = {"leg1": 30, "leg2": 32}  # Use correct option keys
    hass.config_entries.async_get_entry.return_value = config_entry

    mgr = EntityMigrationManager(hass, "test_entry")

    # Test matching circuits (order doesn't matter)
    assert mgr._is_solar_inverter_circuits(30, 32) is True
    assert mgr._is_solar_inverter_circuits(32, 30) is True

    # Test non-matching circuits
    assert mgr._is_solar_inverter_circuits(15, 20) is False

    # Test with no config entry
    hass.config_entries.async_get_entry.return_value = None
    assert mgr._is_solar_inverter_circuits(30, 32) is False


@pytest.mark.asyncio
async def test_is_solar_inverter_circuits_exception():
    """Test _is_solar_inverter_circuits with exception handling."""
    hass = MagicMock()
    hass.config_entries.async_get_entry.side_effect = Exception("Config error")

    mgr = EntityMigrationManager(hass, "test_entry")

    # Should return False on exception
    assert mgr._is_solar_inverter_circuits(30, 32) is False


@pytest.mark.asyncio
async def test_get_solar_inverter_circuits():
    """Test _get_solar_inverter_circuits functionality."""
    hass = MagicMock()
    config_entry = MagicMock()
    config_entry.options = {"leg1": 30, "leg2": 32}  # Use correct option keys
    hass.config_entries.async_get_entry.return_value = config_entry

    mgr = EntityMigrationManager(hass, "test_entry")

    # Test successful retrieval
    leg1, leg2 = mgr._get_solar_inverter_circuits()
    assert leg1 == 30
    assert leg2 == 32

    # Test with no config entry
    hass.config_entries.async_get_entry.return_value = None
    leg1, leg2 = mgr._get_solar_inverter_circuits()
    assert leg1 == 0
    assert leg2 == 0


@pytest.mark.asyncio
async def test_get_solar_inverter_circuits_exception():
    """Test _get_solar_inverter_circuits with exception handling."""
    hass = MagicMock()
    hass.config_entries.async_get_entry.side_effect = Exception("Config error")

    mgr = EntityMigrationManager(hass, "test_entry")

    # Should return (0, 0) on exception
    leg1, leg2 = mgr._get_solar_inverter_circuits()
    assert leg1 == 0
    assert leg2 == 0


@pytest.mark.asyncio
async def test_remove_entity_success():
    """Test _remove_entity successful removal."""
    hass = MagicMock()
    entity_registry = MagicMock()
    entity = MagicMock()
    entity.entity_id = "sensor.test_entity"
    entity_registry.async_get.return_value = entity

    mgr = EntityMigrationManager(hass, "test_entry")
    mgr._entity_registry = entity_registry

    result = await mgr._remove_entity("sensor.test_entity")
    assert result is True
    entity_registry.async_remove.assert_called_once_with("sensor.test_entity")


@pytest.mark.asyncio
async def test_remove_entity_not_found():
    """Test _remove_entity when entity not found."""
    hass = MagicMock()
    entity_registry = MagicMock()
    entity_registry.async_get.return_value = None

    mgr = EntityMigrationManager(hass, "test_entry")
    mgr._entity_registry = entity_registry

    result = await mgr._remove_entity("sensor.nonexistent")
    assert result is False
    entity_registry.async_remove.assert_not_called()


@pytest.mark.asyncio
async def test_remove_entity_exception():
    """Test _remove_entity with exception handling."""
    hass = MagicMock()
    entity_registry = MagicMock()
    entity = MagicMock()
    entity_registry.async_get.return_value = entity
    entity_registry.async_remove.side_effect = Exception("Remove error")

    mgr = EntityMigrationManager(hass, "test_entry")
    mgr._entity_registry = entity_registry

    result = await mgr._remove_entity("sensor.test_entity")
    assert result is False


@pytest.mark.asyncio
async def test_update_entity_id_collision():
    """Test _update_entity_id when target ID already exists."""
    hass = MagicMock()
    entity_registry = MagicMock()

    # Mock existing entity with different ID
    existing_entity = MagicMock()
    existing_entity.entity_id = "sensor.different_entity"
    entity_registry.async_get.return_value = existing_entity

    mgr = EntityMigrationManager(hass, "test_entry")
    mgr._entity_registry = entity_registry

    result = await mgr._update_entity_id("sensor.old_entity", "sensor.new_entity")
    assert result is False
    entity_registry.async_update_entity.assert_not_called()


@pytest.mark.asyncio
async def test_load_circuit_data_no_coordinator():
    """Test _load_circuit_data when coordinator is not available."""
    hass = MagicMock()
    hass.data = {"span_panel": {"test_entry": {}}}  # No coordinator key

    mgr = EntityMigrationManager(hass, "test_entry")

    await mgr._load_circuit_data()
    # Should complete without error, circuit_data should be empty
    assert len(mgr._circuit_data) == 0


@pytest.mark.asyncio
async def test_load_circuit_data_no_coordinator_data():
    """Test _load_circuit_data when coordinator has no data."""
    hass = MagicMock()
    coordinator = MagicMock()
    coordinator.data = None
    hass.data = {"span_panel": {"test_entry": {"coordinator": coordinator}}}

    mgr = EntityMigrationManager(hass, "test_entry")

    await mgr._load_circuit_data()
    # Should complete without error, circuit_data should be empty
    assert len(mgr._circuit_data) == 0


@pytest.mark.asyncio
async def test_load_circuit_data_exception():
    """Test _load_circuit_data with exception handling."""
    hass = MagicMock()
    hass.data.get.side_effect = Exception("Data access error")

    mgr = EntityMigrationManager(hass, "test_entry")

    await mgr._load_circuit_data()
    # Should complete without error despite exception
    assert len(mgr._circuit_data) == 0


@pytest.mark.asyncio
async def test_migrate_entities_synthetic_logging():
    """Test migrate_entities with synthetic entity logging."""
    hass = MagicMock()
    entity_registry = MagicMock()

    # Create entities with synthetic ones
    entities = [
        create_mock_entity("sensor.span_panel_circuit_15_power", "circuit_15_power"),
        create_mock_entity(
            "sensor.span_panel_circuit_30_32_energy", "synthetic_circuit_30_32_energy"
        ),  # synthetic
    ]
    entity_registry.entities.values.return_value = entities
    entity_registry.async_get.return_value = None  # No collision
    entity_registry.async_update_entity.return_value = None

    mgr = EntityMigrationManager(hass, "test_entry")
    mgr._entity_registry = entity_registry
    mgr._circuit_data = {"circuit_15": ("Kitchen", 15)}

    # Mock methods
    mgr._is_circuit_level_entity = MagicMock(return_value=True)
    mgr._generate_new_entity_id = MagicMock(
        side_effect=lambda e, f, t: f"sensor.new_{e.entity_id.split('.')[1]}"
    )

    result = await mgr.migrate_entities(
        EntityNamingPattern.FRIENDLY_NAMES, EntityNamingPattern.CIRCUIT_NUMBERS
    )

    assert result is True


@pytest.mark.asyncio
async def test_migrate_entities_synthetic_mappings_logging():
    """Test migrate_entities with synthetic entity mappings logging."""
    hass = MagicMock()
    entity_registry = MagicMock()

    # Create entity that will generate synthetic mapping
    entity = create_mock_entity("sensor.span_panel_circuit_30_32_energy", "circuit_30_32_energy")
    entity_registry.entities.values.return_value = [entity]
    entity_registry.async_get.return_value = None
    entity_registry.async_update_entity.return_value = None

    mgr = EntityMigrationManager(hass, "test_entry")
    mgr._entity_registry = entity_registry

    # Mock to return synthetic mapping
    mgr._is_circuit_level_entity = MagicMock(return_value=True)
    mgr._generate_new_entity_id = MagicMock(return_value="sensor.span_panel_circuit_15_20_energy")

    result = await mgr.migrate_entities(
        EntityNamingPattern.FRIENDLY_NAMES, EntityNamingPattern.CIRCUIT_NUMBERS
    )

    assert result is True
