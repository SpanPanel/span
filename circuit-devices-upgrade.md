# Upgrading to Circuit Devices

From 2.3.0, each circuit is a device of its own in Home Assistant, under the panel's device. The first time 2.3.0 sets a panel up, it moves each circuit's
entities from the panel's device to the circuit's own. This page describes what that move changes and what it leaves alone. 2.3.0 requires Home Assistant
2026.11 or later, the first release that shows the resulting names correctly.

How circuit devices and their entities are named from then on is described under [Entity Naming Pattern](README.md#entity-naming-pattern).

## What stays the same

- **Entity IDs and unique IDs.** No entity ID changes, so automations, scripts and dashboards that use entity IDs keep working, and each entity keeps its
  history.
- **Names you gave entities.** A name you set is retained. The one change is that a name that already began with the circuit's name drops it, so it does not
  show twice once Home Assistant puts the circuit's name in front.

## What changes

- **The device.** A circuit's entities are on the circuit's device, named by the circuit's name in the SPAN app, and the entities are named by what they
  measure, such as Power. Templates that read an entity's device, such as `device_name`, `device_id` or `device_entities`, now see the circuit's device rather
  than the panel.
- **Displayed names.** A name you never changed displays as before, unless the move changed the entity's area or left it with an area of its own (see
  [Areas](#areas)). An entity you renamed shows its circuit's name in front of yours, as in "Kitchen Outlets Fridge Power".
- **Circuit numbers.** In circuit-numbers mode, **Recreate entity IDs** now offers an ID built from the circuit's name, matching its displayed name, while new
  entities still get circuit-number IDs.

## Areas

A circuit's area moves to its device, and the circuit's other entities then share it instead of keeping areas of their own. The device's area is taken from:

1. the circuit's breaker switch, when the switch has an area;
2. on a circuit with no breaker switch, its power sensor, or else the first of its other entities that has an area.

A circuit whose breaker switch has no area keeps its entities' areas as they were, so a room-wide turn-off still cannot reach its breaker. Those entities show
without the circuit's name, as just "Power". To show it again, set an area on the circuit's device and clear the entities' own areas, knowing that the breaker
switch then joins that area too, where an area-wide turn-off reaches it.

## The repair

After the move, a repair, **SPAN Panel circuits now have their own devices**, lists every displayed name that changed, every area that moved to a circuit's
device, and the circuits whose areas were left as they were. Nothing needs doing; dismiss it once you have read it. A move that changed no displayed name and no
area raises no repair.

When the panel later adds a breaker switch to a circuit whose device has an area, the switch is in that area too, and another repair, **SPAN Panel breaker
switch joined an area**, names the switch and the area.

## Going back to an earlier release

2.3.0 raises the config entry to version 8. Home Assistant refuses to load a config entry whose version is newer than the installed integration understands, and
there is no automatic downgrade, so going back from 2.3.0 to any earlier release is refused. Reinstall 2.3.0, or restore a backup taken before the upgrade.

Removing and re-adding the integration on the earlier release also works and preserves entity IDs, but needs the panel passphrase or physical access to the door
again. It puts each circuit's entities back on the panel's device, and the names the upgrade shortened and the areas it moved are not restored.
