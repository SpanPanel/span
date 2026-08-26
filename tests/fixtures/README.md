# Adapter fixtures

Real schema-adapter inputs, used by the field-path conformance tests via `tests/adapter_fixtures.py`. They are **committed rather than generated** so the test
suite has no cross-repo dependency and CI needs no checkout of the library — and so no runtime wheel has to keep shipping test data for this repository's
benefit. The cost of a copy is that it can go stale, which for `schema_one_tree.json` is answered by the version guard below.

## Provenance

Both are byte-identical copies from the `span-panel-api` repository:

| File here                | Source in `span-panel-api`                        | Version guard            |
| ------------------------ | ------------------------------------------------- | ------------------------ |
| `schema_zero_types.json` | `tests/fixtures/v2/homie_schema.json`             | none                     |
| `schema_one_tree.json`   | `tests/reference_payloads/parent_child_tree.json` | `schema_one_tree.source` |

Refresh by copying them again, and keep `schema_one_tree.json` byte-identical to its source: the library pins what that capture leaves unvalued against
panelbench's own baseline (`tests/test_reference_tree_values.py` there), so a copy that has drifted puts these tests on a wire no producer sends.

Through the 1.1.0 release the parent/child capture also shipped as package data, at `span_panel_api_schema_1/reference_payloads/parent_child_tree.json` inside
the installed wheel, and this repository imported it from there. `span-panel-api#162` takes it back out of the wheels, so the repository path above is the one
to copy from.

If a copy changes shape rather than content, the loader in `tests/adapter_fixtures.py` is what needs updating — note that `schema_one_tree.json` is a **dict
keyed by device id**, whose `$description` value is a **JSON string**, not a parsed object. `tests/test_fixture_provenance.py` pins that shape, so a capture
that arrives pre-parsed fails there rather than somewhere far from the fixture.

## The version guard

`schema_one_tree.source` records the release the capture was copied from, as a pinned requirement:

```text
span-panel-api-schema-1==1.1.0
```

`tests/test_fixture_provenance.py` holds that against `importlib.metadata.version("span-panel-api-schema-1")` — the release actually installed. When the pin in
`manifest.json` moves and nobody refreshes the capture, that test fails and names the refresh, so staleness is loud rather than silent. It needs no checkout of
the library and no network call, which is why a copy is safe to keep here at all.

It is a separate file rather than a key inside the payload because a refresh is a byte-for-byte copy: anything added to the JSON would be overwritten by the
next one. It names the distribution as well as the version because this repository pins three of them.

To refresh, in one commit:

1. Copy `tests/reference_payloads/parent_child_tree.json` from `span-panel-api` over `schema_one_tree.json`, with no reformatting.
2. Set the version in `schema_one_tree.source` to the release it came from.

The guard only sees the version claim, not the bytes. A capture edited in place under an unchanged version is invisible to it — refresh from a release, never
from a working tree that is ahead of one.

## Derived variants

The batteryless and PV-less trees are **derived in memory**, not committed: `adapter_fixtures.schema_one_tree(without="bess")` and `without="pv"` return the
capture with that one device dropped. They were separate files once; deriving them means they cannot drift from the base, since the only difference either ever
had was the one missing device. Each drops exactly one device (13 -> 12) and retains the panel and both lugs devices — a variant that removed more would make
the conformance tests pass for the wrong reason. Note `bess-mid` is typed `energy.ebus.device.mid` and is not the BESS.

## Why these exist

The batteryless tree proves a panel with no BESS produces **no** `battery.*` entries — hardware absence, not degradation. The PV-less tree proves the same for a
panel that has power-flows telemetry but no PV device, which is the case telemetry-based capability detection gets wrong.
