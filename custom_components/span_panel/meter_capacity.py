"""What a meter can carry: the one rule every reader of a capacity applies.

A leaf: it imports only the standard library, so the package-ratings rule and
current monitoring share it without importing each other.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable


def meter_capacity_a(members: Iterable[tuple[Collection[int], float | None]]) -> float | None:
    """Return the current a meter can carry: the rating total of its heaviest space.

    `members` is each breaker's spaces and rating. For each space, the ratings of
    the members that list it add up, since each of those breakers carries that
    space's current; the capacity is the largest total. The panel reports a
    two-pole meter's current as the larger of its two legs, so the space with
    the most capacity behind it is the one that reading is held to. A tandem's
    two breakers list one space and add up; a quad's two-pole circuits list both
    spaces and add up on each; a tandem-quad comes to its two-pole circuit plus
    the larger of its single-pole ones.

    None while any member's rating is unknown, where a partial total would
    understate the meter, and where a member lists no space, since its load
    cannot be placed on a leg.
    """
    totals: dict[int, float] = {}
    for spaces, rating in members:
        if rating is None or not spaces:
            return None
        for space in set(spaces):
            totals[space] = totals.get(space, 0.0) + rating
    return max(totals.values()) if totals else None
