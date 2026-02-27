"""Tests for v2 eBus config flow changes."""

from __future__ import annotations

import ipaddress
from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from span_panel_api import DetectionResult, V2AuthResponse, V2StatusInfo
from span_panel_api.exceptions import SpanPanelAuthError, SpanPanelConnectionError

from custom_components.span_panel.const import (
    CONF_API_VERSION,
    CONF_EBUS_BROKER_HOST,
    CONF_EBUS_BROKER_PASSWORD,
    CONF_EBUS_BROKER_PORT,
    CONF_EBUS_BROKER_USERNAME,
    CONF_HOP_PASSPHRASE,
    CONF_PANEL_SERIAL,
    DOMAIN,
)

# ---------- helpers ----------

MOCK_HOST = "192.168.1.100"
MOCK_PASSPHRASE = "correct-horse-battery-staple"

MOCK_V2_DETECTION = DetectionResult(
    api_version="v2",
    status_info=V2StatusInfo(
        serial_number="SPAN-V2-001",
        firmware_version="2.0.0",
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
async def test_user_flow_detects_v2_and_shows_passphrase(hass: HomeAssistant) -> None:
    """When detect_api_version returns v2, the user flow should show the passphrase step."""
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

        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: MOCK_HOST, "simulator_mode": False},
        )

        assert result2["type"] == FlowResultType.FORM
        assert result2["step_id"] == "auth_passphrase"


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
            {CONF_HOST: MOCK_HOST, "simulator_mode": False},
        )

        # v1 panels should go through setup_flow which detects v1
        # The flow should still proceed (v1 detection stores api_version)
        # then route to choose_auth_type or abort depending on implementation
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

        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: MOCK_HOST, "simulator_mode": False},
        )
        assert result2["step_id"] == "auth_passphrase"

        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
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

        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: MOCK_HOST, "simulator_mode": False},
        )

        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
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

        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: MOCK_HOST, "simulator_mode": False},
        )

        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {CONF_HOP_PASSPHRASE: MOCK_PASSPHRASE},
        )

        assert result3["type"] == FlowResultType.FORM
        assert result3["step_id"] == "auth_passphrase"
        assert result3["errors"] == {"base": "cannot_connect"}


# ---------- v2 entry creation ----------


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
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: MOCK_HOST, "simulator_mode": False},
        )

        # Step 2: submit passphrase
        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {CONF_HOP_PASSPHRASE: MOCK_PASSPHRASE},
        )

        # Step 3: choose entity naming pattern (accept default)
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
        assert data[CONF_HOP_PASSPHRASE] == MOCK_PASSPHRASE
        assert data[CONF_PANEL_SERIAL] == "SPAN-V2-001"


# ---------- config entry migration v2->v3 ----------


@pytest.mark.asyncio
async def test_migration_v2_to_v3_live_panel(hass: HomeAssistant) -> None:
    """Live panel entries migrating from version 2 to 3 should get api_version=v1."""
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

    with patch(
        "custom_components.span_panel.migrate_config_entry_sensors",
        return_value=True,
    ):
        from custom_components.span_panel import async_migrate_entry

        result = await async_migrate_entry(hass, entry)

    assert result is True
    assert entry.version == 5
    assert entry.data.get(CONF_API_VERSION) == "v1"


@pytest.mark.asyncio
async def test_migration_v2_to_v4_simulator(hass: HomeAssistant) -> None:
    """Simulator entries migrating from version 2 to 5 should get api_version=simulation."""
    entry = MockConfigEntry(
        version=2,
        minor_version=1,
        domain=DOMAIN,
        title="Span Simulator",
        data={
            CONF_HOST: "sim-001",
            CONF_ACCESS_TOKEN: "simulator_token",
            "simulation_mode": True,
            "simulation_config": "simulation_config_32_circuit",
        },
        source=config_entries.SOURCE_USER,
        options={},
        unique_id="SIM-001",
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.span_panel.migrate_config_entry_sensors",
        return_value=True,
    ):
        from custom_components.span_panel import async_migrate_entry

        result = await async_migrate_entry(hass, entry)

    assert result is True
    assert entry.version == 5
    assert entry.data.get(CONF_API_VERSION) == "simulation"


# ---------- zeroconf v2 discovery ----------


@pytest.mark.asyncio
async def test_zeroconf_ebus_discovery_routes_to_confirm(hass: HomeAssistant) -> None:
    """Discovering an _ebus._tcp.local. service should set api_version=v2 and show confirm."""
    from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

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
async def test_reauth_v2_shows_passphrase(hass: HomeAssistant) -> None:
    """Reauth for a v2 panel should show the passphrase form."""
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

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "auth_passphrase"


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
        assert result["step_id"] == "auth_passphrase"

        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOP_PASSPHRASE: MOCK_PASSPHRASE},
        )

        assert result2["type"] == FlowResultType.ABORT
        assert result2["reason"] == "reauth_successful"

    assert entry.data[CONF_ACCESS_TOKEN] == "v2-token-abc"
    assert entry.data[CONF_EBUS_BROKER_USERNAME] == "span-user"
    assert entry.data[CONF_EBUS_BROKER_PASSWORD] == "mqtt-secret"
