# -*- coding: utf-8 -*-
"""
reconcile_trades.py -- READ-ONLY check of the stored `positions` against the daily candles (core/trade_guard.reconcile_position).

  python reconcile_trades.py                  native (not backfilled) positions only
  python reconcile_trades.py --all            include backfilled research rows
  python reconcile_trades.py --since 2026-09-29

Flags: entry on a non-trading day, entry above the day's high or below its low, exit outside the day's range, stored profit that does not match the
stored prices, missing candles.  Exit code 1 when any native position is flagged.  Writes nothing.  Run it after each close and before reading any
performance number: rows that fail here must not be quoted as evidence.
"""

import argparse
import logging
import sys

import pandas as pd

logging.disable(logging.CRITICAL)

from core.trade_guard import reconcile_position, to_date


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="include _backfilled research rows")
    ap.add_argument("--since", default=None, help="only entries on or after this date (YYYY-MM-DD)")
    args = ap.parse_args(argv)

    import db
    if db.positions_col is None or db.daily_candles_col is None:
        print("MongoDB is not configured (MONGO_URI); nothing to reconcile")
        return 2
    since = to_date(args.since) if args.since else None
    rows, native_flagged, n_native = [], 0, 0
    for p in db.positions_col.find({}, {"_id": 0}):
        bf = bool(p.get("_backfilled"))
        if bf and not args.all:
            continue
        ed = to_date(p.get("entry_date"))
        if since and (ed is None or ed < since):
            continue
        doc = db.daily_candles_col.find_one({"symbol": p["symbol"]}, {"_id": 0, "candles": 1})
        candles = pd.DataFrame(doc["candles"]) if doc and doc.get("candles") else None
        flags = reconcile_position(p, candles)
        n_native += 0 if bf else 1
        native_flagged += 0 if (bf or not flags) else 1
        rows.append((p["symbol"], "backfilled" if bf else "native", p.get("status"), str(ed), p.get("entry_price"), p.get("exit_price"), flags))
    for sym, kind, status, ed, ep, xp, flags in sorted(rows, key=lambda r: (r[1], r[3])):
        print(f"{sym:<12} {kind:<10} {str(status):<12} entry {ed} @ {ep}  exit @ {xp}  " + ("; ".join(flags) if flags else "ok"))
    print(f"\n{len(rows)} position(s) checked; native flagged {native_flagged} of {n_native}")
    return 1 if native_flagged else 0


if __name__ == "__main__":
    sys.exit(main())
