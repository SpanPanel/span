"""The library is handed Home Assistant's own shared client, checked to be the class it names.

`http_client.async_get_http_client` is where Home Assistant's `httpx2` client
becomes the `httpx.AsyncClient` that `span_panel_api` is typed against. That
holds because importing Home Assistant aliases `httpx` to `httpx2`; these pin the
instance handed over, the premise, and what happens when the premise fails.
"""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers.httpx_client import get_async_client
import httpx
import pytest

from custom_components.span_panel.http_client import async_get_http_client


async def test_the_library_gets_the_instance_home_assistant_hands_out(
    hass: HomeAssistant,
) -> None:
    """Not a client of its own: one per `verify_ssl` setting, as Home Assistant keeps them.

    Home Assistant's clients are held as `object`: typed, they are `httpx2`'s,
    which a type checker cannot see as the library's class, the very gap the
    bridge closes.
    """
    shared_verified: object = get_async_client(hass)
    shared_unverified: object = get_async_client(hass, verify_ssl=False)

    verified = async_get_http_client(hass)
    unverified = async_get_http_client(hass, verify_ssl=False)

    assert verified is shared_verified
    assert unverified is shared_unverified
    assert verified is not unverified


async def test_home_assistant_s_client_is_the_httpx_client_the_library_names(
    hass: HomeAssistant,
) -> None:
    """The premise: with the alias in place, the two names are one class."""
    assert isinstance(get_async_client(hass), httpx.AsyncClient)


async def test_a_client_that_is_not_httpx_s_is_refused_naming_why(hass: HomeAssistant) -> None:
    """Should Home Assistant stop aliasing, the failure is here rather than inside the library."""

    class OtherClient:
        """A client of some other library."""

    with (
        patch(
            "custom_components.span_panel.http_client.get_async_client",
            return_value=OtherClient(),
        ),
        pytest.raises(TypeError, match="no longer aliases httpx to httpx2"),
    ):
        async_get_http_client(hass)
