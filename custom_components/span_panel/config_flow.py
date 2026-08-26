"""Span Panel Config Flow."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import enum
import logging
import ssl
from typing import TYPE_CHECKING, Any

from homeassistant import config_entries
from homeassistant.config_entries import (
    ConfigFlowContext,
    ConfigFlowResult,
)
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_HOST
from homeassistant.core import callback
from homeassistant.helpers.httpx_client import get_async_client
from homeassistant.helpers.service_info.hassio import HassioServiceInfo
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from homeassistant.util.network import is_ipv4_address
from span_panel_api import (
    V2AuthResponse,
    build_panel_ssl_context,
    ca_fingerprint,
    delete_fqdn,
    detect_api_version,
    register_fqdn,
)
from span_panel_api.exceptions import (
    SpanPanelAPIError,
    SpanPanelAuthError,
    SpanPanelConnectionError,
    SpanPanelTimeoutError,
    SpanPanelValidationError,
)
import voluptuous as vol

from .config_flow_options import (
    GENERAL_OPTIONS_SCHEMA,
    get_general_options_defaults,
    process_general_options_input,
)
from .config_flow_validation import (
    PanelRestTransport,
    as_port,
    async_fetch_panel_ca,
    async_leaf_chains_to_ca,
    async_panel_leaf_host,
    check_fqdn_tls_ready,
    is_fqdn,
    panel_rest_transport,
    port_or_none,
    validate_host,
    validate_v2_passphrase,
    validate_v2_proximity,
)
from .const import (
    CONF_API_VERSION,
    CONF_EBUS_BROKER_HOST,
    CONF_EBUS_BROKER_PASSWORD,
    CONF_EBUS_BROKER_PORT,
    CONF_EBUS_BROKER_USERNAME,
    CONF_HOP_PASSPHRASE,
    CONF_HTTP_PORT,
    CONF_HTTPS_PORT,
    CONF_PANEL_CA_PEM,
    CONF_PANEL_SERIAL,
    CONF_REGISTERED_FQDN,
    DEFAULT_HTTPS_PORT,
    DOMAIN,
    ENABLE_ENERGY_DIP_COMPENSATION,
    ENTITY_NAMING_PATTERN,
    PANEL_CA_PENDING,
    USE_CIRCUIT_NUMBERS,
    USE_DEVICE_PREFIX,
    EntityNamingPattern,
)
from .options import (
    ENERGY_DISPLAY_PRECISION,
    POWER_DISPLAY_PRECISION,
)

if TYPE_CHECKING:
    from . import SpanPanelConfigEntry

_LOGGER = logging.getLogger(__name__)


class ConfigFlowError(Exception):
    """Custom exception for config flow internal errors."""


def get_user_data_schema(default_host: str = "") -> vol.Schema:
    """Get the user data schema with optional default host."""
    return vol.Schema(
        {
            vol.Optional(CONF_HOST, default=default_host): str,
            vol.Optional(CONF_HTTP_PORT, default=80): int,
            vol.Optional(POWER_DISPLAY_PRECISION, default=0): int,
            vol.Optional(ENERGY_DISPLAY_PRECISION, default=2): int,
            vol.Optional(ENABLE_ENERGY_DIP_COMPENSATION, default=True): bool,
        }
    )


STEP_USER_DATA_SCHEMA = get_user_data_schema()

STEP_AUTH_PASSPHRASE_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOP_PASSPHRASE): str,
    }
)


def _discovered_port(record: Mapping[str, Any], *names: str) -> int | None:
    """Read a port out of a discovery record, or None when it names none.

    Everything in a discovery record is whatever the panel put on the wire:
    mDNS TXT values arrive as strings, may be absent, and may be spelled in a
    case this integration did not pick. Anything unreadable is treated as
    unpublished rather than raising, because a malformed TXT record must not be
    the reason a panel cannot be set up.

    None is distinct from a published value that happens to equal the default:
    only None leaves the flow free to ask the user.
    """
    for name in names:
        raw = record.get(name)
        if raw is None or raw == "":
            continue
        try:
            return int(raw)
        except (TypeError, ValueError):
            _LOGGER.debug("Discovery record carried an unreadable %s: %r", name, raw)
            return None
    return None


class TriggerFlowType(enum.Enum):
    """Types of configuration flow triggers."""

    CREATE_ENTRY = enum.auto()
    UPDATE_ENTRY = enum.auto()


class SpanPanelConfigFlow(config_entries.ConfigFlow):
    """Handle a config flow for Span Panel."""

    VERSION = 7
    MINOR_VERSION = 1
    domain = DOMAIN

    def is_matching(self, other_flow: SpanPanelConfigFlow) -> bool:
        """Return True if other_flow is a matching Span Panel."""
        return bool(other_flow and other_flow.context.get("source") == "zeroconf")

    def __init__(self) -> None:
        """Initialize the config flow."""
        self.trigger_flow_type: TriggerFlowType | None = None
        self.host: str | None = None
        self.serial_number: str | None = None
        self.access_token: str | None = None
        self.power_display_precision: int = 0
        self.energy_display_precision: int = 2
        self._is_flow_setup: bool = False
        self.context: ConfigFlowContext = {}
        # Initial naming selection chosen during pre-setup
        self._chosen_use_device_prefix: bool | None = None
        self._chosen_use_circuit_numbers: bool | None = None
        # v2 provisioning state
        self.api_version: str = "v2"
        self._v2_broker_host: str | None = None
        self._v2_broker_port: int | None = None
        self._v2_broker_username: str | None = None
        self._v2_broker_password: str | None = None
        self._v2_panel_serial: str | None = None
        self._http_port: int = 80
        self._https_port: int = DEFAULT_HTTPS_PORT
        # Whether something authoritative already told this flow where the panel
        # serves TLS, rather than the value above being a default. Discovery is
        # one such source: a panel that publishes the port knows where it
        # serves, and asking the user for it after being told is asking them to
        # confirm a fact they have no way to check. An existing entry is the
        # other: on reauth the port it was pinned against is the answer.
        self._https_port_known: bool = False
        # The panel CA, once fetched and leaf-confirmed. None means the flow
        # could not acquire one and the entry is created with the pending flag.
        self._panel_ca_pem: str | None = None
        # How this flow's REST calls reach the panel. Set from the existing
        # entry on reauth and reconfigure, where a pin may already exist; left
        # None on initial setup, which has nothing pinned yet by construction.
        self._rest_transport: PanelRestTransport | None = None
        # The address this flow dials while `self.host` is a name the panel's
        # certificate does not yet name -- an FQDN before `register_fqdn` has
        # run, which is every FQDN install up to the moment it authenticates.
        #
        # It carries an invariant the rest of the flow leans on: **it is set
        # only while `self.host` is unverified under the pin.** It is None when
        # the host is an IP, when the leaf already names the host, and again as
        # soon as registration makes the panel serve the name. So anything about
        # to persist `self.host` can read it as "this host is one the anchor
        # would reject", and refuse rather than write an entry that cannot
        # connect to its own panel.
        self._bootstrap_host: str | None = None
        # Whether the panel accepted the FQDN and now serves it. Recorded rather
        # than re-derived from `is_fqdn(host)` at entry creation, because that
        # question is "does this look like a domain name" and the one that
        # matters is "did the registration this flow performed succeed".
        self._fqdn_registered: bool = False
        # Energy dip compensation default for fresh installs
        self._enable_dip_compensation: bool = True
        # FQDN registration task (async_show_progress)
        self._fqdn_task: asyncio.Task[None] | None = None
        self._reconfigure_fqdn_task: asyncio.Task[None] | None = None

    @property
    def _rest_host(self) -> str:
        """The address this flow's REST and TLS calls dial."""
        return self._bootstrap_host or self.host or ""

    def _require_transport(self) -> PanelRestTransport:
        """Return the transport this flow's credential exchanges run over.

        Raised rather than defaulted. Every path that asks for one has been
        through the CA step or adopted an existing entry's pin, so a missing
        transport is a routing bug -- and the only value that could stand in for
        it is a plaintext one, which would send the panel passphrase in the
        clear to paper over the bug.
        """
        if self._rest_transport is None:
            raise ConfigFlowError("Reached a panel REST call with no transport pinned")
        return self._rest_transport

    async def _async_choose_bootstrap_host(self, ca_pem: str, tls_port: int) -> bool:
        """Settle which address the panel is dialled by, and whether it can be at all.

        Which addresses are tried, and in what order, is `async_panel_leaf_host`
        -- one implementation, shared with the setup-time and repair-time
        checks. What is this flow's own is the *consequence* of the answer: a
        panel reached at something other than the host the user gave is a panel
        whose certificate does not name that host, and `_bootstrap_host` records
        exactly that, so every later step about to persist `self.host` can read
        it as "the anchor would reject this" and refuse.

        Returns False when nothing answers under the published CA, which is a
        leaf mismatch and is fatal to the step that called it.
        """
        self._bootstrap_host = None
        host = self.host
        if not host:
            return False

        reached = await async_panel_leaf_host(self.hass, host, tls_port, ca_pem)
        if reached is None:
            return False
        if reached != host:
            self._bootstrap_host = reached
            _LOGGER.debug(
                "The certificate this panel serves does not name %s; reaching it "
                "at %s until it does",
                host,
                reached,
            )
        return True

    def ensure_flow_is_set_up(self) -> None:
        """Ensure the flow is set up."""
        if self._is_flow_setup is False:
            _LOGGER.error("Flow method called before setup")
            raise ConfigFlowError("Flow is not set up")

    async def ensure_not_already_configured(self, raise_on_progress: bool = True) -> None:
        """Ensure the panel is not already configured."""
        self.ensure_flow_is_set_up()

        # Abort if we had already set this panel up.
        # User-initiated flows pass raise_on_progress=False so they can
        # proceed when a zeroconf discovery flow is already running.
        await self.async_set_unique_id(self.serial_number, raise_on_progress=raise_on_progress)
        self._abort_if_unique_id_configured(updates=await self._async_host_update())

    async def _async_host_update(self) -> dict[str, Any] | None:
        """Return the host update the already-configured abort should carry, or None.

        The abort that follows is also how an entry follows its panel: a new
        DHCP lease, a re-announcement on the new address, and the entry is
        rewritten to point at it. Both routes into this reach it before anything
        has been authenticated — an `_ebus._tcp` record is whatever the LAN
        chose to broadcast, and the serial that matched came from the candidate
        host's own unauthenticated `/api/v2/status` — so on its own, "claims to
        be this serial" is a claim anything on the network can make.

        A pinned entry has a better question available: does the candidate serve
        a certificate this entry's own anchor validates? Nothing but the panel
        holding the matching key can answer it, and it is precisely the check
        the entry's own connections will apply afterwards. Moving a pinned entry
        onto a host that fails it is not a recoverable mistake either way round:
        an impostor gets the entry pointed at itself and the user a CA-changed
        repair inviting them to accept its fingerprint, while an honest name the
        panel simply does not serve — a single-label host, an FQDN never
        registered — leaves the entry unable to reach its own panel with no flow
        left to correct it.

        Verified as the host would be *stored*, against the port the entry
        already uses. The address fallback that `_async_choose_bootstrap_host`
        applies is deliberately not repeated: that one exists to dial a panel
        while a name is unverified, and accepting a name here because its
        address validates would persist exactly the host that fallback refuses
        to write.

        An entry with no anchor keeps the behaviour it shipped with. There is no
        pin to protect and no check to make, so refusing the move would cost an
        unpinned install its panel and buy nothing.
        """
        host = self.host
        if not host or self.unique_id is None:
            return None
        entry = self.hass.config_entries.async_entry_for_domain_unique_id(
            self.handler, self.unique_id
        )
        if entry is None:
            return {CONF_HOST: host}
        ca_pem = entry.data.get(CONF_PANEL_CA_PEM)
        if not ca_pem:
            return {CONF_HOST: host}

        tls_port = as_port(entry.data.get(CONF_HTTPS_PORT), DEFAULT_HTTPS_PORT)
        try:
            chains = await async_leaf_chains_to_ca(host, tls_port, str(ca_pem))
        except Exception:
            # `async_leaf_chains_to_ca` answers False for every failure it
            # anticipates, so reaching here at all is unexpected. Caught broadly
            # rather than left to propagate because the two outcomes are not
            # symmetric: an escaped error inside a discovery flow is an
            # unhandled traceback, and what it would abandon is the one check
            # standing between an unauthenticated claim and a pinned entry's
            # host. Anything unrecognised is a refusal.
            _LOGGER.exception("Could not verify %s against the pinned authority", host)
            chains = False
        if chains:
            return {CONF_HOST: host}

        _LOGGER.warning(
            "Refusing to move the entry for panel %s to %s: the certificate served there on "
            "port %s does not chain to the authority this entry is pinned to (SHA-256 %s)",
            self.unique_id,
            host,
            tls_port,
            self._pinned_fingerprint(str(ca_pem)),
        )
        return None

    @staticmethod
    def _pinned_fingerprint(ca_pem: str) -> str:
        """Return the fingerprint of the anchor that refused a host, for the log line.

        The anchor's, not the certificate the refused host served. This is the
        value the install logged and diagnostics reports, so it is the one a
        user can compare something against — and reading the other one would
        mean opening a second, deliberately unverified connection to a host this
        code has just decided not to trust, to obtain a number with nothing to
        compare it to.

        A PEM this system cannot read is not a reason to lose the warning, so it
        degrades to a placeholder rather than raising.
        """
        try:
            return ca_fingerprint(ca_pem)
        except SpanPanelValidationError:
            return "unreadable"

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo) -> ConfigFlowResult:
        """Handle a flow initiated by zeroconf discovery."""
        # Do not probe device if the host is already configured
        self._async_abort_entries_match({CONF_HOST: discovery_info.host})

        # Do not probe device if it is not an ipv4 address
        if not is_ipv4_address(discovery_info.host):
            return self.async_abort(reason="not_ipv4_address")

        # Set a preliminary unique_id from the host to prevent duplicate
        # in-progress discovery flows when mDNS fires repeatedly for the
        # same IP. The default raise_on_progress=True causes subsequent
        # flows for the same host to abort immediately with
        # "already_in_progress". This is replaced with the serial number
        # in ensure_not_already_configured() once the device is validated.
        await self.async_set_unique_id(discovery_info.host)

        # Detect whether this is a v2 panel based on zeroconf service type
        svc_type = getattr(discovery_info, "type", "") or ""
        is_v2_service = svc_type in ("_ebus._tcp.local.", "_secure-mqtt._tcp.local.")

        if is_v2_service:
            # v2 panels discovered via eBus / secure-mqtt service types
            # Read optional httpPort from mDNS TXT records (non-standard port)
            props = discovery_info.properties or {}
            discovered_http_port = _discovered_port(props, "httpPort", "httpport")
            http_port = 80 if discovered_http_port is None else discovered_http_port
            self._http_port = http_port

            # A panel that moved its HTTP port has usually moved its TLS port
            # too, and it is the only party that knows where. Taking the
            # published value here is what keeps the reverse-proxy question off
            # the screen of a user whose panel already answered it.
            https_port = _discovered_port(props, "httpsPort", "httpsport")
            if https_port is not None:
                self._https_port = https_port
                self._https_port_known = True

            detection = await detect_api_version(
                discovery_info.host,
                port=http_port,
                httpx_client=get_async_client(self.hass, verify_ssl=False),
            )
            if detection.api_version != "v2" or detection.status_info is None:
                return self.async_abort(reason="not_span_panel")
            self.api_version = "v2"
            self.host = discovery_info.host
            self.serial_number = detection.status_info.serial_number
            self.trigger_flow_type = TriggerFlowType.CREATE_ENTRY
            self.context = {
                **self.context,
                "title_placeholders": {
                    **self.context.get("title_placeholders", {}),
                    CONF_HOST: discovery_info.host,
                },
            }
            self._is_flow_setup = True
            await self.ensure_not_already_configured()
            return await self.async_step_confirm_discovery()

        # Non-v2 panels are not supported
        return self.async_abort(reason="v1_not_supported")

    async def async_step_hassio(self, discovery_info: HassioServiceInfo) -> ConfigFlowResult:
        """Handle discovery from Home Assistant Supervisor (add-on).

        Unlike zeroconf, several panels may be reachable on the same host IP
        on different HTTP ports (for example the SPAN Panel Simulator add-on).
        Deduplicate by panel serial, not by host, so each panel gets its own
        config entry.
        """
        config = discovery_info.config
        host = str(config.get("host", ""))
        discovered_port = _discovered_port(config, "port")
        port = 80 if discovered_port is None else discovered_port
        serial = str(config.get("serial", ""))

        if not host:
            return self.async_abort(reason="no_host")

        # The add-on serves TLS on a port it allocated and publishes here.
        # Nobody else can tell the flow what it is, so being told is the
        # difference between a silent setup and a prompt for a number the user
        # would have to go read out of the add-on log.
        https_port = _discovered_port(config, "https_port", "httpsPort")
        if https_port is not None:
            self._https_port = https_port
            self._https_port_known = True

        # Validate panel is reachable and v2
        self._http_port = port
        detection = await detect_api_version(
            host, port=port, httpx_client=get_async_client(self.hass, verify_ssl=False)
        )
        if detection.api_version != "v2" or detection.status_info is None:
            return self.async_abort(reason="not_span_panel")

        # Use the serial from the panel (prefer detected over discovery hint)
        panel_serial = detection.status_info.serial_number or serial
        if not panel_serial:
            return self.async_abort(reason="no_serial")

        # Dedup by serial — multiple panels may share the same host IP. The
        # ports go into the update because the add-on reallocates them across
        # restarts: an entry left pointing at last run's ports is an entry that
        # cannot reach its panel.
        await self.async_set_unique_id(panel_serial)
        updates: dict[str, Any] = {CONF_HOST: host, CONF_HTTP_PORT: port}
        if self._https_port_known:
            updates[CONF_HTTPS_PORT] = self._https_port
        self._abort_if_unique_id_configured(updates=updates)

        # Set up flow — same path as v2 zeroconf discovery
        self.api_version = "v2"
        self.host = host
        self.serial_number = panel_serial
        self.trigger_flow_type = TriggerFlowType.CREATE_ENTRY
        self.context = {
            **self.context,
            "title_placeholders": {
                **self.context.get("title_placeholders", {}),
                CONF_HOST: panel_serial,
            },
        }
        self._is_flow_setup = True
        return await self.async_step_confirm_discovery()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle a flow initiated by the user."""
        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=STEP_USER_DATA_SCHEMA)

        # Store precision settings from user input for later flow steps.
        self.power_display_precision = user_input.get(POWER_DISPLAY_PRECISION, 0)
        self.energy_display_precision = user_input.get(ENERGY_DISPLAY_PRECISION, 2)
        self._enable_dip_compensation = user_input.get(ENABLE_ENERGY_DIP_COMPENSATION, True)

        _LOGGER.debug(
            "CONFIG_INPUT_DEBUG: User input precision - power: %s, energy: %s, full input: %s",
            self.power_display_precision,
            self.energy_display_precision,
            user_input,
        )

        host: str = user_input.get(CONF_HOST, "").strip()
        self._http_port = int(user_input.get(CONF_HTTP_PORT, 80))
        if not host:
            return self.async_show_form(
                step_id="user",
                data_schema=STEP_USER_DATA_SCHEMA,
                errors={"base": "host_required"},
            )

        # Validate host before setting up flow
        if not await validate_host(self.hass, host, port=self._http_port):
            return self.async_show_form(
                step_id="user",
                data_schema=STEP_USER_DATA_SCHEMA,
                errors={"base": "cannot_connect"},
            )

        # Detect API version — only v2 is supported
        detection = await detect_api_version(
            host,
            port=self._http_port,
            httpx_client=get_async_client(self.hass, verify_ssl=False),
        )
        if detection.probe_failed:
            return self.async_show_form(
                step_id="user",
                data_schema=STEP_USER_DATA_SCHEMA,
                errors={"base": "cannot_connect"},
            )
        self.api_version = detection.api_version

        if self.api_version == "v2":
            if detection.status_info is None:
                return self.async_show_form(
                    step_id="user",
                    data_schema=STEP_USER_DATA_SCHEMA,
                    errors={"base": "cannot_connect"},
                )
            # Serial comes from detection
            self.host = host
            self.serial_number = detection.status_info.serial_number
            self.trigger_flow_type = TriggerFlowType.CREATE_ENTRY
            self.context = {
                **self.context,
                "title_placeholders": {
                    **self.context.get("title_placeholders", {}),
                    CONF_HOST: host,
                },
            }
            self._is_flow_setup = True
            await self.ensure_not_already_configured(raise_on_progress=False)
            # The CA first, then authentication over it. Registration is the one
            # exchange that carries the passphrase and returns both credentials.
            return await self.async_step_panel_ca_start()

        # Non-v2 panels are not supported
        return self.async_abort(reason="v1_not_supported")

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Handle a flow initiated by re-auth."""
        host = entry_data[CONF_HOST]
        self._http_port = int(entry_data.get(CONF_HTTP_PORT, 80))
        # Only a port that could actually be read counts as known. `as_port`
        # would substitute the default for an unreadable stored value and this
        # flow would then record "somebody authoritative told us" about a number
        # nobody supplied, skipping the one question that could correct it.
        stored_https_port = port_or_none(entry_data.get(CONF_HTTPS_PORT))
        if stored_https_port is not None:
            self._https_port = stored_https_port
            self._https_port_known = True
        # This entry may already have a pinned CA, and a reauth is precisely
        # where fresh credentials cross the wire. Adopted before the first call.
        self._rest_transport = panel_rest_transport(self.hass, entry_data)

        # Detect current API version of the panel
        detection = await detect_api_version(
            host,
            port=self._rest_transport.port,
            httpx_client=self._rest_transport.httpx_client,
            ssl_context=self._rest_transport.ssl_context,
        )
        if detection.probe_failed:
            return self.async_abort(reason="cannot_connect")
        self.api_version = detection.api_version

        if self.api_version == "v2":
            if detection.status_info is None:
                return self.async_abort(reason="cannot_connect")
            # v2 reauth: set up flow state manually and show confirmation
            self.host = host
            self.serial_number = detection.status_info.serial_number
            self.trigger_flow_type = TriggerFlowType.UPDATE_ENTRY
            self._is_flow_setup = True
            self.context["title_placeholders"] = {"host": host}
            if self._rest_transport.ssl_context is None:
                # Nothing pinned, and the next exchange carries the passphrase
                # and returns the broker password. Entries arrive here unpinned
                # by three routes — an entry still recorded as v1, a v2 entry
                # missing its broker credentials, and one whose deferred fetch
                # failed — and all three fail setup before the deferred fetch at
                # setup can settle them, so reauth is where they get an anchor
                # or do not proceed. The CA step returns to `reauth_confirm`.
                return await self.async_step_panel_ca_start()
            return await self.async_step_reauth_confirm()

        # Non-v2 panels are not supported
        return self.async_abort(reason="v1_not_supported")

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show reauth context and let user choose authentication method."""
        return self.async_show_menu(
            step_id="reauth_confirm",
            menu_options=["auth_passphrase", "auth_proximity"],
            description_placeholders={"host": self.host or ""},
        )

    async def async_step_confirm_discovery(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Prompt user to confirm a discovered Span Panel."""
        self.ensure_flow_is_set_up()

        # Prompt the user for confirmation
        if user_input is None:
            self._set_confirm_only()
            host = self.host if self.host is not None else ""
            return self.async_show_form(
                step_id="confirm_discovery",
                description_placeholders={
                    "host": host,
                },
            )

        return await self.async_step_panel_ca_start()

    async def async_step_choose_v2_auth(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose v2 authentication method: passphrase or proximity."""
        return self.async_show_menu(
            step_id="choose_v2_auth",
            menu_options=["auth_passphrase", "auth_proximity"],
        )

    async def async_step_auth_proximity(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Instruct user to complete the door challenge, then confirm or switch method."""
        return self.async_show_menu(
            step_id="auth_proximity",
            menu_options=["auth_proximity_confirm", "auth_passphrase"],
        )

    async def async_step_auth_proximity_confirm(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Verify proximity was proven, then register."""
        if not self.host:
            return self.async_abort(reason="host_not_set")

        transport = self._require_transport()
        # Check proximityProven before calling register_v2 (avoids 15-min block).
        # On older firmware the field is None — fall through to register_v2 directly.
        # Over the pin, like the registration it gates: this probe is what
        # decides whether the door challenge is believed, and a plaintext answer
        # is one anything on the path can write.
        try:
            detection = await detect_api_version(
                self._rest_host,
                port=transport.port,
                httpx_client=transport.httpx_client,
                ssl_context=transport.ssl_context,
            )
        except (SpanPanelAPIError, SpanPanelConnectionError, SpanPanelTimeoutError) as err:
            _LOGGER.warning("Failed to detect API version during proximity auth: %s", err)
            return await self.async_step_auth_proximity()
        proximity_status = (
            detection.status_info.proximity_proven if detection.status_info is not None else None
        )
        if proximity_status is False:
            # Door challenge not completed — send back to the instruction menu.
            return await self.async_step_auth_proximity()

        try:
            result = await validate_v2_proximity(self._rest_host, transport)
        except (SpanPanelAuthError, SpanPanelConnectionError):
            return await self.async_step_auth_proximity()

        self._store_v2_auth_result(result)
        return await self._async_finalize_v2_auth()

    async def async_step_auth_passphrase(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Collect the panel passphrase for v2 authentication."""
        if user_input is None:
            return self.async_show_form(
                step_id="auth_passphrase",
                data_schema=STEP_AUTH_PASSPHRASE_DATA_SCHEMA,
            )

        passphrase = user_input.get(CONF_HOP_PASSPHRASE, "").strip()
        if not passphrase:
            return self.async_show_form(
                step_id="auth_passphrase",
                data_schema=STEP_AUTH_PASSPHRASE_DATA_SCHEMA,
                errors={"base": "invalid_auth"},
            )

        if not self.host:
            return self.async_abort(reason="host_not_set")

        try:
            result = await validate_v2_passphrase(
                self._rest_host, passphrase, self._require_transport()
            )
        except SpanPanelAuthError:
            return self.async_show_form(
                step_id="auth_passphrase",
                data_schema=STEP_AUTH_PASSPHRASE_DATA_SCHEMA,
                errors={"base": "invalid_auth"},
            )
        except SpanPanelConnectionError:
            return self.async_show_form(
                step_id="auth_passphrase",
                data_schema=STEP_AUTH_PASSPHRASE_DATA_SCHEMA,
                errors={"base": "cannot_connect"},
            )

        self._store_v2_auth_result(result)
        return await self._async_finalize_v2_auth()

    def _store_v2_auth_result(self, result: V2AuthResponse) -> None:
        """Store v2 auth credentials from registration result.

        The passphrase that produced this result is deliberately not kept. It is
        an input to registration and nothing afterwards reads it, so holding it
        only widens what a flow in progress has in memory.
        """
        self.access_token = result.access_token
        self._v2_broker_host = result.ebus_broker_host
        self._v2_broker_port = result.ebus_broker_mqtts_port
        self._v2_broker_username = result.ebus_broker_username
        self._v2_broker_password = result.ebus_broker_password
        self._v2_panel_serial = result.serial_number

    async def _async_finalize_v2_auth(self) -> ConfigFlowResult:
        """Route to appropriate next step after successful v2 auth.

        Registration is the one thing that can make the panel name a host it
        does not name yet, so it is the only route allowed to persist a host
        that is currently unverified. Every other route here writes `self.host`
        into an entry pinned to this anchor, and writing one the anchor rejects
        produces an entry that cannot reach its own panel — a single-label name
        that a search domain resolves, or an FQDN this flow bootstrapped over
        the address for and is not going on to register.
        """
        # If host is an FQDN, register it with the panel for TLS cert SAN inclusion
        installing = self.trigger_flow_type != TriggerFlowType.UPDATE_ENTRY
        if installing and self.host and is_fqdn(self.host):
            return await self.async_step_register_fqdn()

        if self._bootstrap_host is not None:
            _LOGGER.warning(
                "Panel %s does not name %s in the certificate it serves, and nothing in "
                "this flow will ask it to; refusing to pin an entry to a host its own "
                "certificate authority rejects",
                self._bootstrap_host,
                self.host,
            )
            return self._async_show_ca_error("ca_leaf_mismatch")

        if self.trigger_flow_type == TriggerFlowType.UPDATE_ENTRY:
            if "entry_id" not in self.context:
                raise ValueError("Entry ID is missing from context")
            return self._update_v2_entry(self.context["entry_id"])
        return await self.async_step_choose_entity_naming_initial()

    async def async_step_register_fqdn(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Register FQDN with the panel and wait for TLS certificate update."""
        if not self._fqdn_task:
            self._fqdn_task = self.hass.async_create_task(
                self._async_register_fqdn_and_wait(),
                "span_panel_register_fqdn",
            )

        if not self._fqdn_task.done():
            return self.async_show_progress(
                step_id="register_fqdn",
                progress_action="registering_fqdn",
                progress_task=self._fqdn_task,
            )

        try:
            self._fqdn_task.result()
        except Exception:
            _LOGGER.exception("FQDN registration failed for %s", self.host)
            self._fqdn_task = None
            return self.async_show_progress_done(next_step_id="fqdn_failed")

        self._fqdn_task = None
        return self.async_show_progress_done(next_step_id="choose_entity_naming_initial")

    async def _async_register_fqdn_and_wait(self) -> None:
        """Register the FQDN, wait for the panel's new leaf, then verify it.

        Three addresses matter here and they are not the same one. The request
        carries the access token, so it goes over the pin -- and it is dialled by
        the address the current leaf names, because the FQDN is precisely what
        this call is asking the panel to add. Only once the panel reports the
        regenerated certificate is the FQDN itself verified, and it is verified
        on the port the entry will actually use, before anything is persisted.
        """
        if not self.host or not self.access_token:
            raise ConfigFlowError("Host and access token required for FQDN registration")

        transport = self._require_transport()
        await register_fqdn(
            self._rest_host,
            self.access_token,
            self.host,
            port=transport.port,
            httpx_client=transport.httpx_client,
            ssl_context=transport.ssl_context,
        )

        mqtts_port = self._v2_broker_port or 8883
        max_attempts = 30
        for attempt in range(max_attempts):
            await asyncio.sleep(2)
            if await check_fqdn_tls_ready(
                self.hass,
                self.host,
                mqtts_port,
                transport.ca_pem,
                http_port=self._http_port,
            ):
                _LOGGER.debug(
                    "FQDN %s found in TLS cert SAN after %d attempts",
                    self.host,
                    attempt + 1,
                )
                await self._async_verify_host_over_pin(transport)
                # The panel serves the name now, so the address it was reached
                # by while it did not is no longer standing in for anything.
                self._bootstrap_host = None
                self._fqdn_registered = True
                return

        raise ConfigFlowError(f"Timed out waiting for TLS certificate to include FQDN {self.host}")

    async def _async_verify_host_over_pin(self, transport: PanelRestTransport) -> None:
        """Confirm the pinned anchor validates the panel at `self.host` and the REST port.

        The readiness poll watches the broker port, which is the port the MQTT
        client uses. The entry's REST calls go somewhere else -- the HTTPS port
        this transport holds -- and an entry is about to be written naming the
        FQDN for both. Confirming the exact pair that will be stored is what
        stops registration reporting success on a combination that then fails on
        the first connect after setup.

        Nothing is pinned on a reconfigure of an entry whose CA fetch never
        succeeded; there is no anchor to verify against and no pin to protect,
        so that entry proceeds exactly as it did before.
        """
        if transport.ca_pem is None:
            return
        if not await async_leaf_chains_to_ca(self.host or "", transport.port, transport.ca_pem):
            raise ConfigFlowError(
                f"Panel registered {self.host} but does not serve it on port "
                f"{transport.port} under the pinned certificate authority"
            )

    async def async_step_fqdn_failed(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle FQDN registration failure — user may continue over the panel's address.

        Continuing keeps the panel, not the name. The certificate does not name
        the domain, because getting it named is exactly what failed, so an entry
        recording the domain would be pinned to a host its own anchor rejects
        and would fail on its first connect. It is set up over the address the
        certificate does name instead, and the domain can be tried again from
        Reconfigure once whatever blocked the registration is fixed.
        """
        if user_input is not None:
            self._fall_back_to_the_bootstrap_address()
            return await self.async_step_choose_entity_naming_initial()
        return self.async_show_form(
            step_id="fqdn_failed",
            data_schema=vol.Schema({}),
            errors={"base": "fqdn_registration_failed"},
        )

    def _fall_back_to_the_bootstrap_address(self) -> None:
        """Adopt the address the panel was reached by as the host to record.

        Only ever called where registration has already failed. `_bootstrap_host`
        is set exactly when `self.host` is a name the pinned leaf does not name,
        so this is the point where an unusable name is traded for the address
        that got this far — and the invariant is restored, because the host
        being recorded is now one the anchor accepts.
        """
        if self._bootstrap_host is None:
            return
        _LOGGER.warning(
            "Could not get panel %s to serve %s, so the entry is set up over %s instead; "
            "the certificate the panel serves does not name %s",
            self._bootstrap_host,
            self.host,
            self._bootstrap_host,
            self.host,
        )
        self.host = self._bootstrap_host
        self._bootstrap_host = None

    async def async_step_panel_ca_start(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Enter CA acquisition, asking for the HTTPS port only when nothing knows it.

        The port is prompted for on an install that already moved the HTTP port,
        because that is the install most likely to be reaching the panel through
        something that also moved the TLS one. Asking everybody would put a
        question about reverse proxies in front of users who have none.

        Discovery outranks the prompt. A panel that published its TLS port has
        already answered the question, and better than the user could: the
        add-on allocates that port per panel and reallocates it across restarts,
        so the only correct answer is the one it just gave.
        """
        if self._http_port == 80 or self._https_port_known:
            return await self.async_step_panel_ca()
        return await self.async_step_panel_https_port()

    async def async_step_panel_https_port(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the port the panel serves TLS on."""
        if user_input is None:
            return self.async_show_form(
                step_id="panel_https_port",
                data_schema=vol.Schema(
                    {
                        vol.Required(CONF_HTTPS_PORT, default=DEFAULT_HTTPS_PORT): int,
                    }
                ),
            )
        self._https_port = int(user_input[CONF_HTTPS_PORT])
        return await self.async_step_panel_ca()

    async def async_step_panel_ca(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Fetch the panel's CA, prove it signs what the panel serves, and offer it.

        This runs **before** authentication, which is the whole point of its
        position in the flow: registration is the exchange that carries the
        passphrase and returns both the access token and the broker password, and
        it is the message most worth protecting. Everything after this step goes
        over the anchor accepted here.

        `user_input` is ignored. The step does its work on entry, and a submitted
        error form re-enters it — which is exactly the retry semantics wanted,
        since the failures are transient network ones.
        """
        if not self.host:
            return self.async_abort(reason="host_not_set")

        try:
            ca_pem = await async_fetch_panel_ca(self.hass, self.host, http_port=self._http_port)
        except (
            SpanPanelAPIError,
            SpanPanelConnectionError,
            SpanPanelTimeoutError,
        ) as err:
            _LOGGER.warning("Could not fetch the CA from panel %s: %s", self.host, err)
            return self._async_show_ca_error("ca_unavailable")

        # A CA that cannot validate the certificate the panel actually serves is
        # not the panel's CA. Checked before the fingerprint is shown, so the
        # user is never asked to accept a value that already failed. Hostname
        # verification stays on throughout -- what the chooser varies is the
        # address the panel is dialled by, never whether the name is checked.
        if not await self._async_choose_bootstrap_host(ca_pem, self._https_port):
            _LOGGER.warning(
                "The certificate served by %s on port %s does not chain to the CA the "
                "panel published; refusing to pin it",
                self.host,
                self._https_port,
            )
            return self._async_show_ca_error("ca_leaf_mismatch")

        try:
            fingerprint = ca_fingerprint(ca_pem)
        except SpanPanelValidationError as err:
            _LOGGER.warning("Panel %s served an unreadable CA: %s", self.host, err)
            return self._async_show_ca_error("ca_unavailable")

        _LOGGER.info(
            "Pinned the certificate authority published by panel %s (SHA-256 %s)",
            self.host,
            fingerprint,
        )
        self._panel_ca_pem = ca_pem
        return await self._async_adopt_anchor_and_authenticate()

    def _async_show_ca_error(self, reason: str) -> ConfigFlowResult:
        """Re-show the CA step with an actionable error.

        Deliberately not a way past. There is no "carry on unpinned" option here,
        because the next thing this flow does is send the panel passphrase: an
        opt-out would quietly restore the plaintext credential exchange that
        pinning before registration exists to remove, at the moment a user is
        least likely to weigh it.

        Submitting the form re-enters `async_step_panel_ca`, so a panel that was
        briefly unreachable is one click away from working.
        """
        return self.async_show_form(
            step_id="panel_ca",
            data_schema=vol.Schema({}),
            errors={"base": reason},
        )

    async def _async_adopt_anchor_and_authenticate(self) -> ConfigFlowResult:
        """Adopt the pinned anchor, then authenticate over it.

        Not a step, and deliberately not a confirmation. Pinning happens either
        way — the protection is that registration runs over the anchor, not that
        somebody pressed Submit — and at first contact there is nothing to check
        the fingerprint against: SPAN publishes it nowhere, so a screen asking a
        user to accept it offers a decision they cannot make and an alarm they
        cannot act on.

        The fingerprint is surfaced where it is actionable instead: in
        diagnostics, and in the Repair raised if it ever changes, where there is
        a prior value to compare against and a real decision to take. It is also
        logged here, so the value is recoverable from the moment of the install
        that pinned it.
        """
        if self._panel_ca_pem is None:
            return await self.async_step_panel_ca()

        try:
            context = build_panel_ssl_context(self._panel_ca_pem)
        except (ssl.SSLError, ValueError) as err:
            # Unreachable in practice — the leaf check above already built a
            # context from this PEM. Handled rather than assumed, because the
            # alternative to raising here is registering in plaintext.
            _LOGGER.warning("Accepted CA from %s could not be used: %s", self.host, err)
            return self._async_show_ca_error("ca_unavailable")

        self._rest_transport = PanelRestTransport(
            port=self._https_port,
            ssl_context=context,
            httpx_client=None,
            ca_pem=self._panel_ca_pem,
        )
        if self.trigger_flow_type == TriggerFlowType.UPDATE_ENTRY:
            # Same two methods, but the reauth wording: this user is not setting
            # a panel up, they are being asked why an entry stopped working.
            return await self.async_step_reauth_confirm()
        return await self.async_step_choose_v2_auth()

    def create_new_entry(
        self, host: str, serial_number: str, access_token: str
    ) -> ConfigFlowResult:
        """Create a new SPAN panel entry."""
        base_name = "Span Panel"
        device_name = self.get_unique_device_name(base_name)
        _LOGGER.debug(
            "CONFIG_FLOW_DEBUG: Creating entry with precision - power: %s, energy: %s",
            self.power_display_precision,
            self.energy_display_precision,
        )
        # Determine initial naming flags with default to Friendly Names
        use_device_prefix = (
            True if self._chosen_use_device_prefix is None else self._chosen_use_device_prefix
        )
        use_circuit_numbers = (
            False if self._chosen_use_circuit_numbers is None else self._chosen_use_circuit_numbers
        )

        entry_data: dict[str, Any] = {
            CONF_HOST: host,
            CONF_ACCESS_TOKEN: access_token,
            "device_name": device_name,
            CONF_API_VERSION: "v2",
            CONF_EBUS_BROKER_HOST: self._v2_broker_host,
            CONF_EBUS_BROKER_PORT: self._v2_broker_port,
            CONF_EBUS_BROKER_USERNAME: self._v2_broker_username,
            CONF_EBUS_BROKER_PASSWORD: self._v2_broker_password,
            CONF_PANEL_SERIAL: self._v2_panel_serial,
        }

        if self._http_port != 80:
            entry_data[CONF_HTTP_PORT] = self._http_port
        if self._https_port != DEFAULT_HTTPS_PORT:
            entry_data[CONF_HTTPS_PORT] = self._https_port
        if self._panel_ca_pem is None:
            # Unreachable: the flow cannot reach entity naming without passing
            # the CA confirmation, and authentication ran over the anchor
            # accepted there. Raised rather than defaulted, because the only
            # other option is to write an unpinned entry whose credentials were
            # nonetheless exchanged over TLS — a state nothing else expects.
            raise ConfigFlowError("Reached entry creation with no pinned panel CA")
        entry_data[CONF_PANEL_CA_PEM] = self._panel_ca_pem
        if self._fqdn_registered:
            # Recorded from what the registration did, not from what the host
            # looks like. `is_fqdn(host)` was true on the path where
            # registration had just failed and the user chose to continue, so
            # the entry claimed a name the panel had never accepted.
            entry_data[CONF_REGISTERED_FQDN] = host

        return self.async_create_entry(
            title=device_name,
            data=entry_data,
            options={
                USE_DEVICE_PREFIX: use_device_prefix,
                USE_CIRCUIT_NUMBERS: use_circuit_numbers,
                POWER_DISPLAY_PRECISION: self.power_display_precision,
                ENERGY_DISPLAY_PRECISION: self.energy_display_precision,
                ENABLE_ENERGY_DIP_COMPENSATION: self._enable_dip_compensation,
            },
        )

    def _update_v2_entry(self, entry_id: str) -> ConfigFlowResult:
        """Update an existing config entry with new v2 MQTT credentials."""
        entry: SpanPanelConfigEntry | None = self.hass.config_entries.async_get_entry(entry_id)
        if entry is None:
            _LOGGER.error("Config entry %s does not exist during v2 reauth", entry_id)
            return self.async_abort(reason="reauth_failed")

        updated_data = dict(entry.data)
        updated_data[CONF_ACCESS_TOKEN] = self.access_token
        updated_data[CONF_API_VERSION] = "v2"
        updated_data[CONF_EBUS_BROKER_HOST] = self._v2_broker_host
        updated_data[CONF_EBUS_BROKER_PORT] = self._v2_broker_port
        updated_data[CONF_EBUS_BROKER_USERNAME] = self._v2_broker_username
        updated_data[CONF_EBUS_BROKER_PASSWORD] = self._v2_broker_password
        updated_data[CONF_PANEL_SERIAL] = self._v2_panel_serial
        # A reauth on an entry that predates v7 is also the moment to drop the
        # passphrase it still carries; the migration only sees entries at setup.
        updated_data.pop(CONF_HOP_PASSPHRASE, None)
        if self._http_port != 80:
            updated_data[CONF_HTTP_PORT] = self._http_port
        if self._panel_ca_pem is not None:
            # The entry reached this reauth with nothing usable pinned, so the
            # flow acquired an anchor before sending the passphrase. Keeping it
            # is what stops the next reauth — and every connect in between —
            # from going back to a plaintext CA fetch. `_panel_ca_pem` is None
            # only when the entry already carried a usable pin, which is left
            # exactly as it was.
            self._log_replaced_anchor(entry.data.get(CONF_PANEL_CA_PEM))
            updated_data[CONF_PANEL_CA_PEM] = self._panel_ca_pem
            updated_data.pop(PANEL_CA_PENDING, None)
            if self._https_port != DEFAULT_HTTPS_PORT:
                updated_data[CONF_HTTPS_PORT] = self._https_port

        self.hass.config_entries.async_update_entry(entry, data=updated_data)
        self.hass.async_create_task(self.hass.config_entries.async_reload(entry_id))
        return self.async_abort(reason="reauth_successful")

    def _log_replaced_anchor(self, previous_pem: object) -> None:
        """Say so, at WARNING, when a reauth overwrites a stored trust anchor.

        The entry only reaches here unpinned, and one of the ways it does is a
        stored PEM this system can no longer load. Replacing that is silently
        changing what the entry trusts, which is the one change a user needs a
        record of: the fingerprint below is the value to compare against the one
        logged at install, and the only trace of it if the old PEM is gone.

        A first pin is not a replacement and says nothing here -- the install
        log already named it.
        """
        if not previous_pem or previous_pem == self._panel_ca_pem or self._panel_ca_pem is None:
            return
        try:
            fingerprint = ca_fingerprint(self._panel_ca_pem)
        except SpanPanelValidationError:  # pragma: no cover - already fingerprinted once
            fingerprint = "unreadable"
        _LOGGER.warning(
            "Replaced the stored certificate authority for panel %s during reauthentication; "
            "the entry is now pinned to SHA-256 %s",
            self.host,
            fingerprint,
        )

    def get_unique_device_name(self, base_name: str) -> str:
        """Return a unique device name based on existing config entry titles."""
        existing_names = {entry.title for entry in self.hass.config_entries.async_entries(DOMAIN)}
        if base_name not in existing_names:
            return base_name
        i = 2
        while f"{base_name} {i}" in existing_names:
            i += 1
        return f"{base_name} {i}"

    async def async_step_choose_entity_naming_initial(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pre-setup choice of Entity ID naming pattern.

        Default to Friendly Names; both choices imply device prefix enabled.
        """

        self.ensure_flow_is_set_up()

        pattern_options = {
            EntityNamingPattern.FRIENDLY_NAMES.value: "Circuit Friendly Names",
            EntityNamingPattern.CIRCUIT_NUMBERS.value: "Tab Based Names",
        }

        if user_input is None:
            schema = vol.Schema(
                {
                    vol.Required(
                        ENTITY_NAMING_PATTERN,
                        default=EntityNamingPattern.FRIENDLY_NAMES.value,
                    ): vol.In(pattern_options)
                }
            )
            return self.async_show_form(
                step_id="choose_entity_naming_initial",
                data_schema=schema,
            )

        selected = user_input.get(ENTITY_NAMING_PATTERN, EntityNamingPattern.FRIENDLY_NAMES.value)
        self._chosen_use_device_prefix = True
        self._chosen_use_circuit_numbers = selected == EntityNamingPattern.CIRCUIT_NUMBERS.value

        # Proceed to create the entry
        if self.host is None or self.serial_number is None or self.access_token is None:
            raise ConfigFlowError("Missing required parameters during entry creation")
        return self.create_new_entry(self.host, self.serial_number, self.access_token)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reconfiguration of the integration (e.g. host change)."""
        reconfigure_entry = self._get_reconfigure_entry()

        if user_input is None:
            current_host = reconfigure_entry.data.get(CONF_HOST, "")
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=vol.Schema({vol.Required(CONF_HOST, default=current_host): str}),
            )

        host = user_input[CONF_HOST].strip()
        if not host:
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=vol.Schema({vol.Required(CONF_HOST, default=""): str}),
                errors={"base": "host_required"},
            )

        # Validate the host is reachable and is a v2 panel
        http_port = int(reconfigure_entry.data.get(CONF_HTTP_PORT, 80))
        self._rest_transport = panel_rest_transport(self.hass, reconfigure_entry.data)
        # The new host may be a name the panel has never heard of, and this
        # entry is pinned: probing it by name would fail hostname verification
        # and report the panel unreachable. Settle the address first, so the
        # probe reaches the panel and `_bootstrap_host` records whether the new
        # name is one the pinned certificate already covers.
        self.host = host
        pinned_pem = self._rest_transport.ca_pem
        if pinned_pem is not None and not await self._async_choose_bootstrap_host(
            pinned_pem, self._rest_transport.port
        ):
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=vol.Schema({vol.Required(CONF_HOST, default=host): str}),
                errors={"base": "ca_leaf_mismatch"},
            )
        try:
            detection = await detect_api_version(
                self._rest_host,
                port=self._rest_transport.port,
                httpx_client=self._rest_transport.httpx_client,
                ssl_context=self._rest_transport.ssl_context,
            )
        except (
            ValueError,
            SpanPanelConnectionError,
            SpanPanelTimeoutError,
            SpanPanelAPIError,
        ):
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=vol.Schema({vol.Required(CONF_HOST, default=host): str}),
                errors={"base": "cannot_connect"},
            )

        if detection.probe_failed:
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=vol.Schema({vol.Required(CONF_HOST, default=host): str}),
                errors={"base": "cannot_connect"},
            )

        if detection.api_version != "v2" or detection.status_info is None:
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=vol.Schema({vol.Required(CONF_HOST, default=host): str}),
                errors={"base": "cannot_connect"},
            )

        # Ensure the serial number matches — prevent switching to a different panel
        await self.async_set_unique_id(detection.status_info.serial_number)
        self._abort_if_unique_id_mismatch(reason="unique_id_mismatch")

        if is_fqdn(host):
            # New host is FQDN — register it (replaces any existing FQDN on the panel)
            self.access_token = str(reconfigure_entry.data.get(CONF_ACCESS_TOKEN, ""))
            self._http_port = http_port
            self._v2_broker_port = int(reconfigure_entry.data.get(CONF_EBUS_BROKER_PORT, 8883))
            return await self.async_step_reconfigure_register_fqdn()

        # New host is not an FQDN — simple update. Nothing on this branch will
        # ask the panel to start naming it, so a host the pinned certificate
        # does not cover is refused here rather than written into the entry.
        if self._bootstrap_host is not None:
            _LOGGER.warning(
                "Panel %s does not name %s in the certificate it serves; refusing to "
                "point a pinned entry at a host its own certificate authority rejects",
                self._bootstrap_host,
                host,
            )
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=vol.Schema({vol.Required(CONF_HOST, default=host): str}),
                errors={"base": "ca_leaf_mismatch"},
            )

        data_updates: dict[str, Any] = {CONF_HOST: host}
        old_fqdn = str(reconfigure_entry.data.get(CONF_REGISTERED_FQDN, ""))
        if old_fqdn:
            # Switching from FQDN to IP — clean up old registration
            access_token = str(reconfigure_entry.data.get(CONF_ACCESS_TOKEN, ""))
            try:
                await delete_fqdn(
                    self._rest_host,
                    access_token,
                    port=self._rest_transport.port,
                    httpx_client=self._rest_transport.httpx_client,
                    ssl_context=self._rest_transport.ssl_context,
                )
            except (
                SpanPanelAPIError,
                SpanPanelAuthError,
                SpanPanelConnectionError,
                SpanPanelTimeoutError,
            ):
                _LOGGER.warning("Failed to delete old FQDN registration: %s", old_fqdn)
            data_updates[CONF_REGISTERED_FQDN] = ""

        return self.async_update_reload_and_abort(
            reconfigure_entry,
            data_updates=data_updates,
        )

    async def async_step_reconfigure_register_fqdn(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Register FQDN during reconfiguration and wait for TLS cert update."""
        if not self._reconfigure_fqdn_task:
            self._reconfigure_fqdn_task = self.hass.async_create_task(
                self._async_register_fqdn_and_wait(),
                "span_panel_reconfigure_fqdn",
            )

        if not self._reconfigure_fqdn_task.done():
            return self.async_show_progress(
                step_id="reconfigure_register_fqdn",
                progress_action="registering_fqdn",
                progress_task=self._reconfigure_fqdn_task,
            )

        try:
            self._reconfigure_fqdn_task.result()
        except Exception:
            _LOGGER.exception("FQDN registration failed during reconfigure for %s", self.host)
            self._reconfigure_fqdn_task = None
            return self.async_show_progress_done(next_step_id="reconfigure_fqdn_failed")

        self._reconfigure_fqdn_task = None
        return self.async_show_progress_done(next_step_id="reconfigure_fqdn_done")

    async def async_step_reconfigure_fqdn_done(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Complete reconfiguration after successful FQDN registration."""
        reconfigure_entry = self._get_reconfigure_entry()
        return self.async_update_reload_and_abort(
            reconfigure_entry,
            data_updates={
                CONF_HOST: self.host or "",
                CONF_REGISTERED_FQDN: self.host or "",
            },
        )

    async def async_step_reconfigure_fqdn_failed(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle FQDN registration failure during reconfigure.

        Same trade as on install: the panel is kept and the name is not. The
        entry's existing `CONF_REGISTERED_FQDN` is deliberately left alone —
        whatever the panel had registered before this attempt, it still has, and
        that is what the next reconfigure needs in order to clean it up.
        """
        if user_input is not None:
            # User chose to continue anyway — update host without FQDN registration
            self._fall_back_to_the_bootstrap_address()
            reconfigure_entry = self._get_reconfigure_entry()
            return self.async_update_reload_and_abort(
                reconfigure_entry,
                data_updates={CONF_HOST: self.host or ""},
            )
        return self.async_show_form(
            step_id="reconfigure_fqdn_failed",
            data_schema=vol.Schema({}),
            errors={"base": "fqdn_registration_failed"},
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: SpanPanelConfigEntry,
    ) -> OptionsFlowHandler:
        """Create the options flow."""
        return OptionsFlowHandler()


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle the options flow for Span Panel."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manage the general options."""
        from . import (  # pylint: disable=import-outside-toplevel
            async_apply_panel_registration,
            async_load_panel_settings,
            async_save_panel_settings,
        )

        if user_input is not None:
            filtered_input, errors, panel_settings = process_general_options_input(
                self.config_entry, user_input
            )

            if not errors:
                # Save panel settings to domain-level storage
                current_ps = await async_load_panel_settings(self.hass)
                current_ps.update(panel_settings)
                await async_save_panel_settings(self.hass, current_ps)
                await async_apply_panel_registration(self.hass)
                return self.async_create_entry(title="", data=filtered_input)
        else:
            errors = {}

        panel_settings = await async_load_panel_settings(self.hass)
        schema = GENERAL_OPTIONS_SCHEMA
        defaults = get_general_options_defaults(self.config_entry, panel_settings=panel_settings)

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(schema, defaults),
            errors=errors,
        )

    async def async_step_general_options(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Redirect to init for backward compatibility."""
        return await self.async_step_init(user_input)


# Register the config flow handler
config_entries.HANDLERS.register(DOMAIN)(SpanPanelConfigFlow)
