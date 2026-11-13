"""Whether a panel publishes the older flat eBus schema, which this integration refuses.

Flat is a Homie schema without a `data_model_version`, and nothing else is. The
property arrived with the parent/child model, so its absence is the flat signal.
It is the rule the library itself dispatches on in `dispatch.select_adapter_key`,
which hands absence to the flat adapter, `schema_0`, and a present value to the
major it names. `is_flat_schema` is the one place the integration states it, so
setup, the config flow and diagnostics cannot come to disagree about a panel.

`async_read_schema` answers one of three things: parent/child, flat, or unread.
Unread is never flat. A request that failed says nothing about the firmware, so
it must never be the reason a panel is refused.

When the read was unread, connecting is the backstop. The library refusing an
adapter for `schema_0`, as missing or as incompatible, means the panel needs the
flat parser, and so does a connect that reached a leftover `schema_0` package
anyway, which a caller tells by `FLAT_ADAPTER_KEY`. `is_flat_adapter_error`
names the refusals, and no adapter error for any other key is reinterpreted as
flat.
"""

from __future__ import annotations

import asyncio
import logging
import ssl
from typing import Final, Literal

import httpx
from span_panel_api import V2HomieSchema, get_homie_schema
from span_panel_api.exceptions import (
    SpanPanelAdapterIncompatibleError,
    SpanPanelAdapterMissingError,
    SpanPanelError,
)

from .hardware_guard import HARDWARE_READ_TIMEOUT_S

_LOGGER = logging.getLogger(__name__)

FLAT_ADAPTER_KEY: Final = "schema_0"
"""The adapter key `dispatch.select_adapter_key` answers for a panel that publishes no version."""

SCHEMA_READ_TIMEOUT_S: Final = HARDWARE_READ_TIMEOUT_S
"""How long a caller waits for the panel's schema before going on without it.

The hardware read's bound, imported rather than restated, so the two short REST
reads made before connecting cannot drift apart.
"""

type SchemaRead = Literal["parent_child", "flat", "unread"]
"""What the panel's schema says it publishes, or "unread" when it cannot be had."""


def is_flat_schema(schema: V2HomieSchema) -> bool:
    """Whether the schema is the flat one, which publishes no `data_model_version`.

    Absence alone decides, so a version that is present but cannot be parsed is
    still not flat: the library refuses such a value at connect rather than hand
    the panel to a parser it may not speak.
    """
    return schema.data_model_version is None


def is_flat_adapter_error(err: Exception) -> bool:
    """Whether a connect error says the panel needs the flat adapter.

    Missing means no installed package registers `schema_0`. Incompatible means
    a leftover one does but cannot be used: it is never upgraded, so it stops
    matching the first time the library's adapter contract moves. Both are a
    flat panel, and both must end in the same retrying refusal rather than a
    terminal setup error. Either error for any other key is not about flat.
    """
    return (
        isinstance(err, SpanPanelAdapterMissingError | SpanPanelAdapterIncompatibleError)
        and err.needed == FLAT_ADAPTER_KEY
    )


async def async_read_schema(
    host: str,
    *,
    port: int,
    httpx_client: httpx.AsyncClient | None,
    ssl_context: ssl.SSLContext | None,
) -> SchemaRead:
    """Read the panel's Homie schema and classify it, or answer "unread" when it cannot be had.

    Fail-open by design, as `hardware_guard.async_read_hardware_version` is: a
    timeout, a refused or reset connection, a certificate the pin rejects, an
    unusable URL and a body that cannot be read all answer "unread". Every caller
    proceeds on it as it would without the read, and its own path reports any of
    these failures in its own terms.

    The schema endpoint is unauthenticated. The arguments go to `get_homie_schema`
    unchanged, so a caller passes the transport its other REST calls to the panel
    use. A None `httpx_client` lets the library build a client of its own, which
    it always does under an `ssl_context`, because httpx fixes its trust store at
    construction.

    No argument has a default, so every caller states its whole transport. A
    pinned caller that left the context out would send plaintext to the HTTPS
    port, fail, and answer "unread". The check would then be skipped on every
    pinned entry, with nothing to show it.
    """
    try:
        async with asyncio.timeout(SCHEMA_READ_TIMEOUT_S):
            schema = await get_homie_schema(
                host, port=port, httpx_client=httpx_client, ssl_context=ssl_context
            )
    except (TimeoutError, SpanPanelError, httpx.HTTPError, httpx.InvalidURL) as err:
        _LOGGER.debug("Could not read the Homie schema of SPAN panel %s: %s", host, err)
        return "unread"
    return "flat" if is_flat_schema(schema) else "parent_child"
