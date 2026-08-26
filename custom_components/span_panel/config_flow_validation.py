"""Validation helpers for Span Panel config flow."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
import ipaddress
import logging
import socket
import ssl

from homeassistant.core import HomeAssistant
from homeassistant.helpers.httpx_client import get_async_client
import httpx
from span_panel_api import (
    V2AuthResponse,
    build_panel_ssl_context,
    detect_api_version,
    download_ca_cert,
    register_v2,
)
from span_panel_api.exceptions import (
    SpanPanelAPIError,
    SpanPanelConnectionError,
    SpanPanelTimeoutError,
)

from .const import (
    CONF_HTTP_PORT,
    CONF_HTTPS_PORT,
    CONF_PANEL_CA_PEM,
    DEFAULT_HTTPS_PORT,
)

_LOGGER = logging.getLogger(__name__)


async def validate_host(
    hass: HomeAssistant,
    host: str,
    port: int = 80,
) -> bool:
    """Validate the host connection by probing the panel's status endpoint."""
    client = get_async_client(hass, verify_ssl=False)
    try:
        result = await detect_api_version(host, port=port, httpx_client=client)
    except (
        ValueError,
        OSError,
        SpanPanelAPIError,
        SpanPanelConnectionError,
        SpanPanelTimeoutError,
    ):
        return False
    if result.probe_failed:
        return False
    return result.api_version == "v2"


async def validate_v2_passphrase(
    host: str,
    passphrase: str,
    transport: PanelRestTransport,
) -> V2AuthResponse:
    """Validate a v2 panel passphrase and return MQTT credentials.

    `transport` is required rather than defaulted, because it is what carries
    the pinned CA and this is the call that sends the panel passphrase. A
    default would be a plaintext transport, and a caller that forgot to pass one
    would get the exchange this pinning exists to prevent without anything
    saying so. Every path that reaches here has a transport by construction: the
    CA step runs before authentication on initial setup, and a reauth of an
    entry that arrived unpinned is routed back through that step first.

    Raises:
        SpanPanelAuthError: on invalid passphrase (401/403).
        SpanPanelConnectionError: on network/timeout failures.
        SpanPanelTimeoutError: on request timeout.

    """
    return await register_v2(
        host,
        "Home Assistant",
        passphrase,
        port=transport.port,
        httpx_client=transport.httpx_client,
        ssl_context=transport.ssl_context,
    )


def is_ip_literal(host: str) -> bool:
    """Whether `host` is already an address rather than a name to be resolved."""
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def is_fqdn(host: str) -> bool:
    """Determine if host is a Fully Qualified Domain Name (not IP, not mDNS).

    Returns True for domain names like 'span.home.lan' or 'panel.example.com'.
    Returns False for IP addresses, mDNS (.local) names, and single-label hostnames.
    """
    if is_ip_literal(host):
        return False
    if host.endswith((".local", ".local.")):
        return False
    return "." in host


async def check_fqdn_tls_ready(
    hass: HomeAssistant,
    fqdn: str,
    mqtts_port: int,
    ca_pem: str | None,
    http_port: int = 80,
) -> bool:
    """Whether the panel now serves a certificate that names `fqdn`.

    A TLS connection is made to the MQTTS port with the FQDN as
    `server_hostname`; a handshake that completes with hostname verification on
    means the panel has regenerated its leaf to include the FQDN, which is what
    this is polling for.

    `ca_pem` is the anchor the caller already pinned, and passing it is the
    point: this used to refetch the CA over plain HTTP on every poll, which
    means a fresh trust decision every two seconds against whatever answered,
    made against a panel the caller had already anchored. Anything that can
    answer that fetch can also serve a leaf signed by the CA it just handed
    over, so the poll would have reported the FQDN ready on a certificate the
    pinned anchor does not sign.

    `None` is for the one caller that genuinely holds no anchor -- a
    reconfigure of an entry whose CA fetch never succeeded -- where the
    plaintext fetch is the only channel there is and is no weaker than the
    unpinned entry it belongs to.
    """
    if ca_pem is None:
        client = get_async_client(hass, verify_ssl=False)
        try:
            ca_pem = await download_ca_cert(fqdn, port=http_port, httpx_client=client)
        except (
            OSError,
            SpanPanelAPIError,
            SpanPanelConnectionError,
            SpanPanelTimeoutError,
        ):
            return False

    return await async_leaf_chains_to_ca(fqdn, mqtts_port, ca_pem)


async def async_resolve_host(hass: HomeAssistant, host: str) -> str | None:
    """Resolve `host` to the IPv4 address the panel answers on, or None.

    The panel's certificate names the addresses it knows itself by -- its IP and
    its mDNS name -- and an FQDN only joins that list once `register_fqdn` has
    run. Everything before that has to be dialled by an address the leaf already
    names, and this is where that address comes from.

    IPv4 only, and deliberately: the library builds bootstrap URLs by string
    concatenation and does not bracket an IPv6 literal, so returning one would
    produce a URL that cannot be parsed. `None` means "no answer this code can
    use", and the caller falls back to the name it was given rather than
    failing -- that is exactly the behaviour that shipped, so a host this
    resolver cannot help with is no worse off than before.
    """
    if is_ip_literal(host):
        return host

    def _resolve() -> str | None:
        try:
            infos = socket.getaddrinfo(host, None, family=socket.AF_INET, type=socket.SOCK_STREAM)
        except OSError:
            return None
        for *_unused, sockaddr in infos:
            return str(sockaddr[0])
        return None

    return await hass.async_add_executor_job(_resolve)


async def async_fetch_panel_ca(hass: HomeAssistant, host: str, http_port: int = 80) -> str:
    """Fetch the panel's CA over plain HTTP.

    Unauthenticated and unverified by construction — it fetches the very anchor
    everything else is checked against, so there is nothing for it to check
    itself against. Anything on the path can answer with a CA of its own. The
    caller owes the trust decision; see `async_leaf_chains_to_ca` for the one
    check that can be made without a value from another channel, and the confirm
    step for the fingerprint a user can compare against one.

    Raises whatever `download_ca_cert` raises; the caller decides what a failure
    to acquire means, and that differs between a config flow and a deferred fetch
    at setup.
    """
    return await download_ca_cert(
        host, port=http_port, httpx_client=get_async_client(hass, verify_ssl=False)
    )


async def async_leaf_chains_to_ca(host: str, tls_port: int, ca_pem: str) -> bool:
    """Whether the certificate served on `tls_port` validates against `ca_pem` alone.

    A CA that cannot validate the certificate the panel actually serves is not
    the panel's CA. That does not make a fetched PEM trustworthy — an attacker
    who can substitute the CA can serve a leaf signed by it — but it does catch
    the fetch that returned something unrelated, and it is the only check
    available without a fingerprint from another channel.

    Hostname verification stays on, so this also fails when the panel's
    certificate does not name the address being used, which is the condition
    `check_fqdn_tls_ready` is polling for.
    """
    loop = asyncio.get_running_loop()

    def _check() -> bool:
        try:
            # The library's builder, not a hand-rolled context: the panel's CA
            # omits the Authority Key Identifier extension, which Python's
            # default-on VERIFY_X509_STRICT rejects outright. A context built
            # here without clearing that flag fails against a healthy panel.
            ctx = build_panel_ssl_context(ca_pem)
        except (ssl.SSLError, ValueError):
            return False
        try:
            with (
                socket.create_connection((host, tls_port), timeout=5) as sock,
                ctx.wrap_socket(sock, server_hostname=host),
            ):
                return True
        except (ssl.SSLCertVerificationError, ssl.SSLError, OSError, TimeoutError):
            return False

    return await loop.run_in_executor(None, _check)


async def validate_v2_proximity(
    host: str,
    transport: PanelRestTransport,
) -> V2AuthResponse:
    """Validate v2 panel proximity (door bypass) and return MQTT credentials.

    Calls register_v2 without a passphrase, which triggers door-bypass
    registration. The panel accepts this when the user opens/closes the
    door 3 times within the proximity window.

    `transport` is required for the same reason as in `validate_v2_passphrase`:
    no passphrase crosses the wire here, but the broker password comes back, and
    a defaulted plaintext transport would return it in the clear.

    Raises:
        SpanPanelAuthError: if proximity was not proven (door not opened).
        SpanPanelConnectionError: on network/timeout failures.
        SpanPanelTimeoutError: on request timeout.

    """
    return await register_v2(
        host,
        "Home Assistant",
        port=transport.port,
        httpx_client=transport.httpx_client,
        ssl_context=transport.ssl_context,
    )


@dataclass(frozen=True, slots=True)
class PanelRestTransport:
    """How to reach one panel's bootstrap REST API.

    Two shapes, and the difference is not cosmetic. With a pinned CA the calls
    go to the TLS port under an anchor built from it, and Home Assistant's shared
    httpx client cannot be used — httpx fixes its trust store at construction, so
    a context cannot be applied to a client somebody else built, and the library
    builds a dedicated one rather than silently ignoring the pin. Without a pin
    they go to the plaintext port on the shared client, exactly as before.

    Mixing the two is refused by the library rather than guessed at: an explicit
    `port=80` alongside an `ssl_context` raises, because "HTTPS on port 80" and
    "a port stored before the CA was pinned" are both plausible readings.

    `ca_pem` is the anchor `ssl_context` was built from, carried alongside it
    because a context cannot be read back into a PEM and two callers need the
    text: the FQDN readiness poll, which verifies a different port against the
    same anchor, and anything that wants to fingerprint what it is pinned to.
    It is None exactly when `ssl_context` is.
    """

    port: int
    ssl_context: ssl.SSLContext | None
    httpx_client: httpx.AsyncClient | None
    ca_pem: str | None


def port_or_none(value: object) -> int | None:
    """Read a port out of untyped entry data, or None when it does not hold one.

    `entry.data` round-trips through JSON and is typed `Any` at the boundary; a
    hand-edited `.storage` can put a string there. `None` is the honest answer
    for anything unreadable, and it is a different answer from a default: a
    caller deciding whether the panel's TLS port is *known* must not be told yes
    because a substitute was available.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def as_port(value: object, default: int) -> int:
    """Read a port out of untyped entry data, falling back to `default`.

    A bad port must not be the reason an otherwise healthy entry cannot start,
    so this never raises. Use `port_or_none` where the difference between a
    stored value and a substituted one matters.
    """
    parsed = port_or_none(value)
    return default if parsed is None else parsed


def panel_rest_transport(
    hass: HomeAssistant, entry_data: Mapping[str, object]
) -> PanelRestTransport:
    """Decide how this entry's REST calls should reach the panel.

    A malformed stored PEM falls back to plaintext rather than raising. The
    alternative is a config entry that cannot make a single REST call until
    somebody edits `.storage` by hand, which is a worse outcome than the
    transport this entry had before it was pinned — and the CA-changed repair is
    the path back to a good pin.
    """
    pem = entry_data.get(CONF_PANEL_CA_PEM)
    if pem:
        try:
            context = build_panel_ssl_context(str(pem))
        except (ssl.SSLError, ValueError):
            _LOGGER.warning(
                "The stored panel CA is not a certificate this system accepts; "
                "falling back to plaintext HTTP for REST calls"
            )
        else:
            https_port = as_port(entry_data.get(CONF_HTTPS_PORT), DEFAULT_HTTPS_PORT)
            return PanelRestTransport(
                port=https_port,
                ssl_context=context,
                httpx_client=None,
                ca_pem=str(pem),
            )

    return PanelRestTransport(
        port=as_port(entry_data.get(CONF_HTTP_PORT), 80),
        ssl_context=None,
        httpx_client=get_async_client(hass, verify_ssl=False),
        ca_pem=None,
    )
