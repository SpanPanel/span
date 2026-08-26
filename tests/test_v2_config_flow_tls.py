"""Config flow tests that run against a real TLS handshake.

The rest of the v2 config-flow suite stubs `async_leaf_chains_to_ca` and
`build_panel_ssl_context`, so hostname verification is never exercised there.
These tests live in their own module precisely so that autouse fixture does not
reach them: a certificate authority and a leaf are generated with
`cryptography`, a small HTTPS listener serves that leaf on loopback, and the
flow's own TLS code runs against it unmodified.

Everything here is synthetic — a throwaway CA, a self-signed panel leaf, and the
name `panel.home.lan` resolved to 127.0.0.1 for the duration of one test.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
import datetime
import http.server
import ipaddress
import json
import socket
import socketserver
import ssl
import threading
from typing import Any
from unittest.mock import AsyncMock, patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from homeassistant import config_entries
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
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

PANEL_FQDN = "panel.home.lan"
PANEL_LOOPBACK = "127.0.0.1"
PANEL_SERIAL = "sp3-synthetic-0001"
PASSPHRASE = "hunter2-synthetic"

# ---------- certificate generation ----------


def _issue_ca() -> tuple[x509.Certificate, rsa.RSAPrivateKey]:
    """Issue the throwaway CA that stands in for the one a panel publishes."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "SPAN Panel Test CA")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return cert, key


def _issue_leaf(
    ca_cert: x509.Certificate,
    ca_key: rsa.RSAPrivateKey,
    names: list[x509.GeneralName],
) -> tuple[str, str]:
    """Issue a server certificate naming exactly `names`, returning PEM cert and key."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "SPAN Panel")]))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName(names), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    return cert_pem, key_pem


def _ca_pem(ca_cert: x509.Certificate) -> str:
    return ca_cert.public_bytes(serialization.Encoding.PEM).decode()


# ---------- a listener that serves the panel's leaf ----------


class _PanelStatusHandler(http.server.BaseHTTPRequestHandler):
    """Answer the one unauthenticated probe `detect_api_version` makes."""

    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        body = json.dumps(
            {
                "serialNumber": PANEL_SERIAL,
                "firmwareVersion": "2.0.0-synthetic",
                "proximityProven": True,
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        """Keep the test output free of one line per handshake."""


class _PanelTLSListener(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """Serve HTTPS on loopback with a certificate the test can swap at will.

    Swappable because that is what the panel does: `register_fqdn` makes it
    regenerate its leaf to add the FQDN, and the flow is supposed to notice.
    """

    daemon_threads = True
    tls_context: ssl.SSLContext

    def get_request(self) -> tuple[socket.socket, Any]:
        sock, addr = super().get_request()
        return self.tls_context.wrap_socket(sock, server_side=True), addr

    def handle_error(self, request: Any, client_address: Any) -> None:
        """Stay quiet: the leaf check hangs up right after the handshake."""


def _server_context(cert_pem: str, key_pem: str, tmp_path: Any) -> ssl.SSLContext:
    """Build a server-side context from PEM text (`load_cert_chain` wants files)."""
    cert_file = tmp_path / f"leaf-{abs(hash(cert_pem))}.pem"
    key_file = tmp_path / f"key-{abs(hash(cert_pem))}.pem"
    cert_file.write_text(cert_pem)
    key_file.write_text(key_pem)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(cert_file), str(key_file))
    return ctx


class _Panel:
    """The synthetic panel: a CA, a leaf, and the port it serves TLS on."""

    def __init__(self, tmp_path: Any) -> None:
        self._tmp_path = tmp_path
        self._ca_cert, self._ca_key = _issue_ca()
        self.ca_pem = _ca_pem(self._ca_cert)
        self._listener: _PanelTLSListener | None = None
        self.port = 0

    def present(self, names: list[x509.GeneralName]) -> None:
        """Serve a freshly issued leaf naming exactly `names`."""
        cert_pem, key_pem = _issue_leaf(self._ca_cert, self._ca_key, names)
        self.present_pem(cert_pem, key_pem)

    def present_pem(self, cert_pem: str, key_pem: str) -> None:
        """Serve this certificate, whoever signed it."""
        context = _server_context(cert_pem, key_pem, self._tmp_path)
        if self._listener is None:
            self._listener = _PanelTLSListener((PANEL_LOOPBACK, 0), _PanelStatusHandler)
            self._listener.tls_context = context
            self.port = self._listener.server_port
            threading.Thread(target=self._listener.serve_forever, daemon=True).start()
        else:
            self._listener.tls_context = context

    def close(self) -> None:
        if self._listener is not None:
            self._listener.shutdown()
            self._listener.server_close()
            self._listener = None


@pytest.fixture
def panel(tmp_path: Any) -> Iterator[_Panel]:
    """Serve a leaf naming the panel's IP only, as one does before registration."""
    instance = _Panel(tmp_path)
    instance.present([x509.IPAddress(ipaddress.ip_address(PANEL_LOOPBACK))])
    yield instance
    instance.close()


@pytest.fixture
def resolves_to_loopback() -> Iterator[None]:
    """Point `panel.home.lan` at the loopback listener, and nothing else.

    The name arrives as `bytes` from anyio's resolver and as `str` from
    `socket.create_connection`, so both spellings are matched.
    """
    real_getaddrinfo = socket.getaddrinfo

    def _fake(host: Any, port: Any, *args: Any, **kwargs: Any) -> Any:
        name = host.decode("ascii") if isinstance(host, bytes) else host
        if name == PANEL_FQDN:
            return real_getaddrinfo(PANEL_LOOPBACK, port, *args, **kwargs)
        return real_getaddrinfo(host, port, *args, **kwargs)

    with patch("socket.getaddrinfo", side_effect=_fake):
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


def _registration_adds_the_fqdn(panel: _Panel) -> Callable[..., Any]:
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
async def test_fresh_fqdn_install_pins_and_completes(
    hass: HomeAssistant, panel: _Panel
) -> None:
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
    # was registered — not the other way round.
    assert fetch_ca.await_args.args[1] == PANEL_LOOPBACK
    assert validate.await_args.args[0] == PANEL_LOOPBACK
    assert register.await_args.args[0] == PANEL_LOOPBACK
    assert register.await_args.args[2] == PANEL_FQDN


@pytest.mark.usefixtures("socket_enabled", "resolves_to_loopback")
@pytest.mark.asyncio
async def test_fresh_install_still_refuses_a_leaf_the_ca_does_not_sign(
    hass: HomeAssistant, panel: _Panel
) -> None:
    """Bootstrapping over the IP must not turn the leaf check off."""
    stranger_cert, stranger_key = _issue_leaf(*_issue_ca(), [x509.DNSName("elsewhere.invalid")])
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
    hass: HomeAssistant, panel: _Panel
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


# ---------- Case B: a pinned IP entry reconfigured to an FQDN ----------


@pytest.mark.usefixtures("socket_enabled", "resolves_to_loopback")
@pytest.mark.asyncio
async def test_reconfigure_pinned_ip_entry_to_fqdn(
    hass: HomeAssistant, panel: _Panel
) -> None:
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
