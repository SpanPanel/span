"""Identity for vendor extension properties on devices this integration models.

The other half of vendor extensibility from `adoption.py`. That module adopts a
whole *device* nobody modelled; this one carries a new *property* on a device
this integration already models -- a battery vendor hanging `battery-2/
cell-temperature` off the BESS -- which until now reached the user nowhere.

**An adopted extension is a terminal identity.** It is a vendor reading on the
correct existing device card, disabled and diagnostic, in plain wire vocabulary,
and it stays that until an external trigger changes it: the publisher stops
publishing the property, or better metadata arrives. Nothing here promotes,
re-homes or migrates one on its own schedule, and curation is never blocked by
one existing -- an entity's `unique_id` and `entity_id` are permanent, its
*identity* carries no expectation of permanence beyond that.

**Terminal is not unimprovable, and the owner of the device is not guessing.**
A user who curates a row asserts what this module refuses to infer, and their
record arrives as metadata `curation.py` composes into the description built
here -- which is how a curated reading carries a state class without this
module naming the thing an AST guard forbids it to spell. It changes what an
entity *says*, never what it *is*: the id, the card and the platform are
untouched, and an uncurated row is exactly the entity it was before curation
existed.

**Nothing is ever removed by this integration.** A row the user deletes is
recreated -- disabled, as it arrives -- for as long as the property is still
published, so deletion is not suppression and no suppression feature is needed.
Deletion sticks exactly when publishing has stopped, because then nothing exists
to recreate it from. The delete button therefore already means "hide until next
setup" for a live reading and "clear this out" for a dead one, decided by the
wire rather than by a feature.
"""

from __future__ import annotations

import logging
import re
from typing import Final

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo, DeviceRegistry
from homeassistant.helpers.entity_registry import EntityRegistry
from span_panel_api import ExtensionProperty, ExtensionSubject, SpanPanelSnapshot

from .adoption import (
    DEVICE_CLASS_BY_UNIT,
    clamp_state,
    homie_boolean,
    humanised,
)
from .const import DOMAIN
from .coordinator import SpanPanelCoordinator
from .curation import (
    CurationOverlay,
    CurationRecord,
    RowContext,
    binary_sensor_device_class,
    entity_category_for,
    sensor_description,
)
from .entity import SpanPanelEntity
from .notices import async_raise_on_change, read_translations
from .util import (
    ADOPTED_IDENTIFIER_TOKEN,
    BOOLEAN_DATATYPE,
    SUB_DEVICE_BESS,
    SUB_DEVICE_EVSE,
    SUB_DEVICE_MID,
    SUB_DEVICE_PV,
    declares_a_number,
)

_LOGGER = logging.getLogger(__name__)

SCOPE_PANEL: Final = "panel"

HINT_READING: Final = "reading"
HINT_DETAIL: Final = "detail"

IDENTITY_FAMILY: Final = frozenset(
    {"vendor", "model", "serial", "part-number", "firmware", "hardware", "build", "revision"}
)
"""Property-name tokens that mark a declaration as device description.

Matched outside the `info` node, which never reaches adoption at all -- so a hit
here is catching a vendor that put identity on a capability node rather than
re-checking what `info` already resolved.
"""

HOMIE_ID: Final = re.compile(r"^[a-z0-9-]+$")
"""The Homie id charset: lowercase alphanumerics and hyphens, nothing else.

Load-bearing rather than decorative. The grammar below joins the node and the
property with `/`, and that separator is only unambiguous because an id cannot
contain one -- which is the same reason upstream's own capability-catalog paths
and `discovery_path()` are spelled with slashes. An id outside this charset is
refused adoption rather than sanitised, because sanitising is what would make
the split ambiguous again.
"""

_SCOPE_BY_KIND: Final[dict[str, str]] = {
    "panel": SCOPE_PANEL,
    "battery": SUB_DEVICE_BESS,
    "mid": SUB_DEVICE_MID,
    "pv": SUB_DEVICE_PV,
}
"""Library subject kind → the scope segment this integration's ids already use.

The singletons only. The kinds below carry an instance key, so their segment is
`{prefix}_{key}` rather than a constant.
"""

_SCOPE_PREFIX_BY_KIND: Final[dict[str, str]] = {
    "evse": SUB_DEVICE_EVSE,
    "circuit": "circuit",
    "lugs": "lugs",
}
"""Multi-instance subject kinds, and the prefix their scope segment takes.

`lugs` is here rather than folded into `panel` even though a lugs device's
curated fields land on the panel: the two lugs devices run identical firmware,
so a vendor extension on one is the expected case of the same extension on
both, and one scope for both would mint one id for two readings. They still
*render* on the panel's card -- see `extension_device_identifier`, where the
divergence between identity and placement is deliberate.
"""


def extension_scope(subject: ExtensionSubject) -> str | None:
    """Return the id scope segment for a subject, or `None` if it names no device.

    `None` rather than a fallback string: a subject kind this integration does
    not place on a device card has no card to hang an entity on, and inventing a
    scope would mint a permanent id for an entity with nowhere to live.
    """
    if subject.kind in _SCOPE_BY_KIND:
        return _SCOPE_BY_KIND[subject.kind]
    if subject.instance_key is None:
        return None
    if subject.kind in _SCOPE_PREFIX_BY_KIND:
        return f"{_SCOPE_PREFIX_BY_KIND[subject.kind]}_{subject.instance_key}"
    return None


def extension_unique_id(
    serial: str, subject: ExtensionSubject, node_id: str, property_id: str
) -> str | None:
    """Return the unique id for one extension property, or `None` if it is unadoptable.

        span_{serial}_adopted_{scope}/{node}/{property}

    Anchored on what is stable and ours -- the panel serial and the curated scope
    -- and addressed by the wire path **verbatim**, which is upstream's own
    capability-catalog spelling (`AdoptedProperty.path`, `discovery_path()`).

    **Verbatim, and never through `get_user_friendly_suffix`.** That helper
    de-*dots* rather than de-hyphens and substitutes a curated suffix on a
    mapping hit, so routing a wire address through it would both mangle the
    address and let a vendor string collide with a curated spelling. Carrying the
    path as published is also what makes this injective: the id *is* the wire
    address, so two distinct addresses cannot collapse into one. Normalising
    hyphens to underscores would collapse `battery-2` + `cell-temperature` and
    `battery` + `2-cell-temperature` into the same id.

    **Not the eBus proxy composition.** `{proxier-id}-{proxied-id}` is upstream's
    device-handle spelling, and upstream is explicit that those handles are not
    identities: they differ across enclosures by design and are unstable across
    the proxy-to-native transition. A permanent id anchored on one would rename
    itself when a device stopped being proxied, stranding every entity keyed on
    it -- and nothing migrates, so there would be no recovery.

    The `adopted` token sits immediately after the serial, so the id is
    namespaced by prefix. Device-level adoption's ids contain no `/`, so the
    presence of a slash is what tells the two adoption grammars apart -- no
    reader has to count token positions.
    """
    scope = extension_scope(subject)
    if scope is None:
        _LOGGER.debug(
            "Extension property %s/%s names no device card (subject kind %s); not adopted",
            node_id,
            property_id,
            subject.kind,
        )
        return None
    if not HOMIE_ID.match(node_id) or not HOMIE_ID.match(property_id):
        # Refused rather than sanitised: see `HOMIE_ID`. The property is still
        # reported in diagnostics, so the case is visible rather than silent.
        _LOGGER.warning(
            "Extension property %s/%s is outside the Homie id charset and is not adopted; "
            "it remains visible in diagnostics",
            node_id,
            property_id,
        )
        return None
    return f"span_{serial}_{ADOPTED_IDENTIFIER_TOKEN}_{scope}/{node_id}/{property_id}"


def is_extension_unique_id(unique_id: str) -> bool:
    """Whether an id was minted by the grammar above.

    The slash is the discriminator, for the reason `extension_unique_id`
    documents: a curated id has no `adopted` token, and a device-level adopted id
    has the token but no slash.
    """
    return f"_{ADOPTED_IDENTIFIER_TOKEN}_" in unique_id and "/" in unique_id


def extension_curation_key(subject: ExtensionSubject, path: str) -> str | None:
    """Return the curation-store key for one extension property: `{scope}/{node}/{property}`.

    Scope-prefixed because `path` is unique only within one wire device; the
    scope segment is exactly the one the unique_id carries, so the key is
    injective for the same reason the id is. `None` mirrors
    `extension_unique_id`: no scope, nothing to curate.
    """
    scope = extension_scope(subject)
    return None if scope is None else f"{scope}/{path}"


def extension_device_identifier(panel_identifier: str, subject: ExtensionSubject) -> str | None:
    """Return the registry identifier of the curated device this property belongs on.

    The identifier only, never a rebuilt `DeviceInfo`. An extension entity joins
    a card that already exists; restating that card's name, manufacturer or model
    here would be a second implementation of `util`'s builders, free to drift and
    able to rename a user's device by disagreeing with them.

    A circuit's entities live on the panel's own card in this integration, so a
    circuit subject resolves there too -- the entity's *name* is what says which
    circuit it came from. Lugs are the same: two devices for identity, one card
    for rendering, which is why this mapping and `extension_scope` are separate
    functions rather than one. Identity must distinguish what placement merges.
    """
    if subject.kind in ("panel", "circuit", "lugs"):
        return panel_identifier
    if subject.kind == "battery":
        return f"{panel_identifier}_{SUB_DEVICE_BESS}"
    if subject.kind == "mid":
        return f"{panel_identifier}_{SUB_DEVICE_MID}"
    if subject.kind == "pv":
        return f"{panel_identifier}_{SUB_DEVICE_PV}"
    if subject.kind == "evse" and subject.instance_key is not None:
        return f"{panel_identifier}_{SUB_DEVICE_EVSE}_{subject.instance_key}"
    return None


def classify_extension(datatype: str) -> Platform:
    """Return the platform a declared datatype surfaces on.

    Two platforms, because `adoption.classify`'s three control rows are
    deliberately absent: an extension property lives on a device whose curated
    controls do real safety work -- the EVSE limit refuses a value above the
    commissioned ceiling, the islanding assertion translates `GRID` into
    `ON_GRID` -- and a generic write path would sit beside them on the same wire
    with neither. A settable property therefore surfaces as a reading.

    **Derived once, at first sighting, and never re-derived.** See
    `resolve_platform`: the domain is baked into `entity_id`, so a later
    datatype change must not move an existing row.
    """
    return Platform.BINARY_SENSOR if datatype == BOOLEAN_DATATYPE else Platform.SENSOR


def resolve_platform(registry: EntityRegistry, unique_id: str, datatype: str) -> Platform:
    """Return the platform this id already uses, or the one its datatype implies.

    **The platform is a one-way door**, and this function is the door. An entity
    domain is part of `entity_id`, and the registry refuses a cross-domain rename
    outright -- `async_update_entity` raises `ValueError("New entity ID should be
    same domain")`. So a row born a `sensor` can never become a `binary_sensor`:
    re-deriving the platform from a changed declaration would not move it, it
    would strand it and mint a second entity beside it.

    Metadata may reshape everything else about a standing entity -- category,
    device class, unit, name, icon. For an uncurated entity that is free,
    because it writes no long-term statistics and so has nothing for a unit
    change to corrupt. A curated one carries what its owner asserted, and
    `curation.sanitise` re-measures that assertion against each new
    declaration, so a reshaping the assertion no longer fits drops it rather
    than applying it over a wire that has moved. The platform is the exception
    either way, and the exception is enforced here rather than remembered:
    whatever domain the id is already registered under wins.
    """
    for platform in (Platform.SENSOR, Platform.BINARY_SENSOR):
        if registry.async_get_entity_id(platform.value, DOMAIN, unique_id) is not None:
            return platform
    return classify_extension(datatype)


def prominence_hint(row: ExtensionProperty) -> str:
    """Return an advisory ranking for one extension property.

    Advisory, and only advisory: every extension entity arrives DIAGNOSTIC
    whatever this says, until its owner curates it out -- which is the act this
    hint exists to rank rather than to perform. `entity_category` is the one
    attribute that is free to revise later -- no id change, no statistics
    consequence -- so the conservative default costs a line in a future release,
    while the mistakes that are *not* free are simply never made here.

    Ranked by confidence, and each signal's failure mode is why it sits where it
    does:

    1. **Identity-family naming → `detail`.** Highest confidence because it is a
       purely *negative* signal: a property named for a vendor, model, serial,
       part number or firmware build is device description, not a reading. Fails
       only on a vendor using an identity word for a live value.
    2. **A unit with a device class → `reading`.** Moderate: a physical
       measurement is more likely a headline than a knob. Fails *systematically*
       in one direction -- the most headline-worthy number a battery publishes is
       a `%` state of charge, and `%` is deliberately absent from
       `DEVICE_CLASS_BY_UNIT` because it is equally a confidence or a duty cycle.
       So this signal may promote and never demote.
    3. **Everything else → `detail`**, by fall-through rather than by a third
       signal. The node a property hangs off is the obvious candidate for one and
       is deliberately not consulted: Homie nodes are organisational rather than
       editorial, and vendors hang configuration knobs off `meter` because that
       is where the code was.

    The upstream `role` declaration proposed alongside this design would retire
    all three for compliant publishers, which is why it is worth asking for.
    """
    if any(token in row.property_id for token in IDENTITY_FAMILY):
        return HINT_DETAIL
    if row.unit and row.unit in DEVICE_CLASS_BY_UNIT:
        return HINT_READING
    return HINT_DETAIL


MAX_PER_DEVICE: Final = 60
"""How many extension entities one device may mint before the rest are declined.

A registry row is permanent in a way a reading is not: nothing here removes one,
and a row a user deletes returns at the next setup while the property is still
published. So a vendor node declaring hundreds of properties would put hundreds
of rows in every entity picker on every install that met it, and no later
release could take them back.

The cap is deliberately generous -- far above any real device, and the sixteen
`pcs` properties are the largest curated example -- so it is a backstop against a
misbehaving publisher rather than a policy on normal ones. Declining is reported
through a notice naming the device, because a silent truncation would read as
"that is everything the vendor publishes" when it is not.
"""


class ExtensionEntity(SpanPanelEntity):
    """Base for an entity built from a vendor extension on a curated device.

    Disabled without exception, and diagnostic unless the owner of the device
    said otherwise, exactly as `AdoptedEntity` is: the integration's job here is
    to make the reading reachable, not to put it on somebody's dashboard. A
    curated row is that decision already made, so `entity_category_for` lets it
    out of diagnostics -- and leaves it disabled all the same, because enabling
    is still their act.
    """

    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        coordinator: SpanPanelCoordinator,
        unique_id: str,
        row: ExtensionProperty,
        *,
        device_identifier: str,
        record: CurationRecord | None = None,
    ) -> None:
        """Bind this entity to one extension property of one curated device."""
        super().__init__(coordinator)
        self._subject_kind = row.subject.kind
        self._instance_key = row.subject.instance_key
        self._declaration_path = row.path
        self._attr_unique_id = unique_id
        # Node-prefixed on purpose. Curated names on these cards carry no wire
        # vocabulary, so a collision with a curated "Power" or "Status" is
        # avoided by construction rather than by a registration-order-dependent
        # dedup -- and two vendor nodes on one device disambiguate without a
        # special case. Plain, clunky, and honestly so: it marks the entity as
        # uncurated, which is what it is.
        self._attr_name = f"{humanised(row.node_id)} {humanised(row.property_id)}"
        # Identifiers only: the card already exists and belongs to `util`'s
        # builders. Restating its name or model here could rename a user's device
        # by disagreeing with them.
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, device_identifier)})
        self._attr_extra_state_attributes = {
            "prominence_hint": prominence_hint(row),
            "wire_path": row.path,
        }
        self._attr_entity_category = entity_category_for(record)

    def _row(self) -> ExtensionProperty | None:
        """Return this property's current row, or None when it has left the tree.

        Matched out of the snapshot each cycle rather than captured at
        construction, so a device that leaves and returns keeps reporting through
        the same entity. A property the publisher stops publishing simply stops
        being found: the entity reads unknown and is never removed, because
        absence on the wire is ambiguous between "gone" and "not yet arrived".
        """
        snapshot: SpanPanelSnapshot = self.coordinator.data
        for row in snapshot.extension_properties:
            if (
                row.subject.kind == self._subject_kind
                and row.subject.instance_key == self._instance_key
                and row.path == self._declaration_path
            ):
                return row
        return None

    def _published(self) -> str | None:
        """Return the published value, or None when nothing has arrived."""
        row = self._row()
        return None if row is None else row.value


class ExtensionSensor(ExtensionEntity, SensorEntity):
    """A vendor reading on a curated device, described by the wire and by its record.

    This module infers nothing that would enrol a reading in long-term
    statistics, and spells no such attribute anywhere -- an AST guard in
    `tests/test_extension_entities.py` asserts the token is absent from the
    syntax rather than from the paths a test happens to construct. So an
    uncurated reading has nothing for a later unit or device-class change to
    corrupt, and metadata may reshape it freely. The owner of the vendor device
    may assert one, and their assertion reaches the entity through
    `curation.sensor_description` rather than from anything guessed here.
    """

    def __init__(
        self,
        coordinator: SpanPanelCoordinator,
        unique_id: str,
        row: ExtensionProperty,
        *,
        device_identifier: str,
        record: CurationRecord | None = None,
    ) -> None:
        """Take the unit and device class from what the publisher declared.

        The description is built in `curation` rather than here, which is what
        lets a curated row carry the one piece of metadata this module may not
        name and still leaves an uncurated row with exactly what it had.
        """
        super().__init__(
            coordinator, unique_id, row, device_identifier=device_identifier, record=record
        )
        self.entity_description = sensor_description(
            row.path, row.unit, DEVICE_CLASS_BY_UNIT.get(row.unit or ""), record
        )
        self._numeric = declares_a_number(row.datatype, row.unit)

    @property
    def native_value(self) -> str | float | None:
        """Return the published value, parsed to a number where the declaration says it is one.

        The declared `$datatype` is what says so, and a declared unit is taken as
        saying so too. The unit alone used to decide it, as a proxy: a property
        carrying `V` is a number whatever else it says. But a vendor count
        declares no unit and is numeric all the same, and the proxy read one as
        text -- harmless while an uncurated reading asserted nothing about
        itself, and not harmless once the owner of the device could put a
        `measurement` on exactly that row and have the recorder handed a string
        under it.

        The union rather than the datatype alone, because a publisher that omits
        a `$datatype` still declares a unit, and nothing that parses today may
        stop parsing.

        Anything else is text, and a vendor string is unbounded on the wire, so
        it goes through the clamp `adoption` holds for both halves of vendor
        extensibility.
        """
        raw = self._published()
        if raw is None:
            return None
        if not self._numeric:
            return clamp_state(raw, f"Extension {self._declaration_path}")
        try:
            return float(raw)
        except ValueError:
            _LOGGER.debug(
                "Extension %s published %r, which is not a number", self._declaration_path, raw
            )
            return None


class ExtensionBinarySensor(ExtensionEntity, BinarySensorEntity):
    """A declared `boolean` vendor extension on a curated device."""

    def __init__(
        self,
        coordinator: SpanPanelCoordinator,
        unique_id: str,
        row: ExtensionProperty,
        *,
        device_identifier: str,
        record: CurationRecord | None = None,
    ) -> None:
        """Take the device class from the record, because there is nothing to default from.

        A sensor's device class can be read off the declared unit; a boolean
        declares no unit, so `door` and `problem` and `running` are
        indistinguishable on the wire. An uncurated binary sensor therefore has
        no device class at all, and the user's assertion is the only one there
        can be.
        """
        super().__init__(
            coordinator, unique_id, row, device_identifier=device_identifier, record=record
        )
        self._attr_device_class = binary_sensor_device_class(record)

    @property
    def is_on(self) -> bool | None:
        """Homie spells a boolean `true`/`false`; anything else is not an answer."""
        return homie_boolean(self._published())


def create_extension_sensors(
    coordinator: SpanPanelCoordinator,
    snapshot: SpanPanelSnapshot,
    device_registry: DeviceRegistry,
    entity_registry: EntityRegistry,
    *,
    overlay: CurationOverlay,
) -> list[ExtensionSensor]:
    """Every extension property that is not a declared boolean."""
    return _create(
        ExtensionSensor,
        coordinator,
        snapshot,
        device_registry,
        entity_registry,
        Platform.SENSOR,
        overlay=overlay,
    )


def create_extension_binary_sensors(
    coordinator: SpanPanelCoordinator,
    snapshot: SpanPanelSnapshot,
    device_registry: DeviceRegistry,
    entity_registry: EntityRegistry,
    *,
    overlay: CurationOverlay,
) -> list[ExtensionBinarySensor]:
    """Every extension property declared `boolean`."""
    return _create(
        ExtensionBinarySensor,
        coordinator,
        snapshot,
        device_registry,
        entity_registry,
        Platform.BINARY_SENSOR,
        overlay=overlay,
    )


def _create[ExtensionT: ExtensionEntity](
    entity_class: type[ExtensionT],
    coordinator: SpanPanelCoordinator,
    snapshot: SpanPanelSnapshot,
    device_registry: DeviceRegistry,
    entity_registry: EntityRegistry,
    platform: Platform,
    *,
    overlay: CurationOverlay,
) -> list[ExtensionT]:
    """Build one platform's share of the extension properties.

    One partition function rather than two bodies, so `resolve_platform` stays
    the only place a property's platform is decided -- two bodies would each
    restate the predicate, and a property could then reach both platforms or
    neither.

    **The curated record is read through `for_row`, never off the overlay.** The
    store keeps whatever the user asserted when they asserted it, and the
    declaration it was asserted against can have moved since. `for_row` measures
    the record against the declaration in hand and drops what no longer fits;
    the raw record would reach the device-class constructor unchecked and raise
    inside `async_setup_entry`, which is the whole platform for one stale row.
    The context is built from `platform` rather than from the datatype, because
    `resolve_platform` has already ruled on which platform this row is on and a
    second derivation could disagree with the registry.

    `extension_curation_key` declines exactly the subjects `extension_unique_id`
    declines, so every row reaching here has a key. The `None` branch is the
    type system holding the two to one contract, not a case that occurs.
    """
    built: list[ExtensionT] = []
    for row, unique_id, device_identifier in adoptable(snapshot, device_registry, entity_registry):
        if resolve_platform(entity_registry, unique_id, row.datatype) is not platform:
            continue
        key = extension_curation_key(row.subject, row.path)
        context = RowContext(platform=platform, datatype=row.datatype, unit=row.unit)
        record = None if key is None else overlay.for_row(key, context)
        built.append(
            entity_class(
                coordinator, unique_id, row, device_identifier=device_identifier, record=record
            )
        )
    return built


def subject_key(subject: ExtensionSubject) -> str:
    """Return the wire device a subject names, as one string.

    What the cap counts. Not the device *card*: `panel`, every circuit and both
    lugs render on the panel's card, so counting per card would pool thirty-five
    wire devices against one allowance -- two vendor properties on each circuit
    of a 32-circuit panel would truncate with no misbehaving publisher anywhere.
    """
    return (
        subject.kind if subject.instance_key is None else f"{subject.kind}:{subject.instance_key}"
    )


def adoptable(
    snapshot: SpanPanelSnapshot,
    device_registry: DeviceRegistry,
    entity_registry: EntityRegistry,
) -> list[tuple[ExtensionProperty, str, str]]:
    """Every extension property that can become an entity, with its id and card.

    Three reasons a declared property is declined here, all of them stated rather
    than silent: its subject resolves to no device card, its card is not in the
    registry yet, or its address is outside the Homie charset. The first two are
    ordinary states on a setup that raced a capability -- the entity appears on
    the next reload, as capability-gated platforms already do.

    **An id the registry already holds is never displaced by the cap.** The cap
    admits rows in the order the adapter emitted them, and that order tracks the
    wire: a firmware update declaring a new property earlier in a description
    shifts everything after it. Capping on arrival order alone would therefore
    let a *new* property evict a standing entity -- whose registry row is
    permanent, and for which nothing here would ever build an entity again, so
    it would read unavailable forever while a stranger took its slot. Nothing
    migrates in this design, so there would be no recovery. Two passes instead:
    everything already registered is admitted first, and the cap applies only to
    what is new.
    """
    return _partition(snapshot, device_registry, entity_registry)[0]


def declined_extensions(
    snapshot: SpanPanelSnapshot,
    device_registry: DeviceRegistry,
    entity_registry: EntityRegistry,
) -> dict[str, int]:
    """How many properties each wire device declared beyond the cap.

    Separate from `adoptable` so the overflow is reported once at setup rather
    than once per platform: both platforms build their share from the same
    partition, and a warning per platform would double-count in the log while
    saying nothing new.
    """
    return _partition(snapshot, device_registry, entity_registry)[1]


def _partition(
    snapshot: SpanPanelSnapshot,
    device_registry: DeviceRegistry,
    entity_registry: EntityRegistry,
) -> tuple[list[tuple[ExtensionProperty, str, str]], dict[str, int]]:
    """Split the declared properties into what is adopted and what the cap declined."""
    known: list[tuple[ExtensionProperty, str, str]] = []
    fresh: list[tuple[ExtensionProperty, str, str]] = []
    for row in snapshot.extension_properties:
        identifier = extension_device_identifier(snapshot.serial_number, row.subject)
        if identifier is None:
            continue
        if device_registry.async_get_device(identifiers={(DOMAIN, identifier)}) is None:
            _LOGGER.debug(
                "Extension property %s has no registered device for %s yet; deferred to the next reload",
                row.path,
                identifier,
            )
            continue
        unique_id = extension_unique_id(
            snapshot.serial_number, row.subject, row.node_id, row.property_id
        )
        if unique_id is None:
            continue
        registered = any(
            entity_registry.async_get_entity_id(platform.value, DOMAIN, unique_id) is not None
            for platform in (Platform.SENSOR, Platform.BINARY_SENSOR)
        )
        (known if registered else fresh).append((row, unique_id, identifier))

    per_subject: dict[str, int] = {}
    for row, _unique_id, _identifier in known:
        key = subject_key(row.subject)
        per_subject[key] = per_subject.get(key, 0) + 1

    adoptable_rows = list(known)
    declined: dict[str, int] = {}
    for row, unique_id, identifier in fresh:
        key = subject_key(row.subject)
        if per_subject.get(key, 0) >= MAX_PER_DEVICE:
            declined[key] = declined.get(key, 0) + 1
            continue
        per_subject[key] = per_subject.get(key, 0) + 1
        adoptable_rows.append((row, unique_id, identifier))
    return adoptable_rows, declined


_OVERFLOW_NOTICE: Final = "extension_overflow"

_OVERFLOW_FALLBACK: Final[dict[str, str]] = {
    "title": "SPAN Panel: some vendor readings were not added",
    "body": (
        "A device on your panel declares more vendor readings than this integration will add "
        "for one device ({limit}). The rest were left out: {devices}.\n\n"
        "Nothing you already have is affected, and nothing is broken. The readings that were "
        "left out are still listed in this integration's diagnostics download, which is what "
        "to attach if you want them surfaced."
    ),
}
"""English text for the overflow notice, used when a translation cannot be read.

Carried here rather than only in `strings.json` for the reason `additions`
documents: an unreadable file should cost the translation, never the notice --
and a silently truncated surface is exactly the thing that must not be silent.
"""


async def async_notice_declined_extensions(
    hass: HomeAssistant,
    entry: ConfigEntry,
    snapshot: SpanPanelSnapshot,
    device_registry: DeviceRegistry,
    entity_registry: EntityRegistry,
) -> None:
    """Tell the user once when the cap left vendor readings out, or say nothing.

    A durable notice rather than a log line, because the alternative is a
    truncation the user cannot see: an entity list showing sixty of a device's
    eighty readings looks exactly like a device with sixty readings. Raised once
    at setup rather than from `adoptable`, which both platforms call.

    **Once, not once per setup.** The overflow is re-derived from the same wire
    every time this runs, so raising it through `async_raise` put the notice back
    on screen after every restart and reload -- for as long as the publisher kept
    publishing, which is forever. `notices.py` says in its own opening paragraphs
    that a notice stands until it is dismissed; one that ignores the dismissal is
    not a notice, and this integration would have taught its users to ignore the
    category.

    The remembered fingerprint is the rendered device/count list rather than the
    message, so a *worse* truncation later -- another device overflowing, or more
    readings declined on the same one -- is news the user is told about, while a
    translation change is not.
    """
    declined = declined_extensions(snapshot, device_registry, entity_registry)
    if not declined:
        return
    rendered = ", ".join(f"{key} ({count})" for key, count in sorted(declined.items()))
    text = await hass.async_add_executor_job(
        read_translations, hass.config.language, _OVERFLOW_NOTICE
    )
    raised = async_raise_on_change(
        hass,
        entry,
        _OVERFLOW_NOTICE,
        title=text.get("title") or _OVERFLOW_FALLBACK["title"],
        message=(text.get("body") or _OVERFLOW_FALLBACK["body"]).format(
            limit=MAX_PER_DEVICE, devices=rendered
        ),
        fingerprint=rendered,
    )
    # Logged on the same decision as the notice, for the same reason. A WARNING
    # per setup for a condition the user has already answered is the log's
    # version of an un-dismissable notice, and it is the line a maintainer would
    # read as "this just happened".
    _LOGGER.log(
        logging.WARNING if raised else logging.DEBUG,
        "Vendor readings beyond the per-device limit of %d were not adopted: %s",
        MAX_PER_DEVICE,
        rendered,
    )
