# -*- coding: utf-8 -*-
"""
px1_shadow.py -- forward test of "policy P with the time-based exits off" (X1).  READ-ONLY: reads the candle cache, writes nothing, sends nothing.

  python px1_shadow.py                      # candles and listing records from MongoDB
  python px1_shadow.py --cache data.pkl     # a research pickle instead (parity checks; use --start to look at older entries)
  python px1_shadow.py --list               # also print every counted pair

It re-runs the replay engine on the CURRENT candle cache, keeps the entries from core.px1_shadow.PX1_START_DATE on, and compares the live exits
with X1 on identical entries (rules and verdict thresholds are fixed in core/px1_shadow.py).  Nothing here changes a live rule: P keeps its time
stops.  Run `python refresh_stale_caches.py` first if this prints a stale-cache warning (held IPOs older than 60 days are not refetched by the scanners).
"""

import argparse
import pickle

import pandas as pd

from core import px1_shadow as px


def load_from_mongo():
    from db import daily_candles_col, listing_data_col
    if daily_candles_col is None or listing_data_col is None:
        raise SystemExit("MongoDB is not connected")
    listing = {d["symbol"]: d for d in listing_data_col.find({}, {"symbol": 1, "listing_date": 1, "listing_day_high": 1, "listing_day_low": 1,
                                                                    "listing_day_close": 1, "listing_day_volume": 1, "is_nse_addition": 1,
                                                                    "bse_liquidity_passed": 1}) if d.get("symbol")}
    candles = {}
    for d in daily_candles_col.find({}, {"_id": 0, "symbol": 1, "candles": 1}):
        sym, cs = d.get("symbol"), d.get("candles")
        if not sym or not cs or len(cs) < 10:
            continue
        df = pd.DataFrame(cs)
        df["DATE"] = pd.to_datetime(df["DATE"])
        for c in ("OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"):
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
        candles[sym] = df.sort_values("DATE").reset_index(drop=True)
    return listing, candles


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=None, help="research pickle (listing_map, candle_map) instead of MongoDB")
    ap.add_argument("--start", default=px.PX1_START_DATE, help="first entry date (research only; the registered start is fixed in core/px1_shadow.py)")
    ap.add_argument("--list", action="store_true", help="print every pair")
    a = ap.parse_args(argv)
    if a.cache:
        listing, candles = pickle.load(open(a.cache, "rb"))
    else:
        listing, candles = load_from_mongo()
    asof = max(pd.Timestamp(df["DATE"].iloc[-1]) for df in candles.values())
    pairs = px.forward_pairs(candles, listing, a.start)
    e = px.evaluate(pairs)

    print(f"X1 SHADOW  (PAPER_ONLY forward replay; version {px.PX1_SHADOW_VERSION}, rules {px.shadow_rules_fingerprint()})")
    print(f"  candles to {asof.date()}; entries counted from {a.start}" + ("" if a.start == px.PX1_START_DATE else "   ** NOT the registered start: research view only **"))
    stale = px.stale_symbols(pairs, asof)
    if stale:
        print(f"  WARNING stale candle cache for held names {stale}: run `python refresh_stale_caches.py` (dry run) and `--apply`, then re-run this")
    print(f"  pairs (live leg closed): {e['n_pairs']}  (X1 leg still open, marked to market: {e['n_x1_open']})   entries with the live leg still open: "
          f"{int(pairs.live_open.sum()) if len(pairs) else 0}")
    if e["n_pairs"]:
        print(f"  mean gain X1 - live {e['mean_gain']:+.2f}pp per trade  CI90 [{e['ci_lo']:+.2f}, {e['ci_hi']:+.2f}]  ex-largest {e['mean_gain_ex_largest']:+.2f}pp  "
              f"X1 better {e['share_better']:.0f}% / worse {e['share_worse']:.0f}%")
        print(f"  net per trade (1.0% costs): live {e['live_mean_net']:+.2f}%  X1 {e['x1_mean_net']:+.2f}%   PF live {e['live_pf']:.2f}  X1 {e['x1_pf']:.2f}   "
              f"mean days held live {e['live_mean_days']:.0f}  X1 {e['x1_mean_days']:.0f}")
    print(f"  VERDICT: {e['verdict']}  ({e['reason']})")
    print("  replay reference (LEARNINGS 22.1): pooled 2006-26 gain +3.17pp per trade, 2006-22 holdout +1.23pp; idealised entries, no slot competition")
    if a.list and len(pairs):
        cols = ["symbol", "entry_date", "live_pnl", "live_exit_date", "live_exit_reason", "x1_pnl", "x1_exit_date", "x1_open", "x1_days"]
        print(pairs[cols].assign(live_exit_reason=lambda z: z.live_exit_reason.str[:26]).sort_values("entry_date").to_string(index=False))
    held = pairs[pairs.x1_open & ~pairs.live_open] if len(pairs) else pairs
    for r in held.itertuples():
        print(f"  X1 still holding {r.symbol:<12} entry {r.entry_date.date()}  live exited {r.live_exit_date} at {r.live_pnl:+.1f}%  X1 now {r.x1_pnl:+.1f}% after {r.x1_days} days")


if __name__ == "__main__":
    main()
