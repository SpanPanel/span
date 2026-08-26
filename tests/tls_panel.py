"""A synthetic SPAN panel that serves a real certificate over a real handshake.

Most of the suite stubs `async_leaf_chains_to_ca` and `build_panel_ssl_context`,
so nothing there ever verifies a certificate. The tests that exist to prove the
integration refuses the wrong anchor cannot use those stubs — the refusal *is*
the verification — so they need a listener with a genuine leaf on it.

Everything here is synthetic: a throwaway CA and a self-signed panel leaf,
torn down with the test that raised them. This is the machinery only; each
suite wraps it in its own fixture, because a fixture imported into a test
module collides with every parameter that requests it.
"""

from __future__ import annotations

from collections.abc import Iterator
import contextlib
import datetime
import http.server
import json
import socket
import socketserver
import ssl
import threading
from typing import Any
from unittest.mock import patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

PANEL_FQDN = "panel.home.lan"
# A single-label name, as a search domain or an add-on's container hostname
# supplies. `is_fqdn` is False for it, so nothing in the flow ever registers it.
PANEL_SHORTNAME = "spanpanel"
PANEL_LOOPBACK = "127.0.0.1"
PANEL_SERIAL = "sp3-synthetic-0001"

#: The names a config-flow test expects to reach the listener.
LOOPBACK_NAMES = frozenset({PANEL_FQDN, PANEL_SHORTNAME})


@contextlib.contextmanager
def resolving_to_loopback(*names: str) -> Iterator[None]:
    """Answer `names` with the test listener's address, and nothing else.

    A name arrives as `bytes` from anyio's resolver and as `str` from
    `socket.create_connection`, so both spellings are matched. What is
    replaced is already a patched resolver -- the Home Assistant test plugin
    refuses real DNS -- so every other name keeps that refusal.
    """
    real_getaddrinfo = socket.getaddrinfo
    wanted = frozenset(names)

    def _fake(host: Any, port: Any, *args: Any, **kwargs: Any) -> Any:
        name = host.decode("ascii") if isinstance(host, bytes) else host
        if name in wanted:
            return real_getaddrinfo(PANEL_LOOPBACK, port, *args, **kwargs)
        return real_getaddrinfo(host, port, *args, **kwargs)

    with patch("socket.getaddrinfo", side_effect=_fake):
        yield


# ---------- certificate generation ----------


def issue_ca() -> tuple[x509.Certificate, rsa.RSAPrivateKey]:
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


def issue_leaf(
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


def ca_pem_of(ca_cert: x509.Certificate) -> str:
    """Return the PEM text of a certificate, as the panel's CA endpoint serves it."""
    return ca_cert.public_bytes(serialization.Encoding.PEM).decode()


def unrelated_ca_pem() -> str:
    """Return a CA that signs nothing this panel serves.

    The proxy case, in one call: something on the path answers the plaintext CA
    fetch with an authority of its own, and the certificate the panel actually
    serves is not signed by it.
    """
    ca_cert, _key = issue_ca()
    return ca_pem_of(ca_cert)


# ---------- a listener that serves the panel's leaf ----------


class PanelStatusHandler(http.server.BaseHTTPRequestHandler):
    """Answer the one unauthenticated probe `detect_api_version` makes."""

    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        """Serve the status document, whatever path was asked for."""
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


class PanelTLSListener(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """Serve HTTPS on loopback with a certificate the test can swap at will.

    Swappable because that is what the panel does: `register_fqdn` makes it
    regenerate its leaf to add the FQDN, and the flow is supposed to notice.
    """

    daemon_threads = True
    tls_context: ssl.SSLContext

    def get_request(self) -> tuple[socket.socket, Any]:
        """Wrap each accepted connection in whatever certificate is current."""
        sock, addr = super().get_request()
        return self.tls_context.wrap_socket(sock, server_side=True), addr

    def handle_error(self, request: Any, client_address: Any) -> None:
        """Stay quiet: the leaf check hangs up right after the handshake."""


def server_context(cert_pem: str, key_pem: str, tmp_path: Any) -> ssl.SSLContext:
    """Build a server-side context from PEM text (`load_cert_chain` wants files)."""
    cert_file = tmp_path / f"leaf-{abs(hash(cert_pem))}.pem"
    key_file = tmp_path / f"key-{abs(hash(cert_pem))}.pem"
    cert_file.write_text(cert_pem)
    key_file.write_text(key_pem)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(cert_file), str(key_file))
    return ctx


class Panel:
    """The synthetic panel: a CA, a leaf, and the port it serves TLS on."""

    def __init__(self, tmp_path: Any) -> None:
        """Mint this panel's authority; nothing is served until `present`."""
        self._tmp_path = tmp_path
        self._ca_cert, self._ca_key = issue_ca()
        self.ca_pem = ca_pem_of(self._ca_cert)
        self._listener: PanelTLSListener | None = None
        self.port = 0

    def present(self, names: list[x509.GeneralName]) -> None:
        """Serve a freshly issued leaf naming exactly `names`."""
        cert_pem, key_pem = issue_leaf(self._ca_cert, self._ca_key, names)
        self.present_pem(cert_pem, key_pem)

    def present_pem(self, cert_pem: str, key_pem: str) -> None:
        """Serve this certificate, whoever signed it."""
        context = server_context(cert_pem, key_pem, self._tmp_path)
        if self._listener is None:
            self._listener = PanelTLSListener((PANEL_LOOPBACK, 0), PanelStatusHandler)
            self._listener.tls_context = context
            self.port = self._listener.server_port
            threading.Thread(target=self._listener.serve_forever, daemon=True).start()
        else:
            self._listener.tls_context = context

    def close(self) -> None:
        """Stop serving and release the port."""
        if self._listener is not None:
            self._listener.shutdown()
            self._listener.server_close()
            self._listener = None
