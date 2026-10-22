# -*- coding: utf-8 -*-
"""
s3_scorecard.py -- is the PAPER_ONLY S3 book working, and is it good?  Reads MongoDB (s3_paper_orders, s3_paper_positions, s3_paper_runs).

  python s3_scorecard.py

Prints (1) operations: did the daily alert job run, were the orders filled and at what price versus the trigger; (2) the forward result of the
closed paper trades with the verdict rules fixed in core/s3_scorecard.py (INSUFFICIENT until 40 closed trades; then GOOD / CONTINUE / BAD).
It never says "good" on fewer than 40 closed trades.  The replay evidence is in LEARNINGS 20 / 20.3 / 20.4 and research_s3_validation.py.
"""

from datetime import datetime

import pandas as pd

from core import s3_scorecard as sc


def main():
    import order_sheet as osh
    from db import (daily_candles_col, s3_paper_orders_col, s3_paper_positions_col, s3_paper_runs_col)
    if s3_paper_orders_col is None:
        raise SystemExit("MongoDB is not connected")
    today = pd.Timestamp(datetime.now().date())
    orders = list(s3_paper_orders_col.find({}, {"_id": 0}))
    positions = list(s3_paper_positions_col.find({}, {"_id": 0}))
    runs = list(s3_paper_runs_col.find({}, {"_id": 0}))
    from core.setup3 import S3_PAPER_COHORT, S3_PAPER_VERSION
    current = [p for p in positions if p.get("s3_version") in S3_PAPER_COHORT]
    other = len(positions) - len(current)
    closed = [p for p in current if p.get("status") == "CLOSED"]
    open_ = {p["symbol"]: p for p in current if p.get("status") == "OPEN"}
    candles = {}
    if open_:
        for d in daily_candles_col.find({"symbol": {"$in": list(open_)}}, {"_id": 0, "symbol": 1, "candles": 1}):
            df = pd.DataFrame(d["candles"])
            df["DATE"] = pd.to_datetime(df["DATE"])
            for c in ("OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"):
                df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
            candles[d["symbol"]] = df.sort_values("DATE").reset_index(drop=True)
    rows = osh.s3_position_rows(open_, candles)
    open_pnls = [r["pnl_pct"] for r in rows if not r.get("error") and r.get("status") == "OPEN"]

    ops = sc.operations(runs, today)
    fs = sc.fill_stats(orders)
    print("S3 PAPER SCORECARD  (forward paper trades only; replay is in LEARNINGS 20-20.4)\n")
    print("OPERATIONS")
    print(f"  daily job heartbeats in the last {ops['window']} business days: {ops['days_with_run']} ({ops['coverage']:.0f}%) -> {'OK' if ops['ok'] else 'NOT OK'}"
          + (f"; missing {ops['missing']}" if ops["missing"] else "") + f"; runs flagged stale (all time): {ops['stale_runs']}")
    print(f"  orders stored {fs['placed']}, filled {fs['filled']} ({fs['fill_rate']:.0f}% of orders)" if fs["placed"] else "  no orders stored yet")
    if fs["filled"]:
        print(f"  mean fill vs trigger {fs['mean_slip_vs_trigger']:+.2f}% (replay assumed +0.30%); fills without a price: {fs['missing_fill_price']}")
    print(f"  rules version {S3_PAPER_VERSION}: {len(closed)} closed, {len(open_)} open" + (f"  ({other} position(s) of other rule versions are NOT counted)" if other else ""))
    e = sc.evaluate(closed, open_pnls)
    print("\nFORWARD RESULT (net of 1.0% round-trip cost; verdict on closed trades only)")
    if e["n_closed"]:
        print(f"  closed N={e['n_closed']}  mean {e['mean']:+.2f}%  median {e['median']:+.2f}%  win {e['win']:.0f}%  CI90 [{e['ci_lo']:+.1f}, {e['ci_hi']:+.1f}]  "
              f"PF closed {e['pf_closed']:.2f}  PF incl open {e['pf_incl_open']:.2f}  ex-best {e['mean_ex_best']:+.2f}%  best {e['best']:+.1f}%  worst {e['worst']:+.1f}%  (replay mean +{e['replay_mean']:.0f}%)")
    print(f"  VERDICT: {e['verdict']}  ({e['reason']})")
    for r in rows:
        if not r.get("error") and r["status"] == "OPEN":
            print(f"  open {r['symbol']:<12} {r['pnl_pct']:+.1f}% (peak {r['max_runup']:+.1f}%) alert level {r['trailing']:.2f}{'  STOP ALERT' if r['alert'] else ''}")


if __name__ == "__main__":
    main()
