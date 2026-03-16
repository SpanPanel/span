"""Simulator utilities for SPAN Panel integration.

Discovers simulators on the local network via mDNS and delegates panel
cloning to the simulator over Socket.IO.  The simulator handles eBus
scraping, translation, and config writing -- the integration provides
the target panel's address, passphrase, and HA's location so the clone
is configured with the correct timezone and seasonal parameters.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
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
PROFILE_OPERATION_TIMEOUT_SECONDS = 30
SIO_NAMESPACE = "/v1/panel"


@dataclass
class SimulatorInfo:
    """A simulator discovered via mDNS."""

    host: str
    http_port: int
    name: str


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


@dataclass
class ProfileResult:
    """Outcome of a usage-profile delivery operation."""

    success: bool
    circuits_updated: int = 0
    error_message: str = ""


async def _sio_call(
    host: str,
    port: int,
    event: str,
    data: dict[str, object],
    timeout_seconds: int,
) -> dict[str, object]:
    """Connect to the simulator's Socket.IO namespace, emit an event, and return the response.

    Raises TimeoutError if the operation exceeds ``timeout_seconds``.
    Raises ValueError if the response is not a dict.
    """
    url = f"http://{host}:{port}"
    client: socketio.AsyncSimpleClient = socketio.AsyncSimpleClient()

    try:
        async with asyncio.timeout(timeout_seconds):
            await client.connect(url, namespace=SIO_NAMESPACE, wait_timeout=10)
            result = await client.call(event, data)

        if not isinstance(result, dict):
            raise TypeError("Unexpected response from simulator")

        return result
    finally:
        if client.connected:
            await client.disconnect()


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


async def execute_clone_via_simulator(
    simulator_host: str,
    simulator_http_port: int,
    panel_host: str,
    panel_passphrase: str | None,
    latitude: float,
    longitude: float,
) -> CloneResult:
    """Clone a panel via the simulator's Socket.IO endpoint.

    Connects to the simulator's ``/v1/panel`` namespace and emits a
    ``clone_panel`` event that triggers the scrape-translate-write
    pipeline.  HA's location is included so the clone config gets the
    correct timezone and seasonal parameters.
    """
    try:
        result = await _sio_call(
            host=simulator_host,
            port=simulator_http_port,
            event="clone_panel",
            data={
                "host": panel_host,
                "passphrase": panel_passphrase,
                "latitude": latitude,
                "longitude": longitude,
            },
            timeout_seconds=CLONE_OPERATION_TIMEOUT_SECONDS,
        )

        if result.get("status") == "ok":
            return CloneResult(
                success=True,
                serial=str(result.get("serial", "")),
                clone_serial=str(result.get("clone_serial", "")),
                filename=str(result.get("filename", "")),
                circuits=int(str(result.get("circuits", 0))),
            )

        return CloneResult(
            success=False,
            error_message=str(result.get("message", "Unknown error")),
            error_phase=str(result.get("phase", "")),
        )

    except TimeoutError:
        return CloneResult(
            success=False,
            error_message="Clone operation timed out",
        )
    except TypeError as err:
        return CloneResult(
            success=False,
            error_message=str(err),
        )
    except Exception as err:
        return CloneResult(
            success=False,
            error_message=f"Cannot connect to simulator: {err}",
        )


async def send_usage_profiles(
    simulator_host: str,
    simulator_http_port: int,
    clone_serial: str,
    profiles: dict[str, dict[str, object]],
) -> ProfileResult:
    """Push usage profiles to the simulator via Socket.IO.

    Connects to the simulator's ``/v1/panel`` namespace and emits an
    ``apply_usage_profiles`` event with the clone serial and profile data.
    """
    try:
        result = await _sio_call(
            host=simulator_host,
            port=simulator_http_port,
            event="apply_usage_profiles",
            data={
                "clone_serial": clone_serial,
                "profiles": profiles,
            },
            timeout_seconds=PROFILE_OPERATION_TIMEOUT_SECONDS,
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
            error_message="Profile delivery timed out",
        )
    except TypeError as err:
        return ProfileResult(
            success=False,
            error_message=str(err),
        )
    except Exception as err:
        return ProfileResult(
            success=False,
            error_message=f"Cannot connect to simulator: {err}",
        )
