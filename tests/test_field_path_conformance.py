"""Every declared field path must be producible by each adapter, or derived.

This is the test that would have caught the battery.product_name drift.

A read that no adapter, or only one, produces cannot satisfy that gate, so it is
exempted in `RESIDUAL_EXEMPT_PATHS`. The second half of this module holds those
exemptions to the same standard: each one states which adapters produce it, and
that statement is checked against the adapters rather than left as prose.
"""

from __future__ import annotations

import ast
from collections import Counter
from collections.abc import Callable, Iterator
import pathlib

import pytest
from span_panel_api.models import FieldMetadata
from span_panel_api_schema_0.adapter import SchemaZeroAdapter
from span_panel_api_schema_1.adapter import SchemaOneAdapter

from custom_components.span_panel import field_paths as field_paths_module, sensor_panel
from custom_components.span_panel.field_paths import (
    RESIDUAL_EXEMPT_PATHS,
    Producibility,
    declared_field_paths,
    residual_field_paths,
)
from tests.adapter_fixtures import schema_one_metadata, schema_zero_metadata

MetadataFn = Callable[[], dict[str, FieldMetadata]]

_ADAPTERS: list[tuple[str, MetadataFn]] = [
    ("schema_0", schema_zero_metadata),
    ("schema_1", schema_one_metadata),
]

_PACKAGE_ROOT = pathlib.Path(field_paths_module.__file__).parent


def _source_declared_paths() -> dict[str, str]:
    """Every ``field_path="..."`` literal in the integration source, by module.

    Read from the source text rather than from the platform collections, so the
    hand-written tuple `declared_field_paths` iterates is pinned against what
    the modules actually declare. A runtime scan of module attributes could not
    do this: most descriptions are inline literals inside their collection, so
    emptying the collection would hide them from the check as well as from the
    gate.
    """
    found: dict[str, str] = {}
    for source in sorted(_PACKAGE_ROOT.rglob("*.py")):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg != "field_path":
                    continue
                # Non-literal values are pass-throughs that copy an existing
                # description's declaration, not new declarations.
                if isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
                    found.setdefault(keyword.value.value, source.name)
    return found


@pytest.mark.parametrize(("adapter", "metadata_fn"), _ADAPTERS)
def test_every_declared_path_is_producible(adapter: str, metadata_fn: MetadataFn) -> None:
    metadata = metadata_fn()
    missing = sorted(path for path in declared_field_paths() if path not in metadata)
    assert not missing, (
        f"{adapter} does not produce declared field paths: {missing}. "
        "Either the declaration is stale, or the entity should declare a DerivedReason."
    )


def test_gate_covers_every_declaration_in_the_source() -> None:
    """A declaration the gate stops iterating must fail, not shrink silently.

    `test_every_declared_path_is_producible` is monotone: a smaller declared set
    always passes, because any subset of a producible set is producible. So
    dropping a platform collection from `declared_field_paths`, or letting a
    description fall out of its collection, would silently retire the gate for
    every entity involved with no signal at all — the same invisible-omission
    failure this whole module exists to prevent, one level up.

    This is that signal. It compares against the source text, so it holds even
    when a collection is emptied rather than unreferenced.
    """
    declared = declared_field_paths()
    # A `SCHEMA_CONDITIONAL_FIELD` description names its source field too, and
    # that path is by definition one adapter short of this gate. It is covered
    # instead by `RESIDUAL_EXEMPT_PATHS`, whose annotation is checked against
    # both adapters below — so being enumerated there is the alternative to
    # being in `declared`, not an escape from being checked.
    uncovered = sorted(
        (path, module)
        for path, module in _source_declared_paths().items()
        if path not in declared and path not in RESIDUAL_EXEMPT_PATHS
    )
    assert not uncovered, (
        "declared_field_paths() does not cover field paths declared in the source: "
        f"{uncovered}. A platform collection likely stopped being iterated, or a "
        "description fell out of its collection — the gate is no longer checking "
        "those entities."
    )


def _iter_source_residuals() -> Iterator[tuple[str, str]]:
    """Yield ``(field_path, module_filename)`` for every residual literal in the source.

    The runtime counterpart, `residual_field_paths()`, unions the class
    attribute over a `SpanPanelEntity.__subclasses__()` walk, and a walk sees
    only what has been imported. This reads the same declarations out of the
    source text, where importedness is not a factor.

    A path declared by two modules yields twice, deliberately: which *paths*
    exist and which *modules* declare one are different questions, and collapsing
    to the first module that mentions a path answers the second wrongly.
    """
    for source in sorted(_PACKAGE_ROOT.rglob("*.py")):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            targets: list[ast.expr]
            if isinstance(node, ast.AnnAssign):
                targets = [node.target]
            elif isinstance(node, ast.Assign):
                targets = list(node.targets)
            else:
                continue
            if not any(
                isinstance(target, ast.Name) and target.id == "_residual_field_paths"
                for target in targets
            ):
                continue
            if not isinstance(node.value, ast.Tuple):
                continue
            for element in node.value.elts:
                if isinstance(element, ast.Constant) and isinstance(element.value, str):
                    yield element.value, source.name


def _source_residual_paths() -> dict[str, str]:
    """Every residual path in the integration source, against a module declaring it."""
    found: dict[str, str] = {}
    for path, module in _iter_source_residuals():
        found.setdefault(path, module)
    return found


def _source_residual_modules() -> set[str]:
    """Every integration module that declares at least one residual path."""
    return {module.removesuffix(".py") for _, module in _iter_source_residuals()}


def test_source_residuals_match_the_subclass_walk() -> None:
    """A residual the walk cannot see must fail, not vanish.

    `residual_field_paths()` imports the platform modules that declare
    residuals so their classes exist to be walked. A class declaring one in a
    module it does not import — a new platform, or an existing one that stops
    being imported — would drop out of the producible gate and out of the
    Repair's affected-entity count with no signal at all: the derived set would
    simply be smaller, and every check downstream of it is monotone in exactly
    the direction that hides the loss.

    This is that signal, read from the source text where import order does not
    apply. The converse direction matters too: a residual assembled at runtime
    rather than written as a tuple literal is invisible to this scan, so the
    scan would silently stop pinning it.
    """
    from_source = _source_residual_paths()
    from_walk = residual_field_paths()

    unwalked = sorted(
        (path, module) for path, module in from_source.items() if path not in from_walk
    )
    assert not unwalked, (
        f"residual paths declared in the source but not reached by the subclass walk: "
        f"{unwalked}. `residual_field_paths()` does not import the declaring module, so "
        "the producible gate and the Repair's affected-entity count both miss these reads."
    )

    unscanned = sorted(from_walk - from_source.keys())
    assert not unscanned, (
        f"residual paths the source scan cannot see: {unscanned}. They are not written "
        "as string literals in a `_residual_field_paths` tuple, so this pin no longer "
        "covers them — declare them literally."
    )


def _walk_imported_modules() -> set[str]:
    """Return the sibling modules `residual_field_paths()` imports for its walk.

    Read out of the source rather than by calling the function, because what is
    under test is the import list itself: a module missing from it still gets
    walked in-process whenever some *other* importer has already pulled it in,
    which is true of every module in the test suite and is exactly why the walk
    test below cannot see the omission.
    """
    tree = ast.parse((_PACKAGE_ROOT / "field_paths.py").read_text(encoding="utf-8"))
    function = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "residual_field_paths"
    )
    return {
        alias.name
        for node in ast.walk(function)
        if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module is None
        for alias in node.names
    }


def test_the_residual_walk_imports_exactly_the_modules_that_declare_one() -> None:
    """The walk's import list, pinned against the declarations it exists to reach.

    `residual_field_paths()` walks `SpanPanelEntity.__subclasses__()`, which sees
    only classes Python has already imported, so it imports the declaring
    platform modules itself. Nothing held that list to the declarations: a module
    dropped from it goes on being walked under pytest, where the platform modules
    are imported many times over for other reasons, and fails only in production
    where `field_paths` may be reached first. A path that leaves the walk leaves
    the producible gate and the Repair's affected-entity count with it, silently.

    Both directions. An unlisted module is the failure above; a listed module
    that declares nothing is a stale import, which is how the list stops meaning
    what its docstring says and starts being copied forward unread.
    """
    declaring = _source_residual_modules()
    imported = _walk_imported_modules()

    assert declaring, "the source scan found no residual declarations at all"
    assert imported == declaring, (
        f"`residual_field_paths()` imports {sorted(imported)} for its subclass walk but "
        f"residuals are declared in {sorted(declaring)}. Unlisted modules drop out of the "
        "producible gate in any process that reaches `field_paths` first; listed modules "
        "that declare nothing are stale."
    )


@pytest.mark.parametrize(("adapter", "metadata_fn"), _ADAPTERS)
def test_gate_is_one_directional(
    adapter: str, metadata_fn: MetadataFn, capsys: pytest.CaptureFixture[str]
) -> None:
    """A produced path nothing reads must NOT fail the build.

    Additions are legal within a major version, so asserting the converse would
    turn correct upstream behaviour into a red CI every time SPAN ships a
    property. This test exists to stop someone "completing" the gate by adding
    that assertion.
    """
    metadata = metadata_fn()
    unread = sorted(set(metadata) - set(declared_field_paths()))
    # The CI inventory the spec asks for: never asserted against, an addition is
    # legal within a major version. Written with capture suspended so it reaches
    # the build log under the plain `pytest` CI runs -- pytest swallows stdout
    # from a passing test, which would leave the inventory existing in code and
    # nowhere else.
    with capsys.disabled():
        print(f"\n[{adapter}] produced but unread ({len(unread)}):")
        for path in unread:
            print(f"  {path}")


def _adapter_paths() -> tuple[frozenset[str], frozenset[str]]:
    """Return the paths each adapter actually produces, from the vendored fixtures."""
    return frozenset(schema_zero_metadata()), frozenset(schema_one_metadata())


def _observed_producibility(
    path: str, s0: frozenset[str], s1: frozenset[str]
) -> Producibility | None:
    """Classify a path by which adapters produce it.

    `None` is the fourth, illegal case — produced by both — which has no
    `Producibility` member precisely because such a path is not exemptible.
    """
    match (path in s0, path in s1):
        case (True, True):
            return None
        case (True, False):
            return Producibility.SCHEMA_0_ONLY
        case (False, True):
            return Producibility.SCHEMA_1_ONLY
        case _:
            return Producibility.NEITHER


def test_every_exempt_path_matches_its_annotation() -> None:
    """Each exemption's stated reason is checked against the adapters that run.

    Before these annotations the 26 exemptions were checked against nothing:
    the neither/one-adapter distinction lived in prose, so an entry could be
    mislabelled from the day it was written, or go stale when the library
    changed what it publishes, with no signal anywhere.
    """
    s0, s1 = _adapter_paths()
    wrong = [
        (path, annotated.name, observed.name if observed else "BOTH")
        for path, annotated in RESIDUAL_EXEMPT_PATHS.items()
        if (observed := _observed_producibility(path, s0, s1)) is not annotated
    ]
    assert not wrong, (
        "RESIDUAL_EXEMPT_PATHS annotations disagree with the adapters "
        f"(path, annotated, actual): {wrong}. Either the annotation is stale or "
        "the library changed what it produces."
    )


def test_no_exempt_path_is_producible_by_both() -> None:
    """A path both adapters produce is a declaration, not an exemption.

    The exemption exists only because the gate demands producibility by both.
    Once both produce it, the reason has evaporated and the path should be
    declared — silently leaving it exempt would retire the gate for that read.
    """
    s0, s1 = _adapter_paths()
    promotable = sorted(RESIDUAL_EXEMPT_PATHS.keys() & s0 & s1)
    assert not promotable, (
        f"exempt paths are now producible by both adapters: {promotable}. "
        "Promote each to a declaration — a description's `field_path=`, or an "
        "entity's `_residual_field_paths` for a reader in entity code — so the "
        "producible gate covers it again."
    )


_EXPECTED_EXEMPT_COUNTS: dict[Producibility, int] = {
    # +3 with the shed forecast: the two full-charge refinements and the
    # confidence enum, read as attributes on the two forecast sensors and
    # carried by no adapter's metadata map.
    # +1 for `mid.grid_state`, the `mid_grid_state` sensor's source field. Like
    # `panel.dominant_power_source` below it was read by a description and
    # enumerated nowhere, so nothing held it against the adapters and
    # `evaluate_field_metadata` had no way to tell it from an unread field.
    # +15 with the PCS: the twelve arbitration inputs and `pcs.enabled` behind
    # `pcs_import_limit`'s attributes, plus the two circuit participation fields
    # read as attributes on the circuit power sensor. schema_1 reads all fifteen
    # and maps none of them, deliberately — they explain the effective limit
    # rather than being readings of their own.
    # +3 for the enclosure's own build identity -- `panel.vendor_name`,
    # `panel.model`, `panel.hardware_version` -- read by `snapshot_to_device_info`
    # for the panel's device card. Flat declares none of the three, and a
    # schema_1 row exists to carry a unit and a datatype for a reading, which an
    # identity string is not; the `mid.*` device-card reads sit here for the
    # same reason.
    # +4 for the shed policy -- the raw `shed/policy` document plus the
    # algorithm and the two SoC thresholds parsed out of it -- read as
    # attributes on `dsm_state`. Flat has no `shed` node, and a JSON document
    # has no unit surface for a schema_1 row to describe.
    # +2 for the EVSE charge-current control's two non-readings:
    # `charge_current_limit_settable`, the `$settable` flag `number.py` gates
    # entity creation on, and `charge_current_limit_target_a`, the Homie
    # `$target` echo it renders as an attribute. Facts about a command rather
    # than readings, so no adapter carries a row for either -- the same shape as
    # the `circuit.*_target` pair.
    Producibility.NEITHER: 43,
    # +1 for `panel.dominant_power_source`, the `grid_forming_entity` sensor's
    # source field. It was read by a `SCHEMA_CONDITIONAL_FIELD` description and
    # enumerated nowhere, so `evaluate_field_metadata` counted it as produced-
    # but-unread while an entity was reading it.
    # -1 for `panel.wifi_ssid`, which left this map entirely: schema_1 grew the
    # `status/wifi-ssid` row, both adapters produce the path, and
    # `test_no_exempt_path_is_producible_by_both` demanded it become a
    # declaration -- `SpanPanelStatus._residual_field_paths`. Its time here as a
    # true `SCHEMA_0_ONLY` annotation is what sanctioned a flat -> v1.0
    # regression: the attribute a flat panel filled, a v1.0 panel did not.
    # +1 for `pv.software_version`, the firmware row on the solar inverter's own
    # device card, which moved here from `NEITHER`. It was annotated on the
    # claim that flat's `pv` device class declares no firmware version; flat
    # declares `software-version` on it, and the library grew the mapping row
    # once a producer valued the v1.0 half. schema_1 still carries no row -- a
    # version string is identity rather than a reading, the same argument as the
    # `mid.*` and `panel.*` card reads above -- so this stays an exemption
    # rather than becoming a declaration.
    Producibility.SCHEMA_0_ONLY: 11,
    # +2 with the shed forecast: the two live estimates, which schema_1 maps and
    # flat firmware does not publish at all.
    # +2 for `battery.power_w` and `battery.communication_state`, the BESS's own
    # meter and link health behind `bess_meter_power` and
    # `bess_communication_state`. schema_1 maps both; flat's BESS device class
    # declares neither property, so neither can ever satisfy the both-adapters
    # gate.
    # +3 for the PCS's result: `pcs.import_limit_a`, `pcs.binding_constraint`
    # and `pcs.active`, behind the two PCS sensors and the `pcs_active` binary
    # sensor. schema_1 maps all three; no flat panel declares the capability at
    # all, so none can ever satisfy the both-adapters gate.
    # +2 for `pv.connected` and `evse.connected`, the enclosure's view of the
    # link to each circuit-fed DER, behind `pv_panel_link` and
    # `evse_panel_link`. schema_1 maps both from one property — the feeding
    # circuit's `connection/feeds-device-status` — while flat publishes
    # `connected` on the BESS and on no other device class, so neither can
    # satisfy the both-adapters gate.
    # +2 for the EVSE charge-current pair behind the
    # `evse_charge_current_limit` number: the settable limit its description
    # names, and the commissioned ceiling the entity reads for
    # `native_max_value`. schema_1 resolves both from the charger's own
    # `$description`; flat's `evse` type declares no settable ceiling at all, so
    # neither can satisfy the both-adapters gate.
    Producibility.SCHEMA_1_ONLY: 12,
}
"""The exemption inventory, by reason. See `test_exempt_inventory_is_complete`."""


def test_exempt_inventory_is_complete() -> None:
    """An exemption disappearing must be a deliberate edit, not an accident.

    Every other check here is consistency-only: deleting an entry leaves the
    survivors perfectly annotated, so the set could quietly shrink and no test
    would notice — the read would simply stop being enumerated anywhere. The
    completeness of the set cannot be derived (a `Producibility.NEITHER` path
    has, by definition, no adapter row to discover it from, and matching reads
    by leaf attribute name collides across snapshot types), so it is pinned by
    size per reason instead. Changing a count is fine; doing it in the same
    commit as the entry, with a reason, is the point.
    """
    counts = Counter(RESIDUAL_EXEMPT_PATHS.values())
    assert counts == _EXPECTED_EXEMPT_COUNTS, (
        f"exemption inventory changed: {counts} != {_EXPECTED_EXEMPT_COUNTS}. An "
        "entry was added or removed; update the expected counts in the same "
        "commit if that was intended."
    )


def test_every_exempt_path_still_has_a_reader() -> None:
    """An exemption outlives its reader silently; the list only ever grows.

    Heuristic by necessity — an exempt path is read outside any description, so
    there is no declaration to match against, only source text. Matching the
    leaf attribute anywhere in the package is deliberately generous: it cannot
    accuse a live read, and it still catches the last reader of a field being
    deleted while its exemption stays behind.
    """
    read_attributes = {
        node.attr
        for source in sorted(_PACKAGE_ROOT.rglob("*.py"))
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"), filename=str(source)))
        if isinstance(node, ast.Attribute)
    }
    unread = sorted(
        path for path in RESIDUAL_EXEMPT_PATHS if path.split(".", 1)[1] not in read_attributes
    )
    assert not unread, (
        f"exempt paths no longer read anywhere in the package: {unread}. The "
        "reader was removed; drop the exemption with it."
    )


def test_the_service_entrance_gate_names_the_installed_adapter_keys() -> None:
    """The one adapter fact this integration states outside `RESIDUAL_EXEMPT_PATHS`.

    `sensor_panel._SERVICE_ENTRANCE_ADAPTER` says which adapter resolves
    `lugs_at_service_entrance`, and the grid power sensor publishes its
    `at_service_entrance` attribute only when that adapter is the one running.
    It is here for the reason the exemption annotations are: a statement about
    what an adapter produces belongs against the adapters, not in prose.

    Without this the failure is silent and one-directional. A renamed adapter key
    matches nothing, every panel looks like an adapter that does not resolve the
    field, and the attribute simply stops appearing -- no error, no unavailable
    entity, nothing in the log. The `!=` half matters just as much: the constant
    naming the *flat* key would publish the library's `True` default on every
    flat panel, which is the defect this gate was added to fix.

    Read off the adapter classes rather than off a live client because the class
    attribute is what a client reports (`SpanMqttClient.schema_major` returns
    `self._adapter.schema_major`), and a class needs no MQTT connection.
    """
    assert SchemaOneAdapter.schema_major == sensor_panel._SERVICE_ENTRANCE_ADAPTER, (
        f"the parent/child adapter's key is {SchemaOneAdapter.schema_major!r} but the "
        f"grid sensor gates on {sensor_panel._SERVICE_ENTRANCE_ADAPTER!r}; "
        "at_service_entrance would silently stop being published"
    )
    assert SchemaZeroAdapter.schema_major != sensor_panel._SERVICE_ENTRANCE_ADAPTER, (
        "the grid sensor gates on the flat adapter's key, which would publish the "
        "library's default as a reading on every flat panel"
    )
