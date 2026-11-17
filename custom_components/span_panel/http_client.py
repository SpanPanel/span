"""Home Assistant's shared HTTP client, as the `httpx` client `span_panel_api` takes.

From 2026.11 Home Assistant's client is an `httpx2` one, and importing Home
Assistant aliases `httpx` to `httpx2` for the whole process. At run time the
client is therefore an instance of the very class `span_panel_api`, written
against `httpx`, names. A type checker reads the two names as separate packages,
whose `AsyncClient` classes could not share a subclass, so `get_async_client`'s
`httpx2.AsyncClient` does not satisfy a parameter annotated `httpx.AsyncClient`.

This is the one place the two meet. The client is checked at run time to be the
class the library names, which holds exactly while Home Assistant keeps the
alias, and the checker is told that type. What the checker cannot see is the
rest: it reads the library's use of the client against the `httpx` 0.28.1
installed beside it, while the object is `httpx2`'s, and only this suite, which
runs under the alias, covers that. Should the alias ever go, the check fails
here, naming why, rather than inside the library.
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

    A `TypeGuard`, and neither a bare `isinstance` nor `TypeIs`. A type checker
    reads both of those as narrowing `httpx2.AsyncClient` to a subclass of both
    classes, decides that subclass cannot exist, and treats what follows as
    unreachable, so it stops checking it. A `TypeGuard` replaces the type instead.
    """
    return isinstance(client, httpx.AsyncClient)
