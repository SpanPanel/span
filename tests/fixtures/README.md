# Test fixtures

Two kinds of vendored byte copy live here, on the same terms: committed rather than generated, held to their source by a guard, and exempt from the formatters
because in both cases the bytes are the artifact.

**Adapter captures** (`schema_*.json`) are real schema-adapter inputs, used by the field-path conformance tests via `tests/adapter_fixtures.py`. They are
committed so the test suite has no cross-repo dependency and no runtime wheel has to keep shipping test data for this repository's benefit. The cost of a copy
is that it can go stale, which for `schema_one_tree.json` is answered by the two guards below. Everything from here to "Why the flat capture has no byte guard"
is about them.

**Historical `pyproject.toml` copies** (`pyproject_*.toml`) are this repository's own file at the two commits where the library path override went wrong. See
"[The historical pyproject copies](#the-historical-pyproject-copies)".

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

### What it caught the first time it ran

Worth recording, because it is the failure mode the guard was built for and it had already happened.

`schema_one_tree.json` was vendored from a **stale sibling checkout**. `pyproject.toml` pointed its `[tool.uv.sources]` path overrides at a `span-panel-api`
worktree that predated span-panel-api#161, so `span_panel_api_schema_1.reference_payloads` resolved there, and the copy inherited a capture that still recorded
the emitter's `$settable` defect on a locked relay together with `load-shed/priority` values (`UNKNOWN`) that no producer publishes. The copy itself was
faithful — byte-identical to the `schema-1-v1.0.0` release artifact — and `schema_one_tree.source` claimed 1.1.0. Both halves looked right in isolation; only
holding the bytes against the release the claim names showed the mismatch.

Nothing else in either repository could see it. The version guard compared a string to a string and passed. The conformance tests passed, because they were
asserting against the capture. The integration was testing its locked-circuit and priority-settability work against a producer defect that upstream had already
fixed.

The fix was to repoint the path overrides at a checkout that tracks `main` and re-vendor. A path override redirects what every import in the suite resolves to,
so one aimed at a stale checkout quietly tests against a producer that no longer exists — worth checking before believing any fixture taken through one.

### Refreshing

Either guard failing names the refresh, and the refresh is one command:

```bash
# SPAN_PANEL_API_DIR names the checkout; see .env.example
uv run python scripts/refresh-vendored-capture.py
```

Commit both files it touches together — they are one fact in two files.

What it does, for anyone reading this without the checkout to hand:

1. Copies `tests/reference_payloads/parent_child_tree.json` from `$SPAN_PANEL_API_DIR` over `schema_one_tree.json`, byte for byte — `shutil.copyfile`, never a
   `json.load`/`json.dump` round trip, which would rewrite indentation, key order and float spelling and produce exactly what the byte comparison rejects. It
   checks the pre-#162 package-data path as a fallback, so it works against a checkout on either side of that merge.
2. Writes `schema_one_tree.source` from the **installed** distribution's version, rather than from anything typed in.

**Doing both from one command is the point, not the convenience.** The bytes and the recorded release used to be updated by two different actions, so doing one
and not the other was a single missed keystroke — and the result was a capture whose claim about itself was wrong, which every conformance test then passed
against. One command cannot do half of it.

It refuses rather than guesses: no `SPAN_PANEL_API_DIR`, a path that does not resolve, a directory that is not a checkout, a checkout whose schema-1 release is
not the one installed, a checkout with the file at neither location, or a checkout that is not standing on the release's tag. That last one is the one that
matters: the refresh that went wrong copied from a worktree that _declared_ the installed release while sitting on unreleased work behind `main`, so the
declaration and the pin agreed while the bytes were a release old. A version in a working tree is intent; a tag is a fact, and only a tag can be checked.

Copying from unreleased work is still possible with `--allow-unreleased`, because it is occasionally correct — the current copy is exactly that case, taken from
`main` because `schema-1-v1.1.0` is not cut and the alternative was to keep shipping a capture that records a producer defect. It verifies against a `main`
checkout today and will verify against the tag once 1.1.0 is released from these bytes; if `main` moves the capture again first, the guard says so, which is the
whole point. What is never acceptable is a capture taken from a checkout _behind_ the recorded release — that is how this one went wrong.

### Why the capture is not copied at test time

Someone will eventually propose having the suite copy the fixture from `$SPAN_PANEL_API_DIR` on every run, so it can never be stale. It would undo both things
vendoring bought.

It puts the cross-repo dependency back — every test run needing a `span-panel-api` checkout, which is exactly what committing the bytes removed. And it makes
the byte comparison vacuous: a copy compared against the thing it was just copied from can only ever pass. **Committed bytes that are allowed to disagree with
their source are the whole mechanism.** That freedom to disagree is what let the comparison find a capture vendored from a stale path override, which nothing
else in either repository could see. A fixture that silently re-syncs itself cannot report anything.

## The historical pyproject copies

`pyproject_both_blocks_redirected.toml` and `pyproject_type_checker_path_left_behind.toml` are byte copies of this repository's own `pyproject.toml`, taken from
its git history. Each has a `.source` file beside it recording the commit it came from, and `tests/test_library_path_hook.py` holds the copy to that commit.

| File                                           | Commit     | State                                                                         |
| ---------------------------------------------- | ---------- | ----------------------------------------------------------------------------- |
| `pyproject_both_blocks_redirected.toml`        | `3cbf02a`  | `[tool.uv.sources]` **and** `[tool.pyright].extraPaths` on a scratch worktree |
| `pyproject_type_checker_path_left_behind.toml` | `82a512f^` | sources block corrected, `extraPaths` still on the worktree                   |

They are the failing cases for `scripts/check-library-path.py`, the hook that rejects a `span-panel-api` path naming anything but `../../span/span-panel-api`.
The real files rather than constructed ones, because **a gate proven against a made-up example is proven against the wrong thing** — a synthetic version of a
defect is written by somebody who already knows what the rule checks, so it exercises the rule rather than the mistake. The second file matters as much as the
first: it is the interval between the two corrections, several commits during which the repository looked fixed, and a hook covering only `[tool.uv.sources]`
calls it clean.

Unlike the adapter captures, these cannot go stale. A commit is content-addressed, so `3cbf02a:pyproject.toml` cannot become different bytes; the only failure
the comparison can report is a copy that was wrong when it was vendored. That is why its guard **skips** when the object is unreachable — a shallow clone has no
history to compare against, and failing there would report the clone rather than the fixture. This is the one place in this directory where a skip is not hiding
a moving target; "A skip here is not a pass" above is the case where it would be, and that distinction is the whole reason this one is allowed.

Re-vendor either with `git show <commit>:pyproject.toml > tests/fixtures/<name>.toml`, and only to correct a copy — never to make a failing case pass.

## These files are exempt from formatting

`tests/fixtures/schema_*.json` and `tests/fixtures/pyproject_*.toml` are excluded from every hook that rewrites files — `trailing-whitespace`,
`end-of-file-fixer` and `mixed-line-ending` in `prek.toml`, and `tests/fixtures/schema_*.json` in `.prettierignore` (Prettier cannot format TOML, so the
historical copies need no line there).

The reason is the whole point of the byte comparison: **these are captured bytes, not source we own.** A vendored fixture held byte-identical to its source and
an unconditional formatter cannot both exist, and it is the formatter that has to yield. A capture reindented on the way in is one nothing upstream can hold to
its own baseline any more, and the resulting failure names the fixture rather than the hook that broke it — so the person debugging it starts in the wrong
repository.

It is not hypothetical, only untriggered so far. Those three hooks cover `tests/` and left the captures alone only because both happen to be newline-terminated
with LF endings and no trailing whitespace. Stripping the final newline from `schema_one_tree.json` and running `prek run end-of-file-fixer --all-files` reports
`Fixing tests/fixtures/schema_one_tree.json` without the exclusion, and leaves it untouched with it. The first capture vendored without a final newline would
have been rewritten on commit, and the guard would then have failed against a source the copy genuinely matched when it was made.

Prettier is excluded as a **precaution, not a fix.** It has never touched these files and caused none of the drift we have seen — nothing here runs it over JSON
(`scripts/fix-markdown.sh` passes only `*.md` globs, `prek.toml` has no Prettier hook), and the drift the byte guard actually caught was a stale source
checkout. The line still earns its place: `schema_zero_types.json` carries its producer's 4-space indentation, and Prettier under `.prettierrc` rewrites it from
14675 to 11779 bytes. That file has no byte guard, so nothing would report it.

The scope is two prefixes rather than the whole directory, because only the vendored byte copies have this property. `tests/fixtures/README.md` is prose this
repository owns and should keep being formatted; the migration YAMLs are hand-written source; and `unread_declarations_baseline.json`, despite being a
mechanically-checked inventory, is hand-maintained — its values are one-line human explanations. Each prefix is a vocabulary something already uses — `schema_*`
is `tests/adapter_fixtures.py`'s for adapter captures, `pyproject_*` is `tests/test_library_path_hook.py`'s for historical copies of this repository's own
`pyproject.toml` — so the next one vendored under either is covered without anyone remembering to widen the rule.

The read-only hooks still cover these files, `check-json` and `check-toml` in particular. A copy that does not parse is worth hearing about wherever it came
from.

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
