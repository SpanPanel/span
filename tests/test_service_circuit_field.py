"""The circuit field of every per-circuit service says what its selector offers and what the service resolves.

Each takes one of the circuit's entities or its circuit id, resolved by
`entity_resolver.resolve_circuit_id`, and its selector offers the circuit's
current sensors, so the description names those and the id, never another sensor.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

_COMPONENT = Path(__file__).parent.parent / "custom_components" / "span_panel"
_LANGUAGES = ("es", "fr", "ja", "pt")
_SERVICES = (
    "set_circuit_threshold",
    "clear_circuit_threshold",
    "set_circuit_package_ratings",
    "clear_circuit_package_ratings",
)
_EXPECTED = "The circuit: its current sensor, any other of its entities, or its circuit id."


def _description(path: Path, service: str) -> str:
    strings = json.loads(path.read_text(encoding="utf-8"))
    description = strings["services"][service]["fields"]["circuit_id"]["description"]
    assert isinstance(description, str)
    return description


@pytest.mark.parametrize("service", _SERVICES)
def test_the_selector_offers_the_circuits_current_sensors(service: str) -> None:
    services = yaml.safe_load((_COMPONENT / "services.yaml").read_text(encoding="utf-8"))
    assert services[service]["fields"]["circuit_id"]["selector"]["entity"]["device_class"] == "current"


@pytest.mark.parametrize("service", _SERVICES)
def test_the_description_names_the_current_sensor_and_the_circuit_id(service: str) -> None:
    assert _description(_COMPONENT / "strings.json", service) == _EXPECTED


@pytest.mark.parametrize("language", _LANGUAGES)
def test_every_locale_says_it_the_same_way_for_every_service(language: str) -> None:
    """One sentence per locale, shared by the four services, as in English."""
    path = _COMPONENT / "translations" / f"{language}.json"
    assert len({_description(path, service) for service in _SERVICES}) == 1


_RETRY = {"en": "Try again", "es": "Vuelve a intentarlo", "fr": "Réessayez", "ja": "もう一度", "pt": "Tente novamente"}
"""How each language would tell the user to retry, which a refusal for a value that may never arrive must not."""


@pytest.mark.parametrize("language", ("en", *_LANGUAGES))
def test_the_not_arrived_refusal_promises_no_value(language: str) -> None:
    """The panel may never send it: a panel that declares no service rating, or a declared one that stays unset."""
    path = _COMPONENT / ("strings.json" if language == "en" else f"translations/{language}.json")
    message = json.loads(path.read_text(encoding="utf-8"))["exceptions"]["package_ratings_not_arrived"]["message"]
    assert _RETRY[language] not in message
    if language == "en":
        assert message == (
            "The panel has not reported this circuit's rating, its breaker space or its service rating, "
            "which copied ratings are checked against, so they cannot be checked."
        )
