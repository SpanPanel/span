"""Verify each declared field_path against what its value_fn actually reads.

Runs every value_fn against a proxy that records attribute access. The
declaration stays authoritative — this only stops it drifting from the reader.
"""

from __future__ import annotations

from typing import Any

from custom_components.span_panel.sensor_definitions import all_sensor_descriptions

# Attributes of the panel snapshot that are themselves sub-snapshots. Their
# fields are addressed as "battery.x", not "panel.battery.x".
_SUB_SNAPSHOTS = {"battery", "pv", "evse", "mid"}


class _Recorder:
    """Records every attribute path touched, and survives arithmetic.

    value_fns do real work — `or "unknown"`, `a - b`, unary minus for PV sign
    flips — so the proxy has to absorb those without raising and without
    ending the recording.
    """

    def __init__(self, sink: set[str], prefix: str, root: bool = False) -> None:
        object.__setattr__(self, "_sink", sink)
        object.__setattr__(self, "_prefix", prefix)
        object.__setattr__(self, "_root", root)

    def __getattr__(self, name: str) -> _Recorder:
        if name.startswith("_"):
            raise AttributeError(name)
        sink: set[str] = object.__getattribute__(self, "_sink")
        prefix: str = object.__getattribute__(self, "_prefix")
        root: bool = object.__getattribute__(self, "_root")
        if root and name in _SUB_SNAPSHOTS:
            return _Recorder(sink, name)
        path = f"{prefix}.{name}" if prefix else name
        sink.add(path)
        return _Recorder(sink, path)

    # Absorb the operations value_fns perform on the values they read.
    def __bool__(self) -> bool:
        return True

    def __sub__(self, other: Any) -> _Recorder:
        return self

    def __rsub__(self, other: Any) -> _Recorder:
        return self

    def __neg__(self) -> _Recorder:
        return self

    def __or__(self, other: Any) -> _Recorder:
        return self

    def __call__(self, *args: Any, **kwargs: Any) -> _Recorder:
        """Absorb method calls, e.g. `(m.grid_state or "unknown").lower()`.

        The extra path this records (`mid.grid_state.lower`) is harmless: the
        check is membership of the declared path, not set equality.
        """
        return self

    def __eq__(self, other: Any) -> bool:
        return False

    def __hash__(self) -> int:
        return 0


# Every description class, and the snapshot type its value_fn receives.
# A class missing here is silently skipped by the `prefix is None` guard below —
# which is exactly the hole this test exists to close, so keep it complete.
_ROOT_PREFIX = {
    "SpanPanelCircuitsSensorEntityDescription": "circuit",
    "SpanPanelDataSensorEntityDescription": "panel",
    "SpanPanelStatusSensorEntityDescription": "panel",
    "SpanPanelBatterySensorEntityDescription": "battery",
    "SpanBessMetadataSensorEntityDescription": "battery",
    # PV metadata value_fns take the whole panel snapshot and reach through
    # `s.pv.x`, so the root prefix is "panel" and _SUB_SNAPSHOTS rewrites it.
    "SpanPVMetadataSensorEntityDescription": "panel",
    "SpanEvseSensorEntityDescription": "evse",
    "SpanMidSensorEntityDescription": "mid",
}


def test_declared_paths_match_what_value_fns_read() -> None:
    mismatches: list[str] = []

    for description in all_sensor_descriptions():
        if description.derived or description.field_path is None:
            continue
        prefix = _ROOT_PREFIX.get(type(description).__name__)
        if prefix is None:
            continue
        sink: set[str] = set()
        proxy = _Recorder(sink, prefix, root=(prefix == "panel"))
        try:
            description.value_fn(proxy)
        except Exception as err:  # noqa: BLE001
            mismatches.append(f"{description.key}: value_fn raised {err!r}")
            continue
        if description.field_path not in sink:
            mismatches.append(
                f"{description.key}: declares {description.field_path!r} but reads {sorted(sink)}"
            )

    assert not mismatches, "Declarations disagree with readers:\n" + "\n".join(mismatches)
