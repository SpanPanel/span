"""Config flow tests that run against a real TLS handshake.

The rest of the v2 config-flow suite stubs `async_leaf_chains_to_ca` and
`build_panel_ssl_context`, so hostname verification is never exercised there.
These tests live in their own module precisely so that autouse fixture does not
reach them: the synthetic panel in `tls_panel` generates a certificate authority
and a leaf, serves that leaf from a small HTTPS listener on loopback, and the
flow's own TLS code runs against it unmodified.

Everything is synthetic — a throwaway CA, a self-signed panel leaf, and the name
`panel.home.lan` resolved to 127.0.0.1 for the duration of one test.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
import contextlib
import ipaddress
from typing import Any
from unittest.mock import AsyncMock, patch

from cryptography import x509
from homeassistant import config_entries
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from span_panel_api import DetectionResult, V2AuthResponse, V2StatusInfo

from custom_components.span_panel.const import (
    CONF_API_VERSION,
    CONF_EBUS_BROKER_PORT,
    CONF_HOP_PASSPHRASE,
    CONF_HTTP_PORT,
    CONF_HTTPS_PORT,
    CONF_PANEL_CA_PEM,
    CONF_REGISTERED_FQDN,
    DOMAIN,
)

from .tls_panel import (
    LOOPBACK_NAMES,
    PANEL_FQDN,
    PANEL_LOOPBACK,
    PANEL_SERIAL,
    PANEL_SHORTNAME,
    Panel,
    issue_ca,
    issue_leaf,
    resolving_to_loopback,
    unrelated_ca_pem,
)

PASSPHRASE = "hunter2-synthetic"


@pytest.fixture
def panel(tmp_path: Any) -> Iterator[Panel]:
    """Serve a leaf naming the panel's IP only, as one does before registration."""
    instance = Panel(tmp_path)
    instance.present([x509.IPAddress(ipaddress.ip_address(PANEL_LOOPBACK))])
    yield instance
    instance.close()


@pytest.fixture
def resolves_to_loopback() -> Iterator[None]:
    """Point the panel's names at the loopback listener, and nothing else."""
    with resolving_to_loopback(*LOOPBACK_NAMES):
        yield


async def _finish_progress(hass: HomeAssistant, result: dict[str, Any]) -> Any:
    """Drive a `async_show_progress` step to whatever it resolves into.

    The registration task does real TLS work, so it is never done on the turn
    that starts it — unlike the rest of the suite, where every call inside it is
    a mock that completes immediately.
    """
    while result["type"] == FlowResultType.SHOW_PROGRESS:
        await hass.async_block_till_done()
        result = await hass.config_entries.flow.async_configure(result["flow_id"])
    if result["type"] == FlowResultType.SHOW_PROGRESS_DONE:
        result = await hass.config_entries.flow.async_configure(result["flow_id"])
    return result


def _auth_response(mqtts_port: int) -> V2AuthResponse:
    return V2AuthResponse(
        access_token="synthetic-token",
        token_type="bearer",
        iat_ms=1700000000000,
        ebus_broker_host=PANEL_LOOPBACK,
        ebus_broker_mqtts_port=mqtts_port,
        ebus_broker_ws_port=8080,
        ebus_broker_wss_port=8443,
        ebus_broker_username="span-user",
        ebus_broker_password="mqtt-secret",
        hostname="span-panel.local",
        serial_number=PANEL_SERIAL,
        hop_passphrase=PASSPHRASE,
    )


@contextlib.contextmanager
def _plaintext_bootstrap_stubbed(panel: Panel) -> Iterator[None]:
    """Stub everything that is not TLS: the probe, the reachability check, the CA fetch.

    The CA fetch is plain HTTP and the probe answers over it, so neither is what
    these tests are about. Everything downstream of them — context building,
    hostname verification, the handshake — runs for real against the listener.
    """
    with (
        patch(
            "custom_components.span_panel.config_flow.validate_host",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "custom_components.span_panel.config_flow.detect_api_version",
            new=AsyncMock(
                return_value=DetectionResult(
                    api_version="v2",
                    status_info=V2StatusInfo(
                        serial_number=PANEL_SERIAL, firmware_version="2.0.0-synthetic"
                    ),
                )
            ),
        ),
        patch(
            "custom_components.span_panel.config_flow.async_fetch_panel_ca",
            new=AsyncMock(return_value=panel.ca_pem),
        ),
    ):
        yield


async def _reach_the_auth_menu(hass: HomeAssistant, host: str, panel: Panel) -> Any:
    """Submit the host form and the TLS port, landing wherever the CA step lands.

    A non-default HTTP port is what makes the flow ask where TLS lives, which is
    how the listener's ephemeral port reaches it.
    """
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: host, CONF_HTTP_PORT: 8080}
    )
    assert result["step_id"] == "panel_https_port", result
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HTTPS_PORT: panel.port}
    )


def _registration_adds_the_fqdn(panel: Panel) -> Callable[..., Any]:
    """Return a `register_fqdn` stand-in with the side effect the real panel has."""

    async def _register(*args: Any, **kwargs: Any) -> None:
        panel.present(
            [
                x509.IPAddress(ipaddress.ip_address(PANEL_LOOPBACK)),
                x509.DNSName(PANEL_FQDN),
            ]
        )

    return _register


# ---------- Case A: a fresh install named by FQDN ----------


@pytest.mark.usefixtures("socket_enabled", "resolves_to_loopback")
@pytest.mark.asyncio
async def test_fresh_fqdn_install_pins_and_completes(hass: HomeAssistant, panel: Panel) -> None:
    """A fresh install by FQDN must reach entry creation with the CA pinned.

    The panel's leaf names its address, not the FQDN — the FQDN only lands in
    the SAN once `register_fqdn` has run, which is after authentication. So the
    pre-registration leaf check has to be made over the address the leaf names.
    """
    register = AsyncMock(side_effect=_registration_adds_the_fqdn(panel))
    fetch_ca = AsyncMock(return_value=panel.ca_pem)
    with (
        patch(
            "custom_components.span_panel.config_flow.validate_host",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "custom_components.span_panel.config_flow.detect_api_version",
            new=AsyncMock(
                return_value=DetectionResult(
                    api_version="v2",
                    status_info=V2StatusInfo(
                        serial_number=PANEL_SERIAL, firmware_version="2.0.0-synthetic"
                    ),
                )
            ),
        ),
        patch("custom_components.span_panel.config_flow.async_fetch_panel_ca", new=fetch_ca),
        patch(
            "custom_components.span_panel.config_flow.validate_v2_passphrase",
            new=AsyncMock(return_value=_auth_response(panel.port)),
        ) as validate,
        patch("custom_components.span_panel.config_flow.register_fqdn", new=register),
        patch("custom_components.span_panel.config_flow.asyncio.sleep", new=AsyncMock()),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        # A non-default HTTP port is what makes the flow ask where TLS lives,
        # which is how the listener's ephemeral port reaches it.
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: PANEL_FQDN, CONF_HTTP_PORT: 8080}
        )
        assert result["step_id"] == "panel_https_port", result
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HTTPS_PORT: panel.port}
        )
        assert result["type"] == FlowResultType.MENU, result
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "auth_passphrase"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOP_PASSPHRASE: PASSPHRASE}
        )
        # Registration runs as a background task behind a progress screen, and
        # the TLS work in it is real, so the flow is driven on from there.
        assert result["type"] == FlowResultType.SHOW_PROGRESS, result
        assert result["progress_action"] == "registering_fqdn"
        result = await _finish_progress(hass, result)
        assert result["step_id"] == "choose_entity_naming_initial", result
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] == FlowResultType.CREATE_ENTRY, result
    assert result["data"][CONF_HOST] == PANEL_FQDN
    assert result["data"][CONF_REGISTERED_FQDN] == PANEL_FQDN
    assert result["data"][CONF_PANEL_CA_PEM] == panel.ca_pem
    # The bootstrap ran over the address the leaf names, and the FQDN is what
    # was registered — not the other way round. The CA fetch is the exception
    # and stays on the name: it is plain HTTP, so which address answers it makes
    # no difference to what may be trusted.
    assert fetch_ca.await_args.args[1] == PANEL_FQDN
    assert validate.await_args.args[0] == PANEL_LOOPBACK
    assert register.await_args.args[0] == PANEL_LOOPBACK
    assert register.await_args.args[2] == PANEL_FQDN


@pytest.mark.usefixtures("socket_enabled", "resolves_to_loopback")
@pytest.mark.asyncio
async def test_fresh_install_still_refuses_a_leaf_the_ca_does_not_sign(
    hass: HomeAssistant, panel: Panel
) -> None:
    """Bootstrapping over the IP must not turn the leaf check off."""
    stranger_cert, stranger_key = issue_leaf(*issue_ca(), [x509.DNSName("elsewhere.invalid")])
    panel.present_pem(stranger_cert, stranger_key)
    with (
        patch(
            "custom_components.span_panel.config_flow.validate_host",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "custom_components.span_panel.config_flow.detect_api_version",
            new=AsyncMock(
                return_value=DetectionResult(
                    api_version="v2",
                    status_info=V2StatusInfo(
                        serial_number=PANEL_SERIAL, firmware_version="2.0.0-synthetic"
                    ),
                )
            ),
        ),
        patch(
            "custom_components.span_panel.config_flow.async_fetch_panel_ca",
            new=AsyncMock(return_value=panel.ca_pem),
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: PANEL_FQDN, CONF_HTTP_PORT: 8080}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HTTPS_PORT: panel.port}
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "panel_ca"
    assert result["errors"] == {"base": "ca_leaf_mismatch"}


@pytest.mark.usefixtures("socket_enabled", "resolves_to_loopback")
@pytest.mark.asyncio
async def test_registration_that_never_lands_does_not_report_success(
    hass: HomeAssistant, panel: Panel
) -> None:
    """A panel that accepts the registration but never serves the FQDN must not pass.

    Also pins down where the readiness poll gets its anchor: from the CA this
    flow already accepted, not from a fresh plaintext fetch every two seconds.
    """
    download = AsyncMock(return_value=panel.ca_pem)
    with (
        patch(
            "custom_components.span_panel.config_flow.validate_host",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "custom_components.span_panel.config_flow.detect_api_version",
            new=AsyncMock(
                return_value=DetectionResult(
                    api_version="v2",
                    status_info=V2StatusInfo(
                        serial_number=PANEL_SERIAL, firmware_version="2.0.0-synthetic"
                    ),
                )
            ),
        ),
        patch(
            "custom_components.span_panel.config_flow.async_fetch_panel_ca",
            new=AsyncMock(return_value=panel.ca_pem),
        ),
        patch(
            "custom_components.span_panel.config_flow.validate_v2_passphrase",
            new=AsyncMock(return_value=_auth_response(panel.port)),
        ),
        # The panel says yes and does nothing: its leaf still names only the IP.
        patch("custom_components.span_panel.config_flow.register_fqdn", new=AsyncMock()),
        patch("custom_components.span_panel.config_flow.asyncio.sleep", new=AsyncMock()),
        patch(
            "custom_components.span_panel.config_flow_validation.download_ca_cert",
            new=download,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: PANEL_FQDN, CONF_HTTP_PORT: 8080}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HTTPS_PORT: panel.port}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "auth_passphrase"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOP_PASSPHRASE: PASSPHRASE}
        )
        result = await _finish_progress(hass, result)

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "fqdn_failed"
    assert result["errors"] == {"base": "fqdn_registration_failed"}
    download.assert_not_awaited()


@pytest.mark.usefixtures("socket_enabled", "resolves_to_loopback")
@pytest.mark.asyncio
async def test_continuing_past_a_failed_registration_uses_the_panels_address(
    hass: HomeAssistant, panel: Panel
) -> None:
    """Continuing anyway must not pin the entry to the name that just failed.

    Registration is what would have made the panel serve the domain, so once it
    has failed the domain is a host the pinned anchor rejects — and an entry
    recording it could not reach its own panel. The address the panel was
    reached by is recorded instead, and no FQDN registration is claimed.
    """
    with (
        _plaintext_bootstrap_stubbed(panel),
        patch(
            "custom_components.span_panel.config_flow.validate_v2_passphrase",
            new=AsyncMock(return_value=_auth_response(panel.port)),
        ),
        # The panel says yes and does nothing: its leaf still names only the IP.
        patch("custom_components.span_panel.config_flow.register_fqdn", new=AsyncMock()),
        patch("custom_components.span_panel.config_flow.asyncio.sleep", new=AsyncMock()),
    ):
        result = await _reach_the_auth_menu(hass, PANEL_FQDN, panel)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "auth_passphrase"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOP_PASSPHRASE: PASSPHRASE}
        )
        result = await _finish_progress(hass, result)
        assert result["step_id"] == "fqdn_failed", result

        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        assert result["step_id"] == "choose_entity_naming_initial", result
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] == FlowResultType.CREATE_ENTRY, result
    assert result["data"][CONF_HOST] == PANEL_LOOPBACK
    assert CONF_REGISTERED_FQDN not in result["data"]
    assert result["data"][CONF_PANEL_CA_PEM] == panel.ca_pem


@pytest.mark.usefixtures("socket_enabled", "resolves_to_loopback")
@pytest.mark.asyncio
async def test_a_name_nothing_will_register_is_refused_rather_than_pinned(
    hass: HomeAssistant, panel: Panel
) -> None:
    """A single-label host is never registered, so it has to be named already.

    `is_fqdn` is False for it, so `_async_finalize_v2_auth` goes straight to
    entry creation with no registration in between. Bootstrapping over the
    resolved address gets the flow that far; persisting the name would leave an
    entry pinned to a host its own anchor rejects, so it fails closed instead.
    """
    with (
        _plaintext_bootstrap_stubbed(panel),
        patch(
            "custom_components.span_panel.config_flow.validate_v2_passphrase",
            new=AsyncMock(return_value=_auth_response(panel.port)),
        ),
    ):
        result = await _reach_the_auth_menu(hass, PANEL_SHORTNAME, panel)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "auth_passphrase"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOP_PASSPHRASE: PASSPHRASE}
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "panel_ca"
    assert result["errors"] == {"base": "ca_leaf_mismatch"}
    assert not hass.config_entries.async_entries(DOMAIN)


@pytest.mark.usefixtures("socket_enabled", "resolves_to_loopback")
@pytest.mark.asyncio
async def test_a_name_the_leaf_already_covers_is_kept_and_never_swapped_for_an_address(
    hass: HomeAssistant, panel: Panel
) -> None:
    """The host the user gave is tried first, so a panel that names it keeps it.

    This is the add-on and search-domain case: the panel serves under a
    hostname, its certificate says so, and there is nothing to work around.
    Preferring the resolved address here would have swapped a working host for
    one the certificate may not name at all.
    """
    panel.present(
        [
            x509.DNSName(PANEL_SHORTNAME),
            x509.IPAddress(ipaddress.ip_address(PANEL_LOOPBACK)),
        ]
    )
    with (
        _plaintext_bootstrap_stubbed(panel),
        patch(
            "custom_components.span_panel.config_flow.validate_v2_passphrase",
            new=AsyncMock(return_value=_auth_response(panel.port)),
        ) as validate,
    ):
        result = await _reach_the_auth_menu(hass, PANEL_SHORTNAME, panel)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "auth_passphrase"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOP_PASSPHRASE: PASSPHRASE}
        )
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] == FlowResultType.CREATE_ENTRY, result
    assert result["data"][CONF_HOST] == PANEL_SHORTNAME
    assert result["data"][CONF_PANEL_CA_PEM] == panel.ca_pem
    # Registration ran over the name too, not over an address substituted for it.
    assert validate.await_args.args[0] == PANEL_SHORTNAME


@pytest.mark.usefixtures("socket_enabled", "resolves_to_loopback")
@pytest.mark.asyncio
async def test_reauth_of_an_unpinned_fqdn_entry_will_not_pin_an_unnamed_host(
    hass: HomeAssistant, panel: Panel
) -> None:
    """Reauth acquires the anchor, and the entry's own host has to survive it.

    An entry recorded against an FQDN the panel does not name reaches reauth
    unpinned, gets bootstrapped over the address like any other, and would then
    have had the anchor written beside a host that anchor rejects. Nothing here
    registers the name, so the reauth fails closed and the entry is left as it
    was.
    """
    entry = MockConfigEntry(
        version=7,
        minor_version=1,
        domain=DOMAIN,
        title="SPAN Panel",
        data={
            CONF_HOST: PANEL_FQDN,
            CONF_ACCESS_TOKEN: "synthetic-token",
            CONF_API_VERSION: "v2",
            CONF_HTTPS_PORT: panel.port,
        },
        source=config_entries.SOURCE_USER,
        options={},
        unique_id=PANEL_SERIAL,
    )
    entry.add_to_hass(hass)

    with (
        _plaintext_bootstrap_stubbed(panel),
        patch(
            "custom_components.span_panel.config_flow.validate_v2_passphrase",
            new=AsyncMock(return_value=_auth_response(panel.port)),
        ),
    ):
        result = await entry.start_reauth_flow(hass)
        assert result["step_id"] == "reauth_confirm", result
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "auth_passphrase"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOP_PASSPHRASE: PASSPHRASE}
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "panel_ca"
    assert result["errors"] == {"base": "ca_leaf_mismatch"}
    # Nothing was written: no anchor beside a host it rejects, no new token.
    assert CONF_PANEL_CA_PEM not in entry.data
    assert entry.data[CONF_HOST] == PANEL_FQDN
    assert entry.data[CONF_ACCESS_TOKEN] == "synthetic-token"


# ---------- Case B: a pinned IP entry reconfigured to an FQDN ----------


@pytest.mark.usefixtures("socket_enabled", "resolves_to_loopback")
@pytest.mark.asyncio
async def test_reconfigure_pinned_ip_entry_to_fqdn(hass: HomeAssistant, panel: Panel) -> None:
    """A pinned entry moved to an FQDN must probe over the address the leaf names."""
    entry = MockConfigEntry(
        version=7,
        minor_version=1,
        domain=DOMAIN,
        title="SPAN Panel",
        data={
            CONF_HOST: PANEL_LOOPBACK,
            CONF_ACCESS_TOKEN: "synthetic-token",
            CONF_API_VERSION: "v2",
            CONF_EBUS_BROKER_PORT: panel.port,
            CONF_HTTPS_PORT: panel.port,
            CONF_PANEL_CA_PEM: panel.ca_pem,
        },
        source=config_entries.SOURCE_USER,
        options={},
        unique_id=PANEL_SERIAL,
    )
    entry.add_to_hass(hass)

    register = AsyncMock(side_effect=_registration_adds_the_fqdn(panel))
    with (
        patch("custom_components.span_panel.config_flow.register_fqdn", new=register),
        patch("custom_components.span_panel.config_flow.asyncio.sleep", new=AsyncMock()),
    ):
        result = await entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: PANEL_FQDN}
        )
        assert result["type"] == FlowResultType.SHOW_PROGRESS, result
        result = await _finish_progress(hass, result)
        await hass.async_block_till_done()

    assert result["type"] == FlowResultType.ABORT, result
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_HOST] == PANEL_FQDN
    assert entry.data[CONF_REGISTERED_FQDN] == PANEL_FQDN
    assert entry.data[CONF_PANEL_CA_PEM] == panel.ca_pem
    assert register.await_args.args[0] == PANEL_LOOPBACK
    assert register.await_args.args[2] == PANEL_FQDN


def _pinned_entry(hass: HomeAssistant, panel: Panel) -> MockConfigEntry:
    """Add an entry already pinned to this panel and reaching it by address."""
    entry = MockConfigEntry(
        version=7,
        minor_version=1,
        domain=DOMAIN,
        title="SPAN Panel",
        data={
            CONF_HOST: PANEL_LOOPBACK,
            CONF_ACCESS_TOKEN: "synthetic-token",
            CONF_API_VERSION: "v2",
            CONF_EBUS_BROKER_PORT: panel.port,
            CONF_HTTPS_PORT: panel.port,
            CONF_PANEL_CA_PEM: panel.ca_pem,
        },
        source=config_entries.SOURCE_USER,
        options={},
        unique_id=PANEL_SERIAL,
    )
    entry.add_to_hass(hass)
    return entry


@pytest.mark.usefixtures("socket_enabled", "resolves_to_loopback")
@pytest.mark.asyncio
async def test_continuing_past_a_failed_reconfigure_keeps_the_panels_address(
    hass: HomeAssistant, panel: Panel
) -> None:
    """The reconfigure "continue anyway" makes the same trade as the install one.

    An entry that was reaching its panel by address stays that way. Writing the
    domain would move a working entry onto a host the anchor it is already
    pinned to rejects, which is a worse outcome than the move simply not
    happening.
    """
    entry = _pinned_entry(hass, panel)

    with (
        # The panel says yes and does nothing: its leaf still names only the IP.
        patch("custom_components.span_panel.config_flow.register_fqdn", new=AsyncMock()),
        patch("custom_components.span_panel.config_flow.asyncio.sleep", new=AsyncMock()),
    ):
        result = await entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: PANEL_FQDN}
        )
        result = await _finish_progress(hass, result)
        assert result["step_id"] == "reconfigure_fqdn_failed", result

        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        await hass.async_block_till_done()

    assert result["type"] == FlowResultType.ABORT, result
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_HOST] == PANEL_LOOPBACK
    assert CONF_REGISTERED_FQDN not in entry.data
    assert entry.data[CONF_PANEL_CA_PEM] == panel.ca_pem


@pytest.mark.usefixtures("socket_enabled", "resolves_to_loopback")
@pytest.mark.asyncio
async def test_reconfigure_refuses_a_name_the_pinned_certificate_does_not_cover(
    hass: HomeAssistant, panel: Panel
) -> None:
    """A move to a single-label name is never registered, so it must already be named.

    The address bootstrap is what lets the probe reach the panel at all; it is
    not permission to store a host the pinned certificate rejects, and nothing
    on this branch will ask the panel to start serving it.

    Refused as a *naming* failure, not a signing one. The panel answered and its
    certificate chains to the pinned anchor -- nothing else holds a key that
    anchor signed -- so telling the user it "is not signed by the authority it
    published" would accuse the network of interception over a name the panel
    simply has not been told about. The remedies differ too, and only the
    naming message can name them.
    """
    entry = _pinned_entry(hass, panel)

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: PANEL_SHORTNAME}
    )

    assert result["type"] == FlowResultType.FORM, result
    assert result["step_id"] == "reconfigure"
    assert result["errors"] == {"base": "ca_name_mismatch"}
    assert entry.data[CONF_HOST] == PANEL_LOOPBACK


# ---------- Case C: an already-configured entry follows a host only under its pin ----------

#: Where the pinned entry already reaches its panel. RFC 5737 TEST-NET-1, so
#: nothing routes there: the guard is about the *candidate* host, and the host
#: the entry already holds is never dialled.
PANEL_ADDRESS = "192.0.2.10"


def _entry_reached_at_the_address(
    hass: HomeAssistant, ca_pem: str | None, tls_port: int
) -> MockConfigEntry:
    """Add an entry for this panel at `PANEL_ADDRESS`, pinned to `ca_pem`.

    `None` is the entry that never acquired an anchor. It is what proves the
    guard leaves that install exactly as it was rather than freezing its host.
    """
    data: dict[str, Any] = {
        CONF_HOST: PANEL_ADDRESS,
        CONF_ACCESS_TOKEN: "synthetic-token",
        CONF_API_VERSION: "v2",
        CONF_HTTPS_PORT: tls_port,
    }
    if ca_pem is not None:
        data[CONF_PANEL_CA_PEM] = ca_pem
    entry = MockConfigEntry(
        version=7,
        minor_version=1,
        domain=DOMAIN,
        title="SPAN Panel",
        data=data,
        source=config_entries.SOURCE_USER,
        options={},
        unique_id=PANEL_SERIAL,
    )
    entry.add_to_hass(hass)
    return entry


async def _announce_from(hass: HomeAssistant, host: str) -> Any:
    """Run an `_ebus._tcp` announcement claiming this panel's serial, from `host`."""
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


@pytest.mark.usefixtures("socket_enabled", "resolves_to_loopback")
@pytest.mark.asyncio
async def test_zeroconf_will_not_move_a_pinned_entry_to_a_host_its_anchor_rejects(
    hass: HomeAssistant, panel: Panel
) -> None:
    """Anything on the LAN can announce this serial; only the pin decides who gets the entry.

    The listener here is the impostor: it answers on the port the entry uses,
    serves a perfectly valid leaf, and the certificate is signed by an authority
    the entry is not pinned to. Following it would point the entry at a host its
    own anchor rejects and hand the user a CA-changed repair inviting them to
    accept the impostor's fingerprint.
    """
    entry = _entry_reached_at_the_address(hass, unrelated_ca_pem(), panel.port)

    with _plaintext_bootstrap_stubbed(panel):
        result = await _announce_from(hass, PANEL_LOOPBACK)

    assert result["type"] == FlowResultType.ABORT, result
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == PANEL_ADDRESS


@pytest.mark.usefixtures("socket_enabled", "resolves_to_loopback")
@pytest.mark.asyncio
async def test_zeroconf_moves_a_pinned_entry_to_a_host_the_pin_validates(
    hass: HomeAssistant, panel: Panel
) -> None:
    """A panel that moved address is still the panel, and the entry has to follow it.

    The whole point of the host update: DHCP hands the panel a new lease, mDNS
    announces it, and the entry has to end up there. The pin is what tells the
    two cases apart, so the check has to pass here or the guard has simply
    turned discovery off.
    """
    entry = _entry_reached_at_the_address(hass, panel.ca_pem, panel.port)

    with _plaintext_bootstrap_stubbed(panel):
        result = await _announce_from(hass, PANEL_LOOPBACK)

    assert result["type"] == FlowResultType.ABORT, result
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == PANEL_LOOPBACK


@pytest.mark.usefixtures("socket_enabled", "resolves_to_loopback")
@pytest.mark.asyncio
async def test_zeroconf_still_moves_an_unpinned_entry(hass: HomeAssistant, panel: Panel) -> None:
    """An entry with no anchor has nothing to check against, and keeps what it had.

    There is no pin to protect and no check that could be made, so refusing the
    move would only stop an unpinned entry following its panel — a regression
    against the behaviour that shipped, bought with no security at all.
    """
    entry = _entry_reached_at_the_address(hass, None, panel.port)

    with _plaintext_bootstrap_stubbed(panel):
        result = await _announce_from(hass, PANEL_LOOPBACK)

    assert result["type"] == FlowResultType.ABORT, result
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == PANEL_LOOPBACK


@pytest.mark.usefixtures("socket_enabled", "resolves_to_loopback")
@pytest.mark.asyncio
async def test_re_adding_a_pinned_panel_by_an_unserved_name_leaves_its_host_alone(
    hass: HomeAssistant, panel: Panel
) -> None:
    """Re-adding by hand goes through the same update, and reaches it before the CA step.

    A user who types a single-label name for a panel already set up gets
    `already_configured` either way — but the abort used to rewrite the entry's
    host on the way out, to a name the panel's certificate does not carry. The
    entry then could not connect to its own panel and there was no flow left to
    correct it.
    """
    entry = _pinned_entry(hass, panel)

    with _plaintext_bootstrap_stubbed(panel):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: PANEL_SHORTNAME, CONF_HTTP_PORT: 8080}
        )

    assert result["type"] == FlowResultType.ABORT, result
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == PANEL_LOOPBACK


@pytest.mark.usefixtures("socket_enabled", "resolves_to_loopback")
@pytest.mark.asyncio
async def test_re_adding_a_pinned_panel_by_a_name_it_serves_moves_the_entry(
    hass: HomeAssistant, panel: Panel
) -> None:
    """The same path must still let a user correct the host to a name that works."""
    panel.present(
        [
            x509.DNSName(PANEL_SHORTNAME),
            x509.IPAddress(ipaddress.ip_address(PANEL_LOOPBACK)),
        ]
    )
    entry = _pinned_entry(hass, panel)

    with _plaintext_bootstrap_stubbed(panel):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: PANEL_SHORTNAME, CONF_HTTP_PORT: 8080}
        )

    assert result["type"] == FlowResultType.ABORT, result
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == PANEL_SHORTNAME


@pytest.fixture
def moved_panel(tmp_path: Any) -> Iterator[Panel]:
    """Serve a leaf naming an address the panel no longer has, as a moved lease does.

    The listener is on loopback, so the flow reaches it while its certificate
    names 10.0.0.99 and nothing else. That is what a DHCP move looks like from
    the integration's side, and it is chain-valid throughout -- the panel is
    still the panel, and still the only holder of a key the pinned anchor
    signed.
    """
    instance = Panel(tmp_path)
    instance.present([x509.IPAddress(ipaddress.ip_address("10.0.0.99"))])
    yield instance
    instance.close()


@pytest.mark.usefixtures("socket_enabled", "resolves_to_loopback")
@pytest.mark.asyncio
async def test_a_moved_panel_can_be_reconfigured_to_an_fqdn(
    hass: HomeAssistant, moved_panel: Panel
) -> None:
    """The case that had no way out before this.

    The panel's certificate names neither the address it now answers on nor the
    name being moved to, so every probe over the pinned transport failed
    hostname verification and the flow reported it unreachable. Reconfigure was
    the documented remedy and refused for the same reason, the mDNS move-guard
    refused, and reauth offers no host field -- deleting the entry was the only
    route left.

    Registration is what repairs it: the flow probes over a transport that keeps
    the pin and drops only the name binding, asks the panel to regenerate its
    certificate around the FQDN, and stores the name once the panel serves it.
    """
    entry = _pinned_entry(hass, moved_panel)

    register = AsyncMock(side_effect=_registration_adds_the_fqdn(moved_panel))
    with (
        patch("custom_components.span_panel.config_flow.register_fqdn", new=register),
        patch("custom_components.span_panel.config_flow.asyncio.sleep", new=AsyncMock()),
    ):
        result = await entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: PANEL_FQDN}
        )
        result = await _finish_progress(hass, result)
        await hass.async_block_till_done()

    assert result["type"] == FlowResultType.ABORT, result
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_HOST] == PANEL_FQDN
    assert entry.data[CONF_REGISTERED_FQDN] == PANEL_FQDN
    # The anchor is untouched throughout: nothing here is a CA change.
    assert entry.data[CONF_PANEL_CA_PEM] == moved_panel.ca_pem
    assert register.await_args.args[2] == PANEL_FQDN


@pytest.mark.usefixtures("socket_enabled", "resolves_to_loopback")
@pytest.mark.asyncio
async def test_a_moved_panel_is_still_refused_at_a_bare_address(
    hass: HomeAssistant, moved_panel: Panel
) -> None:
    """Reaching the panel is not permission to store the address it was reached at.

    Nothing on this branch asks the panel to start naming the address, so the
    entry would be stored pointing somewhere the coordinator and the broker both
    reject -- they verify the hostname and cannot be relaxed the way one flow's
    own probe can.
    """
    entry = _pinned_entry(hass, moved_panel)

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: PANEL_LOOPBACK}
    )

    assert result["type"] == FlowResultType.FORM, result
    assert result["errors"] == {"base": "ca_name_mismatch"}
    assert entry.data[CONF_HOST] == PANEL_LOOPBACK


@pytest.mark.usefixtures("socket_enabled", "resolves_to_loopback")
@pytest.mark.asyncio
async def test_an_impostor_is_still_refused_as_a_signing_failure(
    hass: HomeAssistant, panel: Panel
) -> None:
    """The guarantee the relaxed probe must not weaken.

    Relaxing the name binding leaves the chain, the signature and the expiry
    verified against the pinned anchor, so a host without a key that anchor
    signed fails exactly as it did before -- and is reported as what it is,
    rather than as a naming problem.
    """
    entry = _pinned_entry(hass, panel)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_PANEL_CA_PEM: unrelated_ca_pem()}
    )

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: PANEL_LOOPBACK}
    )

    assert result["type"] == FlowResultType.FORM, result
    assert result["errors"] == {"base": "ca_leaf_mismatch"}


@pytest.mark.usefixtures("socket_enabled", "resolves_to_loopback")
@pytest.mark.asyncio
async def test_an_unreachable_host_is_not_accused_of_interception(
    hass: HomeAssistant, panel: Panel
) -> None:
    """An unplugged cable used to be reported as an unsigned certificate.

    `async_leaf_chains_to_ca` answered False for a timeout and a verification
    failure alike, so a host that never answered produced "the certificate this
    panel serves is not signed by the authority it published" -- an accusation
    about a certificate nothing ever presented.
    """
    entry = _pinned_entry(hass, panel)

    # A port on loopback with nothing listening: connection refused, no TLS.
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_HTTPS_PORT: _closed_port()}
    )

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: PANEL_LOOPBACK}
    )

    assert result["type"] == FlowResultType.FORM, result
    assert result["errors"] == {"base": "cannot_connect"}


def _closed_port() -> int:
    """Return a port nothing is listening on, by binding and immediately closing."""
    import socket as _socket

    with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.mark.usefixtures("socket_enabled", "resolves_to_loopback")
@pytest.mark.asyncio
async def test_a_moved_panel_whose_registration_fails_has_nothing_to_continue_to(
    hass: HomeAssistant, moved_panel: Panel
) -> None:
    """"Continue anyway" must not store a name the panel never started serving.

    On every other path there is a verified address to fall back to -- the one
    the flow reached the panel by. On this one there is not: the certificate
    names neither the FQDN nor anything reachable, which is why the flow relaxed
    the name binding for its own probe in the first place, and registration was
    the single thing that would have fixed that. It failed.

    Falling back therefore has nowhere to fall back to, and continuing would
    write the unserved name and report success -- stranding every runtime
    connection on the hostname check, which the coordinator and the broker apply
    and cannot relax. The entry is left exactly as it was.
    """
    entry = _pinned_entry(hass, moved_panel)

    with (
        # The panel accepts the call and regenerates nothing, so its leaf still
        # names the address it no longer has.
        patch("custom_components.span_panel.config_flow.register_fqdn", new=AsyncMock()),
        patch("custom_components.span_panel.config_flow.asyncio.sleep", new=AsyncMock()),
    ):
        result = await entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: PANEL_FQDN}
        )
        result = await _finish_progress(hass, result)
        assert result["step_id"] == "reconfigure_fqdn_failed", result

        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        await hass.async_block_till_done()

    assert result["type"] == FlowResultType.FORM, result
    assert result["step_id"] == "reconfigure"
    assert result["errors"] == {"base": "ca_name_mismatch"}
    # Untouched: the entry still points where it did, and is still pinned.
    assert entry.data[CONF_HOST] == PANEL_LOOPBACK
    assert CONF_REGISTERED_FQDN not in entry.data
    assert entry.data[CONF_PANEL_CA_PEM] == moved_panel.ca_pem

    # And the form it lands on is live, not a dead end: submitting it re-enters
    # the reconfigure step and is judged again, rather than wedging the flow.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: PANEL_LOOPBACK}
    )
    assert result["type"] == FlowResultType.FORM, result
    assert result["step_id"] == "reconfigure"
    assert result["errors"] == {"base": "ca_name_mismatch"}
    assert entry.data[CONF_HOST] == PANEL_LOOPBACK
