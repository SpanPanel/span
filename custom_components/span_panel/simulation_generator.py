"""Build simulation YAML from a live panel snapshot.

This module inspects the current coordinator data and produces a YAML dict
that matches span_panel_api's simulation reference. It infers templates from
names and seeds energy profiles from current power readings.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class SimulationYamlGenerator:
    """Generate YAML from live panel data."""

    hass: Any
    coordinator: Any
    solar_leg1: int | None = None
    solar_leg2: int | None = None

    async def build_yaml_from_live_panel(self) -> tuple[dict[str, Any], int]:
        """Build YAML from live panel data."""
        data = getattr(self.coordinator, "data", None)
        circuits_obj = getattr(data, "circuits", None)

        # Prepare containers
        circuit_templates: dict[str, Any] = {}
        circuits: list[dict[str, Any]] = []
        mapped_tabs: set[int] = set()

        # Iterate circuits
        iter_dict: dict[str, Any] = {}
        # Safely extract iterable circuits mapping without accessing attributes on None
        if isinstance(circuits_obj, dict):
            iter_dict = circuits_obj
        elif circuits_obj is not None:
            inner_circuits = getattr(circuits_obj, "circuits", None)
            if isinstance(inner_circuits, dict):
                iter_dict = inner_circuits

        for cid, c in iter_dict.items():
            name = str(getattr(c, "name", cid))
            power_w = float(getattr(c, "instant_power_w", 0.0) or 0.0)
            raw_tabs = getattr(c, "tabs", []) if hasattr(c, "tabs") else []
            tabs = (
                list(raw_tabs)
                if isinstance(raw_tabs, (list | tuple))
                else ([] if raw_tabs in (None, "UNSET") else [int(raw_tabs)])
            )
            mapped_tabs.update(tabs)

            template_key = self._infer_template_key(name, power_w, tabs)
            if template_key not in circuit_templates:
                circuit_templates[template_key] = self._make_template(template_key, power_w, name)

            entry: dict[str, Any] = {
                "id": str(cid),
                "name": name,
                "tabs": tabs,
                "template": template_key,
            }
            if power_w != 0.0:
                entry["overrides"] = {"energy_profile": {"typical_power": power_w}}

            circuits.append(entry)

        # Compute total tabs
        num_tabs = 32
        if mapped_tabs:
            max_tab = max(mapped_tabs)
            if max_tab <= 8:
                num_tabs = 8
            elif max_tab <= 32:
                num_tabs = 32
            else:
                num_tabs = 40

        # Panel config
        serial = (
            getattr(getattr(data, "status", None), "serial_number", None) or "span_panel_simulation"
        )
        snapshot_yaml: dict[str, Any] = {
            "panel_config": {
                "serial_number": str(serial),
                "total_tabs": num_tabs,
                "main_size": 200,
            },
            "circuit_templates": circuit_templates,
            "circuits": circuits,
            "unmapped_tabs": sorted(set(range(1, num_tabs + 1)) - mapped_tabs),
            "simulation_params": {
                "update_interval": 5,
                "time_acceleration": 1.0,
                "noise_factor": 0.02,
            },
        }

        # Add solar configuration if legs provided and valid
        self._maybe_add_solar(snapshot_yaml)

        return snapshot_yaml, num_tabs

    def _maybe_add_solar(self, yaml_doc: dict[str, Any]) -> None:
        l1 = int(self.solar_leg1 or 0)
        l2 = int(self.solar_leg2 or 0)
        if l1 <= 0 or l2 <= 0 or l1 == l2:
            return

        # Ensure solar template exists
        templates = yaml_doc.setdefault("circuit_templates", {})
        if "solar_production" not in templates:
            templates["solar_production"] = {
                "energy_profile": {
                    "mode": "producer",
                    "power_range": [-2000.0, 0.0],
                    "typical_power": -1500.0,
                    "power_variation": 0.2,
                    "efficiency": 0.85,
                },
                "relay_behavior": "non_controllable",
                "priority": "MUST_HAVE",
                "time_of_day_profile": {
                    "enabled": True,
                    "peak_hours": [11, 12, 13, 14, 15],
                },
            }

        # Unmapped tab templates for the two solar legs
        unmapped = yaml_doc.setdefault("unmapped_tab_templates", {})
        for tab in (l1, l2):
            key = str(tab)
            if key not in unmapped:
                unmapped[key] = templates["solar_production"]

        # Synchronization group for the two legs
        tab_syncs: list[dict[str, Any]] = yaml_doc.setdefault("tab_synchronizations", [])
        tab_syncs.append(
            {
                "tabs": [l1, l2],
                "behavior": "240v_split_phase",
                "power_split": "equal",
                "energy_sync": True,
                "template": "solar_production",
            }
        )

        # Ensure legs listed as unmapped
        yaml_doc["unmapped_tabs"] = sorted(set(yaml_doc.get("unmapped_tabs", [])) | {l1, l2})

    def _infer_template_key(self, name: str, power_w: float, tabs: list[int]) -> str:
        lname = name.lower()
        if any(k in lname for k in ("light", "lights")):
            return "lighting"
        if "kitchen" in lname and "outlet" in lname:
            return "kitchen_outlets"
        if any(k in lname for k in ("hvac", "furnace", "air conditioner", "ac", "heat pump")):
            return "hvac"
        if any(k in lname for k in ("fridge", "refrigerator", "wine fridge")):
            return "refrigerator"
        if any(k in lname for k in ("ev", "charger")):
            return "ev_charger"
        if any(k in lname for k in ("pool", "spa", "fountain")):
            return "pool_equipment"
        if any(k in lname for k in ("internet", "router", "network", "modem")):
            return "always_on"
        if len(tabs) >= 2:
            return "major_appliance"
        if "outlet" in lname:
            return "outlets"
        if power_w < 0:
            return "producer"
        return "major_appliance"

    def _make_template(self, key: str, typical: float, name: str) -> dict[str, Any]:
        # Base ranges derived from snapshot
        if key == "producer" or typical < 0:
            pr_min = min(typical * 2.0, -50.0)
            profile = {
                "mode": "producer",
                "power_range": [pr_min, 0.0],
                "typical_power": typical,
                "power_variation": 0.3,
            }
            return {
                "energy_profile": profile,
                "relay_behavior": "non_controllable",
                "priority": "MUST_HAVE",
            }

        if key == "ev_charger":
            profile = {
                "mode": "consumer",
                "power_range": [0.0, max(abs(typical) * 2.0, 7200.0)],
                "typical_power": max(typical, 3000.0),
                "power_variation": 0.15,
            }
            return {
                "energy_profile": profile,
                "relay_behavior": "controllable",
                "priority": "NON_ESSENTIAL",
                # Prefer night charging and respond to grid stress
                "time_of_day_profile": {
                    "enabled": True,
                    "peak_hours": [22, 23, 0, 1, 2, 3, 4, 5, 6],
                },
                "smart_behavior": {"responds_to_grid": True, "max_power_reduction": 0.6},
            }

        if key == "refrigerator":
            profile = {
                "mode": "consumer",
                "power_range": [50.0, 200.0],
                "typical_power": max(typical, 120.0),
                "power_variation": 0.2,
            }
            return {
                "energy_profile": profile,
                "relay_behavior": "non_controllable",
                "priority": "MUST_HAVE",
                "cycling_pattern": {"on_duration": 600, "off_duration": 1800},
            }

        if key == "hvac":
            profile = {
                "mode": "consumer",
                "power_range": [0.0, max(abs(typical) * 2.0, 2800.0)],
                "typical_power": max(typical, 1800.0),
                "power_variation": 0.15,
            }
            return {
                "energy_profile": profile,
                "relay_behavior": "controllable",
                "priority": "MUST_HAVE",
                "cycling_pattern": {"on_duration": 1200, "off_duration": 2400},
            }

        if key == "lighting":
            profile = {
                "mode": "consumer",
                "power_range": [0.0, max(abs(typical) * 2.0, 300.0)],
                "typical_power": max(typical, 40.0),
                "power_variation": 0.1,
            }
            return {
                "energy_profile": profile,
                "relay_behavior": "controllable",
                "priority": "NON_ESSENTIAL",
                "time_of_day_profile": {"enabled": True, "peak_hours": [18, 19, 20, 21, 22]},
            }

        if key == "kitchen_outlets":
            profile = {
                "mode": "consumer",
                "power_range": [0.0, max(abs(typical) * 2.0, 2400.0)],
                "typical_power": max(typical, 300.0),
                "power_variation": 0.4,
            }
            return {
                "energy_profile": profile,
                "relay_behavior": "controllable",
                "priority": "MUST_HAVE",
            }

        if key == "outlets":
            profile = {
                "mode": "consumer",
                "power_range": [0.0, max(abs(typical) * 2.0, 1800.0)],
                "typical_power": max(typical, 150.0),
                "power_variation": 0.4,
            }
            return {
                "energy_profile": profile,
                "relay_behavior": "controllable",
                "priority": "MUST_HAVE",
            }

        if key == "always_on":
            profile = {
                "mode": "consumer",
                "power_range": [40.0, 100.0],
                "typical_power": max(typical, 60.0),
                "power_variation": 0.1,
            }
            return {
                "energy_profile": profile,
                "relay_behavior": "controllable",
                "priority": "MUST_HAVE",
            }

        if key == "pool_equipment":
            profile = {
                "mode": "consumer",
                "power_range": [0.0, max(abs(typical) * 2.0, 1200.0)],
                "typical_power": max(typical, 800.0),
                "power_variation": 0.1,
            }
            return {
                "energy_profile": profile,
                "relay_behavior": "controllable",
                "priority": "NON_ESSENTIAL",
                # Typical pump run: 2h on, 4h off, repeating
                "cycling_pattern": {"on_duration": 7200, "off_duration": 14400},
            }

        # major_appliance and fallback
        profile = {
            "mode": "consumer",
            "power_range": [0.0, max(abs(typical) * 2.0, 2500.0)],
            "typical_power": max(typical, 800.0),
            "power_variation": 0.3,
        }
        return {
            "energy_profile": profile,
            "relay_behavior": "controllable",
            "priority": "NON_ESSENTIAL",
        }
