# SPAN Panel MQTT Broker ACL Bug

## Summary

The SPAN Panel's eBus MQTT broker does not enforce publish ACLs on consumer clients. Any authenticated client can publish to any topic, including the panel's
own Homie device state topics. This allows a misbehaving or misconfigured consumer to corrupt the panel's retained state, making the device appear offline to
all other subscribers.

## Reproduction

1. Register a client via `POST /api/v2/auth/register` to obtain MQTT credentials
2. Connect to the eBus broker at `span-{serial}.local:8883` using those credentials
3. Publish a retained message to the panel's own `$state` topic:

   ```text
   Topic: ebus/5/{serial}/$state
   Payload: lost
   QoS: 1
   Retain: true
   ```

4. Disconnect
5. Any new subscriber to `ebus/5/{serial}/#` will now receive `$state=lost` instead of the panel's actual state

The panel does not re-publish `$state=ready` to overwrite the corrupted retained message. The only recovery is to power-cycle the panel.

## Impact

- Any consumer client that sets a Last Will and Testament (LWT) on the panel's `$state` topic will poison the retained state when it disconnects ungracefully
- All subsequent subscribers see `$state=lost` and cannot determine the panel's real state
- The panel continues operating normally (circuits, relays, energy metering all function) but appears "offline" to Homie-compliant consumers
- Recovery requires a panel power cycle to force re-publication of `$state=ready`

## How We Hit This

Our Home Assistant integration's MQTT client set an LWT on `ebus/5/{serial}/$state` with payload `lost` (retained). This is a standard Homie convention for the
device itself, but we are a consumer, not the device. When HA crashed during setup, the LWT fired and overwrote the panel's retained `$state=ready` with `lost`.
All subsequent connection attempts timed out waiting for a `ready` state that would never arrive.

## Expected Broker Behavior

The broker should enforce topic-level ACLs:

| Client              | Publish                   | Subscribe                 |
| ------------------- | ------------------------- | ------------------------- |
| Panel (device)      | `ebus/5/{serial}/#`       | `ebus/5/{serial}/+/+/set` |
| Consumer (HA, etc.) | `ebus/5/{serial}/+/+/set` | `ebus/5/{serial}/#`       |

Specifically:

- Consumer clients should only be able to publish to `.../set` topics (relay commands, priority changes)
- The `$state`, `$description`, and property value topics should be writable only by the panel itself
- The `$state` topic is particularly sensitive because it is retained and controls whether consumers consider the device online

## Workaround

Do not set an LWT (or publish to any topic other than `.../set`) from consumer clients. If the retained `$state` has already been corrupted, power-cycle the
panel to force it to re-publish `$state=ready`.

## Affected Firmware

- Observed on firmware `spanos2/r202603/05`
- Likely affects all v2 firmware versions with eBus MQTT support
