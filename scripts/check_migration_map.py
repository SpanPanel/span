#!/usr/bin/env python3
"""Offline checker for SPAN migration normalization and expected power sensors.

Usage:
  python3 scripts/check_migration_map.py \
    --entity-reg /Volumes/config/.storage/core.entity_registry \
    [--yaml /Volumes/config/span_panel_sensor_config.yaml]

Reports:
  - Count of span_panel sensor entries and how many normalize to helper-format
  - Sample of mismatches (legacy → helper-format)
  - For each circuit seen (by UUID), the expected helper-format unique_id keys for
    power/energy sensors and whether a matching entry exists in YAML (if provided)
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import sys

# Ensure we can import the integration module directly from repo root
REPO_ROOT = Path(__file__).resolve().parents[2]
INTEGRATION_DIR = REPO_ROOT / "span" / "custom_components" / "span_panel"
sys.path.insert(0, str(INTEGRATION_DIR))

def _normalize_panel_description_key(raw_key: str) -> str:
    if "." in raw_key:
        left, right = raw_key.split(".", 1)
        return f"{left}{right[0].upper()}{right[1:]}"
    return raw_key


def _compute_normalized_unique_id(raw_unique_id: str) -> str | None:
    """Local copy of normalization logic without HA deps."""
    try:
        parts = raw_unique_id.split("_", 2)
        if len(parts) < 3 or parts[0] != "span":
            return None
        device_identifier = parts[1]
        remainder = parts[2]

        last_underscore = remainder.rfind("_")
        if last_underscore > 0:
            circuit_id = remainder[:last_underscore]
            raw_api_field = remainder[last_underscore + 1 :]
            api_key_lc = raw_api_field.replace(".", "").lower()
            circuit_map = {
                "instantpowerw": "power",
                "power": "power",
                "producedenergywh": "energy_produced",
                "consumedenergywh": "energy_consumed",
            }
            suffix = circuit_map.get(api_key_lc)
            if suffix is None:
                return None
            return f"span_{device_identifier.lower()}_{circuit_id}_{suffix}"

        normalized_panel_key = _normalize_panel_description_key(remainder)
        # Map a few known panel keys to suffixes; fallback to sanitized
        panel_map = {
            "instantGridPowerW": "current_power",
            "feedthroughPowerW": "feed_through_power",
            "mainMeterEnergyProducedWh": "main_meter_produced_energy",
            "mainMeterEnergyConsumedWh": "main_meter_consumed_energy",
            "feedthroughEnergyProducedWh": "feed_through_produced_energy",
            "feedthroughEnergyConsumedWh": "feed_through_consumed_energy",
        }
        suffix = panel_map.get(normalized_panel_key, normalized_panel_key.replace(".", "_").lower())
        return f"span_{device_identifier.lower()}_{suffix}"
    except Exception:
        return None


def load_entity_registry(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    # Entity registry is a JSON object with "data": {"entities": [...]} in modern HA
    try:
        root = json.loads(text)
    except json.JSONDecodeError:
        # Some installs store as a JSON Lines-ish structure; fall back to regex scan
        entities: list[dict[str, Any]] = []
        for m in re.finditer(r"\{[^{}]*\"unique_id\":\s*\"([^\"]+)\"[\s\S]*?\}", text):
            try:
                obj = json.loads(m.group(0))
                entities.append(obj)
            except Exception:
                continue
        return entities

    if isinstance(root, dict) and "data" in root and isinstance(root["data"], dict):
        data = root["data"]
        if isinstance(data, dict) and "entities" in data and isinstance(data["entities"], list):
            return data["entities"]  # type: ignore[return-value]
    # Fallback older formats
    if isinstance(root, dict) and "entities" in root and isinstance(root["entities"], list):
        return root["entities"]  # type: ignore[return-value]
    raise ValueError("Unrecognized entity registry format")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--entity-reg", required=True, type=Path)
    ap.add_argument("--yaml", required=False, type=Path)
    args = ap.parse_args()

    entities = load_entity_registry(args.entity_reg)
    span_sensors = [
        e
        for e in entities
        if e.get("platform") == "span_panel" and (e.get("entity_id", "").startswith("sensor."))
    ]

    total = len(span_sensors)
    mismatches: list[tuple[str, str]] = []
    circuits: set[str] = set()

    for e in span_sensors:
        uid = e.get("unique_id", "")
        nid = _compute_normalized_unique_id(uid) or ""
        if nid and nid != uid:
            mismatches.append((uid, nid))
        # Collect circuit UUID if present (pattern: span_serial_<circuit>_suffix)
        parts = uid.split("_", 2)
        if len(parts) == 3:
            remainder = parts[2]
            lu = remainder.rfind("_")
            if lu > 0:
                circuits.add(remainder[:lu])

    print(f"SPAN sensors: {total}; normalizable mismatches: {len(mismatches)}")
    for old, new in mismatches[:10]:
        print(f"  {old} -> {new}")
    if len(mismatches) > 10:
        print(f"  ... {len(mismatches) - 10} more")

    # If YAML provided, check for power keys presence per circuit
    if args.yaml and args.yaml.exists():
        yaml_text = args.yaml.read_text(encoding="utf-8")
        missing_power = []
        for circuit in sorted(circuits):
            # Helper-format unique_id keys in YAML are quoted
            key = rf'"span_[^"]*_{re.escape(circuit)}_power"\s*:\s*\n'
            if not re.search(key, yaml_text):
                missing_power.append(circuit)
        print(f"Circuits total (from registry): {len(circuits)}")
        print(f"Circuits missing power in YAML: {len(missing_power)}")
        if missing_power[:10]:
            print("  examples:", ", ".join(missing_power[:10]))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())


