"""The config flow's schema read, run through the library's own transport.

Each step that adds a panel reads `/api/v2/homie/schema` to refuse flat-schema
firmware, and it does so before any CA is pinned, over plaintext. The endpoint
is unauthenticated and carries no credential, so the read must not raise the
library's plaintext warning, which names the passphrase and the broker password
as readable on the path. Nor may it spend the once-per-panel slot that warning
keeps for a request that does carry one.

Every other config-flow test stands in for `get_homie_schema` (the autouse
`panel_schema` in `conftest.py`), so the library's request path never ran under
them and nothing could see the warning. Here only the panel is fake: the flow's
shared-client lookups hand out a client from Home Assistant's own factory whose
transport is an `httpx.MockTransport`, and the rest runs unmodified -- the
flow's steps, the library's request path and its warning.

Each case dials its own host, one no other test uses. The library says the
warning once per host per process, so a host that an earlier test had been
warned about would stay silent here, and the case would pass for the wrong
reason. A host fresh to the process is also what lets the last check expect
exactly one warning.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
import ipaddress
import logging
from typing import Final
from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.httpx_client import create_async_httpx_client
from homeassistant.helpers.service_info.hassio import HassioServiceInfo
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
import httpx
import pytest
from span_panel_api import register_v2
from span_panel_api.exceptions import SpanPanelAuthError

from custom_components.span_panel.const import CONF_HTTP_PORT, DOMAIN

# One TEST-NET-1 address per case, each used by no other test in this suite.
ZEROCONF_HOST: Final = "192.0.2.41"
SUPERVISOR_HOST: Final = "192.0.2.42"
USER_HOST: Final = "192.0.2.43"

PANEL_SERIAL: Final = "sp3-synthetic-0041"
PANEL_FIRMWARE: Final = "spanos3/r202639/03"
SYNTHETIC_PASSPHRASE: Final = "synthetic-passphrase"

#: The logger the library's plaintext warning is said on.
TRANSPORT_LOGGER: Final = "span_panel_api._http"

# The panel's wire API, which the fake panel serves. Spelled out here rather
# than read from the library, so the fake does not move when the library does.
STATUS_PATH: Final = "/api/v2/status"
SCHEMA_PATH: Final = "/api/v2/homie/schema"
REGISTER_PATH: Final = "/api/v2/auth/register"

STATUS_BODY: Final[Mapping[str, object]] = {
    "serialNumber": PANEL_SERIAL,
    "firmwareVersion": PANEL_FIRMWARE,
}

#: A parent/child schema: `dataModelVersion` is what makes it not flat.
SCHEMA_BODY: Final[Mapping[str, object]] = {
    "firmwareVersion": PANEL_FIRMWARE,
    "dataModelVersion": "1.0",
    "deviceClasses": {},
}

# Where the flow looks up Home Assistant's shared client on the way to the
# schema read: the discovery and user steps, and the user step's reachability
# check.
_CONFIG_FLOW_CLIENT = "custom_components.span_panel.config_flow.get_async_client"
_VALIDATION_CLIENT = "custom_components.span_panel.config_flow_validation.get_async_client"


class FakePanel:
    """A parent/child panel's bootstrap API as httpx's transport meets it."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Serve the panel behind a client built the way Home Assistant builds its shared one."""
        self.requests: list[tuple[str, str]] = []
        self.client = create_async_httpx_client(
            hass, verify_ssl=False, transport=httpx.MockTransport(self._answer)
        )

    def _answer(self, request: httpx.Request) -> httpx.Response:
        """Record one request and answer it as the panel would."""
        asked = (request.method, request.url.path)
        self.requests.append(asked)
        if asked == ("GET", STATUS_PATH):
            return httpx.Response(200, json=STATUS_BODY)
        if asked == ("GET", SCHEMA_PATH):
            return httpx.Response(200, json=SCHEMA_BODY)
        if asked == ("POST", REGISTER_PATH):
            # Refused: only that the credential was sent matters, not whether it was accepted.
            return httpx.Response(401)
        return httpx.Response(404)


@pytest.fixture(autouse=True)
def panel_schema() -> None:
    """Leave the schema read to the library, because it is what this module tests.

    Overrides the autouse stand-in of the same name in `conftest.py`.
    """


@pytest.fixture
async def panel(hass: HomeAssistant) -> AsyncIterator[FakePanel]:
    """Answer every request the flow makes to the panel from the fake one."""
    fake = FakePanel(hass)
    with (
        patch(_CONFIG_FLOW_CLIENT, return_value=fake.client),
        patch(_VALIDATION_CLIENT, return_value=fake.client),
    ):
        yield fake


def _plaintext_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Return what the library's transport has warned about so far in this test."""
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == TRANSPORT_LOGGER and record.levelno >= logging.WARNING
    ]


async def _discover_by_zeroconf(hass: HomeAssistant, host: str) -> ConfigFlowResult:
    """Announce a real panel over `_ebus._tcp`, on port 80, so with no `httpPort`."""
    address = ipaddress.IPv4Address(host)
    return await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_ZEROCONF},
        data=ZeroconfServiceInfo(
            ip_address=address,
            ip_addresses=[address],
            hostname="span-panel.local.",
            name="SPAN Panel._ebus._tcp.local.",
            port=8883,
            properties={},
            type="_ebus._tcp.local.",
        ),
    )


async def _discover_by_supervisor(hass: HomeAssistant, host: str) -> ConfigFlowResult:
    """Discover a panel through an add-on, which publishes the ports it allocated."""
    return await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_HASSIO},
        data=HassioServiceInfo(
            config={
                "host": host,
                "port": 9090,
                "https_port": 9443,
                "serial": PANEL_SERIAL,
            },
            name="SPAN Panel Simulator",
            slug="span_panel_simulator",
            uuid="synthetic-uuid-0041",
        ),
    )


async def _add_by_hand(hass: HomeAssistant, host: str) -> ConfigFlowResult:
    """Submit the user step with a moved HTTP port.

    A moved HTTP port makes the flow ask where TLS is served before it fetches
    the CA, so the schema read is the last request the step makes.
    """
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM, result
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: host, CONF_HTTP_PORT: 8080}
    )


type StepDriver = Callable[[HomeAssistant, str], Awaitable[ConfigFlowResult]]


@pytest.mark.parametrize(
    ("drive", "host", "next_step"),
    [
        pytest.param(_discover_by_zeroconf, ZEROCONF_HOST, "confirm_discovery", id="zeroconf"),
        pytest.param(
            _discover_by_supervisor, SUPERVISOR_HOST, "confirm_discovery", id="supervisor"
        ),
        pytest.param(_add_by_hand, USER_HOST, "panel_https_port", id="user"),
    ],
)
async def test_reading_the_schema_before_the_pin_raises_no_plaintext_warning(
    hass: HomeAssistant,
    panel: FakePanel,
    caplog: pytest.LogCaptureFixture,
    drive: StepDriver,
    host: str,
    next_step: str,
) -> None:
    """The unpinned schema read says nothing, and leaves the warning for a credential."""
    caplog.set_level(logging.WARNING, logger=TRANSPORT_LOGGER)

    result = await drive(hass, host)

    # The step read the schema through the library's transport and stopped at
    # the step after it. Nothing was requested after the read, so whatever has
    # been logged by now is everything logged up to it.
    assert result["type"] == FlowResultType.FORM, result
    assert result["step_id"] == next_step
    assert panel.requests[-1] == ("GET", SCHEMA_PATH), panel.requests
    assert _plaintext_warnings(caplog) == []

    # Nor did the read spend the panel's warning: the first request that does
    # carry a credential is still warned about. This is also what makes the
    # silence above meaningful, because it shows the warning is seen when said.
    with pytest.raises(SpanPanelAuthError):
        await register_v2(host, "Home Assistant", SYNTHETIC_PASSPHRASE, httpx_client=panel.client)
    assert panel.requests[-1] == ("POST", REGISTER_PATH)
    assert len(_plaintext_warnings(caplog)) == 1
