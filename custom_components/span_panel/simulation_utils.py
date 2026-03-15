"""Simulator clone utilities for SPAN Panel integration.

Discovers simulators on the local network via mDNS and delegates panel
cloning to the simulator over its WebSocket endpoint.  The simulator
handles eBus scraping, translation, and config writing — the integration
only provides the target panel's address and passphrase.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging
import ssl

import aiohttp
from homeassistant.components import zeroconf as ha_zeroconf
from homeassistant.core import HomeAssistant
from zeroconf import ServiceStateChange, Zeroconf
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo

_LOGGER = logging.getLogger(__name__)

EBUS_SERVICE_TYPE = "_ebus._tcp.local."
CLONE_WSS_PORT_PROPERTY = "cloneWssPort"
DISCOVERY_TIMEOUT_SECONDS = 3.0
CLONE_OPERATION_TIMEOUT_SECONDS = 120


@dataclass
class SimulatorInfo:
    """A simulator discovered via mDNS that supports panel cloning."""

    host: str
    clone_wss_port: int
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


async def discover_clone_simulators(hass: HomeAssistant) -> list[SimulatorInfo]:
    """Browse for simulators advertising a clone WSS port via mDNS.

    Looks for ``_ebus._tcp.local.`` services whose TXT record contains
    ``cloneWssPort``.  Discovery runs for a short window and returns
    all matching services found.
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

        port_str = props.get(CLONE_WSS_PORT_PROPERTY) or props.get(CLONE_WSS_PORT_PROPERTY.lower())
        if not port_str:
            continue

        addresses = info.parsed_scoped_addresses()
        host = addresses[0] if addresses else (info.server or "")
        display_name = name.replace(f".{EBUS_SERVICE_TYPE}", "")

        simulators.append(
            SimulatorInfo(
                host=host.rstrip("."),
                clone_wss_port=int(port_str),
                name=display_name,
            )
        )

    return simulators


async def execute_clone_via_simulator(
    simulator_host: str,
    simulator_port: int,
    panel_host: str,
    panel_passphrase: str | None,
) -> CloneResult:
    """Open a WSS connection to the simulator and run a panel clone.

    The simulator connects to the real panel's eBus, scrapes retained
    messages, translates them into a simulation YAML config, and writes
    the file.  This function streams status updates to the log and
    returns the final result.
    """
    url = f"wss://{simulator_host}:{simulator_port}/ws/clone"

    # The simulator uses a self-signed certificate; trust it for
    # local-network communication.
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE

    try:
        async with asyncio.timeout(CLONE_OPERATION_TIMEOUT_SECONDS):
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(url, ssl=ssl_context) as ws:
                    await ws.send_json(
                        {
                            "type": "clone_panel",
                            "host": panel_host,
                            "passphrase": panel_passphrase,
                        }
                    )

                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            data: dict[str, str | int] = json.loads(msg.data)
                            msg_type = str(data.get("type", ""))

                            if msg_type == "status":
                                _LOGGER.debug(
                                    "Clone progress: %s — %s",
                                    data.get("phase", ""),
                                    data.get("detail", ""),
                                )
                            elif msg_type == "result":
                                if data.get("status") == "ok":
                                    return CloneResult(
                                        success=True,
                                        serial=str(data.get("serial", "")),
                                        clone_serial=str(data.get("clone_serial", "")),
                                        filename=str(data.get("filename", "")),
                                        circuits=int(data.get("circuits", 0)),
                                    )
                                return CloneResult(
                                    success=False,
                                    error_message=str(data.get("message", "Unknown error")),
                                    error_phase=str(data.get("phase", "")),
                                )

                        elif msg.type in (
                            aiohttp.WSMsgType.ERROR,
                            aiohttp.WSMsgType.CLOSED,
                        ):
                            return CloneResult(
                                success=False,
                                error_message="WebSocket connection closed unexpectedly",
                            )

    except TimeoutError:
        return CloneResult(
            success=False,
            error_message="Clone operation timed out",
        )
    except aiohttp.ClientError as err:
        return CloneResult(
            success=False,
            error_message=f"Cannot connect to simulator: {err}",
        )

    return CloneResult(
        success=False,
        error_message="Clone completed without receiving a result",
    )
