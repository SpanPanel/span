"""Utility functions for the Span integration."""

from dataclasses import dataclass
import logging
from typing import Final

from homeassistant.helpers.device_registry import (
    AnyDeviceEntry,
    ChildDeviceEntry,
    ChildDeviceInfo,
    DeviceInfo,
    DeviceRegistry,
)
from span_panel_api import (
    SpanBatterySnapshot,
    SpanCircuitSnapshot,
    SpanEvseSnapshot,
    SpanMidSnapshot,
    SpanPanelSnapshot,
    SpanPVSnapshot,
)

from .const import DOMAIN
from .helpers import circuit_device_name
from .id_builder import build_circuit_device_identifier, circuit_id_from_device_identifier

_LOGGER = logging.getLogger(__name__)

# Every SPAN sub-device hangs off the panel and says so with `via_device_id` --
# the panel's *registry* id, not its identifiers. Identifiers are unique only
# within a config entry, so linking by them is ambiguous by construction; Home
# Assistant deprecated `via_device` for that reason and stops honouring it in
# 2027.8. The id is resolved once during setup and carried on the entry's
# runtime data, because a sub-device is only ever built after the panel device
# exists -- which is what lets the builders below take a plain `str` and leaves
# no caller with an absence to handle.

# A sub-device's registry identifier is `{panel serial}_{kind}`, with EVSE
# carrying its node id after the kind because a panel can have several. PV takes
# both shapes: `{panel serial}_pv` for the panel's solar card, and
# `{panel serial}_pv_{inverter key}` for each inverter on a panel with more than
# one.
#
# Named here, beside the builders that construct them, because the topology
# WebSocket command has to read the grammar back. It used to restate it, and the
# MID shipped classifying as "unknown" for exactly that reason: a third kind was
# added to the writing end and not to the reading one. `classify_sub_device_identifier`
# is the reading end, so the two cannot drift again.
SUB_DEVICE_BESS: Final = "bess"
SUB_DEVICE_MID: Final = "mid"
SUB_DEVICE_EVSE: Final = "evse"
SUB_DEVICE_PV: Final = "pv"

BOOLEAN_DATATYPE: Final = "boolean"
ENUM_DATATYPE: Final = "enum"
NUMERIC_DATATYPES: Final = frozenset({"float", "integer"})
"""The wire datatypes an eBus `$datatype` declares, as far as this integration reads them.

Here rather than in `adoption.py`, where they were first written, because three
modules now decide something from a declared datatype and only one of them is
about adopting a device: `extension.py` picks a platform from it, and
`curation.py` refuses a state class off a row that is not numeric. `util.py` is
the module all three already import from, so this is the one home that does not
make a reader of one feature import the other.
"""


def declares_a_number(datatype: str, unit: str | None) -> bool:
    """Say whether a declaration describes a row whose value is a number.

    Either half is enough. The declared `$datatype` is the truth of it; a
    declared unit is taken as saying the same thing, because a publisher that
    omits a `$datatype` still declares `W`, and a property carrying a unit is a
    number whatever else it says.

    The unit alone used to answer this, as a proxy, and read a bare count -- an
    `integer` with no unit -- as text. Here rather than in each caller because
    two of them decide different things from the same question and must not
    answer it differently: `adoption` and `extension` parse a published value
    with it, and `curation` offers the device classes that go behind that value.
    A row whose reading is a float and whose only offered device class is `enum`
    is the incoherence this being one function prevents.
    """
    return unit is not None or datatype in NUMERIC_DATATYPES


ADOPTED_IDENTIFIER_TOKEN: Final = "adopted"
"""The infix marking a sub-device identifier as adopted rather than curated.

An adopted device is `{panel serial}_adopted_{anchor}`, where the anchor is
whatever the device was first seen under. Kept here beside the curated kinds
because the two namespaces have to be readable apart, and the reading end below
is what would otherwise mistake one for the other: a vendor device whose id
happens to end in `pv` would classify as the solar sub-device under a suffix
rule that had never heard of adoption.
"""


def classify_sub_device_identifier(identifier: str) -> str | None:
    """Return the kind of sub-device an identifier names, or None if it names none.

    None rather than an "unknown" string: the caller knows whether it is looking
    at something that must be a sub-device, and a sentinel that reads like a kind
    is what let an unclassified device render as a device with no type.

    **Most specific first.** EVSE is the one kind whose token is an infix rather
    than a suffix, and a suffix test cannot tell `..._evse_inverter_pv` from a PV
    identifier. Testing the infix first makes the charger's node id opaque to the
    suffix rules below, which is the only ordering that stays right whatever a
    panel names its nodes.
    """
    # Adopted devices are not a curated kind and must not be read as one. Tested
    # before every suffix rule below, because the anchor that follows the token
    # is vendor vocabulary: a device id ending in `pv` would otherwise classify
    # as the panel's solar sub-device.
    if f"_{ADOPTED_IDENTIFIER_TOKEN}_" in identifier:
        return None

    # Infix, not suffix: the node id follows, and it is what distinguishes one
    # charger from another on the same panel.
    if f"_{SUB_DEVICE_EVSE}_" in identifier:
        return SUB_DEVICE_EVSE
    # An inverter's own card, whose key follows the kind as a charger's does.
    # After the EVSE test for the same reason the suffix rules are: a charger's
    # node id is opaque, whatever it contains.
    if f"_{SUB_DEVICE_PV}_" in identifier:
        return SUB_DEVICE_PV
    if identifier.endswith(f"_{SUB_DEVICE_BESS}"):
        return SUB_DEVICE_BESS
    if identifier.endswith(f"_{SUB_DEVICE_MID}"):
        return SUB_DEVICE_MID
    if identifier.endswith(f"_{SUB_DEVICE_PV}"):
        return SUB_DEVICE_PV
    return None


@dataclass(frozen=True, slots=True)
class PvDeviceRef:
    """Which PV card an identifier names: the Solar card, or one inverter's. There is no third kind."""

    inverter_key: str | None
    """The `pv_inverters` key of an inverter's own card; `None` for the Solar card."""


def pv_device_ref(identifier: str, panel_identifier: str) -> PvDeviceRef | None:
    """Read a PV identifier back, the reading end of `pv_device_info` and `pv_inverter_device_info`.

    Anchored on this panel's own prefix rather than tested as an infix, as
    `classify_sub_device_identifier` must be for kinds it cannot anchor: here
    the panel is known, so a charger whose node id contains `_pv_`, an adopted
    device, or another panel's card cannot be mistaken for one of these. Each
    card is read back through the module's one builder of its identifier: the
    Solar card's by `is_pv_device_identifier`, an inverter's by
    `inverter_key_of_device_identifier`.
    """
    if is_pv_device_identifier(panel_identifier, identifier):
        return PvDeviceRef(None)
    key = inverter_key_of_device_identifier(panel_identifier, identifier)
    return PvDeviceRef(key) if key is not None else None


def snapshot_to_device_info(
    snapshot: SpanPanelSnapshot,
    device_name: str | None = None,
    host: str | None = None,
) -> DeviceInfo:
    """Convert a SpanPanelSnapshot to a Home Assistant device info object.

    Manufacturer, model and hardware revision come from the enclosure's own
    `info` node where it publishes them, and fall back to the strings this
    integration has always shown where it does not.

    **The fallbacks are the point, not a courtesy.** A panel that omits one of
    the three must keep the card it has rather than losing a row. `hw_version`
    has no such string to fall back to and so is simply absent where the panel
    publishes none. It is passed as None rather than left unset: `DeviceInfo`
    keeps a None, leaving out only a field never set, and the registry writes
    it, so a revision the panel stops reporting is cleared rather than left
    standing. That None is the difference between "this panel does not report
    a revision" and "this panel reports a blank one".
    """
    configuration_url = f"http://{host}" if host else None

    return DeviceInfo(
        identifiers={(DOMAIN, snapshot.serial_number)},
        manufacturer=snapshot.vendor_name or "Span",
        # The published designation is the panel's own model code (`MAIN_40`),
        # which is also what `panel_size` is derived from. Showing it beats
        # "SPAN Panel" on a card whose whole job is saying which hardware this
        # is -- and the generic string remains for anything that publishes none.
        model=snapshot.model or "SPAN Panel",
        name=device_name or "Span Panel",
        sw_version=snapshot.firmware_version,
        hw_version=snapshot.hardware_version,
        configuration_url=configuration_url,
    )


def bess_device_identifier(panel_identifier: str) -> str:
    """Return the registry identifier of the panel's battery card: `{panel serial}_bess`.

    The one builder of it: the card's `DeviceInfo` (`bess_device_info`) and an
    extension that joins the card (`extension.extension_device_identifier`)
    ask here. `classify_sub_device_identifier` reads its kind back by the
    `SUB_DEVICE_BESS` token it ends in.
    """
    return f"{panel_identifier}_{SUB_DEVICE_BESS}"


def mid_device_identifier(panel_identifier: str) -> str:
    """Return the registry identifier of the panel's microgrid interconnect card: `{panel serial}_mid`.

    The one builder of it, for `mid_device_info` and for an extension that joins
    the card (`extension.extension_device_identifier`).
    `classify_sub_device_identifier` reads its kind back by the
    `SUB_DEVICE_MID` token it ends in.
    """
    return f"{panel_identifier}_{SUB_DEVICE_MID}"


def evse_device_identifier(panel_identifier: str, node_id: str) -> str:
    """Return the registry identifier of a charger's card: `{panel serial}_evse_{node id}`.

    The one builder of it, for `evse_device_info` and for an extension that
    joins the card (`extension.extension_device_identifier`), whose subject's
    instance key is the charger's node id. `classify_sub_device_identifier`
    reads its kind back by the `_evse_` infix.
    """
    return f"{panel_identifier}_{SUB_DEVICE_EVSE}_{node_id}"


def bess_device_info(
    panel_identifier: str,
    battery: SpanBatterySnapshot,
    panel_name: str,
    *,
    panel_device_id: str,
) -> DeviceInfo:
    """Create DeviceInfo for a BESS sub-device linked to the parent panel.

    Two panel-shaped arguments doing different jobs: `panel_identifier` is the
    serial this sub-device namespaces its own identity under, `panel_device_id`
    is the registry id it links to.
    """
    name = f"{panel_name} Battery"
    return DeviceInfo(
        identifiers={(DOMAIN, bess_device_identifier(panel_identifier))},
        name=name,
        manufacturer=battery.vendor_name or "Unknown",
        # `model` is the human designation, which the BESS publishes as `info/model`.
        model=battery.model or "Battery Storage",
        serial_number=battery.serial_number,
        sw_version=battery.software_version,
        via_device_id=panel_device_id,
    )


def mid_device_info(
    panel_identifier: str,
    mid: SpanMidSnapshot,
    panel_name: str,
    *,
    panel_device_id: str,
) -> DeviceInfo:
    """Create DeviceInfo for the Microgrid Interconnect Device.

    v1.0 publishes the MID as a device of its own and puts the `grid` capability on it
    rather than on the enclosure — "the enclosure device itself does not publish them" —
    so islanding decisions belong to hardware with its own identity. Registering it here
    keeps the integration's model in step with the library's rather than folding a
    device's properties onto the panel.

    Linked to the panel, matching BESS and EVSE, even though the wire tree makes the
    MID a child of the BESS. Home Assistant's device graph is about what a user navigates,
    and every SPAN sub-device hangs off the panel there; mirroring the Homie parentage
    would put the MID one level deeper than its siblings for no reader's benefit.
    """
    return DeviceInfo(
        identifiers={(DOMAIN, mid_device_identifier(panel_identifier))},
        name=f"{panel_name} Microgrid Interconnect",
        manufacturer=mid.vendor_name or "Unknown",
        model=mid.model or "Microgrid Interconnect Device",
        serial_number=mid.serial_number,
        # Passed through unguarded, exactly as `bess_device_info` does: `DeviceInfo`
        # omits a `None` field and renders an empty string as a present-but-blank row,
        # so `or ""` here would invent a row for a panel that published nothing. The
        # library preserves that distinction for the same reason.
        #
        # Read without a schema or version gate, deliberately: r202633 documents both
        # on the MID's `info` node, and `has_mid` keeps every caller of this builder
        # off a panel that declares no MID. A conditional here would be unreachable
        # code implying a case that cannot arise.
        sw_version=mid.software_version,
        hw_version=mid.hardware_version,
        via_device_id=panel_device_id,
    )


EMPTY_EVSE: Final = SpanEvseSnapshot(node_id="", feed_circuit_id="")
"""Stand-in for a charger that has left the snapshot mid-session.

One instance rather than one per platform: the binary sensor, the number and the
sensor each carried their own, and a fallback that differs between platforms is
one charger rendering three ways. Safe to share because `SpanEvseSnapshot` is
frozen -- every field below the two ids is the `None` the library gives an
unpublished property, so a charger that has gone away reads exactly like one that
never published, which is what the builder below already handles.
"""


def evse_display_name(
    evse: SpanEvseSnapshot,
    panel_name: str,
    display_suffix: str | None = None,
) -> str:
    """Return the name a charger's device card is registered under.

    It is what the device list shows until the user renames the charger there.
    The errors its controls raise name the card as Home Assistant shows it
    (`SpanPanelEntity._displayed_device_name`) rather than by this. Separate
    from the builder below because those controls keep it as `_charger_name`,
    to name the charger in the log line of each command they send
    (`command=`). The wire calls a charger by its node id, which is what the
    panel needs and what nobody reading a message can act on, so nothing
    user-facing names it by that.
    """
    base_name = evse.model or "EV Charger"
    name = f"{base_name} ({display_suffix})" if display_suffix else base_name
    return f"{panel_name} {name}"


def evse_device_info(
    panel_identifier: str,
    evse: SpanEvseSnapshot,
    panel_name: str,
    display_suffix: str | None = None,
    *,
    panel_device_id: str,
) -> DeviceInfo:
    """Create DeviceInfo for an EVSE sub-device linked to the parent panel."""
    return DeviceInfo(
        identifiers={(DOMAIN, evse_device_identifier(panel_identifier, evse.node_id))},
        name=evse_display_name(evse, panel_name, display_suffix),
        manufacturer=evse.vendor_name or "SPAN",
        model=evse.model or "SPAN Drive",
        serial_number=evse.serial_number,
        sw_version=evse.software_version,
        via_device_id=panel_device_id,
    )


def pv_device_info(
    panel_identifier: str,
    pv: SpanPVSnapshot,
    panel_name: str,
    *,
    panel_device_id: str,
) -> DeviceInfo:
    """Create DeviceInfo for the solar inverter, linked to the parent panel.

    The last DER to get a card of its own. Its vendor, model and nameplate
    capacity have been readable all along and were shown as three diagnostic
    sensors on the *panel's* card, beside the panel's own manufacturer and model,
    which reads as if the enclosure were an Enphase inverter. The firmware
    version the library also reads reached nothing at all, because a version has
    no home but a device card.

    **The identifier deliberately does not mention the inverter's serial.**
    `info/serial-number` is declared by every PV `$description` and published by
    no producer today, so an identifier preferring it would be `<panel>_pv` on
    every panel now and `<panel>_<serial>` on the first panel whose firmware
    starts publishing one -- and a device identifier is what a consumer keys its
    registry on, so that day would read as the inverter being replaced rather
    than as a value arriving. `{panel serial}_pv` answers the only question an
    identifier has to answer, "which panel's inverter", on a panel with one.
    The card describes what its PV entities read: the inverter they are bound
    to, or the inverters together; see `pv_binding`. The
    serial is not on the card either, for the same reason it is not in the
    identifier: nothing in this integration should start depending on it before
    a producer publishes one.

    **No area is seeded**, so an upgraded installation has to assign this card to
    an area the way it assigned the battery's and the chargers'. Two routes were
    considered. `DeviceInfo`'s `suggested_area` seeds an area only when Core
    creates the device. It is not deprecated: what Core deprecated, and removed
    in 2026.11, is the `suggested_area` a stored `DeviceEntry` carried. An explicit
    `device_registry.async_update_device(area_id=...)` after setup cannot tell an
    area a user deliberately cleared from one never assigned -- the registry
    records `None` for both -- so it would silently re-assign the card on every
    reload. Doing nothing also keeps every sub-device the same: the BESS, the MID
    and each charger seed no area either.
    """
    return DeviceInfo(
        identifiers={(DOMAIN, pv_device_identifier(panel_identifier))},
        name=f"{panel_name} Solar",
        manufacturer=pv.vendor_name or "Unknown",
        model=pv.model or "Solar Inverter",
        # Passed through unguarded, as on the BESS and the MID: `DeviceInfo`
        # omits a `None` field and renders an empty string as a present-but-blank
        # row, so `or ""` would invent a version row for an inverter that
        # published none.
        sw_version=pv.software_version,
        via_device_id=panel_device_id,
    )


EMPTY_PV: Final = SpanPVSnapshot()
"""Stand-in for an inverter that has left the snapshot, as `EMPTY_EVSE` is for a charger."""


def pv_device_identifier(panel_identifier: str) -> str:
    """Return the registry identifier of the panel's Solar card: `{panel serial}_pv`.

    The one builder of it: the card's `DeviceInfo` (`pv_device_info`) and an
    extension that joins the card (`extension.extension_device_identifier`)
    ask here, and `is_pv_device_identifier` reads it back. Each inverter's
    card, on a panel with several, is this identifier with its key after it
    (`pv_inverter_device_identifier`).
    """
    return f"{panel_identifier}_{SUB_DEVICE_PV}"


def is_pv_device_identifier(panel_identifier: str, identifier: str) -> bool:
    """Whether `identifier` is this panel's Solar card's, the reading end of `pv_device_identifier`."""
    return identifier == pv_device_identifier(panel_identifier)


def _pv_inverter_identifier_prefix(panel_identifier: str) -> str:
    """Return what every inverter card's identifier on this panel starts with: `{panel serial}_pv_`."""
    return f"{pv_device_identifier(panel_identifier)}_"


def pv_inverter_device_identifier(panel_identifier: str, inverter_key: str) -> str:
    """Return the registry identifier of an inverter's own card: `{panel serial}_pv_{inverter key}`.

    The one builder of it: the card's `DeviceInfo`, an extension that joins
    the card (`extension.extension_device_identifier`) and the device-id
    migration all ask here, and `inverter_key_of_device_identifier` reads it
    back. Where `inverter_key` is the feeding circuit's id, the migration
    builds this for the circuit's old id and its new one to re-key the card
    in place.
    """
    return f"{_pv_inverter_identifier_prefix(panel_identifier)}{inverter_key}"


def inverter_key_of_device_identifier(panel_identifier: str, identifier: str) -> str | None:
    """Return the inverter key an inverter card's identifier carries on this panel, or None.

    The reading end of `pv_inverter_device_identifier`, sharing its spelling.
    It answers only for an identifier of exactly that form, with this panel's
    identifier as given and a non-empty key, and returns the key whole.
    """
    prefix = _pv_inverter_identifier_prefix(panel_identifier)
    if not identifier.startswith(prefix):
        return None
    return identifier[len(prefix) :] or None


def pv_inverter_device_info(
    panel_identifier: str,
    inverter_key: str,
    pv: SpanPVSnapshot,
    panel_name: str,
    display_suffix: str | None = None,
    *,
    panel_device_id: str,
) -> DeviceInfo:
    """Create DeviceInfo for an inverter the Solar card does not read.

    Keyed by `inverter_key`, the library's `pv_inverters` key, for the reason
    `pv_device_info` gives for leaving the serial out of its own identifier:
    the feeding circuit's id is what stays put across firmware, and an inverter
    no circuit feeds has only its device id. Linked to the panel rather than to
    the solar card, as every SPAN sub-device is.

    Named after the circuit that feeds it, as a charger is, unless that circuit
    is called exactly "Solar Inverter": the name already says so, and repeating
    it as the suffix only doubles it. Only an exact match is dropped, because
    `resolve_pv_display_suffixes` keeps suffixes distinct by exact comparison,
    and dropping a near match could leave two inverters with one name. No
    serial on the card, for the reason `pv_device_info` gives.
    """
    label = "Solar Inverter"
    name = f"{panel_name} {label}"
    if display_suffix and display_suffix != label:
        name = f"{name} ({display_suffix})"
    return DeviceInfo(
        identifiers={(DOMAIN, pv_inverter_device_identifier(panel_identifier, inverter_key))},
        name=name,
        manufacturer=pv.vendor_name or "Unknown",
        model=pv.model or "Solar Inverter",
        sw_version=pv.software_version,
        via_device_id=panel_device_id,
    )


def circuit_device_info(
    serial: str,
    circuit: SpanCircuitSnapshot,
    circuit_id: str,
    *,
    panel_device_id: str,
) -> ChildDeviceInfo:
    """Create ChildDeviceInfo for a circuit's own device, a child of the panel device.

    A child device rather than a sub-device. A circuit is a part of the panel,
    not hardware with an identity of its own, so it links to the panel with
    `parent_device_id` and carries no manufacturer, model or serial, which
    `ChildDeviceInfo` has no field for. The parent is the panel device's
    registry id, as `via_device_id` is for a sub-device.

    `serial` is the panel's serial as its own device identifier carries it, and
    `circuit_id` the snapshot's circuit key. Named by `circuit_device_name`.
    """
    return ChildDeviceInfo(
        identifiers={(DOMAIN, build_circuit_device_identifier(serial, circuit_id))},
        name=circuit_device_name(circuit, circuit_id),
        parent_device_id=panel_device_id,
    )


def circuit_device(
    device_registry: DeviceRegistry,
    serial: str,
    circuit_id: str,
    *,
    config_entry_id: str,
) -> ChildDeviceEntry | None:
    """Return a circuit's own device, or None where the registry holds none for it.

    Looked up by the identifier `circuit_device_info` registers it under, within
    the entry that owns the panel. A circuit has no device until its first
    entity is added, and a charger's feed circuit has none at all where it has
    neither a breaker switch nor a priority select.
    """
    return device_registry.async_get_child_device_by_identifier(
        (DOMAIN, build_circuit_device_identifier(serial, circuit_id)), config_entry_id
    )


def circuit_id_of_device(serial: str, device: ChildDeviceEntry) -> str | None:
    """Return the circuit a child device is the device of, on the panel with this serial, or None.

    The reading end of `circuit_device_info`. A child device is a circuit's
    only when an identifier of this integration's says so for this panel, so
    another panel's circuit device and a child device of any other kind answer
    None, whatever they are called and whatever they hang off.
    """
    for domain, identifier in device.identifiers:
        if domain != DOMAIN:
            continue
        circuit_id = circuit_id_from_device_identifier(serial, identifier)
        if circuit_id is not None:
            return circuit_id
    return None


def device_display_name(device: AnyDeviceEntry) -> str | None:
    """Return the name Home Assistant shows for a device: the user's, else the integration's.

    A rename in Home Assistant is stored in `name_by_user`, and `name` stays the
    name the integration registered the device under. Core shows the user's
    wherever there is one, so anything that names a device to a person asks
    here rather than reading `name` -- which is how the card's title kept a
    panel's default name after the user renamed it.

    Empty or None only for a device with neither, which each caller answers in
    its own way. A circuit's entities are named by `effective_circuit_device_name`
    instead, which falls back to the name the device is about to be given
    rather than the one the registry holds.
    """
    return device.name_by_user or device.name


def effective_circuit_device_name(
    device_registry: DeviceRegistry,
    serial: str,
    circuit: SpanCircuitSnapshot,
    circuit_id: str,
    *,
    config_entry_id: str,
) -> str:
    """Return the name Home Assistant composes a circuit's entity ids and names from.

    Core reads a device's `name_by_user` ahead of its `name`, so a name the user
    gave the circuit's device in Home Assistant is the answer wherever there is
    one, whatever the panel calls the circuit.

    Otherwise it is the name `circuit_device_info` gives the device now, not the
    name the registry holds for it. A circuit entity is built before it is
    added, and adding it writes that name over the registry's, so the
    registry's is the circuit's name from before the rename on the panel that
    this reload follows. An id read against the old name is answered for a
    device that no longer carries it.
    """
    device = circuit_device(device_registry, serial, circuit_id, config_entry_id=config_entry_id)
    if device is not None and device.name_by_user:
        return device.name_by_user
    return circuit_device_name(circuit, circuit_id)


def circuit_display_name(
    device_registry: DeviceRegistry,
    serial: str,
    circuit: SpanCircuitSnapshot,
    circuit_id: str,
    *,
    config_entry_id: str,
) -> str:
    """Return a circuit's name as Home Assistant shows it, wherever the integration names the circuit.

    A current alert's name is this one, in its text, the `alert_name` of its
    event and its log line, so a person and an automation find the circuit
    under the name Home Assistant shows.

    Its own device's name (`device_display_name`): the user's where they renamed
    the device, else the name it is registered under. Read from the registry
    each time it is asked, so a rename made since setup is the name the next
    alert carries. Unlike `effective_circuit_device_name`, a registered
    device's `name` is read rather than the one it is about to be given:
    between a rename on the panel and the reload that renames the device, Home
    Assistant still shows the old one.

    A circuit with no device takes the name `circuit_device_info` would give it
    (`circuit_device_name`). A charger's feed circuit has none where it has
    neither a breaker switch nor a priority select, and on the first setup after
    an upgrade from a release before circuits had devices, the monitor judges
    pushes before they are registered.

    A circuit's device is always registered under a name, and Home Assistant
    offers a user no way to clear it.
    """
    device = circuit_device(device_registry, serial, circuit_id, config_entry_id=config_entry_id)
    if device is None:
        return circuit_device_name(circuit, circuit_id)
    if not (name := device_display_name(device)):
        raise RuntimeError(f"The device of circuit {circuit_id} has no name to show")
    return name
