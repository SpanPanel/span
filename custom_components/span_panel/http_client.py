"""Home Assistant's shared HTTP client, as the `httpx` client `span_panel_api` takes.

From 2026.11 Home Assistant's client is an `httpx2` one, and importing Home
Assistant aliases `httpx` to `httpx2` for the whole process, so `span_panel_api`,
written against `httpx`, is handed the very class it names. A type checker reads
the two names as separate packages whose `AsyncClient` classes could not share a
subclass, so `get_async_client`'s `httpx2.AsyncClient` does not satisfy a
parameter annotated `httpx.AsyncClient`.

This is the one place the two meet. The client is checked at run time to be the
library's class, which holds exactly while Home Assistant keeps the alias, so the
type the checker is told is the type the object has. Should the alias ever go,
the check fails here, naming why, rather than inside the library.
"""

from __future__ import annotations

from typing import TypeGuard

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.httpx_client import get_async_client
import httpx


@callback
def async_get_http_client(hass: HomeAssistant, *, verify_ssl: bool = True) -> httpx.AsyncClient:
    """Return Home Assistant's shared client for the library, the very instance it hands out.

    `verify_ssl` is passed through: Home Assistant keeps one shared client for
    each setting.
    """
    client = get_async_client(hass, verify_ssl=verify_ssl)
    if not _is_library_client(client):
        raise TypeError(
            f"Home Assistant's shared HTTP client is a {type(client).__module__}."
            f"{type(client).__qualname__}, not the httpx.AsyncClient span_panel_api "
            "takes: Home Assistant no longer aliases httpx to httpx2"
        )
    return client


def _is_library_client(client: object) -> TypeGuard[httpx.AsyncClient]:
    """Return whether the client is an instance of the `httpx.AsyncClient` the library names.

    A type guard rather than a bare `isinstance`, which a type checker would
    read as narrowing `httpx2.AsyncClient` to a subclass of both that cannot
    exist, and then stop checking what follows.
    """
    return isinstance(client, httpx.AsyncClient)
