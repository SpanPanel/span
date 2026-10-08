# Test fixtures

The files here are committed source this repository owns, with two exceptions, both held byte for byte to where they came from by a guard:

- the **historical `pyproject.toml` copies** (`pyproject_*.toml`) are this repository's own file at the two commits where the library path override went wrong;
- the **captured panels** (`captures/*-tree-v1.json`) are vendored from the emitter's published fixtures; `captures/README.md` names the tag and the commit.

The schema-adapter payloads the conformance tests replay are **not** here. They are package data of `span-panel-api-schema-0` and `span-panel-api-schema-1`,
read out of the installed wheels by `tests/adapter_fixtures.py`; the pin in `custom_components/span_panel/manifest.json` is what says which capture the suite
replays, and bumping the pin is what moves it.

What each captured panel produces is recorded beside it, per stem: `expected_entities/` and `topology/`, written by `tests/test_expected_entities.py` under
`--update-capture-fixtures` and compared otherwise, and `unread_declarations/`, the hand-kept baselines of `tests/test_declared_but_unread.py` (one per tree,
the adapter's reference payload included as `parent_child_tree.json`).

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

These cannot go stale. A commit is content-addressed, so `3cbf02a:pyproject.toml` cannot become different bytes; the only failure the comparison can report is a
copy that was wrong when it was taken. That is why its guard **skips** when the object is unreachable — a shallow clone has no history to compare against, and
failing there would report the clone rather than the fixture. A skip is safe here precisely because the target cannot move, which is not true of a guard over
anything that can.

Re-vendor either with `git show <commit>:pyproject.toml > tests/fixtures/<name>.toml`, and only to correct a copy — never to make a failing case pass.

## The historical copies are exempt from formatting

`tests/fixtures/pyproject_*.toml` and `tests/fixtures/captures/*-tree-v1.json` are excluded from every hook that rewrites files — `trailing-whitespace`, `end-of-file-fixer`
and `mixed-line-ending` in `prek.toml`. Prettier cannot format TOML; it can format JSON, so `.prettierignore` names the captures.

The reason is the whole point of the comparison: **these are captured bytes, not source we own.** A copy held byte-identical to a commit and an unconditional
formatter cannot both exist, and it is the formatter that has to yield. A copy reindented on the way in fails against the commit it genuinely matched when it
was made, and the resulting failure names the fixture rather than the hook that broke it — so the person debugging it starts in the wrong place.

It is not hypothetical, only untriggered so far. Those three hooks cover `tests/`, and they leave the copies alone only because every one happens to be
newline-terminated with LF endings and no trailing whitespace. The first one vendored without a final newline would be rewritten on commit.

The scope is two patterns rather than the whole directory, because only these copies have this property. `tests/fixtures/README.md` and
`captures/README.md` are prose this repository owns and should keep being formatted; the migration YAMLs are hand-written source; `unread_declarations/`,
despite being a mechanically-checked inventory, is hand-maintained — its values are one-line human explanations; and `expected_entities/` and `topology/` are
written by their test in the form the hooks already accept. `pyproject_*` is `tests/test_library_path_hook.py`'s own vocabulary and `captures/*-tree-v1.json`
is `tests/captures_replay.py`'s, so the next copy taken under either is covered without anyone remembering to widen the rule.

The read-only hooks still cover these files, `check-toml` and `check-json` in particular. A copy that does not parse is worth hearing about wherever it came
from.

## Derived variants of the parent/child capture

The batteryless and PV-less trees are **derived in memory**: `adapter_fixtures.schema_one_tree(without="bess")` and `without="pv"` return the adapter's capture
with that one device dropped. They were separate files once; deriving them means they cannot drift from the base, since the only difference either ever had was
the one missing device. Each drops exactly one device (14 -> 13) and retains the panel and both lugs devices — a variant that removed more would make the
conformance tests pass for the wrong reason. Note `bess-mid` is typed `energy.ebus.device.mid` and is not the BESS.

The batteryless tree proves a panel with no BESS produces **no** `battery.*` entries — hardware absence, not degradation. The PV-less tree proves the same for a
panel that has power-flows telemetry but no PV device, which is the case telemetry-based capability detection gets wrong.

The captured r202639 panel's battery-less, unvalued-battery and unpublished-readings variants are derived the same way, in `tests/test_expected_entities.py`. A captured battery
carries its islanding device as its own child, so `captures_replay.without_device` drops the device with everything beneath it and every `connection/*` value
naming one of them. The battery-less variant also drops the panel's `shed-forecast/*` values, keeping their declarations: a panel without a battery makes no
backup forecast. Together that is what a panel that never had the battery publishes. The unpublished-readings variant keeps every declaration and drops
only values: the panel's `power-flows/*` and one drawing circuit's `meter/active-power`, so its expected file holds sensors reading unknown.
