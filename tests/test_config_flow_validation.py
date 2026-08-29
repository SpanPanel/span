"""Tests for Span Panel config flow validation helpers."""

from __future__ import annotations

import socket
import ssl
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from span_panel_api import DetectionResult, V2AuthResponse, V2StatusInfo
from span_panel_api.exceptions import SpanPanelConnectionError

from custom_components.span_panel.config_flow_validation import (
    PanelRestTransport,
    async_download_ca_or_none,
    async_leaf_chains_to_ca,
    async_resolve_host,
    check_fqdn_tls_ready,
    is_fqdn,
    port_or_none,
    validate_host,
    validate_v2_passphrase,
    validate_v2_proximity,
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
    hop_passphrase="correct-horse-battery-staple",
)


@pytest.mark.asyncio
async def test_validate_host_returns_true_for_supported_versions() -> None:
    """Supported API versions should count as valid hosts."""
    hass = MagicMock()
    fake_client = MagicMock()
    with (
        patch(
            "custom_components.span_panel.config_flow_validation.get_async_client",
            return_value=fake_client,
        ) as mock_get_client,
        patch(
            "custom_components.span_panel.config_flow_validation.detect_api_version",
            return_value=DetectionResult(
                api_version="v2",
                status_info=V2StatusInfo(
                    serial_number="SPAN-V2-001", firmware_version="2.0.0"
                ),
            ),
        ) as mock_detect,
    ):
        assert await validate_host(hass, "panel.example.com", port=8080) is True

    mock_get_client.assert_called_once_with(hass, verify_ssl=False)
    mock_detect.assert_awaited_once_with(
        "panel.example.com", port=8080, httpx_client=fake_client
    )


@pytest.mark.asyncio
async def test_validate_host_returns_false_on_detection_error() -> None:
    """Detection failures should be treated as invalid hosts."""
    hass = MagicMock()
    fake_client = MagicMock()
    with (
        patch(
            "custom_components.span_panel.config_flow_validation.get_async_client",
            return_value=fake_client,
        ),
        patch(
            "custom_components.span_panel.config_flow_validation.detect_api_version",
            side_effect=SpanPanelConnectionError("boom"),
        ),
    ):
        assert await validate_host(hass, "panel.example.com") is False


@pytest.mark.asyncio
async def test_validate_host_returns_false_when_probe_failed() -> None:
    """Transport/probe failures must not count as a reachable v1 host."""
    hass = MagicMock()
    fake_client = MagicMock()
    with (
        patch(
            "custom_components.span_panel.config_flow_validation.get_async_client",
            return_value=fake_client,
        ),
        patch(
            "custom_components.span_panel.config_flow_validation.detect_api_version",
            return_value=DetectionResult(
                api_version="v1",
                status_info=None,
                probe_failed=True,
            ),
        ),
    ):
        assert await validate_host(hass, "panel.example.com") is False


@pytest.mark.asyncio
async def test_validate_host_rejects_v1_panel() -> None:
    """A v1-only panel should be rejected since only v2 is supported."""
    hass = MagicMock()
    fake_client = MagicMock()
    with (
        patch(
            "custom_components.span_panel.config_flow_validation.get_async_client",
            return_value=fake_client,
        ),
        patch(
            "custom_components.span_panel.config_flow_validation.detect_api_version",
            return_value=DetectionResult(
                api_version="v1",
                status_info=None,
                probe_failed=False,
            ),
        ),
    ):
        assert await validate_host(hass, "panel.example.com") is False


def test_validate_ipv4_and_fqdn_classification() -> None:
    """FQDN helper should classify host formats correctly."""
    assert is_fqdn("panel.example.com") is True
    assert is_fqdn("192.168.1.10") is False
    assert is_fqdn("2001:db8::1") is False
    assert is_fqdn("span-panel.local") is False
    assert is_fqdn("span-panel.local.") is False
    assert is_fqdn("panel") is False


@pytest.mark.asyncio
async def test_validate_v2_helpers_send_credentials_over_the_given_transport() -> None:
    """Both helpers register over the transport they are handed, and only that.

    Neither takes a port or builds a client of its own any more. These are the
    two calls that carry the panel passphrase and return the broker password, so
    a caller that has nothing pinned cannot reach them at all rather than
    silently getting a plaintext one.
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    transport = PanelRestTransport(
        port=8443, ssl_context=context, httpx_client=None, ca_pem="-----BEGIN..."
    )
    with patch(
        "custom_components.span_panel.config_flow_validation.register_v2",
        new=AsyncMock(return_value=MOCK_V2_AUTH),
    ) as mock_register:
        assert (
            await validate_v2_passphrase("panel.example.com", "passphrase", transport)
            == MOCK_V2_AUTH
        )
        assert await validate_v2_proximity("panel.example.com", transport) == MOCK_V2_AUTH

    assert mock_register.await_args_list[0].args == (
        "panel.example.com",
        "Home Assistant",
        "passphrase",
    )
    assert mock_register.await_args_list[0].kwargs == {
        "port": 8443,
        "httpx_client": None,
        "ssl_context": context,
    }
    assert mock_register.await_args_list[1].args == (
        "panel.example.com",
        "Home Assistant",
    )
    assert mock_register.await_args_list[1].kwargs == {
        "port": 8443,
        "httpx_client": None,
        "ssl_context": context,
    }


@pytest.mark.asyncio
async def test_the_ca_fetch_returns_none_when_the_panel_will_not_serve_one() -> None:
    """An unpinned caller that cannot fetch a CA has nothing to check against."""
    hass = MagicMock()
    fake_client = MagicMock()
    with (
        patch(
            "custom_components.span_panel.config_flow_validation.get_async_client",
            return_value=fake_client,
        ),
        patch(
            "custom_components.span_panel.config_flow_validation.download_ca_cert",
            side_effect=SpanPanelConnectionError("no cert"),
        ) as mock_download,
    ):
        assert await async_download_ca_or_none(hass, "panel.example.com") is None

    mock_download.assert_awaited_once_with(
        "panel.example.com", port=80, httpx_client=fake_client
    )


@pytest.mark.asyncio
async def test_the_ca_fetch_forwards_a_custom_http_port() -> None:
    """The unpinned fetch honours the panel's HTTP port."""
    hass = MagicMock()
    fake_client = MagicMock()
    with (
        patch(
            "custom_components.span_panel.config_flow_validation.get_async_client",
            return_value=fake_client,
        ),
        patch(
            "custom_components.span_panel.config_flow_validation.download_ca_cert",
            side_effect=SpanPanelConnectionError("no cert"),
        ) as mock_download,
    ):
        assert await async_download_ca_or_none(hass, "panel.example.com", http_port=8080) is None

    mock_download.assert_awaited_once_with(
        "panel.example.com", port=8080, httpx_client=fake_client
    )


@pytest.mark.asyncio
async def test_check_fqdn_tls_ready_uses_the_pinned_anchor_without_refetching() -> None:
    """A caller that already holds the anchor is checked against that anchor.

    Refetching would make a fresh trust decision on every poll, against whatever
    answered plain HTTP — and anything that can answer it can also serve a leaf
    signed by the CA it just handed over, so the poll would report the FQDN
    ready on a certificate the pinned anchor does not sign.
    """

    class FakeLoop:
        async def run_in_executor(self, _executor, func):
            return func()

    class FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def getpeercert(self):
            # The handshake now establishes only the chain; the name binding is
            # judged separately from the certificate it hands back.
            return {"subjectAltName": (("DNS", "panel.example.com"),)}

    class FakeSSLContext:
        def wrap_socket(self, _sock, server_hostname: str):
            assert server_hostname == "panel.example.com"
            return FakeSocket()

    with (
        patch(
            "custom_components.span_panel.config_flow_validation.download_ca_cert",
            new=AsyncMock(),
        ) as mock_download,
        patch(
            "custom_components.span_panel.config_flow_validation.asyncio.get_running_loop",
            return_value=FakeLoop(),
        ),
        patch(
            "custom_components.span_panel.config_flow_validation.build_panel_ssl_context",
            return_value=FakeSSLContext(),
        ) as build_context,
        patch(
            "custom_components.span_panel.config_flow_validation.socket.create_connection",
            return_value=FakeSocket(),
        ),
    ):
        assert await check_fqdn_tls_ready("panel.example.com", 8883, "pinned-pem") is True

    mock_download.assert_not_awaited()
    build_context.assert_called_once_with("pinned-pem", check_hostname=False)


@pytest.mark.asyncio
async def test_check_fqdn_tls_ready_returns_true_on_success() -> None:
    """TLS readiness should pass when the handshake succeeds."""

    class FakeLoop:
        async def run_in_executor(self, _executor, func):
            return func()

    class FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def getpeercert(self):
            # The handshake now establishes only the chain; the name binding is
            # judged separately from the certificate it hands back.
            return {"subjectAltName": (("DNS", "panel.example.com"),)}

    class FakeSSLContext:
        def wrap_socket(self, _sock, server_hostname: str):
            assert server_hostname == "panel.example.com"
            return FakeSocket()

    with (
        patch(
            "custom_components.span_panel.config_flow_validation.asyncio.get_running_loop",
            return_value=FakeLoop(),
        ),
        patch(
            "custom_components.span_panel.config_flow_validation.build_panel_ssl_context",
            return_value=FakeSSLContext(),
        ) as build_context,
        patch(
            "custom_components.span_panel.config_flow_validation.socket.create_connection",
            return_value=FakeSocket(),
        ),
    ):
        assert await check_fqdn_tls_ready("panel.example.com", 8883, "pem-data") is True

    # The library's builder, not a hand-rolled context: it is the one that clears
    # VERIFY_X509_STRICT, which the panel's AKI-less CA fails without.
    build_context.assert_called_once_with("pem-data", check_hostname=False)


@pytest.mark.asyncio
async def test_check_fqdn_tls_ready_returns_false_on_ssl_error() -> None:
    """TLS readiness should fail when the hostname/cert handshake fails."""

    class FakeLoop:
        async def run_in_executor(self, _executor, func):
            return func()

    class FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def getpeercert(self):
            # The handshake now establishes only the chain; the name binding is
            # judged separately from the certificate it hands back.
            return {"subjectAltName": (("DNS", "panel.example.com"),)}

    class FakeSSLContext:
        def wrap_socket(self, _sock, server_hostname: str):
            raise ssl.SSLError(f"bad cert for {server_hostname}")

    with (
        patch(
            "custom_components.span_panel.config_flow_validation.asyncio.get_running_loop",
            return_value=FakeLoop(),
        ),
        patch(
            "custom_components.span_panel.config_flow_validation.build_panel_ssl_context",
            return_value=FakeSSLContext(),
        ),
        patch(
            "custom_components.span_panel.config_flow_validation.socket.create_connection",
            return_value=FakeSocket(),
        ),
    ):
        assert await check_fqdn_tls_ready("panel.example.com", 8883, "pem-data") is False


@pytest.mark.asyncio
async def test_leaf_validation_rejects_a_ca_that_does_not_parse() -> None:
    """A PEM the ssl module will not load is not an anchor, and is not fatal."""

    class FakeLoop:
        async def run_in_executor(self, _executor, func):
            return func()

    with (
        patch(
            "custom_components.span_panel.config_flow_validation.asyncio.get_running_loop",
            return_value=FakeLoop(),
        ),
        patch(
            "custom_components.span_panel.config_flow_validation.build_panel_ssl_context",
            side_effect=ssl.SSLError("not a certificate"),
        ),
        patch(
            "custom_components.span_panel.config_flow_validation.socket.create_connection"
        ) as connect,
    ):
        assert await async_leaf_chains_to_ca("panel.example.com", 443, "junk") is False

    # Nothing is dialled when there is no anchor to dial with.
    connect.assert_not_called()


def test_port_or_none_reports_an_unreadable_value_as_unknown() -> None:
    """A substituted default and a stored value are different answers.

    `as_port` exists for callers that just need a number to dial. This one is
    for the caller deciding whether the panel's TLS port is *known*, which must
    not be told yes because a fallback was available.
    """
    assert port_or_none(8443) == 8443
    assert port_or_none("8443") == 8443
    assert port_or_none(None) is None
    assert port_or_none("not-a-port") is None
    assert port_or_none("") is None
    # `True` is an int in Python and would otherwise read as port 1.
    assert port_or_none(True) is None


@pytest.mark.asyncio
async def test_resolve_host_returns_an_ip_literal_unchanged() -> None:
    """An address needs no resolving, and must not be handed to the resolver."""
    hass = MagicMock()
    hass.async_add_executor_job = AsyncMock()
    assert await async_resolve_host(hass, "192.168.1.50") == "192.168.1.50"
    assert await async_resolve_host(hass, "fd00::1") == "fd00::1"
    hass.async_add_executor_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolve_host_returns_the_first_ipv4_answer() -> None:
    """Only IPv4, because the library builds URLs without bracketing v6."""

    async def _run(func):
        return func()

    hass = MagicMock()
    hass.async_add_executor_job = _run
    with patch(
        "custom_components.span_panel.config_flow_validation.socket.getaddrinfo",
        return_value=[(2, 1, 6, "", ("192.168.1.50", 443))],
    ) as resolve:
        assert await async_resolve_host(hass, "panel.example.com") == "192.168.1.50"

    assert resolve.call_args.kwargs["family"] == socket.AF_INET


@pytest.mark.asyncio
async def test_resolve_host_returns_none_when_the_name_does_not_resolve() -> None:
    """The caller falls back to the name it was given, which is what shipped."""

    async def _run(func):
        return func()

    hass = MagicMock()
    hass.async_add_executor_job = _run
    with patch(
        "custom_components.span_panel.config_flow_validation.socket.getaddrinfo",
        side_effect=socket.gaierror("no such host"),
    ):
        assert await async_resolve_host(hass, "panel.example.com") is None

    with patch(
        "custom_components.span_panel.config_flow_validation.socket.getaddrinfo",
        return_value=[],
    ):
        assert await async_resolve_host(hass, "panel.example.com") is None


@pytest.mark.asyncio
async def test_resolve_host_survives_a_name_that_cannot_be_idna_encoded() -> None:
    """A label over 63 characters fails encoding, not lookup, and raises a ValueError.

    `socket.getaddrinfo` raises `UnicodeEncodeError` — a `UnicodeError`, which is
    a `ValueError` and not an `OSError` — before it attempts any lookup. A host
    the user typed into a form must not be able to end the flow in a traceback.
    """

    async def _run(func):
        return func()

    hass = MagicMock()
    hass.async_add_executor_job = _run
    overlong = "a" * 70 + ".example.com"
    with patch(
        "custom_components.span_panel.config_flow_validation.socket.getaddrinfo",
        side_effect=UnicodeEncodeError("idna", overlong, 0, 70, "label too long"),
    ):
        assert await async_resolve_host(hass, overlong) is None


@pytest.mark.asyncio
async def test_leaf_check_survives_a_name_that_cannot_be_idna_encoded() -> None:
    """The leaf check resolves the name too, and fails the same way."""

    class FakeLoop:
        async def run_in_executor(self, _executor, func):
            return func()

    overlong = "a" * 70 + ".example.com"
    with (
        patch(
            "custom_components.span_panel.config_flow_validation.asyncio.get_running_loop",
            return_value=FakeLoop(),
        ),
        patch(
            "custom_components.span_panel.config_flow_validation.build_panel_ssl_context",
            return_value=MagicMock(),
        ),
        patch(
            "custom_components.span_panel.config_flow_validation.socket.create_connection",
            side_effect=UnicodeEncodeError("idna", overlong, 0, 70, "label too long"),
        ),
    ):
        assert await async_leaf_chains_to_ca(overlong, 443, "pem") is False
