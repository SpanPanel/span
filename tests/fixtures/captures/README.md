# Captured panels

Retained MQTT trees of real panels in the emitter's `tree-v1` form, vendored byte for byte from `tests/fixtures/` of
[`electrification-bus/distribution-enclosure-simulator`](https://github.com/electrification-bus/distribution-enclosure-simulator) at tag `v0.9.0`
(commit `2dbddf7c507776a08e03a64825454972696d23df`). They are never regenerated in-test and never edited.

| File                          | Panel   | Firmware             |
| ----------------------------- | ------- | -------------------- |
| `main32-tree-v1.json`         | MAIN 32 | `spanos3/r202633/02` |
| `main32_r202639-tree-v1.json` | MAIN 32 | `spanos3/r202639/03` |

`SHA256SUMS` records each file's digest, and `tests/test_expected_entities.py` holds the files to it in both directions: a capture without a digest has no
provenance, and a digest without its capture describes a file that went away. Re-vendor a capture only to correct a copy, with
`git show <tag>:tests/fixtures/<name> > tests/fixtures/captures/<name>`, checked with `git hash-object` against the tag's blob.

`tests/captures_replay.py` replays a capture through the pinned library exactly as the broker delivers it. What each one produces is recorded beside it:

- `tests/fixtures/expected_entities/<stem>.json`, every device and entity a fresh installation registers, with each entity's classes, unit, category and
  state;
- `tests/fixtures/topology/<stem>.json`, what `span_panel/panel_topology` answers, for the card to render;
- `tests/fixtures/unread_declarations/<stem>.json`, every declared property no entity, attribute or device card reads, with the reason.

The stem is the file name without `-tree-v1.json`. The battery-less, unvalued-battery and unpublished-readings variants of the r202639 capture are derived
in-test, so they have expected files and no capture of their own.
