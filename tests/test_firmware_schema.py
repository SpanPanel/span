"""Read whether a panel publishes the flat eBus schema, and name the errors that mean it does.

Flat is a schema without a `data_model_version`. The read is fail-open: a schema
that cannot be read answers "unread", which is never flat, so a panel is never
refused on the strength of a failed request.
"""

from __future__ import annotations

import ssl
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from span_panel_api import V2HomieSchema
from span_panel_api.dispatch import select_adapter_key
from span_panel_api.exceptions import (
    SpanPanelAdapterIncompatibleError,
    SpanPanelAdapterMissingError,
    SpanPanelConnectionError,
    SpanPanelError,
)

from custom_components.span_panel.firmware_schema import (
    FLAT_ADAPTER_KEY,
    async_read_schema,
    is_flat_adapter_error,
    is_flat_schema,
)

_GET_HOMIE_SCHEMA = "custom_components.span_panel.firmware_schema.get_homie_schema"


def _schema(data_model_version: str | None) -> V2HomieSchema:
    return V2HomieSchema(
        firmware_version="spanos2/r202603/05",
        types_schema_hash="h",
        types={},
        data_model_version=data_model_version,
    )


def test_a_schema_without_a_data_model_version_is_flat() -> None:
    assert is_flat_schema(_schema(None)) is True


def test_a_schema_with_one_is_not() -> None:
    assert is_flat_schema(_schema("1.0")) is False


def test_the_flat_key_is_the_one_the_library_dispatches_absence_to() -> None:
    adapter_key, _reason = select_adapter_key(None)
    assert adapter_key == FLAT_ADAPTER_KEY


async def test_read_classifies_flat_and_parent_child() -> None:
    with patch(_GET_HOMIE_SCHEMA, AsyncMock(return_value=_schema(None))):
        assert await async_read_schema("192.0.2.10", port=80, httpx_client=None) == "flat"
    with patch(_GET_HOMIE_SCHEMA, AsyncMock(return_value=_schema("1.0"))):
        assert await async_read_schema("192.0.2.10", port=80, httpx_client=None) == "parent_child"


async def test_read_forwards_a_plaintext_transport() -> None:
    client = httpx.AsyncClient()
    read = AsyncMock(return_value=_schema("1.0"))
    try:
        with patch(_GET_HOMIE_SCHEMA, read):
            await async_read_schema("192.0.2.10", port=80, httpx_client=client)
    finally:
        await client.aclose()
    read.assert_awaited_once_with("192.0.2.10", port=80, httpx_client=client, ssl_context=None)


async def test_read_forwards_a_pinned_transport() -> None:
    context = ssl.create_default_context()
    read = AsyncMock(return_value=_schema("1.0"))
    with patch(_GET_HOMIE_SCHEMA, read):
        await async_read_schema("192.0.2.10", port=443, httpx_client=None, ssl_context=context)
    read.assert_awaited_once_with("192.0.2.10", port=443, httpx_client=None, ssl_context=context)


@pytest.mark.parametrize(
    "failure",
    [
        TimeoutError(),
        SpanPanelError("x"),
        httpx.ConnectError("x"),
        httpx.InvalidURL("x"),
    ],
)
async def test_read_is_unread_on_failure(failure: Exception) -> None:
    with patch(_GET_HOMIE_SCHEMA, AsyncMock(side_effect=failure)):
        assert await async_read_schema("192.0.2.10", port=80, httpx_client=None) == "unread"


@pytest.mark.parametrize(
    ("err", "flat"),
    [
        (SpanPanelAdapterMissingError(needed="schema_0", reason="r", available=[]), True),
        (SpanPanelAdapterIncompatibleError(needed="schema_0", reason="r", defect="d"), True),
        (SpanPanelAdapterMissingError(needed="schema_1", reason="r", available=[]), False),
        (SpanPanelAdapterIncompatibleError(needed="schema_1", reason="r", defect="d"), False),
        (SpanPanelConnectionError("x"), False),
    ],
)
def test_only_flat_adapter_errors_mean_flat(err: Exception, flat: bool) -> None:
    assert is_flat_adapter_error(err) is flat
