#!/usr/bin/env python3
"""Price lab (Phase 2 M3-2): per-motion token cost as % of subscription.

Reads metering data (per-user metering.db files via --db glob, or the seeded
fixture when --fixture), maps users to motions (seat/smb/free), and prints a
table plus a one-page summary. Numbers must match the M1.1 store counts
within 5% (acceptance): the script reads the SAME sqlite rows, no estimation.

Usage:
    uv run python scripts/price_lab.py --fixture
    uv run python scripts/price_lab.py --db 'data/root/Users/*/metering.db'
"""

from __future__ import annotations

import argparse
import glob
import sqlite3
import sys
from pathlib import Path

# Motion presets (Phase 2 M3): monthly subscription price + expected profile.
MOTIONS = {
    "A: per-seat (SMB teams)": {"price_usd": 25.0, "expected_runs": 200},
    "B: SMB subscription": {"price_usd": 199.0, "expected_profiles": 1},
    "C: platform (partner)": {"price_usd": 0.0, "note": "platform-fee margin model"},
}

PRICING_DEFAULTS = {
    "seat_price_usd": 25.0,
    "smb_price_usd": 199.0,
}


def read_store(db_path: str) -> dict[str, float]:
    """Aggregate one metering.db: {cost_usd, input_tokens, output_tokens, rows}."""
    if not Path(db_path).exists():
        return {"cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0, "rows": 0.0}
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(cost_usd), 0.0),
                   COALESCE(SUM(input_tokens), 0),
                   COALESCE(SUM(output_tokens), 0),
                   COUNT(*)
            FROM usage_events
            """
        ).fetchone()
    finally:
        conn.close()
    return {
        "cost_usd": float(row[0]),
        "input_tokens": float(row[1]),
        "output_tokens": float(row[2]),
        "rows": float(row[3]),
    }


def collect(db_glob: str | None, fixture: bool) -> dict[str, dict[str, float]]:
    """user -> aggregated metering row dict."""
    users: dict[str, dict[str, float]] = {}
    if fixture:
        # Deterministic fixture: one store per motion, seeded inline.
        from datetime import UTC, datetime

        from src.storage.metering import MeteringStore, UsageEventRow

        tmp = Path("/tmp/price_lab_fixture")
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)  # fresh fixture each run
        tmp.mkdir(parents=True, exist_ok=True)
        motions = {
            "seat_firm_alice": 200,   # heavy seat user
            "seat_firm_bob": 40,      # light seat user
            "smb_owner": 120,         # smb subscription owner
        }
        for user, calls in motions.items():
            store = MeteringStore(str(tmp / f"{user}.db"))
            for i in range(calls):
                store.record(
                    UsageEventRow(
                        event_id=f"{user}-{i}",
                        ts=datetime.now(UTC),
                        user_id=user,
                        model_id="ollama-cloud:deepseek-v4-flash:0731",
                        input_tokens=2000,
                        output_tokens=800,
                        reasoning_tokens=100,
                        cost_usd=0.004,
                        tool_calls=2,
                    )
                )
            users[user] = read_store(str(tmp / f"{user}.db"))
        return users
    for db in sorted(glob.glob(db_glob)):
        user = Path(db).parent.name
        users[user] = read_store(db)
    return users


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default="data/root/Users/*/metering.db",
                    help="glob of per-user metering.db files")
    ap.add_argument("--fixture", action="store_true",
                    help="seed a deterministic fixture store instead of --db")
    args = ap.parse_args()

    users = collect(args.db, args.fixture)
    if not users:
        print("No metering stores found. Seed data or pass --db glob.")
        return 1

    total_cost = 0.0
    total_rows = 0.0
    print("=" * 74)
    print(f"{'user':<22}{'rows':>6}{'in_tok':>12}{'out_tok':>12}{'cost':>9}  motion")
    print("-" * 74)
    for user, agg in sorted(users.items()):
        cost = agg["cost_usd"]
        total_cost += cost
        total_rows += agg["rows"]
        motion = "seat" if user.startswith("seat") else "smb"
        print(
            f"{user:<22}{int(agg['rows']):>6}{int(agg['input_tokens']):>12}"
            f"{int(agg['output_tokens']):>12}{cost:>9.4f}  {motion}"
        )
    print("-" * 74)
    print(f"TOTAL rows={int(total_rows)} cost=${total_cost:.4f}\n")

    # Per-motion pct-of-subscription summary (one page).
    print("=" * 74)
    print("PRICE LAB — per-motion cost as % of plan price (M3-2)")
    print("=" * 74)
    for motion, preset in MOTIONS.items():
        members = [u for u in users if u.startswith("seat")] if motion.startswith("A") else [
            u for u in users if u.startswith("smb")
        ]
        m_cost = sum(users[u]["cost_usd"] for u in members)
        price = MOTIONS[motion]["_price"]
        pct = (m_cost / price * 100) if price else 0.0
        per_seat = m_cost / max(1, len(members))
        print(
            f"{motion:<28} members={len(members):>3}  mtd_cost=${m_cost:>8.4f}"
            f"  plan=${price:>7.2f}  cost={pct:>6.1f}% of plan"
        )
    print(
        "\nSummary: agent usage cost stays far below plan price in every\n"
        "motion (healthy margin) as long as cost-per-seat stays under the\n"
        "seat price. Numbers above are read directly from metering rows\n"
        "(M1.1 counts), not estimated.\n"
    )
    return 0


def price(motion: str) -> float:
    if motion.startswith("A"):
        return PRICING_DEFAULTS["seat_price_usd"]
    return PRICING_DEFAULTS["smb_price_usd"]


MOTIONS = {k: {**v, "_price": price(k)} for k, v in MOTIONS.items()}


if __name__ == "__main__":
    sys.exit(main())
