"""gRPC client for Gen3 Span panels (MAIN 40 / MLO 48).

This module provides local gRPC communication with Gen3 Span smart electrical
panels. Gen3 panels replaced the REST API with gRPC on port 50065. No
authentication is required for local connections.

The client uses manual protobuf encoding/decoding to avoid requiring generated
stubs, keeping the dependency footprint minimal (only grpcio is needed).
"""

from __future__ import annotations

import asyncio
import logging
import struct
from collections.abc import Callable
from dataclasses import dataclass, field

import grpc

from .const import (
    BREAKER_OFF_VOLTAGE_MV,
    DEFAULT_GRPC_PORT,
    MAIN_FEED_IID,
    METRIC_IID_OFFSET,
    PRODUCT_GEN3_PANEL,
    TRAIT_CIRCUIT_NAMES,
    TRAIT_POWER_METRICS,
    VENDOR_SPAN,
)

_LOGGER = logging.getLogger(__name__)

# gRPC method paths
_SVC = "/io.span.panel.protocols.traithandler.TraitHandlerService"
_GET_INSTANCES = f"{_SVC}/GetInstances"
_SUBSCRIBE = f"{_SVC}/Subscribe"
_GET_REVISION = f"{_SVC}/GetRevision"


@dataclass
class CircuitInfo:
    """Information about a circuit discovered from trait instances."""

    circuit_id: int
    name: str
    metric_iid: int
    name_iid: int = 0
    is_dual_phase: bool = False


@dataclass
class CircuitMetrics:
    """Real-time metrics for a circuit from the gRPC stream."""

    power_w: float = 0.0
    voltage_v: float = 0.0
    current_a: float = 0.0
    apparent_power_va: float = 0.0
    reactive_power_var: float = 0.0
    frequency_hz: float = 0.0
    power_factor: float = 0.0
    is_on: bool = True
    # Dual-phase legs
    voltage_a_v: float = 0.0
    voltage_b_v: float = 0.0
    current_a_a: float = 0.0
    current_b_a: float = 0.0


@dataclass
class PanelData:
    """Aggregated panel data from gRPC discovery and streaming."""

    serial: str = ""
    firmware: str = ""
    panel_resource_id: str = ""
    circuits: dict[int, CircuitInfo] = field(default_factory=dict)
    metrics: dict[int, CircuitMetrics] = field(default_factory=dict)
    main_feed: CircuitMetrics = field(default_factory=CircuitMetrics)
    # Reverse lookup: metric IID → circuit_id (built during discovery)
    metric_iid_to_circuit: dict[int, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Protobuf helpers — manual varint/field parsing
# ---------------------------------------------------------------------------


def _decode_varint(data: bytes, offset: int) -> tuple[int, int]:
    """Decode a protobuf varint, return (value, new_offset)."""
    result = 0
    shift = 0
    while offset < len(data):
        b = data[offset]
        offset += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            break
        shift += 7
    return result, offset


def _parse_protobuf_fields(data: bytes) -> dict[int, list]:
    """Parse raw protobuf bytes into a dict of field_number -> [values]."""
    fields: dict[int, list] = {}
    offset = 0
    while offset < len(data):
        tag, offset = _decode_varint(data, offset)
        field_num = tag >> 3
        wire_type = tag & 0x07

        if wire_type == 0:  # varint
            value, offset = _decode_varint(data, offset)
        elif wire_type == 1:  # 64-bit
            if offset + 8 > len(data):
                break
            value = struct.unpack_from("<Q", data, offset)[0]
            offset += 8
        elif wire_type == 2:  # length-delimited
            length, offset = _decode_varint(data, offset)
            if offset + length > len(data):
                break
            value = data[offset : offset + length]
            offset += length
        elif wire_type == 5:  # 32-bit
            if offset + 4 > len(data):
                break
            value = struct.unpack_from("<I", data, offset)[0]
            offset += 4
        else:
            break

        fields.setdefault(field_num, []).append(value)
    return fields


def _get_field(fields: dict, num: int, default=None):
    """Get first value for a field number."""
    vals = fields.get(num)
    return vals[0] if vals else default


def _parse_min_max_avg(data: bytes) -> dict[str, float]:
    """Parse a min/max/avg sub-message (fields 1/2/3)."""
    fields = _parse_protobuf_fields(data)
    return {
        "min": _get_field(fields, 1, 0),
        "max": _get_field(fields, 2, 0),
        "avg": _get_field(fields, 3, 0),
    }


# ---------------------------------------------------------------------------
# Metric decoders — single-phase, dual-phase, and main feed
# ---------------------------------------------------------------------------


def _decode_single_phase(data: bytes) -> CircuitMetrics:
    """Decode single-phase (120V) metrics from protobuf field 11."""
    fields = _parse_protobuf_fields(data)
    metrics = CircuitMetrics()

    current_data = _get_field(fields, 1)
    if current_data and isinstance(current_data, bytes):
        current = _parse_min_max_avg(current_data)
        metrics.current_a = current["avg"] / 1000.0

    voltage_data = _get_field(fields, 2)
    if voltage_data and isinstance(voltage_data, bytes):
        voltage = _parse_min_max_avg(voltage_data)
        metrics.voltage_v = voltage["avg"] / 1000.0

    power_data = _get_field(fields, 3)
    if power_data and isinstance(power_data, bytes):
        power = _parse_min_max_avg(power_data)
        metrics.power_w = power["avg"] / 2000.0

    apparent_data = _get_field(fields, 4)
    if apparent_data and isinstance(apparent_data, bytes):
        apparent = _parse_min_max_avg(apparent_data)
        metrics.apparent_power_va = apparent["avg"] / 2000.0

    reactive_data = _get_field(fields, 5)
    if reactive_data and isinstance(reactive_data, bytes):
        reactive = _parse_min_max_avg(reactive_data)
        metrics.reactive_power_var = reactive["avg"] / 2000.0

    metrics.is_on = (metrics.voltage_v * 1000) > BREAKER_OFF_VOLTAGE_MV
    return metrics


def _decode_dual_phase(data: bytes) -> CircuitMetrics:
    """Decode dual-phase (240V) metrics from protobuf field 12."""
    fields = _parse_protobuf_fields(data)
    metrics = CircuitMetrics()

    # Leg A (field 1)
    leg_a_data = _get_field(fields, 1)
    if leg_a_data and isinstance(leg_a_data, bytes):
        leg_a = _parse_protobuf_fields(leg_a_data)
        current_data = _get_field(leg_a, 1)
        if current_data and isinstance(current_data, bytes):
            metrics.current_a_a = _parse_min_max_avg(current_data)["avg"] / 1000.0
        voltage_data = _get_field(leg_a, 2)
        if voltage_data and isinstance(voltage_data, bytes):
            metrics.voltage_a_v = _parse_min_max_avg(voltage_data)["avg"] / 1000.0

    # Leg B (field 2)
    leg_b_data = _get_field(fields, 2)
    if leg_b_data and isinstance(leg_b_data, bytes):
        leg_b = _parse_protobuf_fields(leg_b_data)
        current_data = _get_field(leg_b, 1)
        if current_data and isinstance(current_data, bytes):
            metrics.current_b_a = _parse_min_max_avg(current_data)["avg"] / 1000.0
        voltage_data = _get_field(leg_b, 2)
        if voltage_data and isinstance(voltage_data, bytes):
            metrics.voltage_b_v = _parse_min_max_avg(voltage_data)["avg"] / 1000.0

    # Combined (field 3)
    combined_data = _get_field(fields, 3)
    if combined_data and isinstance(combined_data, bytes):
        combined = _parse_protobuf_fields(combined_data)
        voltage_data = _get_field(combined, 2)
        if voltage_data and isinstance(voltage_data, bytes):
            metrics.voltage_v = _parse_min_max_avg(voltage_data)["avg"] / 1000.0
        power_data = _get_field(combined, 3)
        if power_data and isinstance(power_data, bytes):
            metrics.power_w = _parse_min_max_avg(power_data)["avg"] / 2000.0
        apparent_data = _get_field(combined, 4)
        if apparent_data and isinstance(apparent_data, bytes):
            metrics.apparent_power_va = _parse_min_max_avg(apparent_data)["avg"] / 2000.0
        reactive_data = _get_field(combined, 5)
        if reactive_data and isinstance(reactive_data, bytes):
            metrics.reactive_power_var = _parse_min_max_avg(reactive_data)["avg"] / 2000.0
        pf_data = _get_field(combined, 6)
        if pf_data and isinstance(pf_data, bytes):
            pf = _parse_min_max_avg(pf_data)
            metrics.power_factor = pf["avg"] / 2000.0

    # Frequency (field 4)
    freq_data = _get_field(fields, 4)
    if freq_data and isinstance(freq_data, bytes):
        freq = _parse_min_max_avg(freq_data)
        metrics.frequency_hz = freq["avg"] / 1000.0

    # Total current = leg A + leg B
    metrics.current_a = metrics.current_a_a + metrics.current_b_a

    metrics.is_on = (metrics.voltage_v * 1000) > BREAKER_OFF_VOLTAGE_MV
    return metrics


def _extract_deepest_value(data: bytes, target_field: int = 3) -> int:
    """Extract the deepest varint from nested protobuf.

    Recursively searches for the largest non-zero value at the target field
    within nested sub-messages.
    """
    fields = _parse_protobuf_fields(data)
    best = 0

    for fn, vals in fields.items():
        for v in vals:
            if isinstance(v, bytes) and len(v) > 0:
                inner = _extract_deepest_value(v, target_field)
                if inner > best:
                    best = inner
            elif not isinstance(v, bytes) and fn == target_field:
                if v > best:
                    best = v
    return best


def _decode_main_feed(data: bytes) -> CircuitMetrics:
    """Decode main feed metrics from protobuf field 14.

    Field 14 has deeper nesting than circuit fields 11/12. The structure:
      14.1 = primary data block (leg A)
      14.2 = secondary data block (leg B)
      Each leg: {1: current stats, 2: voltage stats, 3: power stats, 4: frequency}
    """
    fields = _parse_protobuf_fields(data)
    main_data = _get_field(fields, 14)
    if not main_data or not isinstance(main_data, bytes):
        return CircuitMetrics()

    metrics = CircuitMetrics()
    main_fields = _parse_protobuf_fields(main_data)

    # Extract from primary data block (field 1 = leg A)
    leg_a = _get_field(main_fields, 1)
    if leg_a and isinstance(leg_a, bytes):
        la_fields = _parse_protobuf_fields(leg_a)

        power_stats = _get_field(la_fields, 3)
        if power_stats and isinstance(power_stats, bytes):
            metrics.power_w = _extract_deepest_value(power_stats) / 2000.0

        voltage_stats = _get_field(la_fields, 2)
        if voltage_stats and isinstance(voltage_stats, bytes):
            vs_fields = _parse_protobuf_fields(voltage_stats)
            f2 = _get_field(vs_fields, 2)
            if f2 and isinstance(f2, bytes):
                inner = _parse_protobuf_fields(f2)
                v = _get_field(inner, 3, 0)
                if isinstance(v, int) and v > 0:
                    metrics.voltage_a_v = v / 1000.0

        freq_stats = _get_field(la_fields, 4)
        if freq_stats and isinstance(freq_stats, bytes):
            freq_fields = _parse_protobuf_fields(freq_stats)
            freq_val = _get_field(freq_fields, 3, 0)
            if isinstance(freq_val, int) and freq_val > 0:
                metrics.frequency_hz = freq_val / 1000.0

    # Leg B data (field 2)
    leg_b = _get_field(main_fields, 2)
    if leg_b and isinstance(leg_b, bytes):
        lb_fields = _parse_protobuf_fields(leg_b)
        power_stats = _get_field(lb_fields, 3)
        if power_stats and isinstance(power_stats, bytes):
            lb_power = _extract_deepest_value(power_stats) / 2000.0
            if lb_power > 0:
                metrics.power_w += lb_power
        voltage_stats = _get_field(lb_fields, 2)
        if voltage_stats and isinstance(voltage_stats, bytes):
            vs_fields = _parse_protobuf_fields(voltage_stats)
            f2 = _get_field(vs_fields, 2)
            if f2 and isinstance(f2, bytes):
                inner = _parse_protobuf_fields(f2)
                v = _get_field(inner, 3, 0)
                if isinstance(v, int) and v > 0:
                    metrics.voltage_b_v = v / 1000.0

    # Combined voltage (split-phase: leg A + leg B, or 2x leg A)
    if metrics.voltage_b_v > 0:
        metrics.voltage_v = metrics.voltage_a_v + metrics.voltage_b_v
    else:
        metrics.voltage_v = metrics.voltage_a_v * 2  # Assume symmetric

    # Derive current from power and voltage
    if metrics.voltage_v > 0:
        metrics.current_a = metrics.power_w / metrics.voltage_v

    metrics.is_on = True
    return metrics


# ---------------------------------------------------------------------------
# Protobuf encoding helpers
# ---------------------------------------------------------------------------


def _encode_varint(value: int) -> bytes:
    """Encode an integer as a protobuf varint."""
    parts = []
    while value > 0x7F:
        parts.append((value & 0x7F) | 0x80)
        value >>= 7
    parts.append(value & 0x7F)
    return bytes(parts) if parts else b"\x00"


def _encode_varint_field(field_num: int, value: int) -> bytes:
    """Encode a varint field (tag + value)."""
    tag = (field_num << 3) | 0  # wire type 0 = varint
    return _encode_varint(tag) + _encode_varint(value)


def _encode_bytes_field(field_num: int, value: bytes) -> bytes:
    """Encode a length-delimited field (tag + length + value)."""
    tag = (field_num << 3) | 2  # wire type 2 = length-delimited
    return _encode_varint(tag) + _encode_varint(len(value)) + value


def _encode_string_field(field_num: int, value: str) -> bytes:
    """Encode a string field (tag + length + utf-8 bytes)."""
    return _encode_bytes_field(field_num, value.encode("utf-8"))


# ---------------------------------------------------------------------------
# gRPC Client
# ---------------------------------------------------------------------------


class SpanGrpcClient:
    """gRPC client for Gen3 Span panels.

    Connects to the panel's TraitHandlerService on port 50065 (no auth).
    Discovers circuits via GetInstances, fetches names via GetRevision,
    and streams real-time power metrics via Subscribe.
    """

    def __init__(self, host: str, port: int = DEFAULT_GRPC_PORT) -> None:
        """Initialize the client."""
        self._host = host
        self._port = port
        self._channel: grpc.aio.Channel | None = None
        self._stream_task: asyncio.Task | None = None
        self._data = PanelData()
        self._callbacks: list[Callable[[], None]] = []
        self._connected = False

    @property
    def data(self) -> PanelData:
        """Return current panel data."""
        return self._data

    @property
    def connected(self) -> bool:
        """Return connection status."""
        return self._connected

    def register_callback(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Register a callback for data updates. Returns unregister function."""
        self._callbacks.append(callback)
        return lambda: self._callbacks.remove(callback)

    def _notify(self) -> None:
        """Notify all registered callbacks."""
        for cb in self._callbacks:
            try:
                cb()
            except Exception:
                _LOGGER.exception("Error in callback")

    async def connect(self) -> bool:
        """Connect to the panel and fetch initial data."""
        try:
            self._channel = grpc.aio.insecure_channel(
                f"{self._host}:{self._port}",
                options=[
                    ("grpc.keepalive_time_ms", 30000),
                    ("grpc.keepalive_timeout_ms", 10000),
                    ("grpc.keepalive_permit_without_calls", True),
                ],
            )
            await self._fetch_instances()
            await self._fetch_circuit_names()
            self._connected = True
            _LOGGER.info(
                "Connected to Gen3 panel at %s:%s — %d circuits discovered",
                self._host,
                self._port,
                len(self._data.circuits),
            )
            return True
        except Exception:
            _LOGGER.exception(
                "Failed to connect to Gen3 panel at %s:%s", self._host, self._port
            )
            self._connected = False
            return False

    async def disconnect(self) -> None:
        """Disconnect from the panel."""
        self._connected = False
        if self._stream_task and not self._stream_task.done():
            self._stream_task.cancel()
            try:
                await self._stream_task
            except asyncio.CancelledError:
                pass
        if self._channel:
            await self._channel.close()
            self._channel = None

    async def start_streaming(self) -> None:
        """Start the metric streaming task."""
        if self._stream_task and not self._stream_task.done():
            return
        self._stream_task = asyncio.create_task(self._stream_loop())

    async def stop_streaming(self) -> None:
        """Stop the metric streaming task."""
        if self._stream_task and not self._stream_task.done():
            self._stream_task.cancel()
            try:
                await self._stream_task
            except asyncio.CancelledError:
                pass

    async def test_connection(self) -> bool:
        """Test if we can connect to the panel (static method-like)."""
        try:
            channel = grpc.aio.insecure_channel(
                f"{self._host}:{self._port}",
                options=[("grpc.initial_reconnect_backoff_ms", 1000)],
            )
            try:
                response = await asyncio.wait_for(
                    channel.unary_unary(
                        _GET_INSTANCES,
                        request_serializer=lambda x: x,
                        response_deserializer=lambda x: x,
                    )(b""),
                    timeout=5.0,
                )
                return len(response) > 0
            finally:
                await channel.close()
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Instance discovery
    # ------------------------------------------------------------------

    async def _fetch_instances(self) -> None:
        """Fetch all trait instances to discover circuits."""
        response = await self._channel.unary_unary(
            _GET_INSTANCES,
            request_serializer=lambda x: x,
            response_deserializer=lambda x: x,
        )(b"")
        self._parse_instances(response)

    def _parse_instances(self, data: bytes) -> None:
        """Parse GetInstancesResponse to discover circuits and panel info.

        Collects both trait 16 (name) and trait 26 (metric) instance IDs,
        then pairs them by sorted position to build the circuit mapping.
        This avoids hardcoding a fixed offset between the two trait IID
        spaces, which can vary across panel models/configurations.
        """
        fields = _parse_protobuf_fields(data)
        items = fields.get(1, [])

        # Collect instance IDs for both traits before building circuits
        name_iids: list[int] = []      # Trait 16 instance IDs
        metric_iids: list[int] = []    # Trait 26 instance IDs (excl main feed)

        for item_data in items:
            if not isinstance(item_data, bytes):
                continue
            item_fields = _parse_protobuf_fields(item_data)

            trait_info_data = _get_field(item_fields, 1)
            if not trait_info_data or not isinstance(trait_info_data, bytes):
                continue

            trait_info_fields = _parse_protobuf_fields(trait_info_data)

            external_data = _get_field(trait_info_fields, 2)
            if not external_data or not isinstance(external_data, bytes):
                continue

            ext_fields = _parse_protobuf_fields(external_data)

            # resource_id (field 1)
            resource_data = _get_field(ext_fields, 1)
            resource_id_str = ""
            if resource_data and isinstance(resource_data, bytes):
                rid_fields = _parse_protobuf_fields(resource_data)
                rid_val = _get_field(rid_fields, 1)
                if rid_val and isinstance(rid_val, bytes):
                    resource_id_str = rid_val.decode("utf-8", errors="replace")

            # trait_info (field 2)
            inner_info = _get_field(ext_fields, 2)
            if not inner_info or not isinstance(inner_info, bytes):
                continue

            inner_fields = _parse_protobuf_fields(inner_info)

            meta_data = _get_field(inner_fields, 1)
            if not meta_data or not isinstance(meta_data, bytes):
                continue

            meta_fields = _parse_protobuf_fields(meta_data)
            vendor_id = _get_field(meta_fields, 1, 0)
            product_id = _get_field(meta_fields, 2, 0)
            trait_id = _get_field(meta_fields, 3, 0)

            instance_data = _get_field(inner_fields, 2)
            instance_id = 0
            if instance_data and isinstance(instance_data, bytes):
                iid_fields = _parse_protobuf_fields(instance_data)
                instance_id = _get_field(iid_fields, 1, 0)

            # Capture panel resource_id
            if (
                product_id == PRODUCT_GEN3_PANEL
                and resource_id_str
                and not self._data.panel_resource_id
            ):
                self._data.panel_resource_id = resource_id_str

            # Collect trait 16 (circuit names) instance IDs
            if trait_id == TRAIT_CIRCUIT_NAMES and vendor_id == VENDOR_SPAN:
                name_iids.append(instance_id)

            # Collect trait 26 (power metrics) instance IDs
            if trait_id == TRAIT_POWER_METRICS and vendor_id == VENDOR_SPAN:
                if instance_id != MAIN_FEED_IID:
                    metric_iids.append(instance_id)

        # Sort both sets to pair by position (lowest IID = first circuit)
        name_iids.sort()
        metric_iids.sort()

        _LOGGER.debug(
            "Discovered %d name instances (trait 16) and %d metric instances "
            "(trait 26, excl main feed). Name IIDs: %s, Metric IIDs: %s",
            len(name_iids),
            len(metric_iids),
            name_iids[:5],
            metric_iids[:5],
        )

        if name_iids and metric_iids:
            if len(name_iids) != len(metric_iids):
                _LOGGER.warning(
                    "Trait 16 has %d instances but trait 26 has %d — "
                    "pairing by position (some circuits may be unnamed)",
                    len(name_iids),
                    len(metric_iids),
                )

            # Pair by sorted position — each name IID corresponds to the
            # metric IID at the same index
            for idx, metric_iid in enumerate(metric_iids):
                circuit_id = idx + 1
                name_iid = name_iids[idx] if idx < len(name_iids) else 0
                self._data.circuits[circuit_id] = CircuitInfo(
                    circuit_id=circuit_id,
                    name=f"Circuit {circuit_id}",
                    metric_iid=metric_iid,
                    name_iid=name_iid,
                )
                self._data.metric_iid_to_circuit[metric_iid] = circuit_id

        elif metric_iids:
            # No trait 16 instances — fall back to offset-based mapping
            _LOGGER.warning(
                "No trait 16 (name) instances found — falling back to "
                "offset-based circuit mapping (offset=%d)",
                METRIC_IID_OFFSET,
            )
            for metric_iid in metric_iids:
                circuit_id = metric_iid - METRIC_IID_OFFSET
                if 1 <= circuit_id <= 50:
                    self._data.circuits[circuit_id] = CircuitInfo(
                        circuit_id=circuit_id,
                        name=f"Circuit {circuit_id}",
                        metric_iid=metric_iid,
                        name_iid=circuit_id,
                    )
                    self._data.metric_iid_to_circuit[metric_iid] = circuit_id

    # ------------------------------------------------------------------
    # Circuit names
    # ------------------------------------------------------------------

    async def _fetch_circuit_names(self) -> None:
        """Fetch circuit names from trait 16 via GetRevision.

        Uses each circuit's name_iid (trait 16 instance ID) rather than
        the circuit_id, since the two numbering spaces may differ.
        """
        for circuit_id, info in list(self._data.circuits.items()):
            name_iid = info.name_iid or circuit_id
            try:
                name = await self._get_circuit_name(name_iid)
                if name:
                    info.name = name
                    _LOGGER.debug(
                        "Circuit %d (name_iid=%d, metric_iid=%d): %s",
                        circuit_id,
                        name_iid,
                        info.metric_iid,
                        name,
                    )
            except Exception:
                _LOGGER.debug(
                    "Failed to get name for circuit %d (name_iid=%d)",
                    circuit_id,
                    name_iid,
                )

    async def _get_circuit_name(self, circuit_id: int) -> str | None:
        """Get a single circuit name via GetRevision on trait 16."""
        request = self._build_get_revision_request(
            vendor_id=VENDOR_SPAN,
            product_id=PRODUCT_GEN3_PANEL,
            trait_id=TRAIT_CIRCUIT_NAMES,
            instance_id=circuit_id,
        )

        try:
            response = await self._channel.unary_unary(
                _GET_REVISION,
                request_serializer=lambda x: x,
                response_deserializer=lambda x: x,
            )(request)

            return self._parse_circuit_name(response)
        except grpc.aio.AioRpcError:
            return None

    def _build_get_revision_request(
        self, vendor_id: int, product_id: int, trait_id: int, instance_id: int
    ) -> bytes:
        """Build a GetRevisionRequest protobuf message manually."""
        # TraitMetadata (field 1)
        meta = _encode_varint_field(1, vendor_id)
        meta += _encode_varint_field(2, product_id)
        meta += _encode_varint_field(3, trait_id)
        meta += _encode_varint_field(4, 1)  # version

        # ResourceId message
        resource_id_msg = _encode_string_field(1, self._data.panel_resource_id)

        # InstanceMetadata (field 2)
        iid_msg = _encode_varint_field(1, instance_id)
        instance_meta = _encode_bytes_field(1, resource_id_msg)
        instance_meta += _encode_bytes_field(2, iid_msg)

        # RevisionRequest (field 3)
        req_metadata = _encode_bytes_field(2, resource_id_msg)
        revision_request = _encode_bytes_field(1, req_metadata)

        result = _encode_bytes_field(1, meta)
        result += _encode_bytes_field(2, instance_meta)
        result += _encode_bytes_field(3, revision_request)
        return result

    @staticmethod
    def _parse_circuit_name(data: bytes) -> str | None:
        """Parse circuit name from GetRevision response."""
        fields = _parse_protobuf_fields(data)

        sr_data = _get_field(fields, 3)
        if not sr_data or not isinstance(sr_data, bytes):
            return None

        sr_fields = _parse_protobuf_fields(sr_data)
        payload_data = _get_field(sr_fields, 2)
        if not payload_data or not isinstance(payload_data, bytes):
            return None

        pl_fields = _parse_protobuf_fields(payload_data)
        raw = _get_field(pl_fields, 1)
        if not raw or not isinstance(raw, bytes):
            return None

        name_fields = _parse_protobuf_fields(raw)
        name = _get_field(name_fields, 4)
        if name and isinstance(name, bytes):
            return name.decode("utf-8", errors="replace").strip()
        return None

    # ------------------------------------------------------------------
    # Metric streaming
    # ------------------------------------------------------------------

    async def _stream_loop(self) -> None:
        """Main streaming loop with automatic reconnection."""
        while self._connected:
            try:
                await self._subscribe_stream()
            except asyncio.CancelledError:
                return
            except Exception:
                _LOGGER.exception("Stream error, reconnecting in 5s")
                await asyncio.sleep(5)

    async def _subscribe_stream(self) -> None:
        """Subscribe to the gRPC stream and process updates."""
        call = self._channel.unary_stream(
            _SUBSCRIBE,
            request_serializer=lambda x: x,
            response_deserializer=lambda x: x,
        )

        stream = call(b"")
        async for response in stream:
            try:
                self._process_notification(response)
            except Exception:
                _LOGGER.debug("Error processing notification", exc_info=True)

    def _process_notification(self, data: bytes) -> None:
        """Process a TraitInstanceNotification from the stream."""
        fields = _parse_protobuf_fields(data)

        rti_data = _get_field(fields, 1)
        if not rti_data or not isinstance(rti_data, bytes):
            return

        rti_fields = _parse_protobuf_fields(rti_data)
        ext_data = _get_field(rti_fields, 2)
        if not ext_data or not isinstance(ext_data, bytes):
            return

        ext_fields = _parse_protobuf_fields(ext_data)
        info_data = _get_field(ext_fields, 2)
        if not info_data or not isinstance(info_data, bytes):
            return

        info_fields = _parse_protobuf_fields(info_data)
        meta_data = _get_field(info_fields, 1)
        if not meta_data or not isinstance(meta_data, bytes):
            return

        meta_fields = _parse_protobuf_fields(meta_data)
        trait_id = _get_field(meta_fields, 3, 0)

        iid_data = _get_field(info_fields, 2)
        instance_id = 0
        if iid_data and isinstance(iid_data, bytes):
            iid_fields = _parse_protobuf_fields(iid_data)
            instance_id = _get_field(iid_fields, 1, 0)

        # Only process trait 26 (power metrics)
        if trait_id != TRAIT_POWER_METRICS:
            return

        notify_data = _get_field(fields, 2)
        if not notify_data or not isinstance(notify_data, bytes):
            return

        notify_fields = _parse_protobuf_fields(notify_data)

        metrics_list = notify_fields.get(3, [])
        for metric_data in metrics_list:
            if not isinstance(metric_data, bytes):
                continue

            ml_fields = _parse_protobuf_fields(metric_data)
            raw_metrics = ml_fields.get(3, [])

            for raw in raw_metrics:
                if not isinstance(raw, bytes):
                    continue
                self._decode_and_store_metric(instance_id, raw)

        self._notify()

    def _decode_and_store_metric(self, iid: int, raw: bytes) -> None:
        """Decode a raw metric payload and store it."""
        top_fields = _parse_protobuf_fields(raw)

        # Main feed (IID 1) uses field 14 with deeper nesting
        if iid == MAIN_FEED_IID:
            self._data.main_feed = _decode_main_feed(raw)
            return

        # Look up circuit_id from the discovered mapping
        circuit_id = self._data.metric_iid_to_circuit.get(iid)
        if circuit_id is None:
            return

        # Dual-phase (field 12) — check first since it's more specific
        dual_data = _get_field(top_fields, 12)
        if dual_data and isinstance(dual_data, bytes):
            self._data.metrics[circuit_id] = _decode_dual_phase(dual_data)
            if circuit_id in self._data.circuits:
                self._data.circuits[circuit_id].is_dual_phase = True
            return

        # Single-phase (field 11)
        single_data = _get_field(top_fields, 11)
        if single_data and isinstance(single_data, bytes):
            self._data.metrics[circuit_id] = _decode_single_phase(single_data)
            if circuit_id in self._data.circuits:
                self._data.circuits[circuit_id].is_dual_phase = False
