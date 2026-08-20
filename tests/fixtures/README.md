# Adapter fixtures

Real schema-adapter inputs, used by the field-path conformance tests via `tests/adapter_fixtures.py`. They are **committed rather than generated** so the test
suite has no cross-repo dependency and CI needs no checkout of the library. The cost of that choice is that they go stale silently — hence this file.

## Provenance

Both are byte-identical copies from the `span-panel-api` repository:

| File here                | Source in `span-panel-api`                                            |
| ------------------------ | --------------------------------------------------------------------- |
| `schema_zero_types.json` | `tests/fixtures/v2/homie_schema.json`                                 |
| `schema_one_tree.json`   | `packages/schema-1/src/.../reference_payloads/parent_child_tree.json` |

Refresh by copying them again, and keep `schema_one_tree.json` byte-identical to its source: the library pins what that capture leaves unvalued against
panelbench's own baseline (`tests/test_reference_tree_values.py` there), so a copy that has drifted puts these tests on a wire no producer sends.

If a copy changes shape rather than content, the loader in `tests/adapter_fixtures.py` is what needs updating — note that `schema_one_tree.json` is a **dict
keyed by device id**, whose `$description` value is a **JSON string**, not a parsed object.

## Derived variants

Both are produced from `schema_one_tree.json` by dropping every device whose parsed `$description["type"]` contains a marker. Regenerate with:

```bash
uv run python - << 'PY'
import json, pathlib

tree = json.loads(pathlib.Path("tests/fixtures/schema_one_tree.json").read_text())

def drop(marker: str, out_name: str) -> None:
    kept = {
        device_id: topics
        for device_id, topics in tree.items()
        if marker not in json.loads(topics.get("$description", "{}")).get("type", "")
    }
    pathlib.Path(f"tests/fixtures/{out_name}").write_text(json.dumps(kept, indent=2) + "\n")
    print(f"{out_name}: {len(tree)} -> {len(kept)} devices")

drop(".bess", "schema_one_tree_batteryless.json")
drop(".pv", "schema_one_tree_no_pv.json")
PY
```

Each must remove **exactly one** device (13 -> 12) and must retain the panel and both lugs devices. A variant that removed more would make the conformance tests
pass for the wrong reason. Note `bess-mid` is typed `energy.ebus.device.mid` and correctly survives the `.bess` filter.

## Why these exist

`schema_one_tree_batteryless.json` proves a panel with no BESS produces **no** `battery.*` entries — hardware absence, not degradation.
`schema_one_tree_no_pv.json` proves the same for a panel that has power-flows telemetry but no PV device, which is the case telemetry-based capability detection
gets wrong.
