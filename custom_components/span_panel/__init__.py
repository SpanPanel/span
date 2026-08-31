"""The Span Panel integration."""

from __future__ import annotations

import asyncio
import logging

from homeassistant.components.frontend import async_remove_panel as async_remove_panel
from homeassistant.components.panel_custom import async_register_panel as async_register_panel
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import CoreState, HomeAssistant, callback
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryError,
    ConfigEntryNotReady,
)
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.helpers.httpx_client import get_async_client
from homeassistant.helpers.typing import ConfigType
from span_panel_api import (
    LeafNameMismatch,
    SpanMqttClient,
    SpanPanelSnapshot,
    ca_fingerprint,
)
from span_panel_api.exceptions import (
    SpanPanelAPIError,
    SpanPanelAuthError,
    SpanPanelCAChangedError,
    SpanPanelConnectionError,
    SpanPanelError,
    SpanPanelServerError,
    SpanPanelTimeoutError,
    SpanPanelTLSVerificationError,
    SpanPanelValidationError,
)
from span_panel_api.mqtt.models import MqttClientConfig

# Import config flow to ensure it's registered
from . import config_flow  # noqa: F401
from .additions import async_announce_new_entities, async_forget_announcements
from .adoption import async_register_adopted_devices
from .ca_repairs import (
    async_clear_ca_changed,
    async_clear_ca_unusable,
    async_clear_rest_tls_untrusted,
    async_raise_ca_changed,
    async_raise_ca_unusable,
    async_raise_rest_tls_untrusted,
)
from .config_flow_validation import (
    LeafVerdict,
    PanelCaUnusableError,
    as_port,
    async_ca_signs_panel_leaf,
    async_fetch_panel_ca,
    async_leaf_probe,
    panel_rest_transport,
)
from .const import (
    CONF_API_VERSION,
    CONF_EBUS_BROKER_HOST,
    CONF_EBUS_BROKER_PASSWORD,
    CONF_EBUS_BROKER_PORT,
    CONF_EBUS_BROKER_USERNAME,
    CONF_HTTP_PORT,
    CONF_PANEL_CA_PEM,
    DEFAULT_MQTTS_PORT,
    DEFAULT_SNAPSHOT_INTERVAL,
    DOMAIN,
    ENABLE_CURRENT_MONITORING,
    PANEL_CA_PENDING,
)
from .control_gate import ControlGate, ControlLock, ControlPolicy
from .coordinator import SpanPanelCoordinator
from .current_monitor import CurrentMonitor
from .extension import async_notice_declined_extensions
from .frontend import (
    PANEL_FRONTEND_DIR as PANEL_FRONTEND_DIR,
    PANEL_URL as PANEL_URL,
    _async_ensure_lovelace_resource as _async_ensure_lovelace_resource,
    async_apply_panel_registration as async_apply_panel_registration,
    async_load_panel_settings as async_load_panel_settings,
    async_save_panel_settings as async_save_panel_settings,
)
from .graph_horizon import GraphHorizonManager
from .leaf_repairs import async_clear_leaf_name_mismatch, async_raise_leaf_name_mismatch
from .migrations import CURRENT_CONFIG_VERSION, async_migrate_entry  # noqa: F401
from .notices import async_forget, async_restore
from .options import SNAPSHOT_UPDATE_INTERVAL

# Re-exported: the types themselves live in a leaf module (see `runtime.py`), but
# `custom_components.span_panel.SpanPanelRuntimeData` is the name the tests and
# the rest of the integration have always used, so the package root keeps
# answering to it.
from .runtime import (
    SpanPanelConfigEntry as SpanPanelConfigEntry,
    SpanPanelRuntimeData as SpanPanelRuntimeData,
    loaded_runtime_data,
)
from .schema_repairs import (
    async_clear_retired_new_entity_notices,
    async_clear_retired_upgrade_notice,
    async_clear_schema_issues,
)
from .services import (  # noqa: F401
    _async_register_credential_services,
    _async_register_favorites_services,
    _async_register_graph_horizon_services,
    _async_register_monitoring_services,
    _async_register_services,
    _build_clear_circuit_threshold_schema,
    _build_clear_mains_threshold_schema,
    _build_set_circuit_threshold_schema,
    _build_set_global_monitoring_schema,
    _build_set_mains_threshold_schema,
)
from .util import snapshot_to_device_info
from .websocket import async_register_commands

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    # Added with the EVSE charge-current control -- the first number this
    # integration has ever had, and forwarded unconditionally like every other
    # platform: `number.async_setup_entry` creates nothing on a panel with no
    # charger that declares a settable limit.
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Span Panel integration (domain-level, called once)."""
    _async_register_services(hass)
    _async_register_monitoring_services(hass)
    _async_register_graph_horizon_services(hass)
    _async_register_favorites_services(hass)
    _async_register_credential_services(hass)

    await async_apply_panel_registration(hass)

    return True


async def _async_pinned_ca(
    hass: HomeAssistant, entry: SpanPanelConfigEntry, host: str, http_port: int
) -> str | None:
    """Return the CA to anchor this entry's broker connection on.

    An entry provisioned since CA pinning landed already carries one. An entry
    that predates it carries `panel_ca_pending` instead, put there by the v7
    migration, and this is where that is settled — at setup, where the panel is
    about to be reachable anyway, rather than in the migration, which runs during
    startup and would delay boot for an unreachable panel.

    A failed fetch is not a setup failure. The flag survives, the next setup
    retries for free, and in the meantime the library falls back to its 3.0.1
    behaviour of refetching the CA on each connect. Refusing to start would trade
    a partial control for no integration at all.

    The store is logged at WARNING with the fingerprint on purpose. This is
    trust-on-first-use on an upgrade path — nobody confirmed this certificate
    against anything — and the user should be able to find the value afterwards
    to compare it against another install or the panel's own label.

    Trust on first use is not the same as trust in anything that answers. The
    fetch is plaintext and unauthenticated, so what comes back is checked
    against the certificate the panel's broker serves before it is stored; a CA
    that signs nothing the panel serves is not the panel's. A refusal is treated
    exactly as a failed fetch, because it is the same situation from the entry's
    point of view: no anchor was acquired, the flag stays, and the next setup
    tries again.
    """
    pinned = entry.data.get(CONF_PANEL_CA_PEM)
    if pinned:
        return str(pinned)

    if not entry.data.get(PANEL_CA_PENDING):
        return None

    try:
        ca_pem = await async_fetch_panel_ca(hass, host, http_port=http_port)
        fingerprint = ca_fingerprint(ca_pem)
    except (
        SpanPanelAPIError,
        SpanPanelConnectionError,
        SpanPanelTimeoutError,
        SpanPanelValidationError,
    ) as err:
        _LOGGER.warning(
            "Could not acquire the CA for SPAN panel %s (%s). Continuing unpinned; "
            "the fetch is retried on the next setup",
            entry.title,
            err,
        )
        return None

    # The broker's port, not the panel's HTTPS port. The anchor has two uses,
    # and this checks it against one of them: the MQTTS session the coordinator
    # opens below, and -- once it is stored -- the REST transport that
    # `panel_rest_transport` builds on the HTTPS port for reauth detection,
    # reconfigure, FQDN registration and `rotate_credentials`. The broker's is
    # the connection this pin was introduced for, and the only one every v2
    # entry carries a port for: setup refuses an entry without
    # `CONF_EBUS_BROKER_PORT` a few lines above, and the same value is handed to
    # `MqttClientConfig` below.
    #
    # `https_port` was the wrong port to verify on for the entries that actually
    # reach here. It arrived in the same commit as `panel_ca_pending`, so an
    # entry carrying the flag is by definition one migrated from before either
    # existed and can never hold a port: every such check ran against 443, and a
    # pre-pinning install whose TLS lives elsewhere -- behind NAT, a port
    # forward, a proxy -- would have been refused on every setup and left on the
    # unauthenticated refetch path for good, which is the one population this
    # deferred pin exists for.
    #
    # Verifying one port and using the anchor on two has a cost, and it is
    # stated rather than hidden: a CA that signs the broker's leaf but not
    # whatever answers on 443 -- a proxy terminating only the HTTPS port --
    # pins here and then fails every REST path. Since the runtime schema fetch
    # moved onto the pin (issue #264), that failure is no longer quiet: setup
    # retries under `ConfigEntryNotReady` with the `panel_rest_tls_untrusted`
    # Repair naming the port when something answers it badly, and with the
    # retry message naming the port and Reconfigure when nothing answers it at
    # all -- Reconfigure offers the HTTPS port to a pinned entry for exactly
    # this population. Never plaintext either way. README and the changelog
    # say so.
    #
    # One asymmetry to know about: `async_ca_signs_panel_leaf` accepts a leaf
    # reached at the address `host` resolves to, while the bridge dials `host`
    # itself. A certificate that names only the address would pin here and then
    # fail hostname verification at connect. Pre-existing -- the config flow
    # bootstraps the same way -- and not made worse by checking this port.
    mqtts_port = as_port(entry.data.get(CONF_EBUS_BROKER_PORT), DEFAULT_MQTTS_PORT)
    if not await async_ca_signs_panel_leaf(hass, host, mqtts_port, ca_pem):
        _LOGGER.warning(
            "The CA published by SPAN panel %s (SHA-256 %s) does not sign the "
            "certificate served by its broker at %s:%s, so it is not this panel's CA "
            "and has not been pinned. Continuing unpinned and retrying on the next "
            "setup; check that nothing on the network is answering for the panel",
            entry.title,
            fingerprint,
            host,
            mqtts_port,
        )
        return None

    data = dict(entry.data)
    data[CONF_PANEL_CA_PEM] = ca_pem
    data.pop(PANEL_CA_PENDING, None)
    hass.config_entries.async_update_entry(entry, data=data)
    _LOGGER.warning(
        "Pinned the CA advertised by SPAN panel %s: SHA-256 %s. Nothing has confirmed "
        "this certificate — compare it against another install or the panel itself if "
        "you can, and see the integration's security documentation",
        entry.title,
        fingerprint,
    )
    return ca_pem


async def _async_rest_tls_verdict(
    hass: HomeAssistant,
    entry: SpanPanelConfigEntry,
    host: str,
    http_port: int,
    https_port: int,
    pinned_pem: str,
    err: SpanPanelTLSVerificationError,
) -> ConfigEntryError | ConfigEntryNotReady:
    """Say what a REST certificate-verification failure was, and how setup ends.

    The same question the library asks when the MQTT handshake fails under a
    pin, answered the same way, because the failure itself carries no evidence:
    re-read the CA the panel advertises — plaintext deliberately, since a fetch
    verified against the old pin would fail and tell us nothing — and compare
    fingerprints. The fetch is diagnostic only and never re-anchors anything.

    A different fingerprint is the CA-changed Repair, with its guided re-pin —
    a firmware reset rotating the CA is the common legitimate cause of this
    failure, and it already has a flow. It is also the only terminal outcome,
    which mirrors the library's own `_diagnose_verification_failure`: a
    matching fingerprint never escalates, because `SSLCertVerificationError`
    under an unchanged CA is three different conditions and two of them are
    transient. The leaf probe splits them. A leaf that names somewhere else is
    a moved panel — the leaf-mismatch Repair, which promises retrying, and
    setup keeps that promise. A leaf the pin does not validate at all is either
    something terminating TLS in front of the panel or a certificate a clock
    reset pushed outside its validity window — one Repair naming both, retried
    so the clock case heals itself and the proxy case stands visibly refused,
    never downgraded to plaintext. And missing evidence — the CA unreadable,
    the TLS port unreachable — retries with no verdict at all, because a panel
    mid-reboot must not be convicted of anything.
    """
    expected = ca_fingerprint(pinned_pem)
    try:
        advertised = await async_fetch_panel_ca(hass, host, http_port=http_port)
        observed = ca_fingerprint(advertised)
    except (
        SpanPanelAPIError,
        SpanPanelConnectionError,
        SpanPanelTimeoutError,
        SpanPanelValidationError,
    ) as fetch_err:
        _LOGGER.warning(
            "TLS verification failed for SPAN panel %s's REST API and the panel's CA "
            "could not be re-read to say why (%s). Treating this as transient and retrying",
            entry.title,
            fetch_err,
        )
        return ConfigEntryNotReady(f"SPAN panel REST TLS failure is undiagnosed yet: {err}")
    if observed != expected:
        async_raise_ca_changed(hass, entry, expected, observed)
        return ConfigEntryError(
            f"SPAN panel {entry.title} is advertising CA {observed} where {expected} was pinned"
        )
    probe = await async_leaf_probe(host, https_port, pinned_pem)
    if probe.verdict is LeafVerdict.NAME_MISMATCH:
        # The other half of the mutual supersede: `async_raise_rest_tls_untrusted`
        # clears the leaf repair itself, but `leaf_repairs` cannot import
        # `ca_repairs` back without a cycle, so this direction lives with the
        # one caller that can produce both verdicts. A panel cannot be both
        # merely moved and untrusted; only the current verdict may stand.
        async_clear_rest_tls_untrusted(hass, entry)
        async_raise_leaf_name_mismatch(
            hass, entry, LeafNameMismatch(host=host, leaf_names=probe.leaf_names)
        )
        return ConfigEntryNotReady(
            f"SPAN panel {entry.title}'s certificate does not name {host}; "
            "see the Repair, and retrying meanwhile"
        )
    if probe.verdict is LeafVerdict.UNTRUSTED:
        async_raise_rest_tls_untrusted(hass, entry, host, https_port, expected)
        return ConfigEntryNotReady(
            f"{host}:{https_port} answered with a certificate SPAN panel {entry.title}'s "
            "pinned CA does not currently validate; see the Repair, and retrying meanwhile"
        )
    return ConfigEntryNotReady(
        f"SPAN panel {entry.title}'s REST TLS failure did not reproduce under diagnosis "
        f"({probe.verdict}); retrying: {err}"
    )


async def async_setup_entry(hass: HomeAssistant, entry: SpanPanelConfigEntry) -> bool:
    """Set up Span Panel from a config entry."""
    _LOGGER.debug("Setting up entry %s (version %s)", entry.entry_id, entry.version)

    # Register WebSocket commands once per HA instance
    domain_data: dict[str, bool] = hass.data.setdefault(DOMAIN, {})
    if not domain_data.get("websocket_registered"):
        domain_data["websocket_registered"] = True
        async_register_commands(hass)

    config = entry.data
    api_version = config.get(CONF_API_VERSION, "v1")

    # v1 entries: trigger reauthentication so user can provide v2 credentials
    if api_version == "v1":
        raise ConfigEntryAuthFailed(
            "This panel requires reauthentication. "
            "Please reauthenticate with your panel passphrase or proximity."
        )

    # Before the coordinator, because the coordinator can raise a notice as soon
    # as it starts streaming -- a panel that upgraded its firmware while Home
    # Assistant was down announces itself on the first snapshot. Restoring after
    # that would overwrite the record with a view that never saw it.
    await async_restore(hass, entry)

    coordinator: SpanPanelCoordinator | None = None

    try:
        # --- v2 MQTT entries ---
        if api_version == "v2":
            required_keys = (
                CONF_EBUS_BROKER_HOST,
                CONF_EBUS_BROKER_USERNAME,
                CONF_EBUS_BROKER_PASSWORD,
                CONF_EBUS_BROKER_PORT,
            )
            missing = [k for k in required_keys if not config.get(k)]
            if missing:
                raise ConfigEntryAuthFailed(  # noqa: TRY301
                    f"v2 panel is missing MQTT credentials ({', '.join(missing)}). "
                    "Please reauthenticate to provide a passphrase."
                )

            host = config[CONF_HOST]
            serial_number = entry.unique_id
            if not serial_number:
                raise ConfigEntryNotReady(  # noqa: TRY301
                    "Config entry has no unique_id (serial number)"
                )

            # The MQTT broker runs on the panel itself. The panel advertises
            # its own mDNS hostname (.local) as ebusBrokerHost, but mDNS
            # does not resolve across VLAN boundaries. Use the user-configured
            # panel host (IP or FQDN) which is known reachable.
            advertised_broker = config[CONF_EBUS_BROKER_HOST]
            if advertised_broker != host:
                _LOGGER.debug(
                    "Panel advertised broker host '%s' differs from configured "
                    "host '%s'; using configured host for MQTT connection",
                    advertised_broker,
                    host,
                )

            # `as_port` rather than `int()`: `entry.data` round-trips through
            # JSON and is typed `Any` at this boundary, so a hand-edited
            # `.storage` can put anything here, and `int("http")` would raise
            # out of setup where the pin site a few lines above quietly falls
            # back to the documented default. Same treatment, same answer.
            panel_http_port = as_port(config.get(CONF_HTTP_PORT), 80)

            # Before the client is built, because the pin is a constructor
            # argument: supplying it is what stops the bridge fetching a CA over
            # plaintext HTTP on every connect and trusting whatever answers.
            ca_pem = await _async_pinned_ca(hass, entry, host, panel_http_port)

            # From `entry.data` rather than `config`, because the deferred pin
            # above may have just written the anchor there. Fail closed on a
            # stored PEM that cannot be read: the fallback the other callers get
            # would put this entry's schema fetch — the call that runs
            # unattended on every boot — back on plaintext, which is the
            # downgrade the pin exists to prevent (issue #264). The transport's
            # own client is not used here; see the constructor comment below.
            try:
                transport = panel_rest_transport(
                    hass, entry.data, allow_plaintext_fallback=False
                )
            except PanelCaUnusableError as err:
                async_raise_ca_unusable(hass, entry, str(err))
                raise ConfigEntryError(  # noqa: TRY301
                    f"The stored CA for SPAN panel {entry.title} cannot be read; "
                    "re-acquire it in Settings > Repairs"
                ) from err

            broker_config = MqttClientConfig(
                broker_host=host,
                username=config[CONF_EBUS_BROKER_USERNAME],
                password=config[CONF_EBUS_BROKER_PASSWORD],
                mqtts_port=as_port(config[CONF_EBUS_BROKER_PORT], DEFAULT_MQTTS_PORT),
                ca_pem=ca_pem,
            )

            snapshot_interval = entry.options.get(
                SNAPSHOT_UPDATE_INTERVAL, DEFAULT_SNAPSHOT_INTERVAL
            )
            client = SpanMqttClient(
                host,
                serial_number,
                broker_config,
                snapshot_interval=snapshot_interval,
                # Both ports, because the client runs two transports with
                # opposite security properties. The plaintext port carries the
                # bridge's CA fetches, which never follow the pin — they read
                # the very anchor everything else is checked against. The
                # schema fetch rides the pinned transport on the HTTPS port
                # whenever the entry holds an anchor, which is every entry the
                # config flow has created since pinning landed; passing the
                # HTTPS port without a context is refused by the library, hence
                # the pairing (issue #264).
                panel_http_port=panel_http_port,
                panel_https_port=transport.port if transport.ssl_context is not None else None,
                ssl_context=transport.ssl_context,
                # Home Assistant's shared client, which it owns and closes at
                # shutdown, and which serves only the unpinned plaintext path --
                # under a context the library builds a dedicated client per
                # call, because httpx fixes its trust store at construction.
                # `transport.httpx_client` is deliberately not used: it is the
                # config flow's `verify_ssl=False` variant, and asking for that
                # here would stand up a second connection pool for a flag that
                # is inert on plaintext. The default client is the one every
                # other integration already shares.
                httpx_client=get_async_client(hass),
            )

            # Before `connect()`, unlike every other subscription here, because
            # the library runs the diagnosis this reports *inside* the first
            # connect. Registered afterwards it would see nothing on the path it
            # exists for: a mismatch makes `connect()` raise
            # `SpanPanelConnectionError`, setup raises `ConfigEntryNotReady`, and
            # the retry builds a new client -- so every attempt would fire the
            # signal into a client with no subscriber and the user would keep
            # getting the log line and nothing else.
            @callback
            def _on_leaf_name_mismatch(mismatch: LeafNameMismatch) -> None:
                async_raise_leaf_name_mismatch(hass, entry, mismatch)

            entry.async_on_unload(client.register_leaf_mismatch_callback(_on_leaf_name_mismatch))

            try:
                await client.connect()
            except SpanPanelCAChangedError as err:
                # Terminal on purpose, and `ConfigEntryError` rather than
                # `ConfigEntryNotReady` for the same reason the library refuses
                # to retry: a client that keeps trying is a client waiting to
                # succeed against whatever is answering, which is the outcome
                # pinning exists to prevent. The Repair is the only way forward,
                # and it requires a person.
                await client.close()
                async_raise_ca_changed(
                    hass, entry, err.expected_fingerprint, err.observed_fingerprint
                )
                raise ConfigEntryError(str(err)) from err
            except SpanPanelAuthError as err:
                await client.close()
                raise ConfigEntryAuthFailed(f"MQTT authentication failed: {err}") from err
            except SpanPanelValidationError as err:
                # The library refusing a stored combination it will not guess
                # about — an HTTPS port of 80 under a pin is the one that
                # reaches here, written by a config flow that used to accept
                # it. A stored value does not fix itself, so no retry; the
                # message names the remedy.
                await client.close()
                raise ConfigEntryError(  # noqa: TRY301
                    f"SPAN panel {entry.title}'s stored HTTPS port is not usable under "
                    f"its pinned CA ({err}); correct it with Reconfigure"
                ) from err
            except SpanPanelTLSVerificationError as err:
                # Before its parent `SpanPanelConnectionError`, which would
                # retry it with no diagnosis. Something answered the REST HTTPS
                # port with a certificate the pin rejects; which Repair that
                # raises — and whether it is terminal — is decided by the
                # diagnosis below, the only party holding the evidence.
                await client.close()
                pinned_pem = transport.ca_pem
                if pinned_pem is None:
                    # Not reachable: the library raises this only under an
                    # ssl_context, and the transport carries a PEM exactly when
                    # it carries a context. Stated as a guard rather than
                    # laundered through `str()`, so if the pairing is ever
                    # broken the failure names itself instead of fingerprinting
                    # the string "None".
                    raise
                raise await _async_rest_tls_verdict(
                    hass,
                    entry,
                    host,
                    panel_http_port,
                    transport.port,
                    pinned_pem,
                    err,
                ) from err
            except (
                SpanPanelConnectionError,
                SpanPanelTimeoutError,
                # A panel part-way through a reboot answers rather than refusing:
                # 5xx from the front end while the application behind it starts,
                # or a 200 with nothing usable in it. That is not a broken
                # install, it is a panel that is not up yet, and the two arrive
                # together more often than they look -- one power event takes out
                # the house's panel and the Home Assistant host that watches it,
                # and they race each other back. Uncaught it produced a dead entry
                # needing a human, for a condition that clears itself in minutes.
                SpanPanelServerError,
            ) as err:
                await client.close()
                message = f"SPAN panel is not ready yet: {err}"
                if transport.ssl_context is not None:
                    # For the population the deferred pin serves — TLS behind
                    # NAT, a port forward, a proxy — nothing may answer the
                    # default HTTPS port at all, and that failure is a plain
                    # refused connection indistinguishable from a reboot. The
                    # retry message is the one channel that reaches them, so it
                    # carries the remedy instead of blaming the panel.
                    message += (
                        f" (its REST API is expected on HTTPS port {transport.port}; if the "
                        "panel's TLS is served elsewhere, set the HTTPS port via Reconfigure)"
                    )
                raise ConfigEntryNotReady(message) from err

            # The connection got as far as a handshake under the current pin, so
            # any standing Repair describes a state that no longer holds. That
            # covers the name mismatch too: the handshake that just succeeded is
            # the one whose failure the mismatch explains, and it is the same
            # event the library re-arms its own signal on.
            async_clear_ca_changed(hass, entry)
            async_clear_leaf_name_mismatch(hass, entry)
            # Same reconciliation: the REST half of the handshake succeeded
            # under the current pin too — the schema fetch inside connect() is
            # what these two describe failing.
            async_clear_rest_tls_untrusted(hass, entry)
            async_clear_ca_unusable(hass, entry)

            # The other half of the same condition: the reconnect loop runs
            # fire-and-forget, so a CA that changes mid-session cannot surface as
            # an exception on anybody's call stack. The library gives it a
            # channel of its own precisely so a consumer can act on it.
            @callback
            def _on_fatal_transport_error(error: SpanPanelError) -> None:
                if isinstance(error, SpanPanelCAChangedError):
                    async_raise_ca_changed(
                        hass, entry, error.expected_fingerprint, error.observed_fingerprint
                    )

            entry.async_on_unload(client.register_fatal_error_callback(_on_fatal_transport_error))

            # One interceptor, installed before the coordinator starts streaming
            # so no control command can reach the panel ungated — including one
            # issued during the first refresh.
            control_policy = ControlPolicy.from_options(entry.options)
            # Armed for as long as the feature is on, from the moment the
            # interceptor has a lock to consult rather than from the moment the
            # switch is added — the first refresh and the whole platform
            # forward happen in between. `SpanPanelControlLockSwitch` reopens it
            # if that is what the previous run left behind.
            control_lock = ControlLock(armed=control_policy.lock_enabled)
            client.set_control_interceptor(ControlGate(hass, entry, control_policy, control_lock))
            entry.async_on_unload(lambda: client.set_control_interceptor(None))

            coordinator = SpanPanelCoordinator(hass, client, entry)
            await coordinator.async_config_entry_first_refresh()
            await coordinator.async_setup_streaming()

            if entry.options.get(
                ENABLE_CURRENT_MONITORING, False
            ) or await CurrentMonitor.async_is_enabled(hass, entry):
                monitor = CurrentMonitor(hass, entry)
                await monitor.async_start()
                coordinator.current_monitor = monitor

            graph_horizon = GraphHorizonManager(hass, entry)
            await graph_horizon.async_load()
            coordinator.graph_horizon_manager = graph_horizon

        else:
            raise ConfigEntryError(  # noqa: TRY301
                f"Unknown api_version: {api_version}"
            )

        # --- Common setup for all transport modes ---

        entry.async_on_unload(entry.add_update_listener(update_listener))

        snapshot: SpanPanelSnapshot = coordinator.data
        serial_number = snapshot.serial_number

        base_name = "SPAN Panel"

        # Check existing config entries to avoid conflicts
        existing_entries = hass.config_entries.async_entries(DOMAIN)
        existing_titles = {
            e.title
            for e in existing_entries
            if e.title and e.title != serial_number and e.entry_id != entry.entry_id
        }

        smart_device_name = base_name
        counter = 2
        while smart_device_name in existing_titles:
            smart_device_name = f"{base_name} {counter}"
            counter += 1

        # Update config entry title if it's currently the serial number
        if entry.title == serial_number:
            hass.config_entries.async_update_entry(entry, title=smart_device_name)

        # Populated here rather than earlier because the panel's registry id is
        # part of it, and that only exists once the device is registered. Nothing
        # between the coordinator's first refresh and this point reads
        # runtime_data, and platforms — which all do — are forwarded below.
        entry.runtime_data = SpanPanelRuntimeData(
            coordinator=coordinator,
            control_policy=control_policy,
            control_lock=control_lock,
            panel_device_id=await ensure_device_registered(
                hass, entry, snapshot, smart_device_name
            ),
        )

        # Before the forward, because a sub-device's `via_device_id` has to name a
        # device that already exists -- and because an adopted device whose whole
        # declaration is an `info` node has no entity to be created by.
        async_register_adopted_devices(
            hass, entry.entry_id, snapshot, panel_device_id=entry.runtime_data.panel_device_id
        )

        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

        # After the forward, because it reports on what the platforms just built
        # -- and once, rather than from each platform's own call to `adoptable`.
        await async_notice_declined_extensions(
            hass, entry, snapshot, dr.async_get(hass), er.async_get(hass)
        )

        # After the platforms, not before: schema validation runs on the first
        # refresh, which is awaited above, but the Repairs it raises name the
        # entities an unresolved field took down — and those entities only
        # register themselves once their platform has added them.
        coordinator.async_sync_schema_repairs()

        # Also after the platforms, for the other half of the same reason: a
        # newly added entity is only in the registry once its platform has added
        # it. Nobody watches their entity count, so an addition that breaks
        # nothing reaches the user through this and nothing else -- whether or
        # not it arrived switched on.
        async_clear_retired_new_entity_notices(hass, entry)
        async_clear_retired_upgrade_notice(hass, entry)
        await async_announce_new_entities(hass, entry)
    except Exception:
        if coordinator is not None:
            await coordinator.async_shutdown()
        raise
    else:
        return True


async def async_unload_entry(hass: HomeAssistant, entry: SpanPanelConfigEntry) -> bool:
    """Unload a config entry.

    Unload the platforms first; only tear the coordinator down if that
    succeeded. If a platform raises during unload, HA retries with the
    coordinator still alive — shutting it down first would leave
    entities pointing at a closed MQTT client.
    """
    _LOGGER.debug("Unloading SPAN Panel integration")

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False

    runtime_data = loaded_runtime_data(entry)
    if runtime_data is not None:
        if runtime_data.coordinator.current_monitor is not None:
            runtime_data.coordinator.current_monitor.async_stop()
        await runtime_data.coordinator.async_shutdown()

    return True


async def async_remove_entry(hass: HomeAssistant, entry: SpanPanelConfigEntry) -> None:
    """Clean up what this entry left outside its own runtime data.

    Core deletes neither issues nor their dismissals when a config entry is
    removed, so a panel taken out of the system would otherwise leave its schema
    notices behind forever. Scoped to this entry: another panel's issues share the
    domain and must survive.

    The announcement record goes with them, and for a sharper reason: it outliving
    the entry would mean re-adding the same panel announces none of the entities
    it recreates, because every one of them is already recorded as announced.
    """
    async_clear_schema_issues(hass, entry)
    # The CA family too, for the same reason — and the two persistent ones most
    # of all, since nothing else can ever clear an issue whose entry is gone:
    # the fixable flow aborts with `entry_gone`, and the rest have no flow.
    async_clear_ca_changed(hass, entry)
    async_clear_ca_unusable(hass, entry)
    async_clear_rest_tls_untrusted(hass, entry)
    async_clear_leaf_name_mismatch(hass, entry)
    await async_forget_announcements(hass, entry)
    await async_forget(hass, entry)


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    config_entry: SpanPanelConfigEntry,
    device_entry: dr.DeviceEntry,
) -> bool:
    """Allow manual removal of a device (e.g., stale EVSE sub-device).

    The main panel device cannot be removed — only sub-devices (like EVSE
    chargers) that are no longer present can be removed by the user.
    """
    runtime_data = loaded_runtime_data(config_entry)
    if runtime_data is None:
        return True

    coordinator = runtime_data.coordinator
    snapshot = coordinator.data

    # Identify the main panel device identifier
    panel_identifier = snapshot.serial_number

    # Prevent removal of the main panel device
    for identifier in device_entry.identifiers:
        if identifier == (DOMAIN, panel_identifier):
            return False

    return True


async def update_listener(hass: HomeAssistant, entry: SpanPanelConfigEntry) -> None:
    """Handle options updates."""
    _LOGGER.debug("Configuration options changed for entry: %s", entry.entry_id)

    try:
        if hass.state is not CoreState.running:
            return

        await hass.config_entries.async_reload(entry.entry_id)
        _LOGGER.debug("Successfully reloaded SPAN Panel integration")

    except asyncio.CancelledError:
        raise
    except Exception as err:  # noqa: BLE001
        _LOGGER.error("Failed to reload SPAN Panel integration: %s", err)


async def ensure_device_registered(
    hass: HomeAssistant,
    entry: SpanPanelConfigEntry,
    snapshot: SpanPanelSnapshot,
    device_name: str,
) -> str:
    """Register or reconcile the HA Device before creating sensors.

    Ensures the device exists in the device registry with proper naming and
    identifiers, and returns its registry id — the value every sub-device links
    to. Returned rather than looked up again by each platform because this is
    the one place the device is known to exist: either it already did, or this
    call is what created it.
    """
    device_registry = dr.async_get(hass)

    serial_number = snapshot.serial_number
    host = entry.data.get(CONF_HOST)

    # Scoped to this config entry rather than searching every one. Identifiers
    # are unique only within an entry, so the unscoped lookup is ambiguous by
    # construction — it could answer with another integration's device that
    # happens to share the identifier — which is why it is deprecated and stops
    # working in 2027.8.
    existing_device = device_registry.async_get_device_by_identifier(
        (DOMAIN, serial_number), entry.entry_id
    )

    if existing_device is not None:
        if existing_device.name == serial_number:
            device_registry.async_update_device(existing_device.id, name=device_name)
        return existing_device.id

    device_info = snapshot_to_device_info(snapshot, device_name, host=host)
    created = device_registry.async_get_or_create(config_entry_id=entry.entry_id, **device_info)
    return created.id
