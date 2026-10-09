"""Where the panel is installed never reaches a new entity, an attribute, diagnostics or the log.

The library never turns the site's address, locality, region, country code,
coordinates or utility meter serial into a reading. The postal code and time
zone are the two site readings an install may already hold as entities: they
stay on such an install, keep their entity ids and keep updating, while a new
install creates neither. Diagnostics carry neither value on any install.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Final

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest

from custom_components.span_panel.const import DOMAIN
from custom_components.span_panel.diagnostics import async_get_config_entry_diagnostics

from .captures_replay import RetainedTree, capture, description, panel_device_id, snapshot
from .test_expected_entities import R202639_CAPTURE, _install

SITE_VALUES: Final = {
    "address-lines": "1 Example Street",
    "locality": "Exampleville",
    "region": "EX",
    "country-code": "ZZ",
    "latitude": "12.3456",
    "longitude": "-65.4321",
    "utility-meter-serial-number": "UMS-EXAMPLE-0001",
}
"""Illustrative values for every site property, each one distinct enough to search for."""

SITE_READINGS: Final = ("status/postal-code", "status/time-zone")
"""The two site readings an install may already hold as entities."""


@pytest.fixture(autouse=True)
def expected_lingering_timers() -> bool:
    """Allow the switch platform's relock and debounce timers to outlive a test."""
    return True


def _with_site() -> RetainedTree:
    """Return the capture with every site property declared on the panel's `info` node and published."""
    tree = capture(R202639_CAPTURE).tree()
    panel = panel_device_id(tree)
    declared = dict(description(tree, panel))
    nodes = declared["nodes"]
    assert isinstance(nodes, dict)
    info = dict(nodes["info"])
    properties = dict(info["properties"])
    for property_id in SITE_VALUES:
        properties[property_id] = {"datatype": "string", "name": property_id}
    info["properties"] = properties
    declared["nodes"] = {**nodes, "info": info}
    tree[panel]["$description"] = json.dumps(declared)
    tree[panel].update({f"info/{prop}": value for prop, value in SITE_VALUES.items()})
    # Distinctive stand-ins for the two readings, which the capture masks to
    # values too common to search a dump for.
    tree[panel].update(dict(zip(SITE_READINGS, ("PC-EXAMPLE-1", "Example/Zone"), strict=True)))
    return tree


def _site_reading_values(tree: RetainedTree) -> list[str]:
    panel = tree[panel_device_id(tree)]
    values = [panel[path] for path in SITE_READINGS]
    assert all(values), "the capture publishes both site readings"
    return values


def _site_reading_unique_ids(tree: RetainedTree) -> list[str]:
    serial = snapshot(tree).serial_number
    return [f"span_{serial}_adopted_panel/{path}" for path in SITE_READINGS]


def _manifest_loggers() -> list[str]:
    manifest = json.loads(
        (Path(__file__).parent.parent / "custom_components/span_panel/manifest.json").read_text(
            encoding="utf-8"
        )
    )
    loggers = manifest["loggers"]
    assert isinstance(loggers, list)
    return [str(name) for name in loggers]


async def test_no_site_value_reaches_an_entity_diagnostics_or_the_log(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """The log is every logger debug logging for this integration turns on, at DEBUG."""
    tree = _with_site()
    caplog.set_level(logging.DEBUG)

    entry = await _install(hass, tree, "site")
    diagnostics = json.dumps(await async_get_config_entry_diagnostics(hass, entry), default=str)
    states = json.dumps(
        [(state.state, dict(state.attributes)) for state in hass.states.async_all()], default=str
    )
    ours = tuple(_manifest_loggers())
    logged = "\n".join(
        record.getMessage() for record in caplog.records if record.name.startswith(ours)
    )

    for value in (*SITE_VALUES.values(), *_site_reading_values(tree)):
        assert value not in states, value
        assert value not in diagnostics, value
        assert value not in logged, value


async def test_a_new_install_creates_neither_site_reading(hass: HomeAssistant) -> None:
    tree = capture(R202639_CAPTURE).tree()

    entry = await _install(hass, tree, "site-new")
    registry = er.async_get(hass)

    for unique_id in _site_reading_unique_ids(tree):
        assert registry.async_get_entity_id("sensor", DOMAIN, unique_id) is None, unique_id
    assert entry.entry_id


async def test_existing_site_entities_survive_upgrade(hass: HomeAssistant) -> None:
    """An install that already holds them keeps both, under their entity ids, and they keep updating."""
    tree = capture(R202639_CAPTURE).tree()
    registry = er.async_get(hass)
    seeded = {
        unique_id: registry.async_get_or_create(
            "sensor", DOMAIN, unique_id, suggested_object_id=object_id
        ).entity_id
        for unique_id, object_id in zip(
            _site_reading_unique_ids(tree),
            ("span_panel_status_postal_code", "span_panel_status_time_zone"),
            strict=True,
        )
    }
    for entity_id in seeded.values():
        registry.async_update_entity(entity_id, disabled_by=None)

    await _install(hass, tree, "site-upgrade")

    for (unique_id, entity_id), value in zip(
        seeded.items(), _site_reading_values(tree), strict=True
    ):
        assert registry.async_get_entity_id("sensor", DOMAIN, unique_id) == entity_id
        state = hass.states.get(entity_id)
        assert state is not None and state.state == value, entity_id


def test_debug_logging_for_the_integration_never_turns_on_homie() -> None:
    """`homie` logs every value it receives at DEBUG, so the manifest never names it."""
    assert not any(name.split(".")[0] == "homie" for name in _manifest_loggers())
