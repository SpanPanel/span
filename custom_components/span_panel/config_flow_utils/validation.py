"""Validation utilities for Span Panel config flow."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
from pathlib import Path
import socket
import ssl
import tempfile

from homeassistant.core import HomeAssistant
from homeassistant.util.network import is_ipv4_address
from span_panel_api import V2AuthResponse, detect_api_version, download_ca_cert, register_v2

_LOGGER = logging.getLogger(__name__)


async def validate_host(
    hass: HomeAssistant,
    host: str,
    access_token: str | None = None,
    port: int = 80,
) -> bool:
    """Validate the host connection by probing the panel's status endpoint."""
    try:
        result = await detect_api_version(host, port=port)
        return result.api_version in ("v1", "v2")
    except Exception:
        return False


async def validate_auth_token(hass: HomeAssistant, host: str, access_token: str) -> bool:
    """Validate an auth token.

    For v2 panels, token validation is not applicable (passphrase auth is used).
    This function exists for backward compatibility with v1 config flow paths.
    """
    # v1 token validation is no longer supported since SpanPanelClient was removed.
    # v2 panels authenticate via passphrase → register_v2().
    _LOGGER.warning("validate_auth_token called but v1 REST validation is no longer available")
    return False


def validate_ipv4_address(host: str) -> bool:
    """Validate that the host is an IPv4 address."""
    return is_ipv4_address(host)


async def validate_v2_passphrase(host: str, passphrase: str, port: int = 80) -> V2AuthResponse:
    """Validate a v2 panel passphrase and return MQTT credentials.

    Raises:
        SpanPanelAuthError: on invalid passphrase (401/403).
        SpanPanelConnectionError: on network/timeout failures.
        SpanPanelTimeoutError: on request timeout.

    """
    return await register_v2(host, "Home Assistant", passphrase, port=port)


def is_fqdn(host: str) -> bool:
    """Determine if host is a Fully Qualified Domain Name (not IP, not mDNS).

    Returns True for domain names like 'span.home.lan' or 'panel.example.com'.
    Returns False for IP addresses, mDNS (.local) names, and single-label hostnames.
    """
    if is_ipv4_address(host):
        return False
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        pass
    if host.endswith(".local") or host.endswith(".local."):
        return False
    return "." in host


async def check_fqdn_tls_ready(fqdn: str, mqtts_port: int, http_port: int = 80) -> bool:
    """Check if the MQTTS server certificate includes the FQDN in its SAN.

    Downloads the CA certificate from the panel via HTTP, then attempts
    a TLS connection to the MQTTS port using the FQDN as server_hostname.
    If the TLS handshake succeeds with hostname verification, the panel
    has regenerated its certificate to include the FQDN.
    """
    try:
        ca_pem = await download_ca_cert(fqdn, port=http_port)
    except Exception:
        return False

    loop = asyncio.get_running_loop()

    def _check() -> bool:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED

        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False)  # noqa: SIM115
        tmp.write(ca_pem)
        tmp.close()
        ca_path = Path(tmp.name)
        try:
            ctx.load_verify_locations(str(ca_path))
            with (
                socket.create_connection((fqdn, mqtts_port), timeout=5) as sock,
                ctx.wrap_socket(sock, server_hostname=fqdn),
            ):
                return True
        except (ssl.SSLCertVerificationError, ssl.SSLError, OSError, TimeoutError):
            return False
        finally:
            ca_path.unlink(missing_ok=True)

    return await loop.run_in_executor(None, _check)


async def validate_v2_proximity(host: str, port: int = 80) -> V2AuthResponse:
    """Validate v2 panel proximity (door bypass) and return MQTT credentials.

    Calls register_v2 without a passphrase, which triggers door-bypass
    registration. The panel accepts this when the user opens/closes the
    door 3 times within the proximity window.

    Raises:
        SpanPanelAuthError: if proximity was not proven (door not opened).
        SpanPanelConnectionError: on network/timeout failures.
        SpanPanelTimeoutError: on request timeout.

    """
    return await register_v2(host, "Home Assistant", port=port)
