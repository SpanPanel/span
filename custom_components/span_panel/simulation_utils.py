"""Simulator utilities for SPAN Panel integration.

Discovers simulators on the local network via mDNS and delegates panel
cloning to the simulator over Socket.IO.  The simulator handles eBus
scraping, translation, and config writing -- the integration provides
the target panel's address, passphrase, and HA's location so the clone
is configured with the correct timezone and seasonal parameters.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import logging

from homeassistant.components import zeroconf as ha_zeroconf
from homeassistant.core import HomeAssistant
import socketio
from zeroconf import ServiceStateChange, Zeroconf
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo

_LOGGER = logging.getLogger(__name__)

EBUS_SERVICE_TYPE = "_ebus._tcp.local."
HTTP_PORT_PROPERTY = "httpPort"
DISCOVERY_TIMEOUT_SECONDS = 3.0
CLONE_OPERATION_TIMEOUT_SECONDS = 120
PROFILE_READY_TIMEOUT_SECONDS = 30
SIO_NAMESPACE = "/v1/panel"


@dataclass
class SimulatorInfo:
    """A simulator discovered via mDNS."""

    host: str
    http_port: int
    name: str


@dataclass
class ProfileResult:
    """Outcome of a usage-profile delivery operation."""

    success: bool
    circuits_updated: int = 0
    error_message: str = ""


@dataclass
class CloneResult:
    """Outcome of a clone-via-simulator operation."""

    success: bool
    serial: str = ""
    clone_serial: str = ""
    filename: str = ""
    circuits: int = 0
    error_message: str = ""
    error_phase: str = ""
    profile_result: ProfileResult | None = field(default=None, repr=False)


async def discover_clone_simulators(hass: HomeAssistant) -> list[SimulatorInfo]:
    """Browse for simulators via mDNS.

    Looks for ``_ebus._tcp.local.`` services whose TXT record contains
    ``httpPort`` (simulators advertise this; real panels do not).
    Discovery runs for a short window and returns all matching services.
    """
    aiozc = await ha_zeroconf.async_get_async_instance(hass)
    zc = aiozc.zeroconf

    discovered_names: list[str] = []

    def _on_state_change(
        zeroconf: Zeroconf,  # noqa: ARG001
        service_type: str,  # noqa: ARG001
        name: str,
        state_change: ServiceStateChange,
    ) -> None:
        if state_change == ServiceStateChange.Added:
            discovered_names.append(name)

    browser = AsyncServiceBrowser(zc, EBUS_SERVICE_TYPE, handlers=[_on_state_change])
    try:
        await asyncio.sleep(DISCOVERY_TIMEOUT_SECONDS)
    finally:
        await browser.async_cancel()

    simulators: list[SimulatorInfo] = []

    for name in discovered_names:
        info = AsyncServiceInfo(EBUS_SERVICE_TYPE, name)
        await info.async_request(zc, 3000)

        if not info.properties:
            continue

        props: dict[str, str] = {}
        for raw_key, raw_val in info.properties.items():
            key = raw_key.decode() if isinstance(raw_key, bytes) else str(raw_key)
            val = raw_val.decode() if isinstance(raw_val, bytes) else str(raw_val)
            props[key] = val

        http_port_str = props.get(HTTP_PORT_PROPERTY) or props.get(HTTP_PORT_PROPERTY.lower())
        if not http_port_str:
            continue

        addresses = info.parsed_scoped_addresses()
        host = addresses[0] if addresses else (info.server or "")
        display_name = name.replace(f".{EBUS_SERVICE_TYPE}", "")

        simulators.append(
            SimulatorInfo(
                host=host.rstrip("."),
                http_port=int(http_port_str),
                name=display_name,
            )
        )

    return simulators


async def clone_with_profiles(
    simulator_host: str,
    simulator_http_port: int,
    panel_host: str,
    panel_passphrase: str | None,
    latitude: float,
    longitude: float,
    profiles: dict[str, dict[str, object]] | None = None,
) -> CloneResult:
    """Clone a panel via Socket.IO, optionally applying usage profiles.

    Runs the entire operation on a single Socket.IO connection:

    1. Emit ``clone_panel`` and wait for the response.
    2. If the clone succeeded and *profiles* were supplied, wait for the
       simulator to emit ``clone_ready`` before sending
       ``apply_usage_profiles``.

    Keeping the connection open avoids a race where the clone config has
    not yet been registered when profiles are delivered.
    """
    url = f"http://{simulator_host}:{simulator_http_port}"
    client: socketio.AsyncSimpleClient = socketio.AsyncSimpleClient()

    try:
        async with asyncio.timeout(CLONE_OPERATION_TIMEOUT_SECONDS):
            await client.connect(url, namespace=SIO_NAMESPACE, wait_timeout=10)

            result = await client.call(
                "clone_panel",
                {
                    "host": panel_host,
                    "passphrase": panel_passphrase,
                    "latitude": latitude,
                    "longitude": longitude,
                },
            )

        if not isinstance(result, dict):
            return CloneResult(
                success=False,
                error_message="Unexpected response from simulator",
            )

        if result.get("status") != "ok":
            return CloneResult(
                success=False,
                error_message=str(result.get("message", "Unknown error")),
                error_phase=str(result.get("phase", "")),
            )

        clone_result = CloneResult(
            success=True,
            serial=str(result.get("serial", "")),
            clone_serial=str(result.get("clone_serial", "")),
            filename=str(result.get("filename", "")),
            circuits=int(str(result.get("circuits", 0))),
        )

        if profiles and clone_result.clone_serial:
            clone_result.profile_result = await _wait_ready_and_send_profiles(
                client, clone_result.clone_serial, profiles
            )

        return clone_result

    except TimeoutError:
        return CloneResult(
            success=False,
            error_message="Clone operation timed out",
        )
    except Exception as err:
        return CloneResult(
            success=False,
            error_message=f"Cannot connect to simulator: {err}",
        )
    finally:
        if client.connected:
            await client.disconnect()


async def _wait_ready_and_send_profiles(
    client: socketio.AsyncSimpleClient,
    clone_serial: str,
    profiles: dict[str, dict[str, object]],
) -> ProfileResult:
    """Wait for ``clone_ready`` then emit ``apply_usage_profiles`` on the same connection."""
    try:
        async with asyncio.timeout(PROFILE_READY_TIMEOUT_SECONDS):
            # The simulator emits clone_ready when the clone config is registered
            while True:
                event = await client.receive()
                if event[0] == "clone_ready":
                    break

            result = await client.call(
                "apply_usage_profiles",
                {
                    "clone_serial": clone_serial,
                    "profiles": profiles,
                },
            )

        if not isinstance(result, dict):
            return ProfileResult(
                success=False,
                error_message="Unexpected response from simulator",
            )

        if result.get("status") == "ok":
            return ProfileResult(
                success=True,
                circuits_updated=int(str(result.get("templates_updated", 0))),
            )

        return ProfileResult(
            success=False,
            error_message=str(result.get("message", "Unknown error")),
        )

    except TimeoutError:
        return ProfileResult(
            success=False,
            error_message="Timed out waiting for simulator clone_ready",
        )
    except Exception as err:
        return ProfileResult(
            success=False,
            error_message=f"Profile delivery failed: {err}",
        )
