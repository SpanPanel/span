"""Registration says when to retry when the panel rate-limits it, and proximity is offered only where supported.

The panel refuses registration with HTTP 429 once a client makes too many
attempts, and the library raises `SpanPanelRateLimitError` carrying the wait
the panel asked for. That is not a wrong passphrase, so the form says when to
try again rather than "Invalid authentication".

A panel whose REST hardware version registers by passphrase only has no proof
of proximity, so neither the setup nor the reauth menu offers it, and a
rotation whose outcome is unknown points at the passphrase the SPAN app shows.
Every other panel's flows are unchanged.
"""

from __future__ import annotations

import dataclasses
from typing import Final
from unittest.mock import AsyncMock, patch

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from span_panel_api import DetectionResult
from span_panel_api.exceptions import SpanPanelRateLimitError, SpanPanelServerError

from custom_components.span_panel import _async_register_credential_services
from custom_components.span_panel.const import CONF_API_VERSION, CONF_HOP_PASSPHRASE, DOMAIN
from custom_components.span_panel.hardware_guard import (
    PASSPHRASE_ONLY_HARDWARE_VERSIONS,
    registers_by_passphrase_only,
)
from custom_components.span_panel.services import _ROTATION_RECONNECT_DELAYS_S

from .conftest import VALIDATED_PANEL_STATUS
from .test_rotate_credentials_service import (
    NEW_HOP_PASSPHRASE,
    ROTATION,
    _add_v2_entry,
    _admin_context,
    _call_rotate,
)
from .test_v2_config_flow import (
    MOCK_HOST,
    MOCK_PASSPHRASE,
    MOCK_V2_AUTH,
    MOCK_V2_DETECTION,
    MOCK_V2_DETECTION_PROXIMITY_PROVEN,
    _confirm_proximity,
    panel_ca_available,  # noqa: F401 -- autouse: answers the CA step without a socket
)

(PASSPHRASE_ONLY_VERSION,) = sorted(PASSPHRASE_ONLY_HARDWARE_VERSIONS)
VALIDATED_VERSION: Final = "2.0"
"""A validated hardware version string that offers proximity."""

BOTH_METHODS: Final = ["auth_passphrase", "auth_proximity"]


def _detection(hardware_version: str | None) -> DetectionResult:
    status = MOCK_V2_DETECTION.status_info
    assert status is not None
    return dataclasses.replace(
        MOCK_V2_DETECTION,
        status_info=dataclasses.replace(status, hardware_version=hardware_version),
    )


async def _start_user_flow(hass: HomeAssistant) -> ConfigFlowResult:
    """Submit the host and accept the CA, as far as the first authentication step."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    return await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_HOST: MOCK_HOST})


async def _submit_passphrase(hass: HomeAssistant, error: Exception) -> ConfigFlowResult:
    with (
        patch(
            "custom_components.span_panel.config_flow.detect_api_version",
            return_value=_detection(VALIDATED_VERSION),
        ),
        patch("custom_components.span_panel.config_flow.validate_host", return_value=True),
        patch(
            "custom_components.span_panel.config_flow.validate_v2_passphrase",
            side_effect=[error, MOCK_V2_AUTH],
        ),
    ):
        menu = await _start_user_flow(hass)
        form = await hass.config_entries.flow.async_configure(
            menu["flow_id"], {"next_step_id": "auth_passphrase"}
        )
        refused = await hass.config_entries.flow.async_configure(
            form["flow_id"], {CONF_HOP_PASSPHRASE: MOCK_PASSPHRASE}
        )
        retried = await hass.config_entries.flow.async_configure(
            refused["flow_id"], {CONF_HOP_PASSPHRASE: MOCK_PASSPHRASE}
        )
    assert retried["step_id"] == "choose_entity_naming_initial", "a retry registers"
    return refused


def _reauth_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        version=3,
        minor_version=1,
        domain=DOMAIN,
        title="Span Panel",
        data={CONF_HOST: MOCK_HOST, CONF_ACCESS_TOKEN: "old-v2-token", CONF_API_VERSION: "v2"},
        source=config_entries.SOURCE_USER,
        options={},
        unique_id="SPAN-V2-001",
    )
    entry.add_to_hass(hass)
    return entry


# ---------------------------------------------------------------------------
# Rate-limited registration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_rate_limited_passphrase_says_when_to_retry(hass: HomeAssistant) -> None:
    refused = await _submit_passphrase(
        hass, SpanPanelRateLimitError("429", status_code=429, retry_after_s=30.0)
    )

    assert refused["type"] == FlowResultType.FORM
    assert refused["step_id"] == "auth_passphrase"
    assert refused["errors"] == {"base": "rate_limited"}
    assert refused["description_placeholders"] == {"retry_after": "30"}


@pytest.mark.asyncio
async def test_a_fractional_wait_rounds_up_to_whole_seconds(hass: HomeAssistant) -> None:
    refused = await _submit_passphrase(
        hass, SpanPanelRateLimitError("429", status_code=429, retry_after_s=2.2)
    )

    assert refused["description_placeholders"] == {"retry_after": "3"}


@pytest.mark.parametrize("retry_after_s", [None, 0.0])
@pytest.mark.asyncio
async def test_a_rate_limit_without_a_wait_uses_its_own_message(
    hass: HomeAssistant, retry_after_s: float | None
) -> None:
    """No placeholder is ever left empty or reads as "try again in 0 seconds"."""
    refused = await _submit_passphrase(
        hass, SpanPanelRateLimitError("429", status_code=429, retry_after_s=retry_after_s)
    )

    assert refused["errors"] == {"base": "rate_limited_no_retry_after"}
    assert not refused["description_placeholders"]


@pytest.mark.asyncio
async def test_a_rate_limited_proximity_registration_says_when_to_retry(
    hass: HomeAssistant,
) -> None:
    with (
        patch(
            "custom_components.span_panel.config_flow.detect_api_version",
            side_effect=[
                MOCK_V2_DETECTION,
                MOCK_V2_DETECTION_PROXIMITY_PROVEN,
                MOCK_V2_DETECTION_PROXIMITY_PROVEN,
            ],
        ),
        patch("custom_components.span_panel.config_flow.validate_host", return_value=True),
        patch(
            "custom_components.span_panel.config_flow.validate_v2_proximity",
            side_effect=[
                SpanPanelRateLimitError("429", status_code=429, retry_after_s=30.0),
                MOCK_V2_AUTH,
            ],
        ),
    ):
        refused = await _confirm_proximity(hass)
        assert refused["step_id"] == "auth_proximity_confirm"
        assert refused["errors"] == {"base": "rate_limited"}
        assert refused["description_placeholders"] == {"retry_after": "30"}

        retried = await hass.config_entries.flow.async_configure(refused["flow_id"], {})
        assert retried["step_id"] == "choose_entity_naming_initial"


# ---------------------------------------------------------------------------
# Proximity only where the panel supports it
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("hardware_version", "passphrase_only"),
    [
        (PASSPHRASE_ONLY_VERSION, True),
        ("1.2", False),
        ("2.0", False),
        ("UNKNOWN", False),
        (None, False),
    ],
)
def test_only_a_listed_hardware_version_registers_by_passphrase_only(
    hardware_version: str | None, passphrase_only: bool
) -> None:
    assert registers_by_passphrase_only(hardware_version) is passphrase_only


@pytest.mark.asyncio
async def test_setup_goes_straight_to_the_passphrase_where_proximity_is_unsupported(
    hass: HomeAssistant,
) -> None:
    with (
        patch(
            "custom_components.span_panel.config_flow.detect_api_version",
            return_value=_detection(PASSPHRASE_ONLY_VERSION),
        ),
        patch("custom_components.span_panel.config_flow.validate_host", return_value=True),
        patch(
            "custom_components.span_panel.config_flow.validate_v2_passphrase",
            return_value=MOCK_V2_AUTH,
        ),
    ):
        form = await _start_user_flow(hass)
        assert form["type"] == FlowResultType.FORM
        assert form["step_id"] == "auth_passphrase"

        done = await hass.config_entries.flow.async_configure(
            form["flow_id"], {CONF_HOP_PASSPHRASE: MOCK_PASSPHRASE}
        )
        assert done["step_id"] == "choose_entity_naming_initial"


@pytest.mark.asyncio
async def test_reauth_offers_only_the_passphrase_where_proximity_is_unsupported(
    hass: HomeAssistant,
) -> None:
    entry = _reauth_entry(hass)
    with patch(
        "custom_components.span_panel.config_flow.detect_api_version",
        return_value=_detection(PASSPHRASE_ONLY_VERSION),
    ):
        menu = await entry.start_reauth_flow(hass)

    assert menu["type"] == FlowResultType.MENU
    assert menu["step_id"] == "reauth_confirm"
    assert menu["menu_options"] == ["auth_passphrase"]


@pytest.mark.parametrize("hardware_version", [VALIDATED_VERSION, None])
@pytest.mark.asyncio
async def test_every_other_panel_still_chooses_between_both_methods_at_setup(
    hass: HomeAssistant, hardware_version: str | None
) -> None:
    with (
        patch(
            "custom_components.span_panel.config_flow.detect_api_version",
            return_value=_detection(hardware_version),
        ),
        patch("custom_components.span_panel.config_flow.validate_host", return_value=True),
    ):
        menu = await _start_user_flow(hass)

    assert menu["type"] == FlowResultType.MENU
    assert menu["step_id"] == "choose_v2_auth"
    assert menu["menu_options"] == BOTH_METHODS


@pytest.mark.parametrize("hardware_version", [VALIDATED_VERSION, None])
@pytest.mark.asyncio
async def test_every_other_panel_still_chooses_between_both_methods_at_reauth(
    hass: HomeAssistant, hardware_version: str | None
) -> None:
    entry = _reauth_entry(hass)
    with patch(
        "custom_components.span_panel.config_flow.detect_api_version",
        return_value=_detection(hardware_version),
    ):
        menu = await entry.start_reauth_flow(hass)

    assert menu["step_id"] == "reauth_confirm"
    assert menu["menu_options"] == BOTH_METHODS


# ---------------------------------------------------------------------------
# Rotation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("hardware_version", "translation_key"),
    [
        (PASSPHRASE_ONLY_VERSION, "rotate_credentials_outcome_unknown_passphrase_only"),
        (VALIDATED_VERSION, "rotate_credentials_outcome_unknown"),
        (None, "rotate_credentials_outcome_unknown"),
    ],
)
@pytest.mark.asyncio
async def test_a_rotation_504_reports_the_recovery_the_panel_supports(
    hass: HomeAssistant,
    panel_status: AsyncMock,
    hardware_version: str | None,
    translation_key: str,
) -> None:
    """Outcome unknown either way; only a passphrase-only panel is sent to the SPAN app."""
    panel_status.return_value = dataclasses.replace(
        VALIDATED_PANEL_STATUS, hardware_version=hardware_version
    )
    _add_v2_entry(hass)
    _async_register_credential_services(hass)

    with (
        patch(
            "custom_components.span_panel.services.rotate_passphrase",
            AsyncMock(side_effect=SpanPanelServerError("504", status_code=504)),
        ),
        pytest.raises(HomeAssistantError) as err,
    ):
        await _call_rotate(hass, _admin_context(hass))

    assert err.value.translation_key == translation_key


@pytest.mark.asyncio
async def test_an_unreadable_status_keeps_the_message_every_panel_had(
    hass: HomeAssistant, panel_status: AsyncMock
) -> None:
    panel_status.side_effect = SpanPanelServerError("503", status_code=503)
    _add_v2_entry(hass)
    _async_register_credential_services(hass)

    with (
        patch(
            "custom_components.span_panel.services.rotate_passphrase",
            AsyncMock(side_effect=SpanPanelServerError("504", status_code=504)),
        ),
        pytest.raises(HomeAssistantError) as err,
    ):
        await _call_rotate(hass, _admin_context(hass))

    assert err.value.translation_key == "rotate_credentials_outcome_unknown"


@pytest.mark.asyncio
async def test_a_broker_that_accepts_the_new_password_after_30_s_reconnects_without_a_repair(
    hass: HomeAssistant, panel_status: AsyncMock
) -> None:
    """The reconnect schedule outlasts the window in which the broker still refuses."""
    panel_status.return_value = dataclasses.replace(
        VALIDATED_PANEL_STATUS, hardware_version=PASSPHRASE_ONLY_VERSION
    )
    _add_v2_entry(hass)
    _async_register_credential_services(hass)
    waited: list[float] = []

    async def _sleep(delay: float) -> None:
        waited.append(delay)

    async def _reload(_entry_id: str) -> bool:
        return sum(waited) >= 30.0

    with (
        patch(
            "custom_components.span_panel.services.rotate_passphrase",
            AsyncMock(return_value=ROTATION),
        ),
        patch.object(hass.config_entries, "async_reload", AsyncMock(side_effect=_reload)),
        patch("custom_components.span_panel.services.asyncio.sleep", AsyncMock(side_effect=_sleep)),
    ):
        response = await _call_rotate(hass, _admin_context(hass))

    assert response == {"hop_passphrase": NEW_HOP_PASSPHRASE, "reconnected": True}
    assert sum(waited) >= 30.0
    assert len(waited) < len(_ROTATION_RECONNECT_DELAYS_S), "inside the schedule, not at its end"
    assert not ir.async_get(hass).issues
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)
