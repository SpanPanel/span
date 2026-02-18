# Gen3 gRPC — PR #169 Handoff Notes

This document is for @Griswoldlabs. It describes what happened architecturally after PR #169
was filed and what is needed to take the work forward.

The refactoring allows clean integration of the grpc changes you made and the intent is for you to own/change these branches as necessary and create a PR once
you are comfortable.  Before we merge into main I will do one more sanity test to ensure gen2 still works.  The cuurrent branches have not broken gen2 thus far.

I will send an invite for the organization and give you admin rights on both repos.  We use Poetry for dev, see the notes in the readme about dev.

Thanks!

---

## Branches

| Repo | Branch | Purpose |
| --- | --- | --- |
| `span-panel-api` | [`grpc_addition`](https://github.com/SpanPanel/span-panel-api/tree/grpc_addition) | gRPC transport library — `SpanGrpcClient`, protocol/capability abstraction, unified snapshot models |
| `span` | [`gen3-grpc-integration`](https://github.com/SpanPanel/span/tree/gen3-grpc-integration) | Integration rewritten to use the library; no Gen3 transport code remains in the integration |

---

## What Changed

The gRPC transport code was moved out of the integration and into `span-panel-api`, where it
belongs architecturally. Your PR's `gen3/` directory was the starting point for the library
implementation. When this work merges, the integration will contain no transport code — only
calls through the library's abstraction layer.

Key points:

- `SpanGrpcClient` lives in `span-panel-api/src/span_panel_api/grpc/client.py`
- `PanelCapability` flags gate entity platform loading at setup time — Gen3 loads power
  sensors only (no relay switches, no energy history, no battery)
- `SpanPanelCoordinator` self-configures for push-streaming vs polling based on capabilities;
  no polling timer runs for Gen3
- The circuit IID mapping bug (reported by @cecilkootz) is addressed with positional pairing
  plus a `_metric_iid_to_circuit` reverse map built at connect time — see below for the
  outstanding issue

---

## Outstanding Issue — Name / Metric IID Count Mismatch

@cecilkootz's debug log from the MLO48 shows:

```text
Discovered 31 name instances (trait 16) and 36 metric instances (trait 26, excl main feed)
```

The current positional-pairing approach assumes both IID lists have the same length. On this
panel they do not — 31 name instances vs 36 metric instances. The cause is not yet confirmed
but likely candidates are:

- Dual-phase circuits occupy two trait-26 IIDs but share one trait-16 name entry
- Some trait-26 IIDs belong to something other than individual circuits

This is a known open issue. @Griswoldlabs is aware of it. It cannot be resolved without live
Gen3 hardware to inspect what the extra metric IIDs represent.

---

## Planning Docs - these describe the refactoring that took place

| Document | Location |
| --- | --- |
| gRPC transport design (library) | [`span-panel-api/docs/Dev/grpc-transport-design.md`](https://github.com/SpanPanel/span-panel-api/blob/grpc_addition/docs/Dev/grpc-transport-design.md) |
| Gen3 integration plan (integration) | [`span/docs/dev/gen3-grpc-integration-plan.md`](https://github.com/SpanPanel/span/blob/gen3-grpc-integration/docs/dev/gen3-grpc-integration-plan.md) |

---

## Developer Setup

Full setup instructions are in the
[Developer Setup section of grpc-transport-design.md](https://github.com/SpanPanel/span-panel-api/blob/grpc_addition/docs/Dev/grpc-transport-design.md#developer-setup-for-hardware-testing)
and the
[Developer Testing Setup section of gen3-grpc-integration-plan.md](https://github.com/SpanPanel/span/blob/gen3-grpc-integration/docs/dev/gen3-grpc-integration-plan.md#developer-testing-setup).

Short version — install the library in editable mode inside the HA Python environment:

```bash
pip install -e /path/to/span-panel-api[grpc]
pip show span-panel-api   # Location must be a file path, not site-packages
```

After the editable install, edit `src/span_panel_api/grpc/client.py` or `grpc/const.py` and
reload the integration in HA UI (Settings → Devices & Services → SPAN Panel → ⋮ → Reload).
No reinstall or HA restart is needed between iterations.

---

## What Needs Hardware Validation

| Symptom to confirm | File | What to check |
| --- | --- | --- |
| Circuit count correct | `grpc/client.py` → `_parse_instances()` | Does `sorted(set(...))` dedup give the right count on MAIN40 and MLO48? |
| Name / metric mismatch | `grpc/client.py` → `_parse_instances()` | What do the extra metric IIDs represent on the MLO48? |
| Power readings live | `grpc/client.py` → `_decode_and_store_metric()` | Values update within seconds of real load changes |
| Main feed correct | `grpc/client.py` → `_decode_main_feed()` | Field 14 in `Subscribe` contains main feed metrics on both panel models |
| Dual-phase detection | `grpc/client.py` → `_decode_circuit_metrics()` | Voltage threshold and per-leg fields correct |

Decoder fixes go in `grpc/client.py` and `grpc/const.py` in the library repo. The integration
itself should not need changes for hardware-specific tuning.
