#!/usr/bin/env python3
"""
Fix false energy spikes in HA long-term statistics caused by span-panel-api
2.5.2 property clearing on lifecycle resets.

Usage (run locally — HA must be stopped first):
  1. ha core stop
  2. scp root@homeassistant.home.arpa:/config/home-assistant_v2.db /tmp/ha.db
  3. python3 scripts/fix_energy_spike_statistics.py /tmp/ha.db          # dry run
  4. python3 scripts/fix_energy_spike_statistics.py /tmp/ha.db --apply  # apply
  5. scp /tmp/ha.db root@homeassistant.home.arpa:/config/home-assistant_v2.db
  6. ha core start
"""

from __future__ import annotations

import argparse
import sqlite3

# Minimum jump in state (Wh) per HOUR to qualify as a spike.
# 50 kWh/hr is far beyond any residential circuit.
# The threshold is pro-rated by the actual gap between rows to avoid
# flagging legitimate accumulation over multi-hour gaps in sparse data.
SPIKE_THRESHOLD_WH_PER_HOUR = 50_000.0

# Interval between rows in seconds for each statistics table.
# statistics = hourly (3600s), statistics_short_term = 5 min (300s).
TABLE_INTERVAL = {
    "statistics": 3600,
    "statistics_short_term": 300,
}


def find_spike(
    cur: sqlite3.Cursor,
    table: str,
    metadata_id: int,
) -> tuple[float, float] | None:
    """Find the first anomalous upward jump. Returns (start_ts, delta) or None."""
    cur.execute(
        f"SELECT start_ts, state, sum FROM {table} "
        "WHERE metadata_id = ? ORDER BY start_ts",
        (metadata_id,),
    )
    rows = cur.fetchall()
    if len(rows) < 2:
        return None

    for i in range(1, len(rows)):
        prev_ts = rows[i - 1][0]
        curr_ts = rows[i][0]
        prev_state = rows[i - 1][1]
        curr_state = rows[i][1]
        if prev_state is None or curr_state is None:
            continue

        delta = curr_state - prev_state
        gap_seconds = curr_ts - prev_ts
        gap_hours = max(gap_seconds / 3600.0, 1.0)
        threshold = SPIKE_THRESHOLD_WH_PER_HOUR * gap_hours

        if delta >= threshold:
            return (rows[i][0], delta)

    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Fix energy spike statistics")
    parser.add_argument("db_path", help="Path to home-assistant_v2.db")
    parser.add_argument(
        "--apply", action="store_true", help="Actually modify the database"
    )
    args = parser.parse_args()

    mode = "APPLY" if args.apply else "DRY RUN"
    print(f"=== Energy spike statistics fix ({mode}) ===\n")

    conn = sqlite3.connect(args.db_path)
    cur = conn.cursor()

    cur.execute(
        "SELECT id, statistic_id, unit_of_measurement "
        "FROM statistics_meta "
        "WHERE statistic_id LIKE '%span_panel%energy%' "
        "ORDER BY statistic_id"
    )
    sensors = cur.fetchall()
    print(f"Found {len(sensors)} span_panel energy sensors\n")

    total_fixed = 0

    for metadata_id, statistic_id, unit in sensors:
        for table in ("statistics", "statistics_short_term"):
            result = find_spike(cur, table, metadata_id)
            if result is None:
                continue

            spike_ts, delta = result

            # Count affected rows
            cur.execute(
                f"SELECT COUNT(*) FROM {table} "
                "WHERE metadata_id = ? AND start_ts >= ?",
                (metadata_id, spike_ts),
            )
            count = cur.fetchone()[0]

            print(
                f"  {statistic_id}\n"
                f"    {table}: spike of +{delta:,.1f} {unit} at ts={spike_ts}\n"
                f"    {'WOULD ADJUST' if not args.apply else 'ADJUSTING'} "
                f"{count} rows by -{delta:,.1f} {unit}"
            )

            if args.apply:
                cur.execute(
                    f"UPDATE {table} "
                    "SET state = state - ?, sum = sum - ? "
                    "WHERE metadata_id = ? AND start_ts >= ?",
                    (delta, delta, metadata_id, spike_ts),
                )
                print(f"    -> {cur.rowcount} rows updated")

            total_fixed += count
            print()

    if args.apply and total_fixed > 0:
        conn.commit()
        print(f"=== Committed corrections to {total_fixed} rows ===")
    elif total_fixed > 0:
        print(f"=== DRY RUN: {total_fixed} rows would be corrected ===")
        print("Re-run with --apply to fix")
    else:
        print("=== No spikes detected ===")

    conn.close()


if __name__ == "__main__":
    main()
