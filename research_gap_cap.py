# -*- coding: utf-8 -*-
"""
research_gap_cap.py -- does the entry cap (no fill above listing high +2.5%) exclude the biggest winners, and would widening it pay?

PRE-REGISTRATION (written before running, 2026-10-17; motivation: 10 of the 14 +100%-in-4-months IPOs that policy P missed had a first eligible
breakout that gapped above the +2.5% limit; research_big_movers.py)
------------------------------------------------------------------------------------------------------------------------------------------
Variants: the resting-order limit (fill_cap_pct) = 2.5% (policy P), 5%, 8%, 12%, 20%, no cap (1000%).  Everything else is identical: causal entry (no same-day
volume), age 5-60, live exits (close-based, next open), every other live gate (liquidity, R:R >= 1.25, leader score), costs 1.0% round trip.  A gap-up beyond the
old limit is assumed filled at the open plus 0.3% slippage (base case) and plus 1.0% (stress), i.e. a marketable limit placed at the open.
Judged on the ADDED trades (in a wider-cap variant but not in policy P) and on the whole book.
Adoption bar (repo standard): mean net of the added trades positive with a 90% symbol-cluster CI excluding 0, still positive after dropping the single best added
trade, no more than one negative year among years with 5+ added trades, and the whole-book PF (incl. open, closed-only, ex-top-3) not lower than policy P.
Reported also: the share of +50% / +100% IPOs that get a position.

  .venv\\Scripts\\python.exe research_gap_cap.py --cache data.pkl
"""
import argparse
import logging
import pickle
from dataclasses import replace

import numpy as np
import pandas as pd

logging.disable(logging.CRITICAL)
from core.honest_backtest import LIVE_FAITHFUL, Params, simulate_symbol_cross
from research_alpha_lab import cluster_boot, pf
from research_big_movers import build as build_big

CFG = replace(LIVE_FAITHFUL, exit_fill="eod_next_open")
COST = 1.0


def run(candle_map, listing_map, cap, slip):
    out = []
    for sym, df in candle_map.items():
        out.extend(simulate_symbol_cross(df, sym, listing_map.get(sym, {}), Params(), slip_pct=slip, fill_cap_pct=cap, min_age=5, include_open=True,
                                         cfg=CFG, causal=True, vol_gate=False))
    d = pd.DataFrame(out)
    d["net"] = d.pnl_pct - COST
    d["entry_date"] = pd.to_datetime(d.entry_date)
    d["year"] = d.entry_date.dt.year
    d["open"] = d.open.astype(bool)
    d["key"] = d.symbol + "|" + d.entry_date.dt.strftime("%Y-%m-%d")
    return d


def book(d):
    s = np.sort(d.net.values)[::-1]
    cl = d[~d.open]
    return f"N={len(d):3d} net {d.net.mean():+5.2f}%  PF incl-open {pf(d.net.values):.2f} | closed {pf(cl.net.values):.2f} | ex-top3 {pf(s[3:]):.2f}  outliers>=25%: {int((d.net >= 25).sum())}  >=75%: {int((d.net >= 75).sum())}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    a = ap.parse_args()
    with open(a.cache, "rb") as fh:
        listing_map, candle_map = pickle.load(fh)
    A, _ = build_big(listing_map, candle_map)
    w50, w100 = set(A.symbol[A.w50]), set(A.symbol[A.w100])
    base = run(candle_map, listing_map, 2.5, 0.3)
    print("POLICY P (cap 2.5%):", book(base))
    print(f"   +50% IPOs with a position: {len(w50 & set(base.symbol))}/{len(w50)}   +100%: {len(w100 & set(base.symbol))}/{len(w100)}")
    base_keys = set(base.key)
    for slip in (0.3, 1.0):
        print("\n" + "=" * 118 + f"\nSLIP on every fill {slip}% (gap-up fills at the open plus slip)\n" + "=" * 118)
        for cap in (5.0, 8.0, 12.0, 20.0, 1000.0):
            v = run(candle_map, listing_map, cap, slip)
            add = v[~v.key.isin(base_keys)]
            lab = "no cap" if cap >= 1000 else f"cap {cap:g}%"
            print(f"\n{lab:<8} whole book: {book(v)}")
            print(f"         +50% IPOs with a position {len(w50 & set(v.symbol))}/{len(w50)}, +100% {len(w100 & set(v.symbol))}/{len(w100)}")
            if len(add) < 3:
                print("         added trades:", len(add))
                continue
            m, lo, hi = cluster_boot(add.net.values, add.symbol.values)
            s = np.sort(add.net.values)[::-1]
            yrs = {int(y): (int(len(g)), round(float(g.net.mean()), 1)) for y, g in add.groupby("year")}
            neg = sum(1 for n, mu in yrs.values() if n >= 5 and mu < 0)
            print(f"         ADDED trades: N={len(add)} mean net {m:+.2f}% (CI {lo:+.1f}..{hi:+.1f}) win {100 * (add.net > 0).mean():.0f}% median {add.net.median():+.1f}% "
                  f"mean without best {s[1:].mean():+.2f}% | PF {pf(add.net.values):.2f} | outliers>=25%: {int((add.net >= 25).sum())} | negative years (5+ trades): {neg}")
            print(f"         added by year (n, mean): {yrs}")
            ext = (add.entry_price / add.signal_price - 1) * 100 if "signal_price" in add else None
            top = add.sort_values("net", ascending=False).head(5)
            print("         best added:", [(r.symbol, str(r.entry_date.date()), round(r.net, 0)) for r in top.itertuples()],
                  "| worst:", [(r.symbol, round(r.net, 0)) for r in add.sort_values("net").head(3).itertuples()])


if __name__ == "__main__":
    main()
