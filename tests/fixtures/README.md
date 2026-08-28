# Test fixtures

The files here are committed source this repository owns, with one exception: the **historical `pyproject.toml` copies** (`pyproject_*.toml`) are this
repository's own file at the two commits where the library path override went wrong, held to those commits by a guard.

The schema-adapter payloads the conformance tests replay are **not** here. They are package data of `span-panel-api-schema-0` and `span-panel-api-schema-1`,
read out of the installed wheels by `tests/adapter_fixtures.py`; the pin in `custom_components/span_panel/manifest.json` is what says which capture the suite
replays, and bumping the pin is what moves it.

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

`tests/fixtures/pyproject_*.toml` is excluded from every hook that rewrites files — `trailing-whitespace`, `end-of-file-fixer` and `mixed-line-ending` in
`prek.toml`. Prettier cannot format TOML, so `.prettierignore` needs no line for them.

The reason is the whole point of the comparison: **these are captured bytes, not source we own.** A copy held byte-identical to a commit and an unconditional
formatter cannot both exist, and it is the formatter that has to yield. A copy reindented on the way in fails against the commit it genuinely matched when it
was made, and the resulting failure names the fixture rather than the hook that broke it — so the person debugging it starts in the wrong place.

It is not hypothetical, only untriggered so far. Those three hooks cover `tests/`, and they leave the copies alone only because both happen to be
newline-terminated with LF endings and no trailing whitespace. The first one vendored without a final newline would be rewritten on commit.

The scope is one prefix rather than the whole directory, because only these copies have this property. `tests/fixtures/README.md` is prose this repository owns
and should keep being formatted; the migration YAMLs are hand-written source; and `unread_declarations_baseline.json`, despite being a mechanically-checked
inventory, is hand-maintained — its values are one-line human explanations. `pyproject_*` is `tests/test_library_path_hook.py`'s own vocabulary, so the next
copy taken under it is covered without anyone remembering to widen the rule.

The read-only hooks still cover these files, `check-toml` in particular. A copy that does not parse is worth hearing about wherever it came from.

## Derived variants of the parent/child capture

The batteryless and PV-less trees are **derived in memory**: `adapter_fixtures.schema_one_tree(without="bess")` and `without="pv"` return the adapter's capture
with that one device dropped. They were separate files once; deriving them means they cannot drift from the base, since the only difference either ever had was
the one missing device. Each drops exactly one device (14 -> 13) and retains the panel and both lugs devices — a variant that removed more would make the
conformance tests pass for the wrong reason. Note `bess-mid` is typed `energy.ebus.device.mid` and is not the BESS.

The batteryless tree proves a panel with no BESS produces **no** `battery.*` entries — hardware absence, not degradation. The PV-less tree proves the same for a
panel that has power-flows telemetry but no PV device, which is the case telemetry-based capability detection gets wrong.
