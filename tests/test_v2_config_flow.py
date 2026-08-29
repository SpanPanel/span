"""Tests for v2 eBus config flow changes."""

from __future__ import annotations

from collections.abc import Callable
import ipaddress
import logging
import ssl
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant import config_entries
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.hassio import HassioServiceInfo
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from span_panel_api import DetectionResult, V2AuthResponse, V2StatusInfo
from span_panel_api.exceptions import SpanPanelAuthError, SpanPanelConnectionError

from custom_components.span_panel import (
    CURRENT_CONFIG_VERSION,
    async_migrate_entry,
)
from custom_components.span_panel.config_flow import (
    SpanPanelConfigFlow,
    TriggerFlowType,
)
from custom_components.span_panel.config_flow_validation import PanelRestTransport
from custom_components.span_panel.const import (
    CONF_API_VERSION,
    CONF_EBUS_BROKER_HOST,
    CONF_EBUS_BROKER_PASSWORD,
    CONF_EBUS_BROKER_PORT,
    CONF_EBUS_BROKER_USERNAME,
    CONF_HOP_PASSPHRASE,
    CONF_HTTP_PORT,
    CONF_HTTPS_PORT,
    CONF_PANEL_CA_PEM,
    CONF_PANEL_SERIAL,
    CONF_REGISTERED_FQDN,
    DOMAIN,
    PANEL_CA_PENDING,
)

# Shared mock detection for a different panel (used in reconfigure/duplicate tests)
MOCK_V2_DETECTION_OTHER = DetectionResult(
    api_version="v2",
    status_info=V2StatusInfo(
        serial_number="SPAN-V2-OTHER",
        firmware_version="2.0.0",
    ),
)

# ---------- helpers ----------

FAKE_CA_PEM = "-----BEGIN CERTIFICATE-----\nZmFrZQ==\n-----END CERTIFICATE-----\n"
FAKE_CA_FINGERPRINT = "0" * 64

#: An anchor already stored on an entry that `ssl` will not load. Named apart
#: from `FAKE_CA_PEM` — which happens to be unreadable too, hence the autouse
#: fixture — because a test about an unusable pin should say which property of
#: the PEM it is about. `build_panel_ssl_context` is left unpatched wherever
#: this is used, so the refusal comes from the real one.
UNREADABLE_CA_PEM = "-----BEGIN CERTIFICATE-----\nbm90LWEtY2VydA==\n-----END CERTIFICATE-----\n"


@pytest.fixture(autouse=True)
def panel_ca_available():
    """Answer the CA step without touching the network.

    Autouse because every flow that reaches entry creation now passes through
    it, and a test that did not patch it would try to open a socket.
    """
    with (
        patch(
            "custom_components.span_panel.config_flow.async_fetch_panel_ca",
            new=AsyncMock(return_value=FAKE_CA_PEM),
        ),
        patch(
            "custom_components.span_panel.config_flow.async_leaf_chains_to_ca",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "custom_components.span_panel.config_flow.ca_fingerprint",
            return_value=FAKE_CA_FINGERPRINT,
        ),
        # The fake PEM is not a certificate the ssl module will load, and the
        # accepted anchor is turned into a real context before authentication
        # runs over it.
        patch(
            "custom_components.span_panel.config_flow.build_panel_ssl_context",
            return_value=MagicMock(),
        ),
        # Every panel here is reached at the host it was given: no DNS (the
        # harness refuses it outright) and no address substituted for a name,
        # which is what every assertion in this module was written against. The
        # resolved-address bootstrap is exercised for real against a live
        # listener in `test_v2_config_flow_tls.py`.
        patch(
            "custom_components.span_panel.config_flow.async_panel_leaf_host",
            new=AsyncMock(side_effect=lambda _hass, host, _port, _pem: host),
        ),
    ):
        yield


async def _submit_host_and_pin(hass: HomeAssistant, flow_id: str, data: dict):
    """Submit the host form and accept the CA the panel serves.

    The CA step sits between the host form and the authentication menu, because
    registration is the exchange that carries the passphrase and it runs over the
    pin. Tests that are not about the CA itself elide it here rather than
    repeating the same two lines twenty times.
    """
    result = await hass.config_entries.flow.async_configure(flow_id, data)
    assert result["step_id"] == "choose_v2_auth", result["step_id"]
    return result


MOCK_HOST = "192.168.1.100"
MOCK_PASSPHRASE = "correct-horse-battery-staple"

MOCK_V2_DETECTION = DetectionResult(
    api_version="v2",
    status_info=V2StatusInfo(
        serial_number="SPAN-V2-001",
        firmware_version="2.0.0",
    ),
)

MOCK_V2_DETECTION_PROXIMITY_PROVEN = DetectionResult(
    api_version="v2",
    status_info=V2StatusInfo(
        serial_number="SPAN-V2-001",
        firmware_version="2.0.0",
        proximity_proven=True,
    ),
)

MOCK_V2_DETECTION_PROXIMITY_NOT_PROVEN = DetectionResult(
    api_version="v2",
    status_info=V2StatusInfo(
        serial_number="SPAN-V2-001",
        firmware_version="2.0.0",
        proximity_proven=False,
    ),
)

MOCK_V1_DETECTION = DetectionResult(
    api_version="v1",
    status_info=None,
)

MOCK_V2_AUTH = V2AuthResponse(
    access_token="v2-token-abc",
    token_type="bearer",
    iat_ms=1700000000000,
    ebus_broker_host="192.168.1.100",
    ebus_broker_mqtts_port=8883,
    ebus_broker_ws_port=8080,
    ebus_broker_wss_port=8443,
    ebus_broker_username="span-user",
    ebus_broker_password="mqtt-secret",
    hostname="span-panel.local",
    serial_number="SPAN-V2-001",
    hop_passphrase=MOCK_PASSPHRASE,
)


# ---------- v2 detection routing ----------


@pytest.mark.asyncio
async def test_user_flow_detects_v2_and_shows_auth_choice(hass: HomeAssistant) -> None:
    """When detect_api_version returns v2, the user flow should show the auth choice menu."""
    with (
        patch(
            "custom_components.span_panel.config_flow.detect_api_version",
            return_value=MOCK_V2_DETECTION,
        ),
        patch(
            "custom_components.span_panel.config_flow.validate_host",
            return_value=True,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"

        result2 = await _submit_host_and_pin(hass, result["flow_id"], {CONF_HOST: MOCK_HOST})

        assert result2["type"] == FlowResultType.MENU
        assert result2["step_id"] == "choose_v2_auth"
        assert "auth_passphrase" in result2["menu_options"]
        assert "auth_proximity" in result2["menu_options"]


@pytest.mark.asyncio
async def test_user_flow_passes_ha_httpx_client_to_detect_api_version(
    hass: HomeAssistant,
) -> None:
    """User flow should pass the Home Assistant shared httpx client to detection."""
    fake_client = MagicMock()
    with (
        patch(
            "custom_components.span_panel.config_flow.get_async_client",
            return_value=fake_client,
        ) as mock_get_client,
        patch(
            "custom_components.span_panel.config_flow.detect_api_version",
            return_value=MOCK_V2_DETECTION,
        ) as mock_detect,
        patch(
            "custom_components.span_panel.config_flow.validate_host",
            return_value=True,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: MOCK_HOST},
        )

    mock_get_client.assert_called_once_with(hass, verify_ssl=False)
    mock_detect.assert_awaited_once_with(MOCK_HOST, port=80, httpx_client=fake_client)


@pytest.mark.asyncio
async def test_user_flow_v1_aborts(hass: HomeAssistant) -> None:
    """When detect_api_version returns v1, the user flow should abort (v1 not supported)."""
    with (
        patch(
            "custom_components.span_panel.config_flow.detect_api_version",
            return_value=MOCK_V1_DETECTION,
        ),
        patch(
            "custom_components.span_panel.config_flow.validate_host",
            return_value=True,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )

        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: MOCK_HOST},
        )

        # Non-v2 panels are not supported and should abort
        assert result2["type"] in (FlowResultType.FORM, FlowResultType.ABORT)


# ---------- passphrase auth ----------


@pytest.mark.asyncio
async def test_passphrase_auth_success(hass: HomeAssistant) -> None:
    """Successful passphrase auth should proceed to naming step."""
    with (
        patch(
            "custom_components.span_panel.config_flow.detect_api_version",
            return_value=MOCK_V2_DETECTION,
        ),
        patch(
            "custom_components.span_panel.config_flow.validate_host",
            return_value=True,
        ),
        patch(
            "custom_components.span_panel.config_flow.validate_v2_passphrase",
            return_value=MOCK_V2_AUTH,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )

        result2 = await _submit_host_and_pin(hass, result["flow_id"], {CONF_HOST: MOCK_HOST})
        assert result2["step_id"] == "choose_v2_auth"

        # Select passphrase auth from the menu
        result2b = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {"next_step_id": "auth_passphrase"},
        )
        assert result2b["step_id"] == "auth_passphrase"

        result3 = await hass.config_entries.flow.async_configure(
            result2b["flow_id"],
            {CONF_HOP_PASSPHRASE: MOCK_PASSPHRASE},
        )

        assert result3["type"] == FlowResultType.FORM
        assert result3["step_id"] == "choose_entity_naming_initial"


@pytest.mark.asyncio
async def test_passphrase_auth_bad_passphrase(hass: HomeAssistant) -> None:
    """Bad passphrase should re-show the form with invalid_auth error."""
    with (
        patch(
            "custom_components.span_panel.config_flow.detect_api_version",
            return_value=MOCK_V2_DETECTION,
        ),
        patch(
            "custom_components.span_panel.config_flow.validate_host",
            return_value=True,
        ),
        patch(
            "custom_components.span_panel.config_flow.validate_v2_passphrase",
            side_effect=SpanPanelAuthError("Invalid passphrase"),
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )

        result2 = await _submit_host_and_pin(hass, result["flow_id"], {CONF_HOST: MOCK_HOST})

        # Select passphrase auth from the menu
        result2b = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {"next_step_id": "auth_passphrase"},
        )

        result3 = await hass.config_entries.flow.async_configure(
            result2b["flow_id"],
            {CONF_HOP_PASSPHRASE: "wrong-passphrase"},
        )

        assert result3["type"] == FlowResultType.FORM
        assert result3["step_id"] == "auth_passphrase"
        assert result3["errors"] == {"base": "invalid_auth"}


@pytest.mark.asyncio
async def test_passphrase_auth_connection_error(hass: HomeAssistant) -> None:
    """Connection error should re-show form with cannot_connect."""
    with (
        patch(
            "custom_components.span_panel.config_flow.detect_api_version",
            return_value=MOCK_V2_DETECTION,
        ),
        patch(
            "custom_components.span_panel.config_flow.validate_host",
            return_value=True,
        ),
        patch(
            "custom_components.span_panel.config_flow.validate_v2_passphrase",
            side_effect=SpanPanelConnectionError("timeout"),
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )

        result2 = await _submit_host_and_pin(hass, result["flow_id"], {CONF_HOST: MOCK_HOST})

        # Select passphrase auth from the menu
        result2b = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {"next_step_id": "auth_passphrase"},
        )

        result3 = await hass.config_entries.flow.async_configure(
            result2b["flow_id"],
            {CONF_HOP_PASSPHRASE: MOCK_PASSPHRASE},
        )

        assert result3["type"] == FlowResultType.FORM
        assert result3["step_id"] == "auth_passphrase"
        assert result3["errors"] == {"base": "cannot_connect"}


# ---------- v2 entry creation ----------


@pytest.mark.usefixtures("socket_enabled")
@pytest.mark.asyncio
async def test_v2_entry_contains_mqtt_credentials(hass: HomeAssistant) -> None:
    """A completed v2 flow should create an entry with MQTT broker fields."""
    with (
        patch(
            "custom_components.span_panel.config_flow.detect_api_version",
            return_value=MOCK_V2_DETECTION,
        ),
        patch(
            "custom_components.span_panel.config_flow.validate_host",
            return_value=True,
        ),
        patch(
            "custom_components.span_panel.config_flow.validate_v2_passphrase",
            return_value=MOCK_V2_AUTH,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )

        # Step 1: submit host
        result2 = await _submit_host_and_pin(hass, result["flow_id"], {CONF_HOST: MOCK_HOST})

        # Step 2: choose auth method (passphrase)
        result2b = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {"next_step_id": "auth_passphrase"},
        )

        # Step 3: submit passphrase
        result3 = await hass.config_entries.flow.async_configure(
            result2b["flow_id"],
            {CONF_HOP_PASSPHRASE: MOCK_PASSPHRASE},
        )

        # Step 4: choose entity naming pattern (accept default)
        result4 = await hass.config_entries.flow.async_configure(
            result3["flow_id"],
            {"entity_naming_pattern": "friendly_names"},
        )

        assert result4["type"] == FlowResultType.CREATE_ENTRY
        data = result4["data"]
        assert data[CONF_API_VERSION] == "v2"
        assert data[CONF_HOST] == MOCK_HOST
        assert data[CONF_ACCESS_TOKEN] == "v2-token-abc"
        assert data[CONF_EBUS_BROKER_HOST] == "192.168.1.100"
        assert data[CONF_EBUS_BROKER_PORT] == 8883
        assert data[CONF_EBUS_BROKER_USERNAME] == "span-user"
        assert data[CONF_EBUS_BROKER_PASSWORD] == "mqtt-secret"
        # The passphrase is a registration input, never entry data.
        assert CONF_HOP_PASSPHRASE not in data
        assert data[CONF_PANEL_SERIAL] == "SPAN-V2-001"


async def _reach_the_ca_step(hass: HomeAssistant):
    """Drive a user flow as far as the CA step and return that result.

    That is only two steps now: the CA is fetched before authentication, so the
    host form leads straight into it.
    """
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    return await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_HOST: MOCK_HOST})


@pytest.mark.asyncio
async def test_the_ca_is_pinned_before_the_passphrase_is_ever_sent(
    hass: HomeAssistant,
) -> None:
    """Registration carries the passphrase and returns both credentials.

    It is the one exchange most worth protecting, so the anchor has to be
    accepted before the authentication menu is even offered.
    """
    with (
        patch(
            "custom_components.span_panel.config_flow.detect_api_version",
            return_value=MOCK_V2_DETECTION,
        ),
        patch("custom_components.span_panel.config_flow.validate_host", return_value=True),
        patch(
            "custom_components.span_panel.config_flow.validate_v2_passphrase",
            return_value=MOCK_V2_AUTH,
        ) as register,
    ):
        menu = await _reach_the_ca_step(hass)

        # The CA step passes straight through on success, so the menu is the
        # first thing shown after the host form. What matters is not which
        # screen appears but that the panel has been asked nothing yet.
        assert menu["step_id"] == "choose_v2_auth"
        register.assert_not_called()

        picked = await hass.config_entries.flow.async_configure(
            menu["flow_id"], {"next_step_id": "auth_passphrase"}
        )
        await hass.config_entries.flow.async_configure(
            picked["flow_id"], {CONF_HOP_PASSPHRASE: MOCK_PASSPHRASE}
        )

    # And when it was, it went over the accepted anchor rather than plaintext.
    # The transport is the third positional argument and has no default, so
    # there is no shape of this call that sends the passphrase unpinned.
    transport = register.call_args.args[2]
    assert transport.ssl_context is not None
    assert transport.httpx_client is None
    assert transport.port == 443


@pytest.mark.asyncio
async def test_a_ca_that_does_not_sign_what_the_panel_serves_is_not_offered(
    hass: HomeAssistant,
) -> None:
    """The user is never shown a fingerprint that already failed validation."""
    with (
        patch(
            "custom_components.span_panel.config_flow.detect_api_version",
            return_value=MOCK_V2_DETECTION,
        ),
        patch("custom_components.span_panel.config_flow.validate_host", return_value=True),
        patch(
            "custom_components.span_panel.config_flow.async_panel_leaf_host",
            new=AsyncMock(return_value=None),
        ),
    ):
        result = await _reach_the_ca_step(hass)

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "panel_ca"
    assert result["errors"] == {"base": "ca_leaf_mismatch"}


@pytest.mark.asyncio
async def test_a_fetch_failure_is_a_flow_error_with_no_way_past(
    hass: HomeAssistant,
) -> None:
    """There is deliberately no "carry on unpinned" option here.

    The next thing this flow does is send the panel passphrase. An opt-out would
    quietly restore the plaintext credential exchange that pinning before
    registration exists to remove, at the moment a user is least likely to weigh
    it. Resubmitting the form retries the fetch, which is the recovery a
    transient network failure actually needs.
    """
    with (
        patch(
            "custom_components.span_panel.config_flow.detect_api_version",
            return_value=MOCK_V2_DETECTION,
        ),
        patch("custom_components.span_panel.config_flow.validate_host", return_value=True),
        patch(
            "custom_components.span_panel.config_flow.validate_v2_passphrase",
            return_value=MOCK_V2_AUTH,
        ) as register,
        patch(
            "custom_components.span_panel.config_flow.async_fetch_panel_ca",
            new=AsyncMock(side_effect=SpanPanelConnectionError("unreachable")),
        ) as fetch,
    ):
        failed = await _reach_the_ca_step(hass)
        assert failed["type"] == FlowResultType.FORM
        assert failed["step_id"] == "panel_ca"
        assert failed["errors"] == {"base": "ca_unavailable"}
        # No menu, so no option that leads anywhere but back through the fetch.
        assert "menu_options" not in failed

        # Resubmitting retries rather than continuing.
        retried = await hass.config_entries.flow.async_configure(failed["flow_id"], {})
        assert retried["step_id"] == "panel_ca"
        assert fetch.await_count == 2

    # The panel was never asked to register anything.
    register.assert_not_called()


@pytest.mark.asyncio
async def test_a_recovered_panel_can_be_retried_into_a_successful_pin(
    hass: HomeAssistant,
) -> None:
    """The error form is a retry, so a panel that comes back needs no restart."""
    with (
        patch(
            "custom_components.span_panel.config_flow.detect_api_version",
            return_value=MOCK_V2_DETECTION,
        ),
        patch("custom_components.span_panel.config_flow.validate_host", return_value=True),
        patch(
            "custom_components.span_panel.config_flow.async_fetch_panel_ca",
            new=AsyncMock(side_effect=[SpanPanelConnectionError("unreachable"), FAKE_CA_PEM]),
        ),
    ):
        failed = await _reach_the_ca_step(hass)
        assert failed["errors"] == {"base": "ca_unavailable"}

        recovered = await hass.config_entries.flow.async_configure(failed["flow_id"], {})

    assert recovered["step_id"] == "choose_v2_auth"


# ---------- config entry migration (2.0.4 baseline) ----------


@pytest.mark.asyncio
async def test_config_flow_uses_current_config_entry_version() -> None:
    """New core entries should use the current storage version."""

    assert SpanPanelConfigFlow.VERSION == CURRENT_CONFIG_VERSION


@pytest.mark.asyncio
async def test_migration_updates_older_entry_to_current_version(
    hass: HomeAssistant,
) -> None:
    """v1.3.1 entries (version 2) should migrate through to the current version."""
    entry = MockConfigEntry(
        version=2,
        minor_version=1,
        domain=DOMAIN,
        title="Span Panel",
        data={
            CONF_HOST: "192.168.1.50",
            CONF_ACCESS_TOKEN: "old-token",
        },
        source=config_entries.SOURCE_USER,
        options={},
        unique_id="SN-LIVE-001",
    )
    entry.add_to_hass(hass)

    result = await async_migrate_entry(hass, entry)

    assert result is True
    assert entry.version == CURRENT_CONFIG_VERSION
    # v2→v3 migration adds api_version field
    assert entry.data[CONF_API_VERSION] == "v1"


@pytest.mark.asyncio
async def test_simulation_entry_migrates_normally(hass: HomeAssistant) -> None:
    """Simulation entries migrate forward; setup will fail naturally at connection time."""
    entry = MockConfigEntry(
        version=5,
        minor_version=1,
        domain=DOMAIN,
        title="Span Simulator",
        data={
            CONF_HOST: "sim-001",
            CONF_ACCESS_TOKEN: "simulator_token",
            CONF_API_VERSION: "simulation",
            "simulation_mode": True,
        },
        source=config_entries.SOURCE_USER,
        options={},
        unique_id="SIM-001",
    )
    entry.add_to_hass(hass)

    result = await async_migrate_entry(hass, entry)
    assert result is True
    assert entry.version == CURRENT_CONFIG_VERSION


@pytest.mark.asyncio
async def test_v6_migration_drops_the_stored_passphrase(hass: HomeAssistant) -> None:
    """v6 entries lose the persisted passphrase and keep everything else."""
    entry = MockConfigEntry(
        version=6,
        minor_version=1,
        domain=DOMAIN,
        title="Span Panel",
        data={
            CONF_HOST: MOCK_HOST,
            CONF_ACCESS_TOKEN: "v2-token-abc",
            CONF_API_VERSION: "v2",
            CONF_EBUS_BROKER_HOST: MOCK_HOST,
            CONF_EBUS_BROKER_PORT: 8883,
            CONF_EBUS_BROKER_USERNAME: "span-user",
            CONF_EBUS_BROKER_PASSWORD: "mqtt-secret",
            CONF_HOP_PASSPHRASE: MOCK_PASSPHRASE,
            CONF_PANEL_SERIAL: "SPAN-V2-001",
        },
        source=config_entries.SOURCE_USER,
        options={},
        unique_id="SPAN-V2-001",
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry) is True

    assert entry.version == 7
    assert CONF_HOP_PASSPHRASE not in entry.data
    assert entry.data[CONF_ACCESS_TOKEN] == "v2-token-abc"
    assert entry.data[CONF_EBUS_BROKER_PASSWORD] == "mqtt-secret"


@pytest.mark.asyncio
async def test_home_assistant_actually_runs_the_v7_migration(hass: HomeAssistant) -> None:
    """Core must decide to migrate a v6 entry.

    `CURRENT_CONFIG_VERSION` drives the migration body, but it is
    `SpanPanelConfigFlow.VERSION` that core compares against `entry.version` to
    decide whether to call `async_migrate_entry` at all. Calling the migration
    directly cannot catch the two drifting apart, so this goes through
    `async_setup` with the integration's own entry setup stubbed out.
    """
    entry = MockConfigEntry(
        version=6,
        minor_version=1,
        domain=DOMAIN,
        title="Span Panel",
        data={
            CONF_HOST: MOCK_HOST,
            CONF_ACCESS_TOKEN: "v2-token-abc",
            CONF_API_VERSION: "v2",
            CONF_HOP_PASSPHRASE: MOCK_PASSPHRASE,
        },
        source=config_entries.SOURCE_USER,
        options={},
        unique_id="SPAN-V2-001",
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.span_panel.async_setup_entry",
        AsyncMock(return_value=True),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id) is True

    assert entry.version == CURRENT_CONFIG_VERSION
    assert CONF_HOP_PASSPHRASE not in entry.data


# ---------- zeroconf v2 discovery ----------


@pytest.mark.asyncio
async def test_zeroconf_ebus_discovery_routes_to_confirm(hass: HomeAssistant) -> None:
    """Discovering an _ebus._tcp.local. service should set api_version=v2 and show confirm."""

    discovery_info = ZeroconfServiceInfo(
        ip_address=ipaddress.IPv4Address("192.168.1.200"),
        ip_addresses=[ipaddress.IPv4Address("192.168.1.200")],
        hostname="span-panel.local.",
        name="SPAN Panel._ebus._tcp.local.",
        port=8883,
        properties={},
        type="_ebus._tcp.local.",
    )

    with (
        patch(
            "custom_components.span_panel.config_flow.detect_api_version",
            return_value=MOCK_V2_DETECTION,
        ),
        patch(
            "custom_components.span_panel.config_flow.is_ipv4_address",
            return_value=True,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=discovery_info,
        )

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "confirm_discovery"


# ---------- reauth ----------


@pytest.mark.asyncio
async def test_reauth_v2_shows_auth_choice(hass: HomeAssistant) -> None:
    """Reauth for a v2 panel should show the auth choice menu."""
    entry = MockConfigEntry(
        version=3,
        minor_version=1,
        domain=DOMAIN,
        title="Span Panel",
        data={
            CONF_HOST: MOCK_HOST,
            CONF_ACCESS_TOKEN: "old-v2-token",
            CONF_API_VERSION: "v2",
        },
        source=config_entries.SOURCE_USER,
        options={},
        unique_id="SPAN-V2-001",
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.span_panel.config_flow.detect_api_version",
        return_value=MOCK_V2_DETECTION,
    ):
        result = await entry.start_reauth_flow(hass)

        assert result["type"] == FlowResultType.MENU
        assert result["step_id"] == "reauth_confirm"


@pytest.mark.asyncio
async def test_reauth_aborts_cannot_connect_when_probe_failed(
    hass: HomeAssistant,
) -> None:
    """Reauth must abort with cannot_connect when detection probe fails."""
    entry = MockConfigEntry(
        version=3,
        minor_version=1,
        domain=DOMAIN,
        title="Span Panel",
        data={
            CONF_HOST: MOCK_HOST,
            CONF_ACCESS_TOKEN: "old-v2-token",
            CONF_API_VERSION: "v2",
        },
        source=config_entries.SOURCE_USER,
        options={},
        unique_id="SPAN-V2-001",
    )
    entry.add_to_hass(hass)

    probe_failed = DetectionResult(
        api_version="v1",
        status_info=None,
        probe_failed=True,
    )
    with patch(
        "custom_components.span_panel.config_flow.detect_api_version",
        return_value=probe_failed,
    ):
        result = await entry.start_reauth_flow(hass)

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


@pytest.mark.asyncio
async def test_reauth_v2_success_updates_entry(hass: HomeAssistant) -> None:
    """Successful v2 reauth should update the config entry with new MQTT creds."""
    entry = MockConfigEntry(
        version=3,
        minor_version=1,
        domain=DOMAIN,
        title="Span Panel",
        data={
            CONF_HOST: MOCK_HOST,
            CONF_ACCESS_TOKEN: "old-v2-token",
            CONF_API_VERSION: "v2",
            CONF_EBUS_BROKER_HOST: "old-host",
            CONF_EBUS_BROKER_PORT: 8883,
            CONF_EBUS_BROKER_USERNAME: "old-user",
            CONF_EBUS_BROKER_PASSWORD: "old-pass",
        },
        source=config_entries.SOURCE_USER,
        options={},
        unique_id="SPAN-V2-001",
    )
    entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.span_panel.config_flow.detect_api_version",
            return_value=MOCK_V2_DETECTION,
        ),
        patch(
            "custom_components.span_panel.config_flow.validate_v2_passphrase",
            return_value=MOCK_V2_AUTH,
        ),
        patch.object(hass.config_entries, "async_reload", return_value=True),
    ):
        result = await entry.start_reauth_flow(hass)
        assert result["type"] == FlowResultType.MENU
        assert result["step_id"] == "reauth_confirm"

        # Select passphrase auth from the reauth menu
        result1 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"next_step_id": "auth_passphrase"},
        )
        assert result1["step_id"] == "auth_passphrase"

        result2 = await hass.config_entries.flow.async_configure(
            result1["flow_id"],
            {CONF_HOP_PASSPHRASE: MOCK_PASSPHRASE},
        )

        assert result2["type"] == FlowResultType.ABORT
        assert result2["reason"] == "reauth_successful"

    assert entry.data[CONF_ACCESS_TOKEN] == "v2-token-abc"
    assert entry.data[CONF_EBUS_BROKER_USERNAME] == "span-user"
    assert entry.data[CONF_EBUS_BROKER_PASSWORD] == "mqtt-secret"
    # This entry had no anchor. Reauth acquires one before the passphrase goes
    # out and keeps it, so the entry comes back pinned.
    assert entry.data[CONF_PANEL_CA_PEM] == FAKE_CA_PEM


# ---------- reauth: the CA an unpinned entry has never had ----------

# The three ways an entry reaches reauth with nothing pinned. (a) a v1 entry:
# setup raises ConfigEntryAuthFailed before the deferred fetch can run, and the
# v7 migration flags only v2 entries, so it carries neither a CA nor the flag.
# (b) a v2 entry missing its broker credentials: setup raises before the fetch
# too, so the flag it was migrated with is never settled. (c) a v2 entry whose
# deferred fetch failed: the flag survives for the next setup, and reauth may
# well come first.
UNPINNED_REAUTH_POPULATIONS = [
    pytest.param({CONF_API_VERSION: "v1"}, id="v1_entry"),
    pytest.param(
        {CONF_API_VERSION: "v2", PANEL_CA_PENDING: True},
        id="missing_credentials",
    ),
    pytest.param(
        {
            CONF_API_VERSION: "v2",
            PANEL_CA_PENDING: True,
            CONF_EBUS_BROKER_HOST: "old-host",
            CONF_EBUS_BROKER_PORT: 8883,
            CONF_EBUS_BROKER_USERNAME: "old-user",
            CONF_EBUS_BROKER_PASSWORD: "old-pass",
        },
        id="failed_deferred_fetch",
    ),
]


def _unpinned_entry(hass: HomeAssistant, extra_data: dict[str, object]) -> MockConfigEntry:
    """Add an entry that carries no pinned CA."""
    entry = MockConfigEntry(
        version=CURRENT_CONFIG_VERSION,
        minor_version=1,
        domain=DOMAIN,
        title="Span Panel",
        data={
            CONF_HOST: MOCK_HOST,
            CONF_ACCESS_TOKEN: "old-token",
            **extra_data,
        },
        source=config_entries.SOURCE_USER,
        options={},
        unique_id="SPAN-V2-001",
    )
    entry.add_to_hass(hass)
    assert CONF_PANEL_CA_PEM not in entry.data
    return entry


@pytest.mark.parametrize("extra_data", UNPINNED_REAUTH_POPULATIONS)
@pytest.mark.asyncio
async def test_reauth_of_an_unpinned_entry_pins_before_the_passphrase_is_sent(
    hass: HomeAssistant, extra_data: dict[str, object]
) -> None:
    """Reauth is where fresh credentials cross the wire; it must not cross in the clear."""
    entry = _unpinned_entry(hass, extra_data)

    order: list[str] = []
    captured: dict[str, object] = {}

    async def _fetch_ca(*_args: object, **_kwargs: object) -> str:
        order.append("fetch_ca")
        return FAKE_CA_PEM

    async def _register(*_args: object, **kwargs: object) -> V2AuthResponse:
        order.append("register_v2")
        captured.update(kwargs)
        return MOCK_V2_AUTH

    with (
        patch(
            "custom_components.span_panel.config_flow.detect_api_version",
            return_value=MOCK_V2_DETECTION,
        ),
        patch(
            "custom_components.span_panel.config_flow.async_fetch_panel_ca",
            new=_fetch_ca,
        ),
        # The boundary the flow actually reaches: `validate_v2_passphrase` is
        # real here, so what it hands the library is what the panel would see.
        patch(
            "custom_components.span_panel.config_flow_validation.register_v2",
            new=_register,
        ),
        patch.object(hass.config_entries, "async_reload", return_value=True),
    ):
        result = await entry.start_reauth_flow(hass)
        assert result["type"] == FlowResultType.MENU
        assert result["step_id"] == "reauth_confirm"

        result1 = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "auth_passphrase"}
        )
        assert result1["step_id"] == "auth_passphrase"

        result2 = await hass.config_entries.flow.async_configure(
            result1["flow_id"], {CONF_HOP_PASSPHRASE: MOCK_PASSPHRASE}
        )

    assert result2["type"] == FlowResultType.ABORT
    assert result2["reason"] == "reauth_successful"

    # Registration went over a verified connection, and the anchor it used was
    # acquired first rather than after the fact.
    assert captured["ssl_context"] is not None
    assert captured["httpx_client"] is None
    assert order == ["fetch_ca", "register_v2"]

    # And the anchor survives the reauth, so the next connect is pinned too.
    assert entry.data[CONF_PANEL_CA_PEM] == FAKE_CA_PEM
    assert PANEL_CA_PENDING not in entry.data
    assert entry.data[CONF_API_VERSION] == "v2"


@pytest.mark.asyncio
async def test_replacing_an_unusable_stored_anchor_says_what_it_is_now_pinned_to(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A stored PEM this system cannot load is replaced, and that is worth a line.

    Silently swapping what an entry trusts is the one change a user needs a
    record of, and the new fingerprint is the value to compare against the one
    logged at install — the only trace of it once the old PEM is gone.
    """
    entry = MockConfigEntry(
        version=CURRENT_CONFIG_VERSION,
        minor_version=1,
        domain=DOMAIN,
        title="Span Panel",
        data={
            CONF_HOST: MOCK_HOST,
            CONF_ACCESS_TOKEN: "old-token",
            CONF_API_VERSION: "v2",
            CONF_PANEL_CA_PEM: "not-a-certificate",
        },
        source=config_entries.SOURCE_USER,
        options={},
        unique_id="SPAN-V2-001",
    )
    entry.add_to_hass(hass)

    def _build(pem: str, *, check_hostname: bool = True) -> MagicMock:
        # `check_hostname` is accepted and ignored: an anchor that will not load
        # fails the same way whichever question the caller was going to ask of it.
        if pem == "not-a-certificate":
            raise ssl.SSLError("not a certificate")
        return MagicMock()

    with (
        patch(
            "custom_components.span_panel.config_flow.detect_api_version",
            return_value=MOCK_V2_DETECTION,
        ),
        patch(
            "custom_components.span_panel.config_flow_validation.build_panel_ssl_context",
            side_effect=_build,
        ),
        patch(
            "custom_components.span_panel.config_flow.validate_v2_passphrase",
            return_value=MOCK_V2_AUTH,
        ),
        patch.object(hass.config_entries, "async_reload", return_value=True),
        caplog.at_level(logging.WARNING),
    ):
        menu = await entry.start_reauth_flow(hass)
        form = await hass.config_entries.flow.async_configure(
            menu["flow_id"], {"next_step_id": "auth_passphrase"}
        )
        done = await hass.config_entries.flow.async_configure(
            form["flow_id"], {CONF_HOP_PASSPHRASE: MOCK_PASSPHRASE}
        )

    assert done["reason"] == "reauth_successful"
    assert entry.data[CONF_PANEL_CA_PEM] == FAKE_CA_PEM
    assert "Replaced the stored certificate authority" in caplog.text
    assert FAKE_CA_FINGERPRINT in caplog.text


@pytest.mark.asyncio
async def test_reauth_refuses_to_send_the_passphrase_when_the_leaf_does_not_chain(
    hass: HomeAssistant,
) -> None:
    """A CA that cannot validate what the panel serves is no reason to fall back."""
    entry = _unpinned_entry(hass, {CONF_API_VERSION: "v2"})

    register = AsyncMock(return_value=MOCK_V2_AUTH)

    with (
        patch(
            "custom_components.span_panel.config_flow.detect_api_version",
            return_value=MOCK_V2_DETECTION,
        ),
        patch(
            "custom_components.span_panel.config_flow.async_panel_leaf_host",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "custom_components.span_panel.config_flow_validation.register_v2",
            new=register,
        ),
    ):
        result = await entry.start_reauth_flow(hass)

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "panel_ca"
    assert result["errors"] == {"base": "ca_leaf_mismatch"}
    register.assert_not_awaited()
    assert CONF_PANEL_CA_PEM not in entry.data


@pytest.mark.asyncio
async def test_reauth_by_proximity_also_registers_over_the_pin(
    hass: HomeAssistant,
) -> None:
    """The door bypass sends no passphrase but is handed the same broker password back."""
    entry = _unpinned_entry(hass, {CONF_API_VERSION: "v2"})

    captured: dict[str, object] = {}

    async def _register(*_args: object, **kwargs: object) -> V2AuthResponse:
        captured.update(kwargs)
        return MOCK_V2_AUTH

    with (
        patch(
            "custom_components.span_panel.config_flow.detect_api_version",
            return_value=MOCK_V2_DETECTION,
        ),
        patch(
            "custom_components.span_panel.config_flow_validation.register_v2",
            new=_register,
        ),
        patch.object(hass.config_entries, "async_reload", return_value=True),
    ):
        result = await entry.start_reauth_flow(hass)
        assert result["step_id"] == "reauth_confirm"

        proximity = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "auth_proximity"}
        )
        done = await hass.config_entries.flow.async_configure(
            proximity["flow_id"], {"next_step_id": "auth_proximity_confirm"}
        )

    assert done["reason"] == "reauth_successful"
    assert captured["ssl_context"] is not None
    assert captured["httpx_client"] is None
    assert entry.data[CONF_PANEL_CA_PEM] == FAKE_CA_PEM


@pytest.mark.asyncio
async def test_reauth_of_a_pinned_entry_does_not_go_back_for_a_ca(
    hass: HomeAssistant,
) -> None:
    """An entry with a usable anchor already has the protection; leave it alone."""
    entry = MockConfigEntry(
        version=CURRENT_CONFIG_VERSION,
        minor_version=1,
        domain=DOMAIN,
        title="Span Panel",
        data={
            CONF_HOST: MOCK_HOST,
            CONF_ACCESS_TOKEN: "old-token",
            CONF_API_VERSION: "v2",
            CONF_PANEL_CA_PEM: FAKE_CA_PEM,
        },
        source=config_entries.SOURCE_USER,
        options={},
        unique_id="SPAN-V2-001",
    )
    entry.add_to_hass(hass)

    fetch = AsyncMock(return_value=FAKE_CA_PEM)

    with (
        patch(
            "custom_components.span_panel.config_flow.detect_api_version",
            return_value=MOCK_V2_DETECTION,
        ),
        patch("custom_components.span_panel.config_flow.async_fetch_panel_ca", new=fetch),
        patch(
            "custom_components.span_panel.config_flow_validation.build_panel_ssl_context",
            return_value=MagicMock(),
        ),
    ):
        result = await entry.start_reauth_flow(hass)

    assert result["step_id"] == "reauth_confirm"
    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_full_reauth_leaves_a_pinned_entry_pinned_to_the_same_thing(
    hass: HomeAssistant,
) -> None:
    """Reauth replaces credentials. It must not quietly move the trust anchor.

    The anchor and the port it was checked against are what a user compared a
    fingerprint to and what a CA-changed repair compares against next time. A
    reauth that rewrote either would make the repair fire on a panel that never
    rotated anything, or hide one that did.
    """
    entry = MockConfigEntry(
        version=CURRENT_CONFIG_VERSION,
        minor_version=1,
        domain=DOMAIN,
        title="Span Panel",
        data={
            CONF_HOST: MOCK_HOST,
            CONF_ACCESS_TOKEN: "old-token",
            CONF_API_VERSION: "v2",
            CONF_EBUS_BROKER_HOST: MOCK_HOST,
            CONF_EBUS_BROKER_PORT: 8883,
            CONF_EBUS_BROKER_USERNAME: "old-user",
            CONF_EBUS_BROKER_PASSWORD: "old-secret",
            CONF_PANEL_CA_PEM: FAKE_CA_PEM,
            CONF_HTTPS_PORT: 9443,
        },
        source=config_entries.SOURCE_USER,
        options={},
        unique_id="SPAN-V2-001",
    )
    entry.add_to_hass(hass)

    fetch = AsyncMock(return_value="a-different-pem")

    with (
        patch(
            "custom_components.span_panel.config_flow.detect_api_version",
            return_value=MOCK_V2_DETECTION,
        ),
        patch("custom_components.span_panel.config_flow.async_fetch_panel_ca", new=fetch),
        patch(
            "custom_components.span_panel.config_flow_validation.build_panel_ssl_context",
            return_value=MagicMock(),
        ),
        patch(
            "custom_components.span_panel.config_flow.validate_v2_passphrase",
            return_value=MOCK_V2_AUTH,
        ) as register,
        patch.object(hass.config_entries, "async_reload", return_value=True),
    ):
        menu = await entry.start_reauth_flow(hass)
        form = await hass.config_entries.flow.async_configure(
            menu["flow_id"], {"next_step_id": "auth_passphrase"}
        )
        done = await hass.config_entries.flow.async_configure(
            form["flow_id"], {CONF_HOP_PASSPHRASE: MOCK_PASSPHRASE}
        )

    assert done["reason"] == "reauth_successful"
    # Credentials moved on; the anchor and its port did not.
    assert entry.data[CONF_ACCESS_TOKEN] == MOCK_V2_AUTH.access_token
    assert entry.data[CONF_EBUS_BROKER_PASSWORD] == MOCK_V2_AUTH.ebus_broker_password
    assert entry.data[CONF_PANEL_CA_PEM] == FAKE_CA_PEM
    assert entry.data[CONF_HTTPS_PORT] == 9443
    # Nothing was fetched to replace it with, and registration ran over the
    # stored pin on the stored port.
    fetch.assert_not_awaited()
    assert register.call_args.args[2].port == 9443
    assert register.call_args.args[2].ca_pem == FAKE_CA_PEM


@pytest.mark.asyncio
async def test_reauth_asks_where_a_moved_panel_serves_tls_and_keeps_the_answer(
    hass: HomeAssistant,
) -> None:
    """An entry behind a proxy has to say where the leaf is before it can be checked."""
    entry = _unpinned_entry(hass, {CONF_API_VERSION: "v2", CONF_HTTP_PORT: 8080})

    leaf_host = AsyncMock(side_effect=lambda _hass, host, _port, _pem: host)

    with (
        patch(
            "custom_components.span_panel.config_flow.detect_api_version",
            return_value=MOCK_V2_DETECTION,
        ),
        patch(
            "custom_components.span_panel.config_flow.async_panel_leaf_host",
            new=leaf_host,
        ),
        patch(
            "custom_components.span_panel.config_flow.validate_v2_passphrase",
            return_value=MOCK_V2_AUTH,
        ),
        patch.object(hass.config_entries, "async_reload", return_value=True),
    ):
        result = await entry.start_reauth_flow(hass)
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "panel_https_port"

        menu = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HTTPS_PORT: 9443}
        )
        assert menu["step_id"] == "reauth_confirm"

        form = await hass.config_entries.flow.async_configure(
            menu["flow_id"], {"next_step_id": "auth_passphrase"}
        )
        done = await hass.config_entries.flow.async_configure(
            form["flow_id"], {CONF_HOP_PASSPHRASE: MOCK_PASSPHRASE}
        )

    assert done["reason"] == "reauth_successful"
    # Checked on the port the user named, and stored so the pin keeps pointing there.
    assert leaf_host.await_args is not None
    assert leaf_host.await_args.args[2] == 9443
    assert entry.data[CONF_HTTPS_PORT] == 9443
    assert entry.data[CONF_PANEL_CA_PEM] == FAKE_CA_PEM


# ---------- user flow error paths ----------


@pytest.mark.asyncio
async def test_user_flow_empty_host(hass: HomeAssistant) -> None:
    """Submitting an empty host should re-show the form with host_required error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["step_id"] == "user"

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_HOST: ""},
    )

    assert result2["type"] == FlowResultType.FORM
    assert result2["step_id"] == "user"
    assert result2["errors"] == {"base": "host_required"}


@pytest.mark.asyncio
async def test_user_flow_host_unreachable(hass: HomeAssistant) -> None:
    """Unreachable host should re-show the form with cannot_connect error."""
    with patch(
        "custom_components.span_panel.config_flow.validate_host",
        return_value=False,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )

        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: "10.0.0.99"},
        )

        assert result2["type"] == FlowResultType.FORM
        assert result2["step_id"] == "user"
        assert result2["errors"] == {"base": "cannot_connect"}


@pytest.mark.asyncio
async def test_user_flow_cannot_connect_when_second_detection_probe_failed(
    hass: HomeAssistant,
) -> None:
    """Second detection with probe_failed must show cannot_connect, not v1_not_supported."""
    probe_failed = DetectionResult(
        api_version="v1",
        status_info=None,
        probe_failed=True,
    )
    with (
        patch(
            "custom_components.span_panel.config_flow.validate_host",
            return_value=True,
        ),
        patch(
            "custom_components.span_panel.config_flow.detect_api_version",
            return_value=probe_failed,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: MOCK_HOST},
        )

    assert result2["type"] == FlowResultType.FORM
    assert result2["step_id"] == "user"
    assert result2["errors"] == {"base": "cannot_connect"}


@pytest.mark.asyncio
async def test_user_flow_recovery_after_bad_host(hass: HomeAssistant) -> None:
    """User can complete setup after an initial host validation failure."""
    with (
        patch(
            "custom_components.span_panel.config_flow.validate_host",
            side_effect=[False, True],
        ),
        patch(
            "custom_components.span_panel.config_flow.detect_api_version",
            return_value=MOCK_V2_DETECTION,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )

        # First attempt fails
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: "bad-host"},
        )
        assert result2["errors"] == {"base": "cannot_connect"}

        # Second attempt succeeds
        result3 = await _submit_host_and_pin(hass, result2["flow_id"], {CONF_HOST: MOCK_HOST})
        assert result3["type"] == FlowResultType.MENU
        assert result3["step_id"] == "choose_v2_auth"


# ---------- passphrase auth: empty passphrase ----------


@pytest.mark.asyncio
async def test_passphrase_auth_empty_passphrase(hass: HomeAssistant) -> None:
    """Empty passphrase should re-show the form with invalid_auth error."""
    with (
        patch(
            "custom_components.span_panel.config_flow.detect_api_version",
            return_value=MOCK_V2_DETECTION,
        ),
        patch(
            "custom_components.span_panel.config_flow.validate_host",
            return_value=True,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )

        result2 = await _submit_host_and_pin(hass, result["flow_id"], {CONF_HOST: MOCK_HOST})

        result2b = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {"next_step_id": "auth_passphrase"},
        )

        result3 = await hass.config_entries.flow.async_configure(
            result2b["flow_id"],
            {CONF_HOP_PASSPHRASE: ""},
        )

        assert result3["type"] == FlowResultType.FORM
        assert result3["step_id"] == "auth_passphrase"
        assert result3["errors"] == {"base": "invalid_auth"}


@pytest.mark.asyncio
async def test_passphrase_auth_recovery_after_error(hass: HomeAssistant) -> None:
    """User can complete auth after an initial bad passphrase."""
    with (
        patch(
            "custom_components.span_panel.config_flow.detect_api_version",
            return_value=MOCK_V2_DETECTION,
        ),
        patch(
            "custom_components.span_panel.config_flow.validate_host",
            return_value=True,
        ),
        patch(
            "custom_components.span_panel.config_flow.validate_v2_passphrase",
            side_effect=[SpanPanelAuthError("bad"), MOCK_V2_AUTH],
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )

        result2 = await _submit_host_and_pin(hass, result["flow_id"], {CONF_HOST: MOCK_HOST})

        result2b = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {"next_step_id": "auth_passphrase"},
        )

        # First attempt: bad passphrase
        result3 = await hass.config_entries.flow.async_configure(
            result2b["flow_id"],
            {CONF_HOP_PASSPHRASE: "wrong"},
        )
        assert result3["errors"] == {"base": "invalid_auth"}

        # Second attempt: correct passphrase
        result4 = await hass.config_entries.flow.async_configure(
            result3["flow_id"],
            {CONF_HOP_PASSPHRASE: MOCK_PASSPHRASE},
        )
        assert result4["type"] == FlowResultType.FORM
        assert result4["step_id"] == "choose_entity_naming_initial"


# ---------- proximity auth ----------


@pytest.mark.asyncio
async def test_proximity_auth_success(hass: HomeAssistant) -> None:
    """Successful proximity auth should proceed to naming step."""
    with (
        patch(
            "custom_components.span_panel.config_flow.detect_api_version",
            side_effect=[MOCK_V2_DETECTION, MOCK_V2_DETECTION_PROXIMITY_PROVEN],
        ),
        patch(
            "custom_components.span_panel.config_flow.validate_host",
            return_value=True,
        ),
        patch(
            "custom_components.span_panel.config_flow.validate_v2_proximity",
            return_value=MOCK_V2_AUTH,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )

        result2 = await _submit_host_and_pin(hass, result["flow_id"], {CONF_HOST: MOCK_HOST})
        assert result2["step_id"] == "choose_v2_auth"

        result2b = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {"next_step_id": "auth_proximity"},
        )
        assert result2b["step_id"] == "auth_proximity"

        # User confirms they opened the door
        result3 = await hass.config_entries.flow.async_configure(
            result2b["flow_id"],
            {"next_step_id": "auth_proximity_confirm"},
        )

        assert result3["type"] == FlowResultType.FORM
        assert result3["step_id"] == "choose_entity_naming_initial"


@pytest.mark.asyncio
async def test_proximity_not_proven_returns_to_menu(hass: HomeAssistant) -> None:
    """Unproven proximity should return to the auth proximity menu."""
    with (
        patch(
            "custom_components.span_panel.config_flow.detect_api_version",
            side_effect=[MOCK_V2_DETECTION, MOCK_V2_DETECTION_PROXIMITY_NOT_PROVEN],
        ),
        patch(
            "custom_components.span_panel.config_flow.validate_host",
            return_value=True,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )

        result2 = await _submit_host_and_pin(hass, result["flow_id"], {CONF_HOST: MOCK_HOST})

        result2b = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {"next_step_id": "auth_proximity"},
        )

        # User claims they opened the door but proximityProven is false
        result3 = await hass.config_entries.flow.async_configure(
            result2b["flow_id"],
            {"next_step_id": "auth_proximity_confirm"},
        )

        # Should return to the proximity menu
        assert result3["type"] == FlowResultType.MENU
        assert result3["step_id"] == "auth_proximity"


@pytest.mark.asyncio
async def test_proximity_switch_to_passphrase(hass: HomeAssistant) -> None:
    """User should be able to switch from proximity menu to passphrase."""
    with (
        patch(
            "custom_components.span_panel.config_flow.detect_api_version",
            return_value=MOCK_V2_DETECTION,
        ),
        patch(
            "custom_components.span_panel.config_flow.validate_host",
            return_value=True,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )

        result2 = await _submit_host_and_pin(hass, result["flow_id"], {CONF_HOST: MOCK_HOST})

        result2b = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {"next_step_id": "auth_proximity"},
        )
        assert result2b["step_id"] == "auth_proximity"

        # User picks "Use passphrase instead"
        result3 = await hass.config_entries.flow.async_configure(
            result2b["flow_id"],
            {"next_step_id": "auth_passphrase"},
        )

        assert result3["type"] == FlowResultType.FORM
        assert result3["step_id"] == "auth_passphrase"


# ---------- duplicate entry prevention ----------


@pytest.mark.asyncio
async def test_duplicate_entry_aborts(hass: HomeAssistant) -> None:
    """Setting up a panel that is already configured should abort."""
    existing = MockConfigEntry(
        version=3,
        minor_version=1,
        domain=DOMAIN,
        title="Span Panel",
        data={
            CONF_HOST: MOCK_HOST,
            CONF_ACCESS_TOKEN: "existing-token",
            CONF_API_VERSION: "v2",
        },
        source=config_entries.SOURCE_USER,
        options={},
        unique_id="SPAN-V2-001",
    )
    existing.add_to_hass(hass)

    with (
        patch(
            "custom_components.span_panel.config_flow.detect_api_version",
            return_value=MOCK_V2_DETECTION,
        ),
        patch(
            "custom_components.span_panel.config_flow.validate_host",
            return_value=True,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )

        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: MOCK_HOST},
        )

        assert result2["type"] == FlowResultType.ABORT
        assert result2["reason"] == "already_configured"


# ---------- zeroconf edge cases ----------


@pytest.mark.asyncio
async def test_zeroconf_non_ipv4_aborts(hass: HomeAssistant) -> None:
    """Non-IPv4 discovery addresses should abort."""

    discovery_info = ZeroconfServiceInfo(
        ip_address=ipaddress.IPv6Address("fe80::1"),
        ip_addresses=[ipaddress.IPv6Address("fe80::1")],
        hostname="span-panel.local.",
        name="SPAN Panel._ebus._tcp.local.",
        port=8883,
        properties={},
        type="_ebus._tcp.local.",
    )

    with patch(
        "custom_components.span_panel.config_flow.is_ipv4_address",
        return_value=False,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=discovery_info,
        )

        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "not_ipv4_address"


@pytest.mark.asyncio
async def test_zeroconf_already_configured_aborts(hass: HomeAssistant) -> None:
    """Zeroconf discovery of an already-configured host should abort."""
    existing = MockConfigEntry(
        version=3,
        minor_version=1,
        domain=DOMAIN,
        title="Span Panel",
        data={
            CONF_HOST: "192.168.1.200",
            CONF_ACCESS_TOKEN: "existing-token",
            CONF_API_VERSION: "v2",
        },
        source=config_entries.SOURCE_USER,
        options={},
        unique_id="SPAN-V2-001",
    )
    existing.add_to_hass(hass)

    discovery_info = ZeroconfServiceInfo(
        ip_address=ipaddress.IPv4Address("192.168.1.200"),
        ip_addresses=[ipaddress.IPv4Address("192.168.1.200")],
        hostname="span-panel.local.",
        name="SPAN Panel._ebus._tcp.local.",
        port=8883,
        properties={},
        type="_ebus._tcp.local.",
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_ZEROCONF},
        data=discovery_info,
    )

    assert result["type"] == FlowResultType.ABORT


@pytest.mark.asyncio
async def test_zeroconf_not_span_panel_aborts(hass: HomeAssistant) -> None:
    """Zeroconf discovery where v2 endpoint does not respond should abort."""

    # Detection returns v1 (not v2) — this IP is not a valid v2 panel
    mock_bad_detection = DetectionResult(
        api_version="v1",
        status_info=None,
    )

    discovery_info = ZeroconfServiceInfo(
        ip_address=ipaddress.IPv4Address("192.168.1.200"),
        ip_addresses=[ipaddress.IPv4Address("192.168.1.200")],
        hostname="span-panel.local.",
        name="SPAN Panel._ebus._tcp.local.",
        port=8883,
        properties={},
        type="_ebus._tcp.local.",
    )

    with (
        patch(
            "custom_components.span_panel.config_flow.detect_api_version",
            return_value=mock_bad_detection,
        ),
        patch(
            "custom_components.span_panel.config_flow.is_ipv4_address",
            return_value=True,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=discovery_info,
        )

        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "not_span_panel"


@pytest.mark.usefixtures("socket_enabled")
@pytest.mark.asyncio
async def test_zeroconf_end_to_end_entry_creation(hass: HomeAssistant) -> None:
    """Zeroconf discovery through confirm → passphrase → naming → entry creation."""

    discovery_info = ZeroconfServiceInfo(
        ip_address=ipaddress.IPv4Address("192.168.1.200"),
        ip_addresses=[ipaddress.IPv4Address("192.168.1.200")],
        hostname="span-panel.local.",
        name="SPAN Panel._ebus._tcp.local.",
        port=8883,
        properties={},
        type="_ebus._tcp.local.",
    )

    with (
        patch(
            "custom_components.span_panel.config_flow.detect_api_version",
            return_value=MOCK_V2_DETECTION,
        ),
        patch(
            "custom_components.span_panel.config_flow.is_ipv4_address",
            return_value=True,
        ),
        patch(
            "custom_components.span_panel.config_flow.validate_v2_passphrase",
            return_value=MOCK_V2_AUTH,
        ),
    ):
        # Step 1: zeroconf discovery → confirm
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=discovery_info,
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "confirm_discovery"

        # Step 2: confirm → accept the panel's CA → auth choice
        result2 = await _submit_host_and_pin(hass, result["flow_id"], {})
        assert result2["type"] == FlowResultType.MENU
        assert result2["step_id"] == "choose_v2_auth"

        # Step 3: choose passphrase
        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {"next_step_id": "auth_passphrase"},
        )
        assert result3["step_id"] == "auth_passphrase"

        # Step 4: enter passphrase
        result4 = await hass.config_entries.flow.async_configure(
            result3["flow_id"],
            {CONF_HOP_PASSPHRASE: MOCK_PASSPHRASE},
        )
        assert result4["step_id"] == "choose_entity_naming_initial"

        # Step 5: accept naming default → entry created
        result5 = await hass.config_entries.flow.async_configure(
            result4["flow_id"],
            {"entity_naming_pattern": "friendly_names"},
        )
        assert result5["type"] == FlowResultType.CREATE_ENTRY
        assert result5["data"][CONF_API_VERSION] == "v2"
        assert result5["data"][CONF_HOST] == "192.168.1.200"


# ---------- reauth: proximity ----------


@pytest.mark.asyncio
async def test_reauth_v2_proximity_success(hass: HomeAssistant) -> None:
    """Reauth via proximity should update credentials."""
    entry = MockConfigEntry(
        version=3,
        minor_version=1,
        domain=DOMAIN,
        title="Span Panel",
        data={
            CONF_HOST: MOCK_HOST,
            CONF_ACCESS_TOKEN: "old-token",
            CONF_API_VERSION: "v2",
            CONF_EBUS_BROKER_HOST: "old-host",
            CONF_EBUS_BROKER_PORT: 8883,
            CONF_EBUS_BROKER_USERNAME: "old-user",
            CONF_EBUS_BROKER_PASSWORD: "old-pass",
        },
        source=config_entries.SOURCE_USER,
        options={},
        unique_id="SPAN-V2-001",
    )
    entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.span_panel.config_flow.detect_api_version",
            side_effect=[MOCK_V2_DETECTION, MOCK_V2_DETECTION_PROXIMITY_PROVEN],
        ),
        patch(
            "custom_components.span_panel.config_flow.validate_v2_proximity",
            return_value=MOCK_V2_AUTH,
        ),
        patch.object(hass.config_entries, "async_reload", return_value=True),
    ):
        result = await entry.start_reauth_flow(hass)
        assert result["type"] == FlowResultType.MENU
        assert result["step_id"] == "reauth_confirm"

        # Select proximity auth from the reauth menu
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"next_step_id": "auth_proximity"},
        )
        assert result2["step_id"] == "auth_proximity"

        # User confirms door challenge
        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {"next_step_id": "auth_proximity_confirm"},
        )

        assert result3["type"] == FlowResultType.ABORT
        assert result3["reason"] == "reauth_successful"

    assert entry.data[CONF_ACCESS_TOKEN] == "v2-token-abc"
    assert entry.data[CONF_EBUS_BROKER_USERNAME] == "span-user"


# ---------- reconfigure ----------


@pytest.mark.asyncio
async def test_reconfigure_shows_current_host(hass: HomeAssistant) -> None:
    """Reconfigure step should pre-fill the current host."""
    entry = MockConfigEntry(
        version=3,
        minor_version=1,
        domain=DOMAIN,
        title="Span Panel",
        data={
            CONF_HOST: MOCK_HOST,
            CONF_ACCESS_TOKEN: "token",
            CONF_API_VERSION: "v2",
        },
        source=config_entries.SOURCE_USER,
        options={},
        unique_id="SPAN-V2-001",
    )
    entry.add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reconfigure"


@pytest.mark.asyncio
async def test_reconfigure_success(hass: HomeAssistant) -> None:
    """Reconfigure should update the host and reload."""
    entry = MockConfigEntry(
        version=3,
        minor_version=1,
        domain=DOMAIN,
        title="Span Panel",
        data={
            CONF_HOST: MOCK_HOST,
            CONF_ACCESS_TOKEN: "token",
            CONF_API_VERSION: "v2",
        },
        source=config_entries.SOURCE_USER,
        options={},
        unique_id="SPAN-V2-001",
    )
    entry.add_to_hass(hass)

    new_host = "192.168.1.200"

    with patch(
        "custom_components.span_panel.config_flow.detect_api_version",
        return_value=MOCK_V2_DETECTION,
    ):
        result = await entry.start_reconfigure_flow(hass)

        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: new_host},
        )

        assert result2["type"] == FlowResultType.ABORT
        assert result2["reason"] == "reconfigure_successful"

    assert entry.data[CONF_HOST] == new_host
    # Other data should be preserved
    assert entry.data[CONF_ACCESS_TOKEN] == "token"
    assert entry.data[CONF_API_VERSION] == "v2"


@pytest.mark.asyncio
async def test_reconfigure_unreachable_host(hass: HomeAssistant) -> None:
    """Reconfigure with unreachable host should show cannot_connect error."""
    entry = MockConfigEntry(
        version=3,
        minor_version=1,
        domain=DOMAIN,
        title="Span Panel",
        data={
            CONF_HOST: MOCK_HOST,
            CONF_ACCESS_TOKEN: "token",
            CONF_API_VERSION: "v2",
        },
        source=config_entries.SOURCE_USER,
        options={},
        unique_id="SPAN-V2-001",
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.span_panel.config_flow.detect_api_version",
        side_effect=SpanPanelConnectionError("timeout"),
    ):
        result = await entry.start_reconfigure_flow(hass)

        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: "10.0.0.99"},
        )

        assert result2["type"] == FlowResultType.FORM
        assert result2["step_id"] == "reconfigure"
        assert result2["errors"] == {"base": "cannot_connect"}


@pytest.mark.asyncio
async def test_reconfigure_different_panel_aborts(hass: HomeAssistant) -> None:
    """Reconfigure to a different panel serial should abort with unique_id_mismatch."""
    entry = MockConfigEntry(
        version=3,
        minor_version=1,
        domain=DOMAIN,
        title="Span Panel",
        data={
            CONF_HOST: MOCK_HOST,
            CONF_ACCESS_TOKEN: "token",
            CONF_API_VERSION: "v2",
        },
        source=config_entries.SOURCE_USER,
        options={},
        unique_id="SPAN-V2-001",
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.span_panel.config_flow.detect_api_version",
        return_value=MOCK_V2_DETECTION_OTHER,
    ):
        result = await entry.start_reconfigure_flow(hass)

        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: "192.168.1.250"},
        )

        assert result2["type"] == FlowResultType.ABORT
        assert result2["reason"] == "unique_id_mismatch"


@pytest.mark.asyncio
async def test_reconfigure_empty_host(hass: HomeAssistant) -> None:
    """Reconfigure with empty host should re-show with host_required error."""
    entry = MockConfigEntry(
        version=3,
        minor_version=1,
        domain=DOMAIN,
        title="Span Panel",
        data={
            CONF_HOST: MOCK_HOST,
            CONF_ACCESS_TOKEN: "token",
            CONF_API_VERSION: "v2",
        },
        source=config_entries.SOURCE_USER,
        options={},
        unique_id="SPAN-V2-001",
    )
    entry.add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_HOST: "   "},
    )

    assert result2["type"] == FlowResultType.FORM
    assert result2["step_id"] == "reconfigure"
    assert result2["errors"] == {"base": "host_required"}


@pytest.mark.asyncio
async def test_reconfigure_recovery_after_error(hass: HomeAssistant) -> None:
    """User can successfully reconfigure after an initial connection error."""
    entry = MockConfigEntry(
        version=3,
        minor_version=1,
        domain=DOMAIN,
        title="Span Panel",
        data={
            CONF_HOST: MOCK_HOST,
            CONF_ACCESS_TOKEN: "token",
            CONF_API_VERSION: "v2",
        },
        source=config_entries.SOURCE_USER,
        options={},
        unique_id="SPAN-V2-001",
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.span_panel.config_flow.detect_api_version",
        side_effect=[SpanPanelConnectionError("timeout"), MOCK_V2_DETECTION],
    ):
        result = await entry.start_reconfigure_flow(hass)

        # First attempt: connection error
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: "10.0.0.99"},
        )
        assert result2["errors"] == {"base": "cannot_connect"}

        # Second attempt: success
        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {CONF_HOST: "192.168.1.200"},
        )
        assert result3["type"] == FlowResultType.ABORT
        assert result3["reason"] == "reconfigure_successful"

    assert entry.data[CONF_HOST] == "192.168.1.200"


# ---------- hassio (Supervisor) discovery ----------


MOCK_HASSIO_CONFIG = {
    "host": "192.168.1.50",
    "port": 9090,
    "serial": "SPAN-SIM-001",
}

MOCK_V2_DETECTION_SIM = DetectionResult(
    api_version="v2",
    status_info=V2StatusInfo(
        serial_number="SPAN-SIM-001",
        firmware_version="2.0.0",
    ),
)


def _hassio_service_info(config: dict[str, str | int]) -> HassioServiceInfo:
    """Build a HassioServiceInfo for testing."""
    return HassioServiceInfo(
        config=config,
        name="SPAN Panel Simulator",
        slug="span_panel_simulator",
        uuid="test-uuid-1234",
    )


@pytest.mark.asyncio
async def test_hassio_missing_host_aborts(hass: HomeAssistant) -> None:
    """Hassio discovery with no host should abort with no_host."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_HASSIO},
        data=_hassio_service_info({"port": 9090, "serial": "SPAN-SIM-001"}),
    )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "no_host"


@pytest.mark.asyncio
async def test_hassio_missing_host_empty_string_aborts(hass: HomeAssistant) -> None:
    """Hassio discovery with empty host string should abort with no_host."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_HASSIO},
        data=_hassio_service_info({"host": "", "port": 9090, "serial": "SPAN-SIM-001"}),
    )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "no_host"


@pytest.mark.asyncio
async def test_hassio_not_v2_aborts(hass: HomeAssistant) -> None:
    """Hassio discovery of a non-v2 panel should abort with not_span_panel."""
    with patch(
        "custom_components.span_panel.config_flow.detect_api_version",
        return_value=MOCK_V1_DETECTION,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_HASSIO},
            data=_hassio_service_info(MOCK_HASSIO_CONFIG),
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "not_span_panel"


@pytest.mark.asyncio
async def test_hassio_no_serial_aborts(hass: HomeAssistant) -> None:
    """Hassio discovery where panel returns no serial should abort."""
    detection_no_serial = DetectionResult(
        api_version="v2",
        status_info=V2StatusInfo(
            serial_number="",
            firmware_version="2.0.0",
        ),
    )
    config_no_serial = {"host": "192.168.1.50", "port": 9090, "serial": ""}

    with patch(
        "custom_components.span_panel.config_flow.detect_api_version",
        return_value=detection_no_serial,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_HASSIO},
            data=_hassio_service_info(config_no_serial),
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "no_serial"


@pytest.mark.asyncio
async def test_hassio_discovery_routes_to_confirm(hass: HomeAssistant) -> None:
    """Successful hassio discovery should route to confirm_discovery."""
    with patch(
        "custom_components.span_panel.config_flow.detect_api_version",
        return_value=MOCK_V2_DETECTION_SIM,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_HASSIO},
            data=_hassio_service_info(MOCK_HASSIO_CONFIG),
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "confirm_discovery"


@pytest.mark.asyncio
def _hassio_configured_entry(hass: HomeAssistant, **data: object) -> MockConfigEntry:
    """Add an entry for the simulator's serial, configured at a host of its own."""
    entry = MockConfigEntry(
        version=3,
        minor_version=1,
        domain=DOMAIN,
        title="Span Panel",
        data={
            CONF_HOST: "192.168.1.40",
            CONF_EBUS_BROKER_HOST: "192.168.1.40",
            CONF_ACCESS_TOKEN: "existing-token",
            CONF_API_VERSION: "v2",
            CONF_HTTP_PORT: 80,
            **data,
        },
        source=config_entries.SOURCE_USER,
        options={},
        unique_id="SPAN-SIM-001",
    )
    entry.add_to_hass(hass)
    return entry


def _detection_by_host(*answering: str) -> Callable[..., DetectionResult]:
    """Answer as the simulator for the named hosts and as nothing for the rest.

    Keyed by host because that is the whole question here: the discovered host
    and the configured one are two different addresses, and which of them
    answers is what decides whether the entry moves.
    """

    def detect(host: str, **_kwargs: object) -> DetectionResult:
        if host in answering:
            return MOCK_V2_DETECTION_SIM
        return DetectionResult(api_version="v1", probe_failed=True)

    return detect


@pytest.mark.asyncio
async def test_hassio_dedup_by_serial(hass: HomeAssistant) -> None:
    """A discovery of an already-configured serial aborts rather than adding a second entry."""
    existing = _hassio_configured_entry(hass)

    with patch(
        "custom_components.span_panel.config_flow.detect_api_version",
        side_effect=_detection_by_host("192.168.1.50", "192.168.1.40"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_HASSIO},
            data=_hassio_service_info(MOCK_HASSIO_CONFIG),
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert existing.data[CONF_HTTP_PORT] == 9090


@pytest.mark.asyncio
async def test_hassio_keeps_a_configured_host_that_still_answers(hass: HomeAssistant) -> None:
    """The defect: an add-on restart overwrote a working host with its own hostname.

    The add-on republishes the container hostname it answers on, which is not a
    claim that the panel moved and is generally not a name the panel's
    certificate carries. Writing it over the configured host broke the entry
    seconds after it was created — every connection afterwards failed
    verification against the entry's own pin.
    """
    existing = _hassio_configured_entry(hass)

    with patch(
        "custom_components.span_panel.config_flow.detect_api_version",
        side_effect=_detection_by_host("192.168.1.50", "192.168.1.40"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_HASSIO},
            data=_hassio_service_info(MOCK_HASSIO_CONFIG),
        )

    assert result["reason"] == "already_configured"
    assert existing.data[CONF_HOST] == "192.168.1.40"
    assert existing.data[CONF_EBUS_BROKER_HOST] == "192.168.1.40"
    # The ports are the add-on's own and are taken as published either way.
    assert existing.data[CONF_HTTP_PORT] == 9090


@pytest.mark.asyncio
async def test_hassio_probes_the_configured_host_on_the_newly_published_port(
    hass: HomeAssistant,
) -> None:
    """A reallocated port on the same machine is the case the ports are unguarded for.

    Probing the configured host on its *stored* port would report it dead every
    time the add-on moved, which is the move this is meant to prevent.
    """
    _hassio_configured_entry(hass)
    probe = AsyncMock(side_effect=_detection_by_host("192.168.1.50", "192.168.1.40"))

    with patch("custom_components.span_panel.config_flow.detect_api_version", probe):
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_HASSIO},
            data=_hassio_service_info(MOCK_HASSIO_CONFIG),
        )

    probed = [(call.args[0], call.kwargs["port"]) for call in probe.call_args_list]
    assert ("192.168.1.40", 9090) in probed


@pytest.mark.asyncio
async def test_hassio_moves_host_and_broker_host_together_when_the_old_one_is_dead(
    hass: HomeAssistant,
) -> None:
    """A configured host that has stopped answering is a panel that really moved.

    Both keys move or neither does: they are the same address for the same
    panel, and moving one without the other left the entry naming two different
    machines.
    """
    existing = _hassio_configured_entry(hass)

    with patch(
        "custom_components.span_panel.config_flow.detect_api_version",
        side_effect=_detection_by_host("192.168.1.50"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_HASSIO},
            data=_hassio_service_info(MOCK_HASSIO_CONFIG),
        )

    assert result["reason"] == "already_configured"
    assert existing.data[CONF_HOST] == "192.168.1.50"
    assert existing.data[CONF_EBUS_BROKER_HOST] == "192.168.1.50"
    assert existing.data[CONF_HTTP_PORT] == 9090


@pytest.mark.asyncio
async def test_hassio_takes_both_ports_whichever_way_the_host_goes(
    hass: HomeAssistant,
) -> None:
    """The published ports are the add-on's own and are never held to stored values."""
    kept = _hassio_configured_entry(hass, **{CONF_HTTPS_PORT: 443})

    with patch(
        "custom_components.span_panel.config_flow.detect_api_version",
        side_effect=_detection_by_host("192.168.1.50", "192.168.1.40"),
    ):
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_HASSIO},
            data=_hassio_service_info({**MOCK_HASSIO_CONFIG, "https_port": 10090}),
        )

    assert kept.data[CONF_HOST] == "192.168.1.40"
    assert kept.data[CONF_HTTP_PORT] == 9090
    assert kept.data[CONF_HTTPS_PORT] == 10090


@pytest.mark.usefixtures("socket_enabled")
@pytest.mark.asyncio
async def test_hassio_end_to_end_entry_creation(hass: HomeAssistant) -> None:
    """Hassio discovery through confirm -> passphrase -> naming -> entry creation."""
    with (
        patch(
            "custom_components.span_panel.config_flow.detect_api_version",
            return_value=MOCK_V2_DETECTION_SIM,
        ),
        patch(
            "custom_components.span_panel.config_flow.validate_v2_passphrase",
            return_value=MOCK_V2_AUTH,
        ),
    ):
        # Step 1: hassio discovery -> confirm
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_HASSIO},
            data=_hassio_service_info(MOCK_HASSIO_CONFIG),
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "confirm_discovery"

        # Step 2: confirm -> HTTPS port (this panel moved its HTTP port) -> CA
        port_step = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        assert port_step["step_id"] == "panel_https_port"
        result2 = await _submit_host_and_pin(hass, port_step["flow_id"], {CONF_HTTPS_PORT: 9443})
        assert result2["type"] == FlowResultType.MENU
        assert result2["step_id"] == "choose_v2_auth"

        # Step 3: choose passphrase
        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {"next_step_id": "auth_passphrase"},
        )
        assert result3["step_id"] == "auth_passphrase"

        # Step 4: enter passphrase
        result4 = await hass.config_entries.flow.async_configure(
            result3["flow_id"],
            {CONF_HOP_PASSPHRASE: MOCK_PASSPHRASE},
        )
        # Step 5: accept naming default -> entry created
        assert result4["step_id"] == "choose_entity_naming_initial"
        result5 = await hass.config_entries.flow.async_configure(
            result4["flow_id"],
            {"entity_naming_pattern": "friendly_names"},
        )
        assert result5["type"] == FlowResultType.CREATE_ENTRY
        assert result5["data"][CONF_API_VERSION] == "v2"
        assert result5["data"][CONF_HOST] == "192.168.1.50"
        assert result5["data"][CONF_HTTP_PORT] == 9090
        assert result5["data"][CONF_HTTPS_PORT] == 9443
        assert result5["data"][CONF_PANEL_CA_PEM] == FAKE_CA_PEM


@pytest.mark.usefixtures("socket_enabled")
@pytest.mark.asyncio
async def test_hassio_published_https_port_is_used_without_asking(hass: HomeAssistant) -> None:
    """A discovery record naming the TLS port skips the prompt and is believed.

    The add-on allocates a TLS port per panel and reallocates it across
    restarts, so it is the only party that knows the answer. Asking the user
    for a number they would have to go read out of an add-on log is a question
    with a worse answer available.
    """
    leaf_host = AsyncMock(side_effect=lambda _hass, host, _port, _pem: host)
    with (
        patch(
            "custom_components.span_panel.config_flow.detect_api_version",
            return_value=MOCK_V2_DETECTION_SIM,
        ),
        patch(
            "custom_components.span_panel.config_flow.validate_v2_passphrase",
            return_value=MOCK_V2_AUTH,
        ),
        patch(
            "custom_components.span_panel.config_flow.async_panel_leaf_host",
            new=leaf_host,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_HASSIO},
            data=_hassio_service_info({**MOCK_HASSIO_CONFIG, "https_port": 10090}),
        )
        assert result["step_id"] == "confirm_discovery"

        # Confirm goes straight past the port question to the auth menu.
        result2 = await _submit_host_and_pin(hass, result["flow_id"], {})
        assert result2["step_id"] == "choose_v2_auth"

        # The published port is the one the CA was checked against, not 443.
        assert leaf_host.await_args.args[2] == 10090

        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"], {"next_step_id": "auth_passphrase"}
        )
        result4 = await hass.config_entries.flow.async_configure(
            result3["flow_id"], {CONF_HOP_PASSPHRASE: MOCK_PASSPHRASE}
        )
        result5 = await hass.config_entries.flow.async_configure(
            result4["flow_id"], {"entity_naming_pattern": "friendly_names"}
        )

    assert result5["type"] == FlowResultType.CREATE_ENTRY
    assert result5["data"][CONF_HTTP_PORT] == 9090
    assert result5["data"][CONF_HTTPS_PORT] == 10090


@pytest.mark.asyncio
async def test_zeroconf_https_port_txt_record_is_used_without_asking(
    hass: HomeAssistant,
) -> None:
    """The same holds over mDNS, where the panel publishes the port in TXT."""
    discovery_info = ZeroconfServiceInfo(
        ip_address=ipaddress.IPv4Address("192.168.1.200"),
        ip_addresses=[ipaddress.IPv4Address("192.168.1.200")],
        hostname="span-panel.local.",
        name="SPAN Panel._ebus._tcp.local.",
        port=8883,
        properties={"httpPort": "8081", "httpsPort": "9081"},
        type="_ebus._tcp.local.",
    )

    leaf_host = AsyncMock(side_effect=lambda _hass, host, _port, _pem: host)
    with (
        patch(
            "custom_components.span_panel.config_flow.detect_api_version",
            return_value=MOCK_V2_DETECTION,
        ),
        patch(
            "custom_components.span_panel.config_flow.is_ipv4_address",
            return_value=True,
        ),
        patch(
            "custom_components.span_panel.config_flow.async_panel_leaf_host",
            new=leaf_host,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=discovery_info,
        )
        assert result["step_id"] == "confirm_discovery"

        result2 = await _submit_host_and_pin(hass, result["flow_id"], {})
        assert result2["step_id"] == "choose_v2_auth"
        assert leaf_host.await_args.args[2] == 9081


@pytest.mark.asyncio
async def test_moved_http_port_alone_still_asks_for_the_https_one(
    hass: HomeAssistant,
) -> None:
    """A panel that publishes no TLS port is still asked about, as before.

    Hardware behind a reverse proxy advertises ``httpPort`` and nothing else.
    Reading a port from discovery must not become a way for that install to
    silently end up checking the CA against 443.
    """
    discovery_info = ZeroconfServiceInfo(
        ip_address=ipaddress.IPv4Address("192.168.1.200"),
        ip_addresses=[ipaddress.IPv4Address("192.168.1.200")],
        hostname="span-panel.local.",
        name="SPAN Panel._ebus._tcp.local.",
        port=8883,
        properties={"httpPort": "8080"},
        type="_ebus._tcp.local.",
    )

    with (
        patch(
            "custom_components.span_panel.config_flow.detect_api_version",
            return_value=MOCK_V2_DETECTION,
        ),
        patch(
            "custom_components.span_panel.config_flow.is_ipv4_address",
            return_value=True,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=discovery_info,
        )
        port_step = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert port_step["step_id"] == "panel_https_port"


@pytest.mark.asyncio
async def test_unreadable_https_port_in_discovery_falls_back_to_asking(
    hass: HomeAssistant,
) -> None:
    """A TXT record that cannot be read is not a port, so the question stands."""
    discovery_info = ZeroconfServiceInfo(
        ip_address=ipaddress.IPv4Address("192.168.1.200"),
        ip_addresses=[ipaddress.IPv4Address("192.168.1.200")],
        hostname="span-panel.local.",
        name="SPAN Panel._ebus._tcp.local.",
        port=8883,
        properties={"httpPort": "8080", "httpsPort": "not-a-port"},
        type="_ebus._tcp.local.",
    )

    with (
        patch(
            "custom_components.span_panel.config_flow.detect_api_version",
            return_value=MOCK_V2_DETECTION,
        ),
        patch(
            "custom_components.span_panel.config_flow.is_ipv4_address",
            return_value=True,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=discovery_info,
        )
        port_step = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert port_step["step_id"] == "panel_https_port"


# ---------- user flow: null status_info ----------


@pytest.mark.asyncio
async def test_user_flow_v2_null_status_info_shows_error(hass: HomeAssistant) -> None:
    """User flow should show cannot_connect when v2 detection has null status_info."""
    detection_no_status = DetectionResult(
        api_version="v2",
        status_info=None,
    )

    with (
        patch(
            "custom_components.span_panel.config_flow.detect_api_version",
            return_value=detection_no_status,
        ),
        patch(
            "custom_components.span_panel.config_flow.validate_host",
            return_value=True,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )

        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: MOCK_HOST},
        )

        assert result2["type"] == FlowResultType.FORM
        assert result2["step_id"] == "user"
        assert result2["errors"] == {"base": "cannot_connect"}


@pytest.mark.asyncio
async def test_reauth_v2_null_status_info_aborts(hass: HomeAssistant) -> None:
    """Reauth should abort with cannot_connect when v2 detection has null status_info."""
    entry = MockConfigEntry(
        version=3,
        minor_version=1,
        domain=DOMAIN,
        title="Span Panel",
        data={
            CONF_HOST: MOCK_HOST,
            CONF_ACCESS_TOKEN: "old-token",
            CONF_API_VERSION: "v2",
        },
        source=config_entries.SOURCE_USER,
        options={},
        unique_id="SPAN-V2-001",
    )
    entry.add_to_hass(hass)

    detection_no_status = DetectionResult(
        api_version="v2",
        status_info=None,
    )

    with patch(
        "custom_components.span_panel.config_flow.detect_api_version",
        return_value=detection_no_status,
    ):
        result = await entry.start_reauth_flow(hass)

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


@pytest.mark.asyncio
async def test_zeroconf_invalid_http_port_defaults_to_80(hass: HomeAssistant) -> None:
    """Invalid httpPort TXT records should fall back to port 80."""

    discovery_info = ZeroconfServiceInfo(
        ip_address=ipaddress.IPv4Address("192.168.1.200"),
        ip_addresses=[ipaddress.IPv4Address("192.168.1.200")],
        hostname="span-panel.local.",
        name="SPAN Panel._ebus._tcp.local.",
        port=8883,
        properties={"httpPort": "bad-port"},
        type="_ebus._tcp.local.",
    )

    fake_client = MagicMock()
    with (
        patch(
            "custom_components.span_panel.config_flow.get_async_client",
            return_value=fake_client,
        ),
        patch(
            "custom_components.span_panel.config_flow.detect_api_version",
            return_value=MOCK_V2_DETECTION,
        ) as mock_detect,
        patch(
            "custom_components.span_panel.config_flow.is_ipv4_address",
            return_value=True,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=discovery_info,
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "confirm_discovery"
    mock_detect.assert_awaited_once_with("192.168.1.200", port=80, httpx_client=fake_client)


@pytest.mark.usefixtures("socket_enabled")
@pytest.mark.asyncio
async def test_user_flow_fqdn_registration_progress_then_naming(
    hass: HomeAssistant,
) -> None:
    """FQDN hosts should route through the registration progress step."""
    with (
        patch(
            "custom_components.span_panel.config_flow.detect_api_version",
            return_value=MOCK_V2_DETECTION,
        ),
        patch(
            "custom_components.span_panel.config_flow.validate_host",
            return_value=True,
        ),
        patch(
            "custom_components.span_panel.config_flow.validate_v2_passphrase",
            return_value=MOCK_V2_AUTH,
        ),
        patch(
            "custom_components.span_panel.config_flow.register_fqdn",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.span_panel.config_flow.check_fqdn_tls_ready",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "custom_components.span_panel.config_flow.asyncio.sleep",
            new=AsyncMock(),
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result2 = await _submit_host_and_pin(
            hass, result["flow_id"], {CONF_HOST: "panel.example.com"}
        )
        result2b = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {"next_step_id": "auth_passphrase"},
        )
        result3 = await hass.config_entries.flow.async_configure(
            result2b["flow_id"],
            {CONF_HOP_PASSPHRASE: MOCK_PASSPHRASE},
        )
    # The progress step carries its triggering input through to the naming step,
    # which takes the default and creates the entry in one go.
    assert result3["type"] == FlowResultType.CREATE_ENTRY
    assert result3["data"][CONF_REGISTERED_FQDN] == "panel.example.com"
    # Registration itself ran over this anchor, not just what followed it.
    assert result3["data"][CONF_PANEL_CA_PEM] == FAKE_CA_PEM


@pytest.mark.asyncio
async def test_an_anchorless_readiness_wait_fetches_the_ca_once(
    hass: HomeAssistant,
) -> None:
    """The leaf changes while the panel regenerates it; the anchor does not.

    An entry whose CA fetch never succeeded has no anchor of its own, so the
    wait has to fetch one. It used to fetch a fresh one on every poll -- thirty
    plaintext trust decisions, two seconds apart, each against whatever
    answered. One fetch, held for the rest of the wait.
    """
    flow = SpanPanelConfigFlow()
    flow.hass = hass
    flow.host = "panel.example.com"
    flow.access_token = "token"
    flow._http_port = 80
    flow._rest_transport = PanelRestTransport(
        port=80, ssl_context=None, httpx_client=None, ca_pem=None
    )

    # False twice, then ready: the poll runs more than once, which is the only
    # way a per-poll fetch would show up.
    ready = AsyncMock(side_effect=[False, False, True])
    download = AsyncMock(return_value="fetched-pem")

    with (
        patch("custom_components.span_panel.config_flow.register_fqdn", new=AsyncMock()),
        patch("custom_components.span_panel.config_flow.async_download_ca_or_none", new=download),
        patch("custom_components.span_panel.config_flow.check_fqdn_tls_ready", new=ready),
        patch("custom_components.span_panel.config_flow.asyncio.sleep", new=AsyncMock()),
        patch.object(flow, "_async_verify_host_over_pin", new=AsyncMock()),
    ):
        await flow._async_register_fqdn_and_wait()

    assert ready.await_count == 3
    download.assert_awaited_once()
    # Every poll checked against the anchor that single fetch produced.
    assert [call.args[2] for call in ready.await_args_list] == ["fetched-pem"] * 3


@pytest.mark.usefixtures("socket_enabled")
@pytest.mark.asyncio
async def test_user_flow_fqdn_registration_failure_can_continue(
    hass: HomeAssistant,
) -> None:
    """Failed FQDN registration should allow continuing without registration."""
    with (
        patch(
            "custom_components.span_panel.config_flow.detect_api_version",
            return_value=MOCK_V2_DETECTION,
        ),
        patch(
            "custom_components.span_panel.config_flow.validate_host",
            return_value=True,
        ),
        patch(
            "custom_components.span_panel.config_flow.validate_v2_passphrase",
            return_value=MOCK_V2_AUTH,
        ),
        patch(
            "custom_components.span_panel.config_flow.register_fqdn",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.span_panel.config_flow.check_fqdn_tls_ready",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "custom_components.span_panel.config_flow.asyncio.sleep",
            new=AsyncMock(),
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result2 = await _submit_host_and_pin(
            hass, result["flow_id"], {CONF_HOST: "panel.example.com"}
        )
        result2b = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {"next_step_id": "auth_passphrase"},
        )
        result3 = await hass.config_entries.flow.async_configure(
            result2b["flow_id"],
            {CONF_HOP_PASSPHRASE: MOCK_PASSPHRASE},
        )
        assert result3["type"] == FlowResultType.FORM
        assert result3["step_id"] == "choose_entity_naming_initial"


@pytest.mark.usefixtures("socket_enabled")
@pytest.mark.asyncio
async def test_fqdn_entry_creation_sets_registered_fqdn_and_unique_title(
    hass: HomeAssistant,
) -> None:
    """FQDN-based entries should store the registered host and keep unique titles."""
    existing = MockConfigEntry(
        domain=DOMAIN,
        title="Span Panel",
        data={CONF_HOST: "192.168.1.10", CONF_ACCESS_TOKEN: "existing-token"},
        unique_id="EXISTING-PANEL-001",
    )
    existing.add_to_hass(hass)

    with (
        patch(
            "custom_components.span_panel.config_flow.detect_api_version",
            return_value=MOCK_V2_DETECTION,
        ),
        patch(
            "custom_components.span_panel.config_flow.validate_host",
            return_value=True,
        ),
        patch(
            "custom_components.span_panel.config_flow.validate_v2_passphrase",
            return_value=MOCK_V2_AUTH,
        ),
        patch(
            "custom_components.span_panel.config_flow.register_fqdn",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.span_panel.config_flow.check_fqdn_tls_ready",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "custom_components.span_panel.config_flow.asyncio.sleep",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.span_panel.async_setup_entry",
            new=AsyncMock(return_value=True),
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        port_step = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: "panel.example.com", CONF_HTTP_PORT: 8080},
        )
        assert port_step["step_id"] == "panel_https_port"
        # Left at the default, so it is not written to the entry.
        result2 = await _submit_host_and_pin(hass, port_step["flow_id"], {CONF_HTTPS_PORT: 443})
        result2b = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {"next_step_id": "auth_passphrase"},
        )
        result3 = await hass.config_entries.flow.async_configure(
            result2b["flow_id"],
            {CONF_HOP_PASSPHRASE: MOCK_PASSPHRASE},
        )
    assert result3["type"] == FlowResultType.CREATE_ENTRY
    assert result3["title"] == "Span Panel 2"
    assert result3["data"][CONF_HOST] == "panel.example.com"
    assert result3["data"][CONF_REGISTERED_FQDN] == "panel.example.com"
    assert result3["data"][CONF_HTTP_PORT] == 8080
    assert CONF_HTTPS_PORT not in result3["data"]


@pytest.mark.asyncio
async def test_update_v2_entry_missing_entry_aborts_with_reauth_failed(
    hass: HomeAssistant,
) -> None:
    """Missing entries during reauth should abort cleanly."""
    flow = SpanPanelConfigFlow()
    flow.hass = hass
    flow.trigger_flow_type = TriggerFlowType.UPDATE_ENTRY
    flow.context = {"entry_id": "missing-entry"}
    flow.host = MOCK_HOST
    flow.serial_number = "SPAN-V2-001"
    flow.access_token = MOCK_V2_AUTH.access_token
    flow._is_flow_setup = True
    flow._store_v2_auth_result(MOCK_V2_AUTH)

    result = await flow._async_finalize_v2_auth()

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reauth_failed"


@pytest.mark.asyncio
async def test_reconfigure_to_fqdn_registers_and_updates_registered_fqdn(
    hass: HomeAssistant,
) -> None:
    """Reconfiguring to an FQDN should go through registration and persist it."""
    entry = MockConfigEntry(
        version=3,
        minor_version=1,
        domain=DOMAIN,
        title="Span Panel",
        data={
            CONF_HOST: MOCK_HOST,
            CONF_ACCESS_TOKEN: "token",
            CONF_API_VERSION: "v2",
            CONF_EBUS_BROKER_PORT: 8883,
        },
        source=config_entries.SOURCE_USER,
        options={},
        unique_id="SPAN-V2-001",
    )
    entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.span_panel.config_flow.detect_api_version",
            return_value=MOCK_V2_DETECTION,
        ),
        patch(
            "custom_components.span_panel.config_flow.register_fqdn",
            new=AsyncMock(),
        ),
        # This entry pins no CA, so the readiness wait fetches one itself.
        patch(
            "custom_components.span_panel.config_flow.async_download_ca_or_none",
            new=AsyncMock(return_value=FAKE_CA_PEM),
        ),
        patch(
            "custom_components.span_panel.config_flow.check_fqdn_tls_ready",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "custom_components.span_panel.config_flow.asyncio.sleep",
            new=AsyncMock(),
        ),
    ):
        result = await entry.start_reconfigure_flow(hass)
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: "panel.example.com"},
        )
        result3 = result2

    assert result3["type"] == FlowResultType.ABORT
    assert result3["reason"] == "reconfigure_successful"
    assert entry.data[CONF_HOST] == "panel.example.com"
    assert entry.data[CONF_REGISTERED_FQDN] == "panel.example.com"


@pytest.mark.asyncio
async def test_reconfigure_fqdn_failure_can_continue_without_registration(
    hass: HomeAssistant,
) -> None:
    """Failed FQDN registration during reconfigure should allow continue."""
    entry = MockConfigEntry(
        version=3,
        minor_version=1,
        domain=DOMAIN,
        title="Span Panel",
        data={
            CONF_HOST: MOCK_HOST,
            CONF_ACCESS_TOKEN: "token",
            CONF_API_VERSION: "v2",
            CONF_EBUS_BROKER_PORT: 8883,
        },
        source=config_entries.SOURCE_USER,
        options={},
        unique_id="SPAN-V2-001",
    )
    entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.span_panel.config_flow.detect_api_version",
            return_value=MOCK_V2_DETECTION,
        ),
        patch(
            "custom_components.span_panel.config_flow.register_fqdn",
            new=AsyncMock(),
        ),
        # This entry pins no CA, so the readiness wait fetches one itself.
        patch(
            "custom_components.span_panel.config_flow.async_download_ca_or_none",
            new=AsyncMock(return_value=FAKE_CA_PEM),
        ),
        patch(
            "custom_components.span_panel.config_flow.check_fqdn_tls_ready",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "custom_components.span_panel.config_flow.asyncio.sleep",
            new=AsyncMock(),
        ),
    ):
        result = await entry.start_reconfigure_flow(hass)
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: "panel.example.com"},
        )
        result4 = result2

    assert result4["type"] == FlowResultType.ABORT
    assert result4["reason"] == "reconfigure_successful"
    assert entry.data[CONF_HOST] == "panel.example.com"
    assert CONF_REGISTERED_FQDN not in entry.data


@pytest.mark.asyncio
async def test_reconfigure_switch_from_fqdn_to_ip_clears_registration(
    hass: HomeAssistant,
) -> None:
    """Switching from FQDN back to IP should delete the old registration."""
    entry = MockConfigEntry(
        version=3,
        minor_version=1,
        domain=DOMAIN,
        title="Span Panel",
        data={
            CONF_HOST: "panel.example.com",
            CONF_REGISTERED_FQDN: "panel.example.com",
            CONF_ACCESS_TOKEN: "token",
            CONF_API_VERSION: "v2",
        },
        source=config_entries.SOURCE_USER,
        options={},
        unique_id="SPAN-V2-001",
    )
    entry.add_to_hass(hass)

    fake_client = MagicMock()
    with (
        # The transport is resolved in config_flow_validation, which is where the
        # shared client is taken from when the entry has no pinned CA.
        patch(
            "custom_components.span_panel.config_flow_validation.get_async_client",
            return_value=fake_client,
        ),
        patch(
            "custom_components.span_panel.config_flow.detect_api_version",
            return_value=MOCK_V2_DETECTION,
        ),
        patch(
            "custom_components.span_panel.config_flow.delete_fqdn",
            new=AsyncMock(),
        ) as mock_delete,
    ):
        result = await entry.start_reconfigure_flow(hass)
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: "192.168.1.201"},
        )

    assert result2["type"] == FlowResultType.ABORT
    assert result2["reason"] == "reconfigure_successful"
    # Unpinned entry: the plaintext port and the shared client, and no context.
    mock_delete.assert_awaited_once_with(
        "192.168.1.201", "token", port=80, httpx_client=fake_client, ssl_context=None
    )
    assert entry.data[CONF_HOST] == "192.168.1.201"
    assert entry.data[CONF_REGISTERED_FQDN] == ""


@pytest.mark.asyncio
async def test_reconfigure_refuses_to_downgrade_an_unreadable_pin(
    hass: HomeAssistant,
) -> None:
    """A pin that cannot be read is not permission to reconfigure over plaintext.

    This flow carries the entry's access token to the panel — `delete_fqdn` on
    this branch, `register_fqdn` on the other — and checks the new host against
    the anchor before writing it. With no usable anchor it can do neither, so it
    refuses instead of falling back to the transport the entry had before it was
    pinned. Nothing reaches the network and nothing is written.
    """
    entry = MockConfigEntry(
        version=7,
        minor_version=1,
        domain=DOMAIN,
        title="SPAN Panel",
        data={
            CONF_HOST: "panel.example.com",
            CONF_REGISTERED_FQDN: "panel.example.com",
            CONF_ACCESS_TOKEN: "token",
            CONF_API_VERSION: "v2",
            CONF_PANEL_CA_PEM: UNREADABLE_CA_PEM,
        },
        source=config_entries.SOURCE_USER,
        options={},
        unique_id="SPAN-V2-001",
    )
    entry.add_to_hass(hass)

    detect = AsyncMock(return_value=MOCK_V2_DETECTION)
    delete = AsyncMock()
    with (
        patch("custom_components.span_panel.config_flow.detect_api_version", new=detect),
        patch("custom_components.span_panel.config_flow.delete_fqdn", new=delete),
    ):
        result = await entry.start_reconfigure_flow(hass)
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: "192.168.1.201"},
        )

    assert result2["type"] == FlowResultType.FORM
    assert result2["step_id"] == "reconfigure"
    assert result2["errors"] == {"base": "ca_unusable"}
    detect.assert_not_awaited()
    delete.assert_not_awaited()
    assert entry.data[CONF_HOST] == "panel.example.com"
    assert entry.data[CONF_REGISTERED_FQDN] == "panel.example.com"
    assert entry.data[CONF_PANEL_CA_PEM] == UNREADABLE_CA_PEM
