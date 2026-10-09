# Captured panels

Retained MQTT trees in the emitter's `tree-v1` form, vendored byte for byte from `tests/fixtures/` of
[`electrification-bus/distribution-enclosure-simulator`](https://github.com/electrification-bus/distribution-enclosure-simulator) at tag `v0.9.0`
(commit `2dbddf7c507776a08e03a64825454972696d23df`). They are never regenerated in-test and never edited.

| File                          | What the tree reports                     | What upstream says about it                                                                                                   |
| ----------------------------- | ----------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| `main32-tree-v1.json`         | `MAIN_32`, firmware `spanos3/r202633/02`  | A live panel's tree on released firmware, with device ids, serial numbers, the postal code and the Wi-Fi SSID replaced and circuit names made generic (`tests/test_capture_fixture.py`) |
| `main32_r202639-tree-v1.json` | `MAIN_32`, firmware `spanos3/r202639/03`  | Nothing beyond the file itself                                                                                                |

## License

The captures are published under the MIT License. `LICENSE` here is that notice, copied byte for byte from `LICENSE` at the root of the same repository and
commit (blob `1f404548e840872fba2148d9eec8731ee442e42c`), as the license requires of a copy. It covers the two capture files and nothing else in this
repository.

## Provenance

`SHA256SUMS` records the digest of every vendored file: the two captures and `LICENSE`. `README.md` and `SHA256SUMS` are this repository's own and are not
listed. `tests/test_expected_entities.py` holds the vendored files to their digests in both directions, since a file without a digest has no provenance and a
digest without its file describes one that went away, and it fails on any other file appearing here. Re-vendor a file only to correct a copy, with
`git show <tag>:<path> > tests/fixtures/captures/<name>`, checked with `git hash-object` against the tag's blob.

## What each capture produces

`tests/captures_replay.py` replays a capture through the pinned library exactly as the broker delivers it. What each one produces is recorded beside it:

- `tests/fixtures/expected_entities/<stem>.json`, every device and entity a fresh installation registers, with each entity's classes, unit, category and
  state;
- `tests/fixtures/topology/<stem>.json`, what `span_panel/panel_topology` answers, for the card to render;
- `tests/fixtures/unread_declarations/<stem>.json`, every declared property no entity, attribute or device card reads, with the reason.

The stem is the file name without `-tree-v1.json`. The battery-less, unvalued-battery and unpublished-readings variants of the r202639 capture are derived
in-test, so they have expected files and no capture of their own.
