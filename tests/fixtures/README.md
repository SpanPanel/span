# Adapter fixtures

Real schema-adapter inputs, used by the field-path conformance tests via `tests/adapter_fixtures.py`. They are **committed rather than generated** so the test
suite has no cross-repo dependency and no runtime wheel has to keep shipping test data for this repository's benefit. The cost of a copy is that it can go
stale, which for `schema_one_tree.json` is answered by the two guards below.

## Provenance

Both are byte-identical copies from the `span-panel-api` repository:

| File here                | Source in `$SPAN_PANEL_API_DIR`                   | Guarded by               |
| ------------------------ | ------------------------------------------------- | ------------------------ |
| `schema_zero_types.json` | `tests/reference_payloads/homie_schema.json`      | nothing                  |
| `schema_one_tree.json`   | `tests/reference_payloads/parent_child_tree.json` | `schema_one_tree.source` |

Refresh by copying them again, and keep `schema_one_tree.json` byte-identical to its source: the library pins what that capture leaves unvalued against
panelbench's own baseline (`tests/test_reference_tree_values.py` there), so a copy that has drifted puts these tests on a wire no producer sends.

Both payloads shipped as package data until recently, and this repository imported them from the installed wheels rather than copying them:

| File here                | Path before `span-panel-api#162`                                                          |
| ------------------------ | ----------------------------------------------------------------------------------------- |
| `schema_zero_types.json` | `src/span_panel_api/reference_payloads/homie_schema.json`                                 |
| `schema_one_tree.json`   | `packages/schema-1/src/span_panel_api_schema_1/reference_payloads/parent_child_tree.json` |

`span-panel-api#162` takes both back out of the wheels and moves them to `tests/reference_payloads/`, so the paths in the first table are the ones to copy from
now. The byte comparison below checks both locations for the tree, newest first, so it works against a checkout on either side of that merge; the older path can
be dropped once no release this repository can pin still predates it.

`schema_zero_types.json` has been verified byte-identical to `homie_schema.json` at `schema-1-v1.0.0`, `v3.0.1`, `main`, and the post-#162 location — the
bootstrap payload has not moved a byte across any of them. It has no automated guard: see "Why the flat capture has no byte guard" below.

If a copy changes shape rather than content, the loader in `tests/adapter_fixtures.py` is what needs updating — note that `schema_one_tree.json` is a **dict
keyed by device id**, whose `$description` value is a **JSON string**, not a parsed object. `tests/test_fixture_provenance.py` pins that shape, so a capture
that arrives pre-parsed fails there rather than somewhere far from the fixture.

## The two guards

They catch different failures, and neither one substitutes for the other.

| Guard           | Compares                                                    | Catches                                                                                                                 | Needs a checkout |
| --------------- | ----------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- | ---------------- |
| Version claim   | `schema_one_tree.source` against the installed distribution | a pin that moved while the copy stayed put                                                                              | no               |
| Byte comparison | `schema_one_tree.json` against the capture in that release  | a copy that was refreshed **wrongly** — reformatted, edited, or taken from a working tree ahead of the release it names | yes              |

### The version claim

`schema_one_tree.source` records the release the capture was copied from, as a pinned requirement:

```text
span-panel-api-schema-1==1.1.0
```

`tests/test_fixture_provenance.py` holds that against `importlib.metadata.version("span-panel-api-schema-1")` — the release actually installed. When the pin in
`manifest.json` moves and nobody refreshes the capture, that test fails and names the refresh, so staleness is loud rather than silent. It needs no checkout of
the library and no network call, which is why it runs everywhere and why a copy is safe to keep here at all.

It is a separate file rather than a key inside the payload because a refresh is a byte-for-byte copy: anything added to the JSON would be overwritten by the
next one. It names the distribution as well as the version because this repository pins three of them.

What it cannot see is the bytes. A capture edited in place, reindented on the way in, or copied from a working tree that is ahead of the release it names
satisfies this guard completely — the claim is a string, and the string is correct.

### The byte comparison

`test_the_vendored_capture_is_byte_identical_to_its_source` closes exactly that hole. It reads `SPAN_PANEL_API_DIR` (see `.env.example`), confirms the checkout
is on the release `schema_one_tree.source` names, and compares the vendored copy against the capture there byte for byte, with no reformatting tolerance.

The release check is not decoration. Written without it, the comparison passed against a checkout sitting one release behind the recorded one, whose bytes
happened to be the ones vendored here — proving only that the copy matched _something_, and "something" includes the working tree it was mistakenly taken from.

**A skip here is not a pass.** Without a checkout, or with one on another release, the comparison skips: not every developer keeps a sibling checkout, and
failing them for it is what gets a check deleted rather than configured. Under `CI` it fails instead, because the workflow clones `span-panel-api` at the
release the pin names, so an unset or misplaced path there means the wiring has come undone rather than that the check is unavailable. `span-panel-api` paid for
this asymmetry and states the reason plainly in its `DEVELOPMENT.md`, under "A skip here is not a pass": _"A skip reads in a summary line exactly like a pass,
and that is how a stale vendored capture went unnoticed for nine days."_ Do not make the skip unconditional.

CI derives the tag from `schema_one_tree.source` rather than hardcoding it — `span-panel-api-schema-1==1.1.0` becomes `schema-1-v1.1.0` — so there is no second
copy of the pin that could disagree with the first.

### Refreshing

In one commit:

1. Copy `tests/reference_payloads/parent_child_tree.json` from `$SPAN_PANEL_API_DIR` over `schema_one_tree.json`, with no reformatting.
2. Set the version in `schema_one_tree.source` to the release it came from.

Copy from a checkout positioned on a **released tag**, never from a working tree that is ahead of one. A capture taken from unreleased work carries a version
claim nothing can ever be positioned to verify: the byte comparison clones the tag that claim names, and until that tag exists there is nothing to compare
against.

## These files are exempt from formatting

`tests/fixtures/schema_*.json` is excluded from every hook that rewrites files — `trailing-whitespace`, `end-of-file-fixer` and `mixed-line-ending` in
`prek.toml`, and `tests/fixtures/schema_*.json` in `.prettierignore`.

The reason is the whole point of the byte comparison: **these are captured bytes, not source we own.** A vendored fixture held byte-identical to its source and
an unconditional formatter cannot both exist, and it is the formatter that has to yield. A capture reindented on the way in is one nothing upstream can hold to
its own baseline any more, and the resulting failure names the fixture rather than the hook that broke it — so the person debugging it starts in the wrong
repository.

It is not hypothetical, only untriggered so far. Those three hooks cover `tests/` and left the captures alone only because both happen to be newline-terminated
with LF endings and no trailing whitespace. Stripping the final newline from `schema_one_tree.json` and running `prek run end-of-file-fixer --all-files` reports
`Fixing tests/fixtures/schema_one_tree.json` without the exclusion, and leaves it untouched with it. The first capture vendored without a final newline would
have been rewritten on commit, and the guard would then have failed against a source the copy genuinely matched when it was made.

Prettier is excluded as a precaution rather than a fix: nothing here currently runs it over JSON — `scripts/fix-markdown.sh` passes only `*.md` globs and
`prek.toml` has no Prettier hook — but `.prettierrc` sets `tabWidth: 2` while these captures carry their producer's own 1-space indentation, so a JSON glob
added later would silently reindent them.

The scope is `schema_*.json` rather than the whole directory, because only the adapter captures have this property. `tests/fixtures/README.md` is prose this
repository owns and should keep being formatted; the migration YAMLs are hand-written source; and `unread_declarations_baseline.json`, despite being a
mechanically-checked inventory, is hand-maintained — its values are one-line human explanations. The `schema_*` prefix is the vocabulary
`tests/adapter_fixtures.py` already uses for adapter captures, so the next one vendored is covered without anyone remembering to widen the rule.

The read-only hooks still cover these files, `check-json` in particular. A capture that does not parse is worth hearing about wherever it came from.

## Why the flat capture has no byte guard

`schema_zero_types.json` is vendored on the same terms as the tree but has no `.source` file and no byte comparison, and that is a deliberate gap rather than an
oversight.

It comes from the **bootstrap** distribution, `span-panel-api`, which is released on its own tag line (`v3.1.0`), while the tree comes from
`span-panel-api-schema-1` (`schema-1-v1.1.0`). CI clones the library once, at one revision. A checkout positioned at the schema-1 tag holds the bootstrap
sources at whatever state they happened to be in at that commit, which is not the same thing as a bootstrap _release_ — so a byte comparison there would be
against an arbitrary revision, which is exactly the "proves only that the copy matches something" failure the tree's release check exists to prevent.

Guarding it properly means either a second checkout at the bootstrap tag or a second job, and neither is free. Against that: the payload has been byte-stable
upstream across every revision checked. The gap is recorded here so the next person weighs it rather than rediscovering it.

## Derived variants

The batteryless and PV-less trees are **derived in memory**, not committed: `adapter_fixtures.schema_one_tree(without="bess")` and `without="pv"` return the
capture with that one device dropped. They were separate files once; deriving them means they cannot drift from the base, since the only difference either ever
had was the one missing device. Each drops exactly one device (13 -> 12) and retains the panel and both lugs devices — a variant that removed more would make
the conformance tests pass for the wrong reason. Note `bess-mid` is typed `energy.ebus.device.mid` and is not the BESS.

## Why these exist

The batteryless tree proves a panel with no BESS produces **no** `battery.*` entries — hardware absence, not degradation. The PV-less tree proves the same for a
panel that has power-flows telemetry but no PV device, which is the case telemetry-based capability detection gets wrong.
