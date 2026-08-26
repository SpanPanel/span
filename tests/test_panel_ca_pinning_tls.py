"""The two pins that happen outside the config flow, against a real handshake.

The config flow proves the CA it fetched signs the certificate the panel serves
before it pins anything. Two other places pin: the deferred fetch at setup, for
an entry migrated from a release that predates pinning, and the CA-changed
repair, where a person accepts a new authority. Both took whatever the plaintext
fetch returned.

Anything on the path can answer that fetch — a reverse proxy in front of the
panel answers it with an authority of its own — and pinning that CA is
unrecoverable: the broker connection then fails against a certificate the pin
rejects, the change diagnosis sees the fetched CA and the pinned one agree, and
the entry retries forever with no repair to offer. So these tests use a listener
serving a genuine leaf rather than the suite's stubs: the refusal is the
verification, and stubbing it out would test nothing.
"""

from __future__ import annotations

from collections.abc import Iterator
import ipaddress
from typing import Any
from unittest.mock import AsyncMock, patch

from cryptography import x509
from homeassistant import config_entries
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_HOST
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.span_panel import _async_pinned_ca
from custom_components.span_panel.const import (
    CONF_API_VERSION,
    CONF_EBUS_BROKER_PORT,
    CONF_HTTP_PORT,
    CONF_HTTPS_PORT,
    CONF_PANEL_CA_PEM,
    DOMAIN,
    PANEL_CA_PENDING,
)
from custom_components.span_panel.repairs import PanelCAChangedRepairFlow

from .tls_panel import (
    PANEL_LOOPBACK,
    PANEL_SERIAL,
    PANEL_SHORTNAME,
    Panel,
    resolving_to_loopback,
    unrelated_ca_pem,
)


@pytest.fixture
def panel(tmp_path: Any) -> Iterator[Panel]:
    """Serve a leaf naming the panel's address, signed by the panel's own CA."""
    instance = Panel(tmp_path)
    instance.present([x509.IPAddress(ipaddress.ip_address(PANEL_LOOPBACK))])
    yield instance
    instance.close()


def _entry(hass: HomeAssistant, panel: Panel, **data: object) -> MockConfigEntry:
    """Add an entry whose broker is the listener, shaped as a v7 migration leaves it.

    No `https_port`: that key and `panel_ca_pending` arrived in the same commit,
    so an entry carrying the flag predates both and can never hold one. The
    broker port is what the pin is checked against and every v2 entry has it --
    setup refuses one without it before the pin is ever reached.
    """
    entry = MockConfigEntry(
        version=7,
        minor_version=1,
        domain=DOMAIN,
        title="SPAN Panel",
        data={
            CONF_HOST: PANEL_LOOPBACK,
            CONF_ACCESS_TOKEN: "synthetic-token",
            CONF_API_VERSION: "v2",
            CONF_HTTP_PORT: 8080,
            CONF_EBUS_BROKER_PORT: panel.port,
            **data,
        },
        source=config_entries.SOURCE_USER,
        options={},
        unique_id=PANEL_SERIAL,
    )
    entry.add_to_hass(hass)
    return entry


# ---------- the deferred pin at setup ----------


@pytest.mark.usefixtures("socket_enabled")
@pytest.mark.asyncio
async def test_the_deferred_pin_refuses_a_ca_that_signs_nothing_the_panel_serves(
    hass: HomeAssistant, panel: Panel
) -> None:
    """The upgrade pin is trust on first use; it still has to be the panel's CA."""
    entry = _entry(hass, panel, **{PANEL_CA_PENDING: True})

    with patch(
        "custom_components.span_panel.async_fetch_panel_ca",
        new=AsyncMock(return_value=unrelated_ca_pem()),
    ):
        assert await _async_pinned_ca(hass, entry, PANEL_LOOPBACK, 8080) is None

    # Nothing stored, and still pending: the next setup tries again, and until
    # then the entry runs unpinned exactly as a failed fetch leaves it.
    assert CONF_PANEL_CA_PEM not in entry.data
    assert entry.data[PANEL_CA_PENDING] is True


@pytest.mark.usefixtures("socket_enabled")
@pytest.mark.asyncio
async def test_the_deferred_pin_still_stores_the_panels_own_ca(
    hass: HomeAssistant, panel: Panel
) -> None:
    """The check must not cost a healthy upgrade its pin."""
    entry = _entry(hass, panel, **{PANEL_CA_PENDING: True})

    with patch(
        "custom_components.span_panel.async_fetch_panel_ca",
        new=AsyncMock(return_value=panel.ca_pem),
    ):
        assert await _async_pinned_ca(hass, entry, PANEL_LOOPBACK, 8080) == panel.ca_pem

    assert entry.data[CONF_PANEL_CA_PEM] == panel.ca_pem
    assert PANEL_CA_PENDING not in entry.data


@pytest.mark.usefixtures("socket_enabled")
@pytest.mark.asyncio
async def test_the_deferred_pin_checks_the_broker_port_an_upgraded_entry_actually_has(
    hass: HomeAssistant, panel: Panel
) -> None:
    """The one population this pin exists for has no `https_port` and never will.

    `https_port` and `panel_ca_pending` were added in the same commit, so an
    entry reaching the deferred pin is by definition migrated from before either
    existed. Checking a port such an entry cannot hold means checking 443 for
    all of them, which refuses every pre-pinning install whose TLS is somewhere
    else -- behind a port forward, a NAT rule, a proxy -- and leaves exactly
    those entries on the unauthenticated refetch path for good.

    The broker's port is on every v2 entry by construction and is the connection
    the anchor is used for, so it is what the anchor is checked against.
    """
    entry = _entry(hass, panel, **{PANEL_CA_PENDING: True})
    assert CONF_HTTPS_PORT not in entry.data
    # Nothing is listening on 443 here, so a check made there could not pass.
    assert entry.data[CONF_EBUS_BROKER_PORT] == panel.port

    with patch(
        "custom_components.span_panel.async_fetch_panel_ca",
        new=AsyncMock(return_value=panel.ca_pem),
    ):
        assert await _async_pinned_ca(hass, entry, PANEL_LOOPBACK, 8080) == panel.ca_pem

    assert entry.data[CONF_PANEL_CA_PEM] == panel.ca_pem
    assert PANEL_CA_PENDING not in entry.data


@pytest.mark.usefixtures("socket_enabled")
@pytest.mark.asyncio
async def test_the_deferred_pin_reaches_a_named_host_by_its_address(
    hass: HomeAssistant, panel: Panel
) -> None:
    """A migrated entry recorded by name is checked over the address the leaf names.

    The panel's leaf names the addresses it knows itself by. An entry configured
    with a hostname -- an add-on's container name, a search-domain short name --
    would fail hostname verification against it and never pin, so the address it
    resolves to is tried too, exactly as the config flow does.
    """
    entry = _entry(hass, panel, **{CONF_HOST: PANEL_SHORTNAME, PANEL_CA_PENDING: True})

    with (
        resolving_to_loopback(PANEL_SHORTNAME),
        patch(
            "custom_components.span_panel.async_fetch_panel_ca",
            new=AsyncMock(return_value=panel.ca_pem),
        ),
    ):
        pinned = await _async_pinned_ca(hass, entry, PANEL_SHORTNAME, 8080)

    assert pinned == panel.ca_pem
    assert entry.data[CONF_PANEL_CA_PEM] == panel.ca_pem


# ---------- the re-pin the CA-changed repair performs ----------


@pytest.mark.usefixtures("socket_enabled")
@pytest.mark.asyncio
async def test_the_repair_refuses_a_ca_that_signs_nothing_the_panel_serves(
    hass: HomeAssistant, panel: Panel
) -> None:
    """The repair asks a person to accept a fingerprint; it must be a real one.

    A CA that does not sign what the panel serves cannot be the panel's, so the
    user is never shown its fingerprint and the standing pin is left alone. The
    issue stays raised, so the repair can be run again once whatever answered
    the fetch is out of the way.
    """
    entry = _entry(hass, panel, **{CONF_PANEL_CA_PEM: panel.ca_pem})
    flow = PanelCAChangedRepairFlow(entry.entry_id)
    flow.hass = hass

    with patch(
        "custom_components.span_panel.repairs.async_fetch_panel_ca",
        new=AsyncMock(return_value=unrelated_ca_pem()),
    ):
        result = await flow.async_step_init()

    assert result["type"] == "abort"
    assert result["reason"] == "ca_leaf_mismatch"
    assert entry.data[CONF_PANEL_CA_PEM] == panel.ca_pem


@pytest.mark.usefixtures("socket_enabled")
@pytest.mark.asyncio
async def test_the_repair_still_offers_a_ca_the_panel_serves_a_leaf_for(
    hass: HomeAssistant, panel: Panel
) -> None:
    """A panel that legitimately rotated both certificate and authority re-pins."""
    entry = _entry(hass, panel, **{CONF_PANEL_CA_PEM: unrelated_ca_pem()})
    flow = PanelCAChangedRepairFlow(entry.entry_id)
    flow.hass = hass

    with patch(
        "custom_components.span_panel.repairs.async_fetch_panel_ca",
        new=AsyncMock(return_value=panel.ca_pem),
    ):
        shown = await flow.async_step_init()
        assert shown["type"] == "form"
        assert shown["description_placeholders"] is not None
        assert shown["description_placeholders"]["fingerprint"]

        with patch.object(hass.config_entries, "async_reload", new=AsyncMock()) as reload:
            done = await flow.async_step_confirm({})

    assert done["type"] == "create_entry"
    assert entry.data[CONF_PANEL_CA_PEM] == panel.ca_pem
    reload.assert_awaited_once_with(entry.entry_id)
