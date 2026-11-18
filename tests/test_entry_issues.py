"""Tests for the Repairs an entry raises: their ids, their link to the entry, and removal.

Home Assistant deletes an issue linked to an entry when the entry is removed,
dismissal included, and hides it while the entry is disabled. These tests hold
the integration to linking every issue it raises, and to sweeping the unlinked
ones an older release left, which Home Assistant never deletes.
"""

from __future__ import annotations

import ast
from collections.abc import Callable
import itertools
import logging
from pathlib import Path
import re
from typing import Final
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import ConfigEntryDisabler
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er, issue_registry as ir
from homeassistant.setup import async_setup_component
from homeassistant.util.ulid import ulid_now
from homeassistant.util.uuid import random_uuid_hex
import pytest
from pytest_homeassistant.common import MockConfigEntry
from pytest_homeassistant.typing import WebSocketGenerator
from span_panel_api import FieldMetadata, LeafNameMismatch
from span_panel_api.exceptions import SpanPanelCAChangedError

from custom_components.span_panel import async_setup_entry
from custom_components.span_panel.ca_repairs import (
    async_raise_ca_changed,
    async_raise_ca_unusable,
    async_raise_rest_tls_untrusted,
    ca_changed_issue_id,
)
from custom_components.span_panel.circuit_device_conversion import (
    ConversionReport,
    ConvertedEntity,
)
from custom_components.span_panel.const import (
    CONF_API_VERSION,
    CONF_EBUS_BROKER_HOST,
    CONF_EBUS_BROKER_PASSWORD,
    CONF_EBUS_BROKER_PORT,
    CONF_EBUS_BROKER_USERNAME,
    CONF_HTTP_PORT,
    DOMAIN,
)
from custom_components.span_panel.conversion_repairs import (
    async_raise_breaker_switch_area_notice,
    async_raise_conversion_notice,
)
from custom_components.span_panel.coordinator import SpanPanelCoordinator
from custom_components.span_panel.entry_issues import EntryIssue, EntryIssueFamily
from custom_components.span_panel.flat_firmware_repairs import (
    async_raise_flat_firmware,
    flat_firmware_issue_id,
)
from custom_components.span_panel.hardware_repairs import (
    RefusedHardware,
    async_raise_unvalidated_hardware,
)
from custom_components.span_panel.leaf_repairs import async_raise_leaf_name_mismatch
from custom_components.span_panel.schema_repairs import async_sync_schema_issues
from custom_components.span_panel.schema_validation import SchemaFindings, UnitMismatch

from .conftest import recording_unlinked_issues
from .factories import SpanPanelSnapshotFactory

_PACKAGE: Final = Path(__file__).resolve().parent.parent / "custom_components" / "span_panel"

_RETIRED: Final[frozenset[EntryIssue | EntryIssueFamily]] = frozenset(
    {EntryIssue.RETIRED_EBUS_UPGRADE, EntryIssueFamily.RETIRED_NEW_ENTITIES}
)
"""The kinds nothing raises any more, named only so what untagged builds raised is still cleared."""

_MOVED: Final = LeafNameMismatch(host="192.168.1.100", leaf_names=("span-panel.local",))
_NAMELESS: Final = LeafNameMismatch(host="192.168.1.100", leaf_names=())


def _entry(hass: HomeAssistant, entry_id: str) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        entry_id=entry_id,
        title=f"SPAN Panel {entry_id}",
        unique_id=f"sp3-{entry_id}",
    )
    entry.add_to_hass(hass)
    return entry


def _issues(hass: HomeAssistant) -> list[ir.IssueEntry]:
    return [issue for issue in ir.async_get(hass).issues.values() if issue.domain == DOMAIN]


def _kind_of(issue_id: str, entry_id: str) -> EntryIssue | EntryIssueFamily | None:
    for kind in EntryIssue:
        if issue_id == kind.issue_id(entry_id):
            return kind
    for family in EntryIssueFamily:
        if issue_id.startswith(family.entry_prefix(entry_id)):
            return family
    return None


def _every_kind_s_id(entry_id: str) -> list[str]:
    """One id of every kind for one entry, retired kinds included."""
    return [
        *(kind.issue_id(entry_id) for kind in EntryIssue),
        *(family.issue_id(entry_id, "key") for family in EntryIssueFamily),
    ]


def _raise_unlinked(hass: HomeAssistant, issue_id: str, *, persistent: bool) -> None:
    """Raise an issue as a release that did not link issues to their entry raised it."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        is_persistent=persistent,
        severity=ir.IssueSeverity.WARNING,
        translation_key="unlinked",
    )


# ---------- the ids ----------


def test_every_kind_keeps_the_id_it_was_released_with() -> None:
    """A dismissal is stored under its issue's id, and the sweep finds an older release's issues by it.

    Neither survives an id that changes.
    """
    assert {kind.name: kind.value for kind in EntryIssue} == {
        "UNVALIDATED_HARDWARE": "unvalidated_hardware_",
        "FLAT_FIRMWARE": "flat_firmware_",
        "CA_CHANGED": "panel_ca_changed_",
        "CA_UNUSABLE": "panel_ca_unusable_",
        "REST_TLS_UNTRUSTED": "panel_rest_tls_untrusted_",
        "LEAF_NAME_MISMATCH": "panel_leaf_name_mismatch_",
        "CIRCUIT_DEVICES_CONVERTED": "circuit_devices_converted_",
        "RETIRED_EBUS_UPGRADE": "panel_upgraded_to_ebus_v1_",
    }
    assert {family.name: family.value for family in EntryIssueFamily} == {
        "UNRESOLVED_FIELD": "unresolved_",
        "UNIT_MISMATCH": "unit_mismatch_",
        "BREAKER_SWITCH_IN_AREA": "breaker_switch_in_area_",
        "RETIRED_NEW_ENTITIES": "new_entities_",
    }
    assert EntryIssue.CA_CHANGED.issue_id("entry-1") == "panel_ca_changed_entry-1"
    assert (
        EntryIssueFamily.UNRESOLVED_FIELD.issue_id("entry-1", "circuit.instant_power_w")
        == "unresolved_entry-1_circuit.instant_power_w"
    )


def test_no_kind_s_ids_start_with_another_kind_s_prefix() -> None:
    """Issues are told apart by prefix, so no prefix may start another.

    The fix flows are chosen by prefix, and the schema reconcile deletes every
    issue its prefixes match that it did not raise again.
    """
    prefixes: list[EntryIssue | EntryIssueFamily] = [*EntryIssue, *EntryIssueFamily]
    for one, other in itertools.permutations(prefixes, 2):
        assert not other.value.startswith(one.value), (one, other)


def test_no_entry_id_home_assistant_creates_holds_an_underscore() -> None:
    """A family's ids close the entry's part with an underscore, which an entry id never holds.

    Home Assistant creates a ULID, and created 32 hexadecimal digits before.
    """
    assert re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{26}", ulid_now())
    assert re.fullmatch(r"[0-9a-f]{32}", random_uuid_hex())
    assert "_" not in MockConfigEntry(domain=DOMAIN).entry_id


def test_an_entry_id_holding_an_underscore_would_share_another_entry_s_family_ids() -> None:
    """What the rule above protects: every family id of `panel_2` starts with `panel`'s prefix.

    The removal sweep and the schema reconcile of entry `panel` would then take
    entry `panel_2`'s issues too. A kind of one issue per entry is matched
    whole, so only the families depend on it.
    """
    for family in EntryIssueFamily:
        assert family.issue_id("panel_2", "key").startswith(family.entry_prefix("panel"))


# ---------- raising ----------


async def test_every_kind_raised_is_linked_to_its_entry(hass: HomeAssistant) -> None:
    """Each place an issue is raised links it, so Home Assistant owns its lifetime.

    Every kind is raised the way the integration raises it, and every kind not
    retired must be among them: a new kind has to be added here to pass.
    """
    entry = _entry(hass, "entry-linked")
    row = er.async_get(hass).async_get_or_create(
        "sensor", DOMAIN, "uid-new", config_entry=entry, original_name="New"
    )
    findings = SchemaFindings(
        frozenset({"circuit.instant_power_w"}),
        (UnitMismatch("panel.l1_voltage", "V", "kV"),),
        frozenset(),
    )
    affected = {"circuit.instant_power_w": ["sensor.a"], "panel.l1_voltage": ["sensor.v"]}
    # The name mismatch is raised after the CA-changed and untrusted-TLS Repairs,
    # because each of those takes a standing one down.
    raisers: tuple[Callable[[], None], ...] = (
        lambda: async_raise_unvalidated_hardware(
            hass, entry, RefusedHardware(version="9.9", model="OTHER_MODEL")
        ),
        lambda: async_raise_unvalidated_hardware(
            hass, entry, RefusedHardware(version="9.9", model=None)
        ),
        lambda: async_raise_flat_firmware(hass, entry),
        lambda: async_raise_ca_changed(hass, entry, "aa" * 32, "bb" * 32),
        lambda: async_raise_ca_unusable(hass, entry, "unreadable"),
        lambda: async_raise_rest_tls_untrusted(hass, entry, "192.168.1.100", 443, "cc" * 32),
        lambda: async_raise_leaf_name_mismatch(hass, entry, _NAMELESS),
        lambda: async_raise_leaf_name_mismatch(hass, entry, _MOVED),
        lambda: async_raise_conversion_notice(
            hass, entry, ConversionReport((ConvertedEntity(row.entity_id, "Old"),), (), (), ())
        ),
        lambda: async_raise_breaker_switch_area_notice(
            hass,
            entry,
            switch_entity_id="switch.kitchen_breaker",
            switch_unique_id="span_sp3-entry-linked_relay_1",
            area_name="Kitchen",
        ),
        lambda: async_sync_schema_issues(hass, entry, findings, affected),
    )

    raised: set[EntryIssue | EntryIssueFamily | None] = set()
    for raise_one in raisers:
        raise_one()
        for issue in _issues(hass):
            assert issue.config_entry_id == entry.entry_id, issue.issue_id
            raised.add(_kind_of(issue.issue_id, entry.entry_id))

    assert raised == {*EntryIssue, *EntryIssueFamily} - _RETIRED


_ISSUE_REGISTRY: Final = "homeassistant.helpers.issue_registry"
_CORE_CREATES: Final = frozenset({"async_create_issue", "create_issue"})
_REGISTRY_CREATE: Final = "async_get_or_create"


def _dotted(node: ast.expr) -> str | None:
    """Return a name or a chain of attributes as dotted text, or None for anything else."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute) and (base := _dotted(node.value)) is not None:
        return f"{base}.{node.attr}"
    return None


def _issue_registry_names(tree: ast.Module) -> tuple[set[str], set[str], list[str]]:
    """Return the module's spellings of the issue registry module and of its `async_get`.

    Also returns the imports that bring in one of Core's create functions
    under any name, which are a way round the rule by themselves.
    """
    modules = {_ISSUE_REGISTRY}
    getters: set[str] = set()
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(
                alias.asname
                for alias in node.names
                if alias.name == _ISSUE_REGISTRY and alias.asname
            )
        elif isinstance(node, ast.ImportFrom) and node.module == "homeassistant":
            modules.update(
                f"{alias.asname or alias.name}.issue_registry"
                for alias in node.names
                if alias.name == "helpers"
            )
        elif isinstance(node, ast.ImportFrom) and node.module == "homeassistant.helpers":
            modules.update(
                alias.asname or alias.name for alias in node.names if alias.name == "issue_registry"
            )
        elif isinstance(node, ast.ImportFrom) and node.module == _ISSUE_REGISTRY:
            for alias in node.names:
                if alias.name in _CORE_CREATES or alias.name == "*":
                    imported.append(f"line {node.lineno}: imports {alias.name}")
                elif alias.name == "async_get":
                    getters.add(alias.asname or alias.name)
    getters.update(f"{module}.async_get" for module in modules)
    return modules, getters, imported


def _issue_creations(source: str) -> list[str]:
    """Return each place a module can raise an issue without `async_create_entry_issue`.

    Three ways round it, each under any name: importing one of Core's create
    functions from the issue registry module; referring to one of them on that
    module, called or not, since `functools.partial(ir.async_create_issue, ...)`
    is a reference; and referring to the issue registry's own
    `async_get_or_create`, on `async_get(hass)` or on a name assigned from it.

    Read statically, so what it cannot see includes `getattr`, the module
    reassigned to another name, and a registry held anywhere but a plain or
    annotated name: one bound by `:=`, kept on an attribute or in a dict, taken
    as a parameter typed `ir.IssueRegistry`, or read from
    `hass.data[ir.DATA_REGISTRY]`. The autouse `every_issue_linked` fixture in
    `conftest` covers those, on every path the suite runs: every route ends in
    `IssueRegistry.async_get_or_create`, which it watches.
    """
    tree = ast.parse(source)
    modules, getters, found = _issue_registry_names(tree)

    def is_registry(node: ast.expr) -> bool:
        return isinstance(node, ast.Call) and _dotted(node.func) in getters

    registries: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and is_registry(node.value):
            registries.update(target.id for target in node.targets if isinstance(target, ast.Name))
        elif (
            isinstance(node, ast.AnnAssign)
            and node.value is not None
            and is_registry(node.value)
            and isinstance(node.target, ast.Name)
        ):
            registries.add(node.target.id)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        if node.attr in _CORE_CREATES and _dotted(node.value) in modules:
            found.append(f"line {node.lineno}: refers to {_dotted(node)}")
        elif node.attr == _REGISTRY_CREATE and (
            is_registry(node.value)
            or (isinstance(node.value, ast.Name) and node.value.id in registries)
        ):
            found.append(f"line {node.lineno}: refers to the issue registry's {node.attr}")
    return found


def test_only_entry_issues_raises_an_issue_with_home_assistant() -> None:
    """Every issue goes through `async_create_entry_issue`, which links it.

    Raised again anywhere without the link, an issue would come unlinked, since
    Home Assistant writes the link it is given each time.
    """
    found = {
        path.relative_to(_PACKAGE).as_posix(): creations
        for path in sorted(_PACKAGE.rglob("*.py"))
        if (creations := _issue_creations(path.read_text(encoding="utf-8")))
    }

    assert set(found) == {"entry_issues.py"}, found


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            "from homeassistant.helpers import issue_registry as ir\n"
            "ir.async_create_issue(hass, 'span_panel', 'x')\n",
            id="called",
        ),
        pytest.param(
            "from functools import partial\n"
            "from homeassistant.helpers import issue_registry as ir\n"
            "raise_it = partial(ir.async_create_issue, hass, 'span_panel')\n",
            id="referred to without a call",
        ),
        pytest.param(
            "from homeassistant.helpers.issue_registry import async_create_issue as raise_it\n"
            "raise_it(hass, 'span_panel', 'x')\n",
            id="imported under another name",
        ),
        pytest.param(
            "from homeassistant.helpers.issue_registry import create_issue\n",
            id="thread-safe variant imported",
        ),
        pytest.param("from homeassistant.helpers.issue_registry import *\n", id="star import"),
        pytest.param(
            "import homeassistant.helpers.issue_registry as issues\n"
            "issues.create_issue(hass, 'span_panel', 'x')\n",
            id="module imported under another name",
        ),
        pytest.param(
            "import homeassistant.helpers.issue_registry\n"
            "homeassistant.helpers.issue_registry.async_create_issue(hass, 'span_panel', 'x')\n",
            id="module spelled out",
        ),
        pytest.param(
            "from homeassistant import helpers\n"
            "helpers.issue_registry.async_create_issue(hass, 'span_panel', 'x')\n",
            id="through the helpers package",
        ),
        pytest.param(
            "from homeassistant.helpers import issue_registry\n"
            "issue_registry.async_get(hass).async_get_or_create('span_panel', 'x')\n",
            id="registry method on async_get",
        ),
        pytest.param(
            "from homeassistant.helpers import issue_registry as ir\n"
            "registry = ir.async_get(hass)\n"
            "registry.async_get_or_create('span_panel', 'x')\n",
            id="registry method on an assigned name",
        ),
        pytest.param(
            "from homeassistant.helpers import issue_registry as ir\n"
            "registry: ir.IssueRegistry = ir.async_get(hass)\n"
            "registry.async_get_or_create('span_panel', 'x')\n",
            id="registry method on an annotated name",
        ),
        pytest.param(
            "from homeassistant.helpers.issue_registry import async_get as issues\n"
            "issues(hass).async_get_or_create('span_panel', 'x')\n",
            id="registry getter imported under another name",
        ),
    ],
)
def test_the_rule_finds_each_way_round_it(source: str) -> None:
    """The guard above is only as good as what it can see, so each bypass is shown to it."""
    assert _issue_creations(source)


@pytest.mark.unlinked_issues_allowed
async def test_the_runtime_guard_records_an_unlinked_issue_and_where_it_was_raised(
    hass: HomeAssistant,
) -> None:
    """The conftest guard catches what the AST rule cannot see, so it is shown each case too.

    Marked so the autouse guard stands aside, and the same wrapper is applied
    here by hand: an unlinked issue of this integration's is recorded with its
    id and the line that raised it, and a linked one, or another integration's,
    is not.
    """
    entry = _entry(hass, "entry-watched")
    unlinked: list[str] = []

    with patch.object(
        ir.IssueRegistry,
        "async_get_or_create",
        recording_unlinked_issues(ir.IssueRegistry.async_get_or_create, unlinked),
    ):
        _raise_unlinked(
            hass, EntryIssue.UNVALIDATED_HARDWARE.issue_id(entry.entry_id), persistent=False
        )
        async_raise_flat_firmware(hass, entry)
        ir.async_create_issue(
            hass,
            "another_integration",
            "unlinked",
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="unlinked",
        )

    assert len(unlinked) == 1, unlinked
    assert unlinked[0].startswith(
        "unvalidated_hardware_entry-watched (raised at tests/test_entry_issues.py:"
    )


def test_the_rule_leaves_reads_deletes_and_other_registries_alone() -> None:
    """Reading or deleting an issue, and another registry's `async_get_or_create`, raise nothing."""
    source = (
        "from homeassistant.helpers import entity_registry as er, issue_registry as ir\n"
        "er.async_get(hass).async_get_or_create('sensor', 'span_panel', 'uid')\n"
        "registry = ir.async_get(hass)\n"
        "registry.async_get_issue('span_panel', 'x')\n"
        "ir.async_delete_issue(hass, 'span_panel', 'x')\n"
    )

    assert _issue_creations(source) == []


# ---------- removal ----------


async def test_removal_deletes_the_entry_s_linked_issues_and_their_dismissals(
    hass: HomeAssistant,
) -> None:
    """One persistent and one not, both dismissed: Home Assistant deletes them with the entry."""
    entry = _entry(hass, "entry-removed")
    async_raise_ca_changed(hass, entry, "aa" * 32, "bb" * 32)
    async_raise_flat_firmware(hass, entry)
    registry = ir.async_get(hass)
    issue_ids = (ca_changed_issue_id(entry.entry_id), flat_firmware_issue_id(entry.entry_id))
    persistence = set()
    for issue_id in issue_ids:
        registry.async_ignore(DOMAIN, issue_id, True)
        issue = registry.async_get_issue(DOMAIN, issue_id)
        assert issue is not None
        assert issue.dismissed_version is not None
        # Linked, so the sweep leaves it alone and its deletion is Home Assistant's.
        assert issue.config_entry_id == entry.entry_id
        persistence.add(issue.is_persistent)
    assert persistence == {True, False}

    assert await hass.config_entries.async_remove(entry.entry_id) == {"require_restart": False}

    for issue_id in issue_ids:
        assert registry.async_get_issue(DOMAIN, issue_id) is None, issue_id


@pytest.mark.unlinked_issues_allowed
async def test_removal_sweeps_an_older_release_s_issues_and_leaves_another_entry_s(
    hass: HomeAssistant,
) -> None:
    """Issues raised before they were linked go with their entry too, every kind of them.

    Home Assistant never deletes an unlinked issue. The other panel's id starts
    with the removed one's, so a match on anything less than the whole entry
    part of an id would take its issues too.
    """
    removed = _entry(hass, "panel")
    kept = _entry(hass, "panel-2")
    for entry in (removed, kept):
        for index, issue_id in enumerate(_every_kind_s_id(entry.entry_id)):
            _raise_unlinked(hass, issue_id, persistent=index % 2 == 0)
            if index % 3 == 0:
                ir.async_ignore_issue(hass, DOMAIN, issue_id, True)

    assert await hass.config_entries.async_remove(removed.entry_id) == {"require_restart": False}

    registry = ir.async_get(hass)
    assert [
        issue_id
        for issue_id in _every_kind_s_id(removed.entry_id)
        if registry.async_get_issue(DOMAIN, issue_id) is not None
    ] == []
    assert [
        issue_id
        for issue_id in _every_kind_s_id(kept.entry_id)
        if registry.async_get_issue(DOMAIN, issue_id) is None
    ] == []


# ---------- a disabled entry ----------


async def test_a_disabled_entry_s_issues_are_hidden_and_kept(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """The Repairs list leaves out a disabled entry's issues, and the registry keeps them."""
    assert await async_setup_component(hass, "repairs", {})
    entry = _entry(hass, "entry-disabled")
    async_raise_flat_firmware(hass, entry)
    issue_id = flat_firmware_issue_id(entry.entry_id)
    client = await hass_ws_client(hass)

    async def listed() -> list[str]:
        await client.send_json_auto_id({"type": "repairs/list_issues"})
        reply = await client.receive_json()
        assert reply["success"] is True
        return [issue["issue_id"] for issue in reply["result"]["issues"]]

    assert issue_id in await listed()

    await hass.config_entries.async_set_disabled_by(entry.entry_id, ConfigEntryDisabler.USER)
    await hass.async_block_till_done()

    assert issue_id not in await listed()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None


# ---------- callbacks that outlive their entry ----------


def _v2_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Add an entry carrying everything `async_setup_entry` needs of a v2 panel."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_API_VERSION: "v2",
            CONF_HOST: "192.168.1.100",
            CONF_EBUS_BROKER_HOST: "span-panel.local",
            CONF_EBUS_BROKER_USERNAME: "mqtt-user",
            CONF_EBUS_BROKER_PASSWORD: "mqtt-pass",
            CONF_EBUS_BROKER_PORT: 8883,
            CONF_HTTP_PORT: 80,
        },
        entry_id="entry-outlived",
        title="sp3-outlived-001",
        unique_id="sp3-outlived-001",
    )
    entry.add_to_hass(hass)
    return entry


async def test_the_client_s_callbacks_raise_nothing_once_the_entry_is_removed(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A client whose unload failed keeps calling back after its entry is gone.

    Set up outside Home Assistant's own setup, the entry is not loaded as far as
    Home Assistant knows, so removing it unloads nothing, as after a failed
    unload: the callbacks stay registered. Home Assistant would refuse an issue
    linked to the gone entry, and the user has no panel left to act on.
    """
    entry = _v2_entry(hass)
    client = MagicMock()
    client.connect = AsyncMock()
    coordinator = MagicMock()
    coordinator.async_config_entry_first_refresh = AsyncMock()
    coordinator.package_rating_manager.async_load = AsyncMock()
    coordinator.async_setup_streaming = AsyncMock()
    coordinator.data = SpanPanelSnapshotFactory.create(serial_number="sp3-outlived-001")

    with (
        patch("custom_components.span_panel.async_register_commands"),
        patch("custom_components.span_panel.SpanMqttClient", return_value=client),
        patch("custom_components.span_panel.SpanPanelCoordinator", return_value=coordinator),
        patch("custom_components.span_panel.ensure_device_registered", AsyncMock()),
        patch.object(hass.config_entries, "async_forward_entry_setups", AsyncMock()),
        patch.object(hass.config_entries, "async_update_entry"),
    ):
        await async_setup_entry(hass, entry)
    on_mismatch = client.register_leaf_mismatch_callback.call_args.args[0]
    on_fatal = client.register_fatal_error_callback.call_args.args[0]

    assert await hass.config_entries.async_remove(entry.entry_id) == {"require_restart": False}
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="custom_components.span_panel"):
        on_mismatch(_MOVED)
        on_fatal(SpanPanelCAChangedError("aa" * 32, "bb" * 32))

    assert _issues(hass) == []
    assert caplog.records == []


async def test_a_coordinator_that_outlives_its_entry_raises_no_schema_repair(
    hass: HomeAssistant,
) -> None:
    """A validation pass that first succeeds after removal reconciles nothing."""
    entry = _entry(hass, "entry-outlived")
    client = MagicMock()
    client.field_metadata = None
    coordinator = SpanPanelCoordinator(hass, client, entry)
    coordinator.async_register_field_path_entity("circuit.instant_power_w", "sensor.kitchen_power")
    # Setup's reconcile point, with nothing validated yet.
    coordinator.async_sync_schema_repairs()

    assert await hass.config_entries.async_remove(entry.entry_id) == {"require_restart": False}
    client.field_metadata = {
        "circuit.instant_power_w": FieldMetadata(None, "unknown", resolved=False)
    }
    coordinator._run_schema_validation()

    assert coordinator.unresolved_paths == frozenset({"circuit.instant_power_w"})
    assert _issues(hass) == []
