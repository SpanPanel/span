"""Tests for the Repair raised when the panel's certificate does not name the host.

The condition this covers is the one the library used to report to nobody: a
handshake that fails against a pin the panel still advertises, because the
certificate underneath names an address the entry does not use. The user was left
with a minute-by-minute log warning, every entity unavailable, and nothing in the
UI at all.

The wiring is the substance of it, so most of these tests are about *where* the
subscription happens rather than what it does with the signal. The library runs
the diagnosis inside `connect()`, and a mismatch makes `connect()` raise, so a
subscription placed after it -- the natural place, and where the fatal-error one
lives -- would never see the case it exists for.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant import config_entries
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import issue_registry as ir
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from span_panel_api import LeafNameMismatch
from span_panel_api.exceptions import SpanPanelConnectionError

from custom_components.span_panel import async_setup_entry
from custom_components.span_panel.ca_repairs import async_raise_ca_changed, ca_changed_issue_id
from custom_components.span_panel.const import (
    CONF_API_VERSION,
    CONF_EBUS_BROKER_HOST,
    CONF_EBUS_BROKER_PASSWORD,
    CONF_EBUS_BROKER_PORT,
    CONF_EBUS_BROKER_USERNAME,
    CONF_HTTP_PORT,
    CONF_PANEL_CA_PEM,
    DOMAIN,
)
from custom_components.span_panel.coordinator import SpanPanelCoordinator
from custom_components.span_panel.leaf_repairs import (
    async_clear_leaf_name_mismatch,
    async_raise_leaf_name_mismatch,
    leaf_name_mismatch_issue_id,
)

from .factories import SpanPanelSnapshotFactory

PEM = "-----BEGIN CERTIFICATE-----\nZmFrZQ==\n-----END CERTIFICATE-----\n"

_STRINGS = (
    Path(__file__).resolve().parent.parent / "custom_components" / "span_panel" / "strings.json"
)

MOVED = LeafNameMismatch(host="192.168.1.100", leaf_names=("span-panel.local", "192.168.1.187"))
NAMELESS = LeafNameMismatch(host="192.168.1.100", leaf_names=())


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    """Add a pinned v2 entry, the only shape this condition can arise on."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=7,
        title="Span Panel",
        data={
            CONF_HOST: "192.168.1.100",
            CONF_ACCESS_TOKEN: "token",
            CONF_API_VERSION: "v2",
            CONF_PANEL_CA_PEM: PEM,
        },
        source=config_entries.SOURCE_USER,
        unique_id="SPAN-V2-001",
    )
    entry.add_to_hass(hass)
    return entry


def _setup_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Add an entry carrying everything `async_setup_entry` needs of a v2 panel."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_API_VERSION: "v2",
            CONF_HOST: "192.168.1.100",
            CONF_EBUS_BROKER_HOST: "span-panel.local",
            CONF_EBUS_BROKER_USERNAME: "mqtt-user",
            CONF_EBUS_BROKER_PASSWORD: "mqtt-pass",
            CONF_EBUS_BROKER_PORT: 8883,
            CONF_HTTP_PORT: 80,
        },
        entry_id="entry-leaf",
        title="sp3-leaf-001",
        unique_id="sp3-leaf-001",
    )
    entry.add_to_hass(hass)
    return entry


def _issue(hass: HomeAssistant, entry: MockConfigEntry) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, leaf_name_mismatch_issue_id(entry.entry_id))


# ---------- what the notice says ----------


async def test_a_mismatch_names_the_addresses_the_panel_does_answer_to(
    hass: HomeAssistant,
) -> None:
    """Saying the address is wrong without saying which is right is the log line again."""
    entry = _entry(hass)

    async_raise_leaf_name_mismatch(hass, entry, MOVED)

    issue = _issue(hass, entry)
    assert issue is not None
    assert issue.translation_key == "panel_leaf_name_mismatch"
    assert issue.translation_placeholders == {
        "panel": "Span Panel",
        "host": "192.168.1.100",
        "leaf_names": "span-panel.local, 192.168.1.187",
    }


async def test_the_notice_is_a_warning_the_user_cannot_click_through(
    hass: HomeAssistant,
) -> None:
    """Not fixable, because Reconfigure is the remedy and a fix flow cannot start one.

    Not persistent either, and not an error: the transport is alive and still
    retrying, the chain verified under the pin, and nothing was refused.
    """
    entry = _entry(hass)

    async_raise_leaf_name_mismatch(hass, entry, MOVED)

    issue = _issue(hass, entry)
    assert issue is not None
    assert issue.is_fixable is False
    assert issue.is_persistent is False
    assert issue.severity == ir.IssueSeverity.WARNING


async def test_a_certificate_naming_nothing_gets_its_own_description(
    hass: HomeAssistant,
) -> None:
    """The empty case is a different sentence, not an empty value.

    A placeholder is substituted verbatim, so a phrase passed as one would reach
    a Spanish reader in English. It is also a different fault -- the panel's
    certificate rather than the user's address -- and "names only: " followed by
    nothing is not a sentence in any language.
    """
    entry = _entry(hass)

    async_raise_leaf_name_mismatch(hass, entry, NAMELESS)

    issue = _issue(hass, entry)
    assert issue is not None
    assert issue.translation_key == "panel_leaf_name_mismatch_no_names"
    assert issue.translation_placeholders == {
        "panel": "Span Panel",
        "host": "192.168.1.100",
    }


async def test_both_descriptions_exist_and_use_only_the_placeholders_supplied(
    hass: HomeAssistant,
) -> None:
    """`strings.json` is the source of truth, and it has to match what is passed.

    A description referencing a placeholder nothing supplies renders the literal
    `{leaf_names}` to the user, which is how the empty case would fail if it were
    ever pointed at the other key.
    """
    strings = json.loads(_STRINGS.read_text(encoding="utf-8"))
    issues = strings["issues"]
    entry = _entry(hass)

    for mismatch in (MOVED, NAMELESS):
        async_raise_leaf_name_mismatch(hass, entry, mismatch)
        issue = _issue(hass, entry)
        assert issue is not None
        assert issue.translation_placeholders is not None
        block = issues[issue.translation_key]
        assert block["title"]
        block["description"].format(**issue.translation_placeholders)


# ---------- when it goes away ----------


async def test_a_connection_clears_a_standing_notice(hass: HomeAssistant) -> None:
    """The same edge the library re-arms its once-per-outage signal on.

    Cleared on the connection callback rather than on a snapshot so the two
    cannot drift: if the notice outlived the re-arm, the next mismatch would fire
    against an issue that is already standing and tell the user nothing new.
    """
    entry = _entry(hass)
    async_raise_leaf_name_mismatch(hass, entry, MOVED)
    coordinator = SpanPanelCoordinator(hass, MagicMock(), entry)

    coordinator._on_connection_change(True)

    assert _issue(hass, entry) is None


async def test_a_disconnect_leaves_the_notice_standing(hass: HomeAssistant) -> None:
    """Losing the connection is the condition, not its resolution."""
    entry = _entry(hass)
    async_raise_leaf_name_mismatch(hass, entry, MOVED)
    coordinator = SpanPanelCoordinator(hass, MagicMock(), entry)

    coordinator._on_connection_change(False)

    assert _issue(hass, entry) is not None


async def test_a_ca_change_takes_the_notice_over(hass: HomeAssistant) -> None:
    """Two findings out of one handshake, and only one of them carries a decision.

    Telling somebody to re-point their configuration at an address served by a
    certificate the integration has just refused to trust is the wrong
    instruction, so the CA Repair does not stand beside this one.
    """
    entry = _entry(hass)
    async_raise_leaf_name_mismatch(hass, entry, MOVED)

    async_raise_ca_changed(hass, entry, "aa" * 32, "bb" * 32)

    assert _issue(hass, entry) is None
    assert (
        ir.async_get(hass).async_get_issue(DOMAIN, ca_changed_issue_id(entry.entry_id)) is not None
    )


async def test_clearing_a_notice_that_was_never_raised_is_not_an_error(
    hass: HomeAssistant,
) -> None:
    """Setup and every connect call this, and almost every one has nothing to drop."""
    entry = _entry(hass)

    async_clear_leaf_name_mismatch(hass, entry)

    assert _issue(hass, entry) is None


# ---------- the wiring ----------


def _diagnosing_client(mismatch: LeafNameMismatch | None) -> tuple[MagicMock, list[str]]:
    """Build a client that diagnoses inside `connect()` and then raises, as the library does.

    The signal is fired from within `connect`, so a callback registered after it
    returns -- or after it raises -- never sees it. That is the whole point of
    the ordering under test, and a mock that fired it from anywhere else would
    prove nothing.

    Returns the calls it saw, in order, so the ordering can be asserted directly
    as well as through its consequence.
    """
    client = MagicMock()
    subscribers: list[Any] = []
    order: list[str] = []

    def register(callback: Any) -> Any:
        order.append("register")
        subscribers.append(callback)
        return MagicMock(name="unregister")

    async def connect() -> None:
        order.append("connect")
        for callback in subscribers:
            if mismatch is not None:
                callback(mismatch)
        raise SpanPanelConnectionError("TLS handshake failed")

    client.register_leaf_mismatch_callback = MagicMock(side_effect=register)
    client.connect = AsyncMock(side_effect=connect)
    client.close = AsyncMock()
    return client, order


async def test_a_mismatch_on_the_very_first_connect_still_reaches_the_user(
    hass: HomeAssistant,
) -> None:
    """The regression this ordering exists for.

    A mismatch makes `connect()` raise, setup raises `ConfigEntryNotReady`, and
    the retry builds a new client -- so a subscription registered after a
    successful connect would be registered on a client that never has one. Every
    setup attempt would fire the signal into nothing.
    """
    entry = _setup_entry(hass)
    client, _order = _diagnosing_client(MOVED)

    with (
        patch("custom_components.span_panel.async_register_commands"),
        patch("custom_components.span_panel.SpanMqttClient", return_value=client),
        pytest.raises(ConfigEntryNotReady),
    ):
        await async_setup_entry(hass, entry)

    issue = _issue(hass, entry)
    assert issue is not None
    assert issue.translation_placeholders is not None
    assert issue.translation_placeholders["leaf_names"] == "span-panel.local, 192.168.1.187"


async def test_the_subscription_is_registered_before_the_connect_attempt(
    hass: HomeAssistant,
) -> None:
    """Stated as an ordering too, not only through its consequence.

    The test above would keep passing on a subscription moved back after
    `connect()` if the library ever stopped raising on a mismatch, which is a
    change to the library rather than to this decision.
    """
    entry = _setup_entry(hass)
    client, order = _diagnosing_client(None)

    with (
        patch("custom_components.span_panel.async_register_commands"),
        patch("custom_components.span_panel.SpanMqttClient", return_value=client),
        pytest.raises(ConfigEntryNotReady),
    ):
        await async_setup_entry(hass, entry)

    assert order == ["register", "connect"]


async def test_a_successful_setup_clears_a_notice_left_from_a_previous_run(
    hass: HomeAssistant,
) -> None:
    """A handshake that succeeded is the same disproof a reconnect is."""
    entry = _setup_entry(hass)
    async_raise_leaf_name_mismatch(hass, entry, MOVED)

    snapshot = SpanPanelSnapshotFactory.create(serial_number="sp3-leaf-001")
    client = MagicMock()
    client.connect = AsyncMock()
    coordinator = MagicMock()
    coordinator.async_config_entry_first_refresh = AsyncMock()
    coordinator.async_setup_streaming = AsyncMock()
    coordinator.data = snapshot

    with (
        patch("custom_components.span_panel.async_register_commands"),
        patch("custom_components.span_panel.SpanMqttClient", return_value=client),
        patch("custom_components.span_panel.SpanPanelCoordinator", return_value=coordinator),
        patch("custom_components.span_panel.ensure_device_registered", AsyncMock()),
        patch.object(hass.config_entries, "async_forward_entry_setups", AsyncMock()),
        patch.object(hass.config_entries, "async_update_entry"),
    ):
        assert await async_setup_entry(hass, entry) is True

    assert _issue(hass, entry) is None


async def test_unloading_the_entry_takes_the_subscription_with_it(
    hass: HomeAssistant,
) -> None:
    """The client goes when the entry does, and so must the callback holding `hass`."""
    entry = _setup_entry(hass)
    unregister = MagicMock(name="unregister")
    client = MagicMock()
    client.connect = AsyncMock()
    client.register_leaf_mismatch_callback = MagicMock(return_value=unregister)
    coordinator = MagicMock()
    coordinator.async_config_entry_first_refresh = AsyncMock()
    coordinator.async_setup_streaming = AsyncMock()
    coordinator.data = SpanPanelSnapshotFactory.create(serial_number="sp3-leaf-001")

    with (
        patch("custom_components.span_panel.async_register_commands"),
        patch("custom_components.span_panel.SpanMqttClient", return_value=client),
        patch("custom_components.span_panel.SpanPanelCoordinator", return_value=coordinator),
        patch("custom_components.span_panel.ensure_device_registered", AsyncMock()),
        patch.object(hass.config_entries, "async_forward_entry_setups", AsyncMock()),
        patch.object(hass.config_entries, "async_update_entry"),
    ):
        assert await async_setup_entry(hass, entry) is True

    unregister.assert_not_called()
    await entry._async_process_on_unload(hass)
    unregister.assert_called_once_with()
