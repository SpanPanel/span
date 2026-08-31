"""The runtime client rides the pinned transport, and refuses to ride without it.

Issue #264: every other REST call site adopted `panel_rest_transport` when the
pin landed — config flow, reauth, repairs, `rotate_credentials` — and the one
that runs on every startup did not. The schema fetch inside `connect()` stayed
on plaintext HTTP with the pin sitting unused in `entry.data`, which is exactly
the condition the library's transport warning names.

Fail closed, both ways it can fail. A stored anchor that cannot be read is not
downgraded to plaintext at the one call that runs unattended on every boot; a
certificate the pin rejects is not retried into submission. Each gets a Repair,
because both need a person: the first needs a new anchor accepted, the second is
either a legitimately rotated CA (accept it) or something standing in front of
the panel's TLS port (investigate it) — and which of those it is gets diagnosed
the same way the library diagnoses the MQTT side, by re-reading the advertised
CA and comparing fingerprints.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import ConfigEntryError, ConfigEntryNotReady
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.httpx_client import get_async_client
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from span_panel_api.exceptions import (
    SpanPanelConnectionError,
    SpanPanelTLSVerificationError,
    SpanPanelValidationError,
)

from custom_components.span_panel import async_remove_entry, async_setup_entry
from custom_components.span_panel.ca_repairs import (
    ca_changed_issue_id,
    ca_unusable_issue_id,
    rest_tls_untrusted_issue_id,
)
from custom_components.span_panel.config_flow_validation import LeafProbeResult, LeafVerdict
from custom_components.span_panel.const import (
    CONF_API_VERSION,
    CONF_EBUS_BROKER_HOST,
    CONF_EBUS_BROKER_PASSWORD,
    CONF_EBUS_BROKER_PORT,
    CONF_EBUS_BROKER_USERNAME,
    CONF_HTTP_PORT,
    CONF_HTTPS_PORT,
    CONF_PANEL_CA_PEM,
    DOMAIN,
)
from custom_components.span_panel.leaf_repairs import leaf_name_mismatch_issue_id

from .factories import SpanPanelSnapshotFactory

PEM = "-----BEGIN CERTIFICATE-----\nZmFrZQ==\n-----END CERTIFICATE-----\n"
OTHER_PEM = "-----BEGIN CERTIFICATE-----\nb3RoZXI=\n-----END CERTIFICATE-----\n"
SERIAL = "sp3-rest-001"


def _entry(hass: HomeAssistant, **data_overrides: object) -> MockConfigEntry:
    data: dict[str, object] = {
        CONF_API_VERSION: "v2",
        CONF_HOST: "192.168.1.50",
        CONF_EBUS_BROKER_HOST: "span-panel.local",
        CONF_EBUS_BROKER_USERNAME: "mqtt-user",
        CONF_EBUS_BROKER_PASSWORD: "mqtt-pass",
        CONF_EBUS_BROKER_PORT: 8883,
        CONF_HTTP_PORT: 80,
    }
    data.update(data_overrides)
    entry = MockConfigEntry(
        domain=DOMAIN, data=data, entry_id="entry-rest", title=SERIAL, unique_id=SERIAL
    )
    entry.add_to_hass(hass)
    return entry


def _happy_client() -> MagicMock:
    client = MagicMock()
    client.connect = AsyncMock()
    client.close = AsyncMock()
    return client


def _happy_coordinator() -> MagicMock:
    coordinator = MagicMock()
    coordinator.async_config_entry_first_refresh = AsyncMock()
    coordinator.async_setup_streaming = AsyncMock()
    coordinator.data = SpanPanelSnapshotFactory.create(serial_number=SERIAL)
    return coordinator


@contextmanager
def _full_setup(client: MagicMock, hass: HomeAssistant) -> Iterator[MagicMock]:
    """Everything a successful setup needs mocked, yielding the client class."""
    with (
        patch("custom_components.span_panel.async_register_commands"),
        patch(
            "custom_components.span_panel.SpanMqttClient", return_value=client
        ) as mock_client_cls,
        patch(
            "custom_components.span_panel.SpanPanelCoordinator",
            return_value=_happy_coordinator(),
        ),
        patch(
            "custom_components.span_panel.ensure_device_registered",
            AsyncMock(return_value="panel-device-id"),
        ),
        patch.object(hass.config_entries, "async_forward_entry_setups", AsyncMock()),
        patch.object(hass.config_entries, "async_update_entry"),
    ):
        yield mock_client_cls


class TestThePinReachesTheRuntimeClient:
    """The transport decision follows the entry's pin, port by port."""

    async def test_a_pinned_entry_hands_its_anchor_to_the_runtime_client(
        self, hass: HomeAssistant
    ) -> None:
        """The pin stored on the entry anchors the schema fetch at connect."""
        entry = _entry(hass, **{CONF_PANEL_CA_PEM: PEM})
        client = _happy_client()
        context = MagicMock(name="pinned-context")

        with (
            patch(
                "custom_components.span_panel.config_flow_validation.build_panel_ssl_context",
                return_value=context,
            ),
            _full_setup(client, hass) as mock_client_cls,
        ):
            assert await async_setup_entry(hass, entry) is True

        kwargs = mock_client_cls.call_args.kwargs
        assert kwargs["ssl_context"] is context
        # 443 by default — the stored plaintext port is the bridge's, not TLS's.
        assert kwargs["panel_https_port"] == 443
        assert kwargs["panel_http_port"] == 80
        # The shared client stays: the library ignores it under a context, and
        # it is what the unpinned plaintext path uses.
        assert kwargs["httpx_client"] is get_async_client(hass)

    async def test_a_pinned_entry_honours_a_configured_https_port(
        self, hass: HomeAssistant
    ) -> None:
        """A stored HTTPS port names where the panel's TLS actually lives."""
        entry = _entry(hass, **{CONF_PANEL_CA_PEM: PEM, CONF_HTTPS_PORT: 8443})
        client = _happy_client()

        with (
            patch(
                "custom_components.span_panel.config_flow_validation.build_panel_ssl_context",
                return_value=MagicMock(),
            ),
            _full_setup(client, hass) as mock_client_cls,
        ):
            assert await async_setup_entry(hass, entry) is True

        assert mock_client_cls.call_args.kwargs["panel_https_port"] == 8443

    async def test_an_unpinned_entry_keeps_the_plaintext_transport(
        self, hass: HomeAssistant
    ) -> None:
        """No pin is not a failure — it is the TOFU path, plaintext as designed."""
        entry = _entry(hass)
        client = _happy_client()

        with _full_setup(client, hass) as mock_client_cls:
            assert await async_setup_entry(hass, entry) is True

        kwargs = mock_client_cls.call_args.kwargs
        assert kwargs["ssl_context"] is None
        assert kwargs["panel_https_port"] is None
        assert kwargs["panel_http_port"] == 80


class TestFailingClosed:
    """Both ways the pinned transport can fail end with a person, not a downgrade."""

    async def test_an_unusable_stored_anchor_fails_setup_with_a_repair(
        self, hass: HomeAssistant
    ) -> None:
        """A pin that cannot be read must not quietly become no pin at all."""
        entry = _entry(hass, **{CONF_PANEL_CA_PEM: "not a certificate"})

        with (
            patch("custom_components.span_panel.async_register_commands"),
            patch("custom_components.span_panel.SpanMqttClient") as mock_client_cls,
            pytest.raises(ConfigEntryError),
        ):
            await async_setup_entry(hass, entry)

        mock_client_cls.assert_not_called()
        issue = ir.async_get(hass).async_get_issue(DOMAIN, ca_unusable_issue_id(entry.entry_id))
        assert issue is not None
        assert issue.is_fixable

    async def test_a_rotated_ca_behind_a_rest_failure_raises_the_ca_changed_repair(
        self, hass: HomeAssistant
    ) -> None:
        """The common legitimate cause, given the guided re-pin it already has."""
        entry = _entry(hass, **{CONF_PANEL_CA_PEM: PEM})
        client = _happy_client()
        client.connect = AsyncMock(
            side_effect=SpanPanelTLSVerificationError("certificate verify failed")
        )

        with (
            patch(
                "custom_components.span_panel.config_flow_validation.build_panel_ssl_context",
                return_value=MagicMock(),
            ),
            patch("custom_components.span_panel.async_register_commands"),
            patch("custom_components.span_panel.SpanMqttClient", return_value=client),
            patch(
                "custom_components.span_panel.async_fetch_panel_ca",
                new=AsyncMock(return_value=OTHER_PEM),
            ),
            pytest.raises(ConfigEntryError),
        ):
            await async_setup_entry(hass, entry)

        client.close.assert_awaited_once()
        issue = ir.async_get(hass).async_get_issue(DOMAIN, ca_changed_issue_id(entry.entry_id))
        assert issue is not None

    async def test_an_untrusted_leaf_behind_an_unchanged_ca_retries_with_a_repair(
        self, hass: HomeAssistant
    ) -> None:
        """The CA did not rotate and the leaf does not chain to it.

        A proxy terminating the TLS port with its own certificate, or a leaf an
        outage's clock reset left outside its validity window — the probe cannot
        tell them apart, so the repair names both and the entry retries: the
        clock case heals itself, and never plaintext either way.
        """
        entry = _entry(hass, **{CONF_PANEL_CA_PEM: PEM})
        client = _happy_client()
        client.connect = AsyncMock(
            side_effect=SpanPanelTLSVerificationError("certificate verify failed")
        )

        with (
            patch(
                "custom_components.span_panel.config_flow_validation.build_panel_ssl_context",
                return_value=MagicMock(),
            ),
            patch("custom_components.span_panel.async_register_commands"),
            patch("custom_components.span_panel.SpanMqttClient", return_value=client),
            patch(
                "custom_components.span_panel.async_fetch_panel_ca",
                new=AsyncMock(return_value=PEM),
            ),
            patch(
                "custom_components.span_panel.async_leaf_probe",
                new=AsyncMock(return_value=LeafProbeResult(LeafVerdict.UNTRUSTED, ())),
            ),
            pytest.raises(ConfigEntryNotReady),
        ):
            await async_setup_entry(hass, entry)

        client.close.assert_awaited_once()
        issue = ir.async_get(hass).async_get_issue(
            DOMAIN, rest_tls_untrusted_issue_id(entry.entry_id)
        )
        assert issue is not None
        assert not issue.is_fixable
        # Re-derived on every retry, so it must not outlive a restart that fixes it.
        assert not issue.is_persistent

    async def test_a_moved_panel_behind_an_unchanged_ca_raises_the_leaf_repair(
        self, hass: HomeAssistant
    ) -> None:
        """The panel is who it says it is, and not where the entry says it is.

        The leaf-mismatch repair already promises "the integration keeps
        retrying", and setup keeps that promise with NotReady rather than
        contradicting it with a terminal accusation of interception.
        """
        entry = _entry(hass, **{CONF_PANEL_CA_PEM: PEM})
        client = _happy_client()
        client.connect = AsyncMock(
            side_effect=SpanPanelTLSVerificationError("certificate verify failed")
        )

        with (
            patch(
                "custom_components.span_panel.config_flow_validation.build_panel_ssl_context",
                return_value=MagicMock(),
            ),
            patch("custom_components.span_panel.async_register_commands"),
            patch("custom_components.span_panel.SpanMqttClient", return_value=client),
            patch(
                "custom_components.span_panel.async_fetch_panel_ca",
                new=AsyncMock(return_value=PEM),
            ),
            patch(
                "custom_components.span_panel.async_leaf_probe",
                new=AsyncMock(
                    return_value=LeafProbeResult(
                        LeafVerdict.NAME_MISMATCH, ("span-panel.local", "10.0.0.7")
                    )
                ),
            ),
            pytest.raises(ConfigEntryNotReady),
        ):
            await async_setup_entry(hass, entry)

        client.close.assert_awaited_once()
        issue = ir.async_get(hass).async_get_issue(
            DOMAIN, leaf_name_mismatch_issue_id(entry.entry_id)
        )
        assert issue is not None
        assert (
            ir.async_get(hass).async_get_issue(DOMAIN, rest_tls_untrusted_issue_id(entry.entry_id))
            is None
        )

    async def test_the_two_same_fingerprint_repairs_supersede_each_other(
        self, hass: HomeAssistant
    ) -> None:
        """A panel cannot be both intercepted and merely moved; only the current verdict stands.

        Without the supersede, a panel that first probed UNTRUSTED and then
        moved (or the reverse) showed both repairs at once — one saying "keep
        retrying, it will recover", the other implying interception — for the
        rest of the session.
        """
        entry = _entry(hass, **{CONF_PANEL_CA_PEM: PEM})
        client = _happy_client()
        client.connect = AsyncMock(
            side_effect=SpanPanelTLSVerificationError("certificate verify failed")
        )
        registry = ir.async_get(hass)

        async def _setup_with_verdict(result: LeafProbeResult) -> None:
            with (
                patch(
                    "custom_components.span_panel.config_flow_validation.build_panel_ssl_context",
                    return_value=MagicMock(),
                ),
                patch("custom_components.span_panel.async_register_commands"),
                patch("custom_components.span_panel.SpanMqttClient", return_value=client),
                patch(
                    "custom_components.span_panel.async_fetch_panel_ca",
                    new=AsyncMock(return_value=PEM),
                ),
                patch(
                    "custom_components.span_panel.async_leaf_probe",
                    new=AsyncMock(return_value=result),
                ),
                pytest.raises(ConfigEntryNotReady),
            ):
                await async_setup_entry(hass, entry)

        await _setup_with_verdict(LeafProbeResult(LeafVerdict.UNTRUSTED, ()))
        await _setup_with_verdict(
            LeafProbeResult(LeafVerdict.NAME_MISMATCH, ("span-panel.local",))
        )
        assert registry.async_get_issue(DOMAIN, leaf_name_mismatch_issue_id(entry.entry_id))
        assert (
            registry.async_get_issue(DOMAIN, rest_tls_untrusted_issue_id(entry.entry_id)) is None
        )

        await _setup_with_verdict(LeafProbeResult(LeafVerdict.UNTRUSTED, ()))
        assert registry.async_get_issue(DOMAIN, rest_tls_untrusted_issue_id(entry.entry_id))
        assert (
            registry.async_get_issue(DOMAIN, leaf_name_mismatch_issue_id(entry.entry_id)) is None
        )

    async def test_an_unreachable_tls_port_behind_an_unchanged_ca_just_retries(
        self, hass: HomeAssistant
    ) -> None:
        """The probe reached nothing, which is a panel mid-reboot, not a verdict."""
        entry = _entry(hass, **{CONF_PANEL_CA_PEM: PEM})
        client = _happy_client()
        client.connect = AsyncMock(
            side_effect=SpanPanelTLSVerificationError("certificate verify failed")
        )

        with (
            patch(
                "custom_components.span_panel.config_flow_validation.build_panel_ssl_context",
                return_value=MagicMock(),
            ),
            patch("custom_components.span_panel.async_register_commands"),
            patch("custom_components.span_panel.SpanMqttClient", return_value=client),
            patch(
                "custom_components.span_panel.async_fetch_panel_ca",
                new=AsyncMock(return_value=PEM),
            ),
            patch(
                "custom_components.span_panel.async_leaf_probe",
                new=AsyncMock(return_value=LeafProbeResult(LeafVerdict.UNREACHABLE, ())),
            ),
            pytest.raises(ConfigEntryNotReady),
        ):
            await async_setup_entry(hass, entry)

        client.close.assert_awaited_once()
        registry = ir.async_get(hass)
        assert registry.async_get_issue(DOMAIN, rest_tls_untrusted_issue_id(entry.entry_id)) is None
        assert registry.async_get_issue(DOMAIN, leaf_name_mismatch_issue_id(entry.entry_id)) is None

    async def test_a_pinned_entry_that_cannot_connect_names_the_https_port(
        self, hass: HomeAssistant
    ) -> None:
        """Nothing answering the TLS port must not read as 'panel is down'.

        The deferred-pin population can have TLS living somewhere 443 is not —
        behind NAT, a port forward, a proxy — and was never asked for the port.
        Their failure is a plain refused connection, indistinguishable from a
        reboot, so the retry message is where the remedy has to travel.
        """
        entry = _entry(hass, **{CONF_PANEL_CA_PEM: PEM})
        client = _happy_client()
        client.connect = AsyncMock(side_effect=SpanPanelConnectionError("connection refused"))

        with (
            patch(
                "custom_components.span_panel.config_flow_validation.build_panel_ssl_context",
                return_value=MagicMock(),
            ),
            patch("custom_components.span_panel.async_register_commands"),
            patch("custom_components.span_panel.SpanMqttClient", return_value=client),
            pytest.raises(ConfigEntryNotReady, match="HTTPS port 443"),
        ):
            await async_setup_entry(hass, entry)

        client.close.assert_awaited_once()

    async def test_a_stored_https_port_of_80_fails_setup_cleanly(
        self, hass: HomeAssistant
    ) -> None:
        """The library refuses the plaintext port under a context; setup must not crash.

        `SpanPanelValidationError` out of connect() used to escape as a raw
        traceback with the client left open. It is a stored-configuration
        problem, so it ends as a clear terminal error naming the remedy.
        """
        entry = _entry(hass, **{CONF_PANEL_CA_PEM: PEM, CONF_HTTPS_PORT: 80})
        client = _happy_client()
        client.connect = AsyncMock(
            side_effect=SpanPanelValidationError("port=80 was passed together with an ssl_context")
        )

        with (
            patch(
                "custom_components.span_panel.config_flow_validation.build_panel_ssl_context",
                return_value=MagicMock(),
            ),
            patch("custom_components.span_panel.async_register_commands"),
            patch("custom_components.span_panel.SpanMqttClient", return_value=client),
            pytest.raises(ConfigEntryError),
        ):
            await async_setup_entry(hass, entry)

        client.close.assert_awaited_once()

    async def test_missing_evidence_retries_rather_than_escalating(
        self, hass: HomeAssistant
    ) -> None:
        """A panel unreachable on its HTTP port is a panel mid-reboot, not a verdict."""
        entry = _entry(hass, **{CONF_PANEL_CA_PEM: PEM})
        client = _happy_client()
        client.connect = AsyncMock(
            side_effect=SpanPanelTLSVerificationError("certificate verify failed")
        )

        with (
            patch(
                "custom_components.span_panel.config_flow_validation.build_panel_ssl_context",
                return_value=MagicMock(),
            ),
            patch("custom_components.span_panel.async_register_commands"),
            patch("custom_components.span_panel.SpanMqttClient", return_value=client),
            patch(
                "custom_components.span_panel.async_fetch_panel_ca",
                new=AsyncMock(side_effect=SpanPanelConnectionError("unreachable")),
            ),
            pytest.raises(ConfigEntryNotReady),
        ):
            await async_setup_entry(hass, entry)

        client.close.assert_awaited_once()
        registry = ir.async_get(hass)
        assert registry.async_get_issue(DOMAIN, ca_changed_issue_id(entry.entry_id)) is None
        assert (
            registry.async_get_issue(DOMAIN, rest_tls_untrusted_issue_id(entry.entry_id)) is None
        )

    async def test_a_clean_connect_clears_the_standing_repairs(
        self, hass: HomeAssistant
    ) -> None:
        """A handshake under the current pin refutes what the issues describe."""
        entry = _entry(hass, **{CONF_PANEL_CA_PEM: PEM})
        registry = ir.async_get(hass)
        registry.async_get_or_create(
            DOMAIN,
            rest_tls_untrusted_issue_id(entry.entry_id),
            is_fixable=False,
            is_persistent=True,
            severity=ir.IssueSeverity.ERROR,
            translation_key="panel_rest_tls_untrusted",
        )
        registry.async_get_or_create(
            DOMAIN,
            ca_unusable_issue_id(entry.entry_id),
            is_fixable=True,
            is_persistent=True,
            severity=ir.IssueSeverity.ERROR,
            translation_key="panel_ca_unusable",
        )
        client = _happy_client()

        with (
            patch(
                "custom_components.span_panel.config_flow_validation.build_panel_ssl_context",
                return_value=MagicMock(),
            ),
            _full_setup(client, hass),
        ):
            assert await async_setup_entry(hass, entry) is True

        assert (
            registry.async_get_issue(DOMAIN, rest_tls_untrusted_issue_id(entry.entry_id)) is None
        )
        assert registry.async_get_issue(DOMAIN, ca_unusable_issue_id(entry.entry_id)) is None


class TestRemovalLeavesNothingBehind:
    """Core deletes no issues when an entry is removed; the CA family goes here."""

    async def test_removing_the_entry_clears_its_ca_repairs(self, hass: HomeAssistant) -> None:
        """Persistent issues especially: nothing else can clear one whose entry is gone."""
        entry = _entry(hass, **{CONF_PANEL_CA_PEM: PEM})
        registry = ir.async_get(hass)
        for issue_id, fixable in (
            (ca_changed_issue_id(entry.entry_id), True),
            (ca_unusable_issue_id(entry.entry_id), True),
            (rest_tls_untrusted_issue_id(entry.entry_id), False),
            (leaf_name_mismatch_issue_id(entry.entry_id), False),
        ):
            registry.async_get_or_create(
                DOMAIN,
                issue_id,
                is_fixable=fixable,
                is_persistent=True,
                severity=ir.IssueSeverity.ERROR,
                translation_key="panel_ca_changed",
            )

        with (
            patch("custom_components.span_panel.async_forget_announcements", AsyncMock()),
            patch("custom_components.span_panel.async_forget", AsyncMock()),
        ):
            await async_remove_entry(hass, entry)

        for issue_id in (
            ca_changed_issue_id(entry.entry_id),
            ca_unusable_issue_id(entry.entry_id),
            rest_tls_untrusted_issue_id(entry.entry_id),
            leaf_name_mismatch_issue_id(entry.entry_id),
        ):
            assert registry.async_get_issue(DOMAIN, issue_id) is None, issue_id
