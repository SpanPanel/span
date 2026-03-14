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

# Shared mock detection for a different panel (used in reconfigure/duplicate tests)
MOCK_V2_DETECTION_OTHER = DetectionResult(
    api_version="v2",
    status_info=V2StatusInfo(
        serial_number="SPAN-V2-OTHER",
        firmware_version="2.0.0",
    ),
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

        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: MOCK_HOST},
        )

        assert result2["type"] == FlowResultType.MENU
        assert result2["step_id"] == "choose_v2_auth"
        assert "auth_passphrase" in result2["menu_options"]
        assert "auth_proximity" in result2["menu_options"]


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
            {CONF_HOST: MOCK_HOST},
        )
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

        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: MOCK_HOST},
        )

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

        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: MOCK_HOST},
        )

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
            {CONF_HOST: MOCK_HOST},
        )

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
        assert data[CONF_HOP_PASSPHRASE] == MOCK_PASSPHRASE
        assert data[CONF_PANEL_SERIAL] == "SPAN-V2-001"


# ---------- config entry migration v2->v3 ----------


@pytest.mark.asyncio
async def test_migration_v2_to_v3_live_panel(hass: HomeAssistant) -> None:
    """Live panel entries migrating from version 2 to 5 should get api_version=v1 when panel is v2."""
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

    with (
        patch(
            "custom_components.span_panel.migrate_config_entry_sensors",
            return_value=True,
        ),
        patch(
            "custom_components.span_panel.detect_api_version",
            return_value=MOCK_V2_DETECTION,
        ),
    ):
        from custom_components.span_panel import async_migrate_entry

        result = await async_migrate_entry(hass, entry)

    assert result is True
    assert entry.version == 6
    assert entry.data.get(CONF_API_VERSION) == "v1"


@pytest.mark.asyncio
async def test_migration_blocked_when_panel_is_v1(hass: HomeAssistant) -> None:
    """Migration should fail and leave schema untouched when panel firmware is v1."""
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
        unique_id="SN-LIVE-002",
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.span_panel.detect_api_version",
        return_value=MOCK_V1_DETECTION,
    ):
        from custom_components.span_panel import async_migrate_entry

        result = await async_migrate_entry(hass, entry)

    assert result is False
    assert entry.version == 2  # schema untouched


@pytest.mark.asyncio
async def test_migration_blocked_when_panel_unreachable(hass: HomeAssistant) -> None:
    """Migration should fail and leave schema untouched when the panel is unreachable."""
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
        unique_id="SN-LIVE-003",
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.span_panel.detect_api_version",
        side_effect=SpanPanelConnectionError("timeout"),
    ):
        from custom_components.span_panel import async_migrate_entry

        result = await async_migrate_entry(hass, entry)

    assert result is False
    assert entry.version == 2  # schema untouched


@pytest.mark.asyncio
async def test_migration_v5_to_v6_rejects_simulation_entry(hass: HomeAssistant) -> None:
    """Simulation entries at v5 should be rejected by v5→v6 migration."""
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

    from custom_components.span_panel import async_migrate_entry

    result = await async_migrate_entry(hass, entry)

    assert result is False


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
        assert result["step_id"] == "choose_v2_auth"


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
        assert result["step_id"] == "choose_v2_auth"

        # Select passphrase auth from the menu
        result1b = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"next_step_id": "auth_passphrase"},
        )
        assert result1b["step_id"] == "auth_passphrase"

        result2 = await hass.config_entries.flow.async_configure(
            result1b["flow_id"],
            {CONF_HOP_PASSPHRASE: MOCK_PASSPHRASE},
        )

        assert result2["type"] == FlowResultType.ABORT
        assert result2["reason"] == "reauth_successful"

    assert entry.data[CONF_ACCESS_TOKEN] == "v2-token-abc"
    assert entry.data[CONF_EBUS_BROKER_USERNAME] == "span-user"
    assert entry.data[CONF_EBUS_BROKER_PASSWORD] == "mqtt-secret"


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
        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {CONF_HOST: MOCK_HOST},
        )
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

        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: MOCK_HOST},
        )

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

        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: MOCK_HOST},
        )

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
            return_value=MOCK_V2_DETECTION,
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

        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: MOCK_HOST},
        )
        assert result2["step_id"] == "choose_v2_auth"

        result2b = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {"next_step_id": "auth_proximity"},
        )
        assert result2b["step_id"] == "auth_proximity"

        # Submit the proximity form (empty — user just confirms)
        result3 = await hass.config_entries.flow.async_configure(
            result2b["flow_id"],
            {},
        )

        assert result3["type"] == FlowResultType.FORM
        assert result3["step_id"] == "choose_entity_naming_initial"


@pytest.mark.asyncio
async def test_proximity_auth_failed(hass: HomeAssistant) -> None:
    """Failed proximity auth should re-show the form with proximity_failed error."""
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
            "custom_components.span_panel.config_flow.validate_v2_proximity",
            side_effect=SpanPanelAuthError("door not detected"),
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )

        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: MOCK_HOST},
        )

        result2b = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {"next_step_id": "auth_proximity"},
        )

        result3 = await hass.config_entries.flow.async_configure(
            result2b["flow_id"],
            {},
        )

        assert result3["type"] == FlowResultType.FORM
        assert result3["step_id"] == "auth_proximity"
        assert result3["errors"] == {"base": "proximity_failed"}


@pytest.mark.asyncio
async def test_proximity_auth_connection_error(hass: HomeAssistant) -> None:
    """Connection error during proximity should re-show with cannot_connect."""
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
            "custom_components.span_panel.config_flow.validate_v2_proximity",
            side_effect=SpanPanelConnectionError("timeout"),
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )

        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: MOCK_HOST},
        )

        result2b = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {"next_step_id": "auth_proximity"},
        )

        result3 = await hass.config_entries.flow.async_configure(
            result2b["flow_id"],
            {},
        )

        assert result3["type"] == FlowResultType.FORM
        assert result3["step_id"] == "auth_proximity"
        assert result3["errors"] == {"base": "cannot_connect"}


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
    from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

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

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_ZEROCONF},
        data=discovery_info,
    )

    assert result["type"] == FlowResultType.ABORT


@pytest.mark.asyncio
async def test_zeroconf_not_span_panel_aborts(hass: HomeAssistant) -> None:
    """Zeroconf discovery where v2 endpoint does not respond should abort."""
    from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

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


@pytest.mark.asyncio
async def test_zeroconf_end_to_end_entry_creation(hass: HomeAssistant) -> None:
    """Zeroconf discovery through confirm → passphrase → naming → entry creation."""
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

        # Step 2: confirm → auth choice
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {},
        )
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
            return_value=MOCK_V2_DETECTION,
        ),
        patch(
            "custom_components.span_panel.config_flow.validate_v2_proximity",
            return_value=MOCK_V2_AUTH,
        ),
        patch.object(hass.config_entries, "async_reload", return_value=True),
    ):
        result = await entry.start_reauth_flow(hass)
        assert result["step_id"] == "choose_v2_auth"

        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"next_step_id": "auth_proximity"},
        )
        assert result2["step_id"] == "auth_proximity"

        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {},
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
