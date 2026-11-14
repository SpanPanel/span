# Tandem and Multi-Load Breakers

A multi-load breaker puts more than one breaker where a standard breaker would go. There are three types:

- **Tandem:** two single-pole breakers in one space, both on that space's leg. For example, a 20 A and a 30 A circuit side by side, which the SPAN app shows as
  20-30A.
- **Quad:** two two-pole breakers across two spaces, each spanning both legs. For example, two 240 V circuits, a 20 A and a 30 A.
- **Tandem-quad:** two single-pole breakers and one two-pole breaker across two spaces. The two-pole breaker spans both legs, and each single-pole breaker is on
  one of them. For example, a 30 A 240 V dryer with 120 V circuits beside it.

The SPAN app shows a multi-load breaker's ratings together, such as 20-30A.

This page describes what the integration does with them today. A MAIN 32 publishes them differently from every other SPAN panel, so it comes first, and
[Other SPAN panels](#other-span-panels), such as the MAIN 16, MLO 24, MAIN 40 and MLO 48, follow.

To see which section applies, open the panel's device under **Settings → Devices & Services → Span Panel** and read its model: `MAIN_32` is covered by
[MAIN 32](#main-32), and any other, such as `MAIN_16`, `MLO_24`, `MAIN_40` or `MLO_48`, by [Other SPAN panels](#other-span-panels).

## MAIN 32

### Why a MAIN 32 cannot report its tandem breakers

The SPAN app shows every breaker's rating, such as 20-30A, but a MAIN 32 itself does not have that information. Its configuration describes each breaker space
as one circuit with one rating, and nothing in it marks a tandem, quad or tandem-quad, or carries each breaker's rating. The panel's API can publish only what
the panel has, so it cannot give the integration that information either.

SPAN has said that publishing each breaker's rating is not planned for MAIN 32
([spanio/SPAN-API-Client-Docs#21](https://github.com/spanio/SPAN-API-Client-Docs/issues/21)). As far as we know, copying the ratings from the SPAN app, as
described below, is how a MAIN 32's monitoring is set right.

### What the panel publishes

A MAIN 32 has one meter and one relay for each breaker space, and publishes one circuit for each space.

- **A tandem in one space** is published like an ordinary single-pole breaker: one circuit, one current reading for both breakers, and one rating, the first
  breaker's. A 20-30 tandem publishes 20 A, and so does a 20-20. Nothing the panel publishes marks the circuit as a tandem, so the integration cannot tell it
  from a single breaker. The SPAN app shows both ratings, such as "20-30A".
- **A quad or tandem-quad across two spaces** is published like an ordinary two-pole breaker: one circuit covering both spaces, with one rating, the first
  breaker's. Nothing the panel publishes marks it as a quad or a tandem-quad, or says which of the two it is.
- **Every circuit across two spaces** reports its current as the average of its two legs, so on a quad or tandem-quad one leg can carry more than its breaker
  while the reading stays under the rating.

### What that means

- **Current monitoring** compares both breakers' current against one breaker's rating, so a busy tandem raises high-current alerts again and again until you
  copy its second rating (below).
- **On a quad or tandem-quad**, no rating makes monitoring reliable, because the averaged reading can hide an overloaded leg (below).
- **Breaker switch and shed priority** act on both breakers of a tandem together, because they share the space's relay.
- **Power and energy** are the two loads combined, as one circuit, so the circuit counts once in the Energy dashboard.

### A tandem in one space: copy its second rating

**Example:** the SPAN app shows the Kitchen Counters circuit, in one space, as **20-30A**. The panel publishes it as a 20 A circuit, so a draw of 26 A reads as
130% of its rating and raises an alert, though its two breakers together can carry 50 A.

Copy the second rating, 30, in either of two places. Both show the same copy.

**In the Monitoring tab's table**, for several circuits at once: type 30 in the circuit's field in the **Rating** column, then press Enter or move to the next
field.

```text
Name               Rating
Kitchen Counters   20 A + [ 30 ] = 50 A  ✕
```

`20 A` is the panel's rating, `= 50 A` is what monitoring now judges the space against, and ✕ removes the copy. Leave the field empty for a circuit with a
single breaker. Instructions above the table say what to copy, and a row with monitoring off keeps its field, so you can copy every tandem before turning
monitoring on for it. A field saves when you press Enter or leave it; nothing is saved while you type.

**In the circuit's side panel**, opened from its gear icon, in its **Monitoring** section while monitoring is on for the circuit:

```text
Breaker ratings from the SPAN app
Panel publishes 20 A
Other breaker (A)  [ 30 ]
[ Remove copied ratings ]

Judged against 50 A: 20-30 from your SPAN app (the panel publishes 20 A)
```

With the copy, the same 26 A draw reads as 52% of 50 A. Thresholds, utilization and alert text all use the 50 A. The Breaker Rating sensor, the panel view's
breaker labels and every `breaker_rating_a` the integration reports keep showing the panel's 20 A.

Thresholds are percentages of the rating a circuit is judged against, and copying changes no threshold by itself. If you raised a tandem's thresholds above 100%
to quiet its alerts, they now apply to both breakers together, which can let a real overload pass. The table and the Monitoring section warn of that and offer
**Reset** on the circuit's row and **Use global thresholds** respectively. Where the thresholds above 100% are the global ones, they say so and point to
**Global Settings** on the Monitoring tab instead.

### A quad or tandem-quad across two spaces

If the SPAN app shows two ratings for a circuit that takes two spaces, such as 20-30A, the breaker is a quad or a tandem-quad.

**Example:** the SPAN app shows the Laundry circuit, on two spaces, as **20-30A**: a 30 A two-pole dryer, with a 20 A single-pole washer on one of its legs. One
leg carries the washer and the dryer, the other only the dryer, and the panel reports the average of the two: legs carrying 28 A and 8 A read as 18 A, which
hides the heavier leg.

You can copy the second rating, 30, the same way, but the copy is kept and not applied, and the circuit is still judged against the panel's 20 A. While
monitoring is on for the circuit, the Monitoring tab shows:

```text
Name      Rating
Laundry   20 A + [ 30 ]  ✕
          SPAN Panel MAIN 32 panels report only the average of this circuit's two legs, which can hide an overloaded leg.
          We recommend turning monitoring off for this circuit.  [ Turn off monitoring ]
```

There is no `= …` total, because the copy is not applied. The circuit's side panel says "Judged against: 20 A" and shows the same recommendation and button.
**Turn off monitoring** does what the circuit's monitoring switch does; the integration never turns monitoring off by itself. Its thresholds keep their meaning,
so no threshold warning shows, and setting such a copy raises no notification, since the recommendation shows where you entered it.

The field takes one rating besides the panel's, so a breaker the SPAN app shows with three ratings cannot be copied in full. On a MAIN 32 such a copy would not
be applied either.

### When a copy goes away

Removing the copy, or setting the panel's rating alone, returns the circuit to being judged against the panel's rating. Turning monitoring off keeps the copy
for when monitoring is turned back on, and resetting thresholds leaves it alone.

A notification tells you when the integration removes ratings you copied:

- the rating the panel publishes for the circuit changed, so it is no longer the first of the ratings you copied;
- the circuit's breaker spaces changed;
- the panel no longer reports a rating for the circuit;
- the circuit is no longer on the panel;
- the two breakers together are above the panel's service rating, or the panel no longer publishes one;
- the panel now publishes each circuit of a multi-load breaker with its own rating.

Each line names the circuit and the ratings you copied, and for a changed rating the panel's rating before and after. Where they still apply, copy them again
from the SPAN app.

### Services, attributes and alerts

- **Services:** `span_panel.set_circuit_package_ratings` takes the ratings in the SPAN app's order, such as `[20, 30]`, and
  `span_panel.clear_circuit_package_ratings` removes them. Each rating is a whole number of amps, the first must be the panel's, and both together cannot exceed
  the panel's service rating. A circuit in one or two breaker spaces that the panel publishes a rating for takes a copy.
- **Alerts:** the `{breaker_rating_a}` alert placeholder keeps its name and carries the rating judged against, 50 A for the tandem above. The Breaker Rating
  sensor, the panel view's breaker labels and every `breaker_rating_a` the integration reports show the panel's rating, and energy counter checks keep using it.
- **Attributes:** the panel topology, the monitoring status and the `span_panel_current_alert` event add `monitoring_rating_a`, the rating judged against,
  `monitoring_rating_user_set` beside it, `package_ratings_a`, the ratings copied, and `package_ratings_state`:
  - `applies`: the copy is in force;
  - `pending`: the copy is kept while a rating or space it depends on has not arrived from the panel yet;
  - `averaged_legs`: the copy is kept across two spaces and never applied.
- **Diagnostics:** **Download diagnostics** on the integration's page lists every copy, with the spaces it was made for and its state, and any removal a
  notification reported that you have not yet dismissed.

## Other SPAN panels

This section covers SPAN panels other than the MAIN 32, such as the MAIN 16, MLO 24, MAIN 40 and MLO 48. Unlike a MAIN 32, they publish every breaker of a
multi-load breaker.

### What the panel publishes

Each breaker in a multi-load breaker is its own circuit, with its own name and the rating it was commissioned with: the two halves of a 20-30 tandem are a 20 A
circuit and a 30 A circuit. A tandem is two circuits, a quad two and a tandem-quad three.

The circuits on one multi-load breaker share its one meter and its one relay, and each names the others.

### What you see in Home Assistant

- **Entities:** each circuit keeps its own entities. Its `meter_shared_with` and `relay_shared_with` attributes name the circuits it shares with, and they also
  appear on its Breaker switch (see [Circuit Power Sensor Attributes](README.md#circuit-power-sensor-attributes)).
- **Power and energy:** every circuit on the breaker reports the same meter, so each shows the breaker's combined reading. Do not add them together, in the
  Energy dashboard or in a template: count one of them.
- **Breaker switch and shed priority:** the circuits share one relay, so switching one circuit or changing its shed priority changes them all. The others follow
  once the panel reports them.
- **Panel view:** the dashboard draws the breaker as one slot covering all of its spaces.
- **Current monitoring:** the breaker is judged once, as a group, against what its heaviest space can carry. For each space, the ratings of the circuits that
  list it are added, and the largest total is the group's capacity, because the panel reports a two-pole meter's current as the larger of its two legs.
  - A tandem or a quad comes to the sum of its ratings.
  - A tandem-quad comes to its two-pole rating plus the larger of its single-pole ones.
  - A threshold override set on any circuit in the group applies to the whole group.
- **Nothing to copy:** these panels already publish every breaker's rating, so the dashboard offers no field for copying ratings from the SPAN app, and the
  services refuse one.

## Per-leg current: what SPAN may publish

SPAN may at some point publish each leg's current, `current-a` and `current-b`, on two-pole circuits, a MAIN 32's included. No date has been given; if it ships,
it will appear in SPAN's changelog (see [spanio/SPAN-API-Client-Docs#21](https://github.com/spanio/SPAN-API-Client-Docs/issues/21)).

If it does, it would address these gaps:

- **A quad or tandem-quad on a MAIN 32.** The panel reports only the average of the two legs, which can hide an overloaded leg, so today the dashboard
  recommends turning monitoring off for it. Each leg's own current would show the heavier leg. Judging each leg would also need to know which breakers sit on
  it, which a MAIN 32 does not publish. A copy you made for such a circuit stays stored.
- **A tandem-quad on other SPAN panels.** The panel reports the larger leg's current without saying which leg that is, and its two legs carry different
  single-pole breakers, so monitoring judges the reading against the leg that can carry more, which can let an overload on the other leg pass. Each leg's own
  current would let each leg be judged against the breakers on it.

It would not change a **tandem in one space on a MAIN 32**: both breakers sit on the same leg behind one sensor, so there is no second leg to report. Copying
its second rating from the SPAN app remains the fix there.
