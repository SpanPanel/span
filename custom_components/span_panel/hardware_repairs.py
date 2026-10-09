"""The Repair raised when setup refuses hardware this release has not been validated with.

Its own module rather than a neighbour of the CA Repairs, because it shares
nothing with them but the shape: the finding is about what the panel is, not
about how it is reached.

- **Not persistent.** Every setup and reload reads the panel's status again and
  re-derives the verdict, so a restart that still refuses re-raises it, and one
  that proceeds clears it.
- **Severity `ERROR`.** Setup has stopped and no entity exists for the panel.
- **Not fixable.** Nothing this integration can do validates hardware; the
  remedy is a release that has been validated with it.
- **No log line of its own.** Setup raises a `ConfigEntryError` carrying the
  same translation, and Home Assistant logs that once per refused setup.

Two translation keys rather than one, for the reason `leaf_repairs` gives: a
panel that publishes no model is a different sentence, not an empty value, and
a phrase passed as a placeholder would reach every user untranslated.
"""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN

UNVALIDATED_HARDWARE_ISSUE_PREFIX = "unvalidated_hardware_"

_MODEL_TRANSLATION_KEY = "unvalidated_hardware"
_NO_MODEL_TRANSLATION_KEY = "unvalidated_hardware_no_model"


@dataclass(frozen=True, slots=True)
class RefusedHardware:
    """What setup refused, worded the same for the Repair and for the setup error."""

    version: str
    """The REST `hardwareVersion`; never empty, since an absent one proceeds."""
    model: str | None
    """The panel's `info/model`, or None when it publishes none."""

    @property
    def translation_key(self) -> str:
        """The sentence for a panel with a model, or the one for a panel without."""
        return _MODEL_TRANSLATION_KEY if self.model else _NO_MODEL_TRANSLATION_KEY

    @property
    def translation_placeholders(self) -> dict[str, str]:
        """The values the chosen sentence names."""
        if self.model:
            return {"version": self.version, "model": self.model}
        return {"version": self.version}


def unvalidated_hardware_issue_id(entry_id: str) -> str:
    """Issue id for one entry's refused hardware."""
    return f"{UNVALIDATED_HARDWARE_ISSUE_PREFIX}{entry_id}"


@callback
def async_raise_unvalidated_hardware(
    hass: HomeAssistant, entry: ConfigEntry, refused: RefusedHardware
) -> None:
    """Raise the Repair, naming the hardware version and model the panel reported."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        unvalidated_hardware_issue_id(entry.entry_id),
        is_fixable=False,
        is_persistent=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key=refused.translation_key,
        translation_placeholders=refused.translation_placeholders,
        data={"entry_id": entry.entry_id},
    )


@callback
def async_clear_unvalidated_hardware(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Drop the Repair, on a setup that proceeds and on removal of the entry."""
    ir.async_delete_issue(hass, DOMAIN, unvalidated_hardware_issue_id(entry.entry_id))
