"""Refuse to create entities on hardware this release has not been validated with.

The public r202639 changelog gives `hardwareVersion` as `1.2`, `2.0` or `UNKNOWN`,
and `MAIN_32` is a public model. The read is fail-open: a status that cannot be read
never locks out a panel the existing connect path accepts.
"""

from __future__ import annotations

import pytest

from custom_components.span_panel.hardware_guard import (
    HardwareVerdict,
    hardware_verdict,
    refuses_entities,
)

P, C, R = HardwareVerdict.PROCEED, HardwareVerdict.CHECK_MODEL, HardwareVerdict.REFUSE


@pytest.mark.parametrize(
    ("version", "verdict"),
    [
        ("1.2", P),
        ("2.0", P),
        (" 2.0 ", P),
        (None, P),
        ("", P),
        ("  ", P),
        ("UNKNOWN", C),
        ("unknown", C),
        ("9.9", R),
    ],
)
def test_the_verdict_from_the_rest_value_alone(version: str | None, verdict: HardwareVerdict) -> None:
    assert hardware_verdict(version) is verdict


@pytest.mark.parametrize(
    ("verdict", "model", "refused"),
    [
        (P, "ANY_MODEL", False),
        (C, "MAIN_32", False),
        (R, "MAIN_32", False),
        (C, None, False),
        (C, "", False),
        (R, None, True),
        (C, "OTHER_MODEL", True),
        (R, "OTHER_MODEL", True),
    ],
)
def test_the_model_rule(verdict: HardwareVerdict, model: str | None, refused: bool) -> None:
    assert refuses_entities(verdict, model) is refused
