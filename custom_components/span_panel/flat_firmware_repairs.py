"""The Repair raised when setup refuses a panel on flat-schema firmware.

spanos2 firmware publishes the older flat eBus schema, which this integration no
longer parses. `firmware_schema` decides whether a panel is flat; this module
holds the Repair that tells the user so, which setup raises and clears.

Its own module rather than a neighbour of the hardware Repair, although setup
raises both when it refuses a panel. They are refused on opposite terms:
unvalidated hardware stops setup until a release that has been validated with
it, while a flat panel is retried, because the remedy arrives on the panel and
nothing here has to change for it to take effect.

- **Not persistent.** Every setup and every retry reads the panel's schema
  again and re-derives the verdict, so there is nothing for a restart to carry
  over: one that still finds flat firmware raises it afresh.
- **Severity `ERROR`.** Setup has stopped, so the panel has no live entities
  until its firmware is updated.
- **Not fixable.** Nothing this integration can do changes the panel's
  firmware. The remedy is a firmware update on the panel, which the retrying
  setup notices without being asked; that is why the description tells the user
  the panel reconnects on its own.
- **No log line of its own.** Setup raises `ConfigEntryNotReady` carrying the
  `panel_firmware_flat` exception translation. Home Assistant shows that message
  as the entry's retry reason and records it at info level on each attempt, so
  the Repair is the notice, and a warning here would only repeat it on every
  retry for as long as the panel stays on spanos2.

One translation key, unlike the hardware Repair's two, because the only value it
names is the entry's title, which every entry has.
"""

from __future__ import annotations

from typing import Final

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN

FLAT_FIRMWARE_ISSUE_PREFIX: Final = "flat_firmware_"

FLAT_FIRMWARE_TRANSLATION_KEY: Final = "panel_firmware_flat"
"""The key every refusal of a flat panel is worded under.

The Repair here, setup's `ConfigEntryNotReady`, and the config flow's error and
abort each find their sentence under it, in their own section of `strings.json`.
"""


def flat_firmware_issue_id(entry_id: str) -> str:
    """Issue id for one entry's flat-firmware refusal."""
    return f"{FLAT_FIRMWARE_ISSUE_PREFIX}{entry_id}"


@callback
def async_raise_flat_firmware(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Raise the Repair, naming the panel by its entry's title."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        flat_firmware_issue_id(entry.entry_id),
        is_fixable=False,
        is_persistent=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key=FLAT_FIRMWARE_TRANSLATION_KEY,
        translation_placeholders={"panel": entry.title},
        data={"entry_id": entry.entry_id},
    )


@callback
def async_clear_flat_firmware(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Drop the Repair, on a setup that finds the panel is not flat and on removal of the entry.

    Setup finds that by reading a parent/child schema, or, when the read could
    not be had, by connecting on an adapter other than the flat one.
    """
    ir.async_delete_issue(hass, DOMAIN, flat_firmware_issue_id(entry.entry_id))
