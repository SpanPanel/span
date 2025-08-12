"""Integration YAML Generator for testing purposes."""

from typing import Any, Dict


class IntegrationYAMLGenerator:
    """Placeholder YAML generator for testing."""

    def __init__(self) -> None:
        """Initialize the YAML generator."""
        pass

    async def generate_yaml_for_naming_pattern(
        self,
        hass: Any,
        naming_flags: Dict[str, Any]
    ) -> str:
        """Generate YAML for a specific naming pattern."""
        # Placeholder implementation
        return "# Placeholder YAML content"

    async def generate_yaml_for_all_patterns(self, hass: Any) -> Dict[str, str]:
        """Generate YAML for all naming patterns."""
        # Placeholder implementation
        return {
            "legacy_no_prefix": "# Placeholder YAML content",
            "device_prefix_friendly": "# Placeholder YAML content",
            "device_prefix_circuit_numbers": "# Placeholder YAML content",
            "circuit_numbers_only": "# Placeholder YAML content"
        }

    async def generate_yaml_with_solar_enabled(
        self,
        hass: Any,
        naming_flags: Dict[str, Any],
        leg1_circuit: int,
        leg2_circuit: int
    ) -> str:
        """Generate YAML with solar configuration enabled."""
        # Placeholder implementation
        return "# Placeholder YAML content with solar enabled"
