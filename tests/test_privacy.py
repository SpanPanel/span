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

from .captures_replay import (
    CAPTURES,
    Capture,
    RetainedTree,
    capture,
    description,
    panel_device_id,
    snapshot,
)
from .test_expected_entities import R202639_CAPTURE, _install

SITE_VALUES: Final = {
    "address-lines": "1 Example Street",
    "locality": "Exampleville",
    "region": "EX",
    "country-code": "XE",
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


EXPORT_MASKS: Final = {
    "address-lines": "Masked Address",
    "locality": "Masked City",
    "region": "Masked Region",
    "country-code": "ZZ",
    "latitude": "0.0",
    "longitude": "0.0",
    "utility-meter-serial-number": "MASKED-METER",
    "postal-code": "00000",
    "time-zone": "UTC",
    "wifi-ssid": "masked-ssid",
    "name": "Masked Panel",
}
"""What the masked device tree writes for each site property, by the property's id on any node.

`name` is the exception: only the panel's own `info/name` is masked, never another device's.
"""


def _without_the_tree(diagnostics: dict[str, object]) -> str:
    """The dump with `device_tree` taken out, which is where no site value may appear at all."""
    return json.dumps({k: v for k, v in diagnostics.items() if k != "device_tree"}, default=str)


def _tree_text(diagnostics: dict[str, object]) -> str:
    return json.dumps(diagnostics["device_tree"], default=str)


def _unmasked_in_the_tree(tree: RetainedTree, values: list[str]) -> list[str]:
    """The site values the tree must not hold: all but one that is the export's own mask for the property it came from.

    A capture already masked upstream publishes a value equal to the library's
    mask, and the tree cannot be told to hold it or not; keyed by property, so a
    value the mask of a different property would explain is still searched for.
    """
    panel = tree[panel_device_id(tree)]
    leaves = {
        value: {topic.rsplit("/", 1)[-1] for topic, v in panel.items() if v == value}
        for value in values
    }
    return [
        value
        for value in values
        if not leaves[value] or any(EXPORT_MASKS.get(leaf) != value for leaf in leaves[value])
    ]


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
    payload = await async_get_config_entry_diagnostics(hass, entry)
    diagnostics = _without_the_tree(payload)
    tree_text = _tree_text(payload)
    states = json.dumps(
        [(state.state, dict(state.attributes)) for state in hass.states.async_all()], default=str
    )
    ours = tuple(_manifest_loggers())
    logged = "\n".join(
        record.getMessage() for record in caplog.records if record.name.startswith(ours)
    )

    for value in (*SITE_VALUES.values(), *_site_reading_values(tree)):
        assert value not in states, value
        assert value not in logged, value
    for value in (*SITE_VALUES.values(), *_site_reading_values(tree)):
        assert value not in diagnostics, value
    for value in _unmasked_in_the_tree(tree, [*SITE_VALUES.values(), *_site_reading_values(tree)]):
        assert value not in tree_text, value


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


def _searchable_site_values(tree: RetainedTree) -> list[str]:
    """Every site value the panel publishes that is distinctive enough to search a dump for.

    A masked capture carries some values so common ("CA", "0.0", "00000") that
    finding them would say nothing; those are left to the synthetic tree above.
    """
    panel = tree[panel_device_id(tree)]
    values = [
        value
        for topic, value in panel.items()
        if topic.rsplit("/", 1)[-1] in {*SITE_VALUES, "name", "postal-code", "time-zone"}
        and topic.split("/", 1)[0] in {"info", "status"}
    ]
    return [value for value in values if len(value) >= 6 and any(c.isalpha() for c in value)]


@pytest.mark.parametrize("captured", CAPTURES, ids=[c.name for c in CAPTURES])
async def test_no_captured_site_value_reaches_an_entity_diagnostics_or_the_log(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture, captured: Capture
) -> None:
    tree = captured.tree()
    searchable = _searchable_site_values(tree)
    assert searchable, "every capture publishes at least its time zone"
    caplog.set_level(logging.DEBUG)

    entry = await _install(hass, tree, captured.name)
    payload = await async_get_config_entry_diagnostics(hass, entry)
    diagnostics = _without_the_tree(payload)
    tree_text = _tree_text(payload)
    states = json.dumps(
        [(state.state, dict(state.attributes)) for state in hass.states.async_all()], default=str
    )
    ours = tuple(_manifest_loggers())
    logged = "\n".join(
        record.getMessage() for record in caplog.records if record.name.startswith(ours)
    )

    for value in searchable:
        assert value not in states, value
        assert value not in logged, value
    for value in searchable:
        assert value not in diagnostics, value
    for value in _unmasked_in_the_tree(tree, searchable):
        assert value not in tree_text, value
