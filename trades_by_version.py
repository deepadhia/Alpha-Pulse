# -*- coding: utf-8 -*-
"""
trades_by_version.py -- READ-ONLY: positions grouped by the `version` tag they were stored with, next to what the audit says about them.

  python trades_by_version.py            one line per (version, native/backfilled)
  python trades_by_version.py --list     also list every position

Columns: n, open (ACTIVE / PAPER_ONLY), closed (CLOSED / PAPER_CLOSED), realised mean and win rate of the closed rows (stored pnl_pct, BEFORE costs),
and how many rows fail core/trade_guard.reconcile_position.  Rows tagged 3.7.0 or later are written by the guarded path (core/versioning.TRUSTED_RECORD_VERSIONS);
older tags are not trusted even when they pass.  Counts of ledger events per code version are printed at the end.
The mean here is a plain average of stored pnl_pct on very few trades: use it to see what is in each version, never as a performance claim
(report performance with paper_scorecard.py).
"""

import argparse
import logging
import sys
from collections import defaultdict

import pandas as pd

logging.disable(logging.CRITICAL)

from core.trade_guard import reconcile_position, to_date
from core.versioning import TRUSTED_RECORD_VERSIONS


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args(argv)

    import db
    if db.positions_col is None:
        print("MongoDB is not configured (MONGO_URI)")
        return 2
    groups = defaultdict(list)
    for p in db.positions_col.find({}, {"_id": 0}):
        doc = db.daily_candles_col.find_one({"symbol": p["symbol"]}, {"_id": 0, "candles": 1}) if db.daily_candles_col is not None else None
        candles = pd.DataFrame(doc["candles"]) if doc and doc.get("candles") else None
        p["_flags"] = reconcile_position(p, candles)
        groups[(str(p.get("version") or "unknown"), "backfilled" if p.get("_backfilled") else "native")].append(p)

    print(f"{'version':<9} {'kind':<11} {'n':>3} {'open':>5} {'closed':>7} {'mean%':>7} {'win%':>6} {'flagged':>8}  trusted-record version")
    for (ver, kind), rows in sorted(groups.items()):
        closed = [r for r in rows if r.get("status") in ("CLOSED", "PAPER_CLOSED") and r.get("pnl_pct") is not None]
        pnl = [float(r["pnl_pct"]) for r in closed]
        flagged = sum(1 for r in rows if r["_flags"])
        trusted = "yes" if (ver in TRUSTED_RECORD_VERSIONS and kind == "native") else "no"
        print(f"{ver:<9} {kind:<11} {len(rows):>3} {len(rows) - len(closed):>5} {len(closed):>7} "
              f"{(sum(pnl) / len(pnl)) if pnl else float('nan'):>7.2f} {(100.0 * sum(1 for x in pnl if x > 0) / len(pnl)) if pnl else float('nan'):>6.1f} "
              f"{flagged:>8}  {trusted}")
        if args.list:
            for r in sorted(rows, key=lambda r: str(to_date(r.get("entry_date")))):
                print(f"    {r['symbol']:<12} {r.get('status'):<12} entry {to_date(r.get('entry_date'))} @ {r.get('entry_price')}  "
                      f"pnl {r.get('pnl_pct')}  " + ("; ".join(r["_flags"]) if r["_flags"] else "ok"))
    if not groups:
        print("(no positions)")

    led = getattr(db, "trade_ledger_col", None)
    if led is not None:
        counts = defaultdict(int)
        for e in led.find({}, {"_id": 0, "code_version": 1, "event_type": 1}):
            counts[(e.get("code_version") or "unknown", e.get("event_type"))] += 1
        print("\nledger events by code version:")
        for (v, t), n in sorted(counts.items()):
            print(f"  {v:<8} {t:<18} {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
