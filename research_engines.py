# -*- coding: utf-8 -*-
"""
research_engines.py -- per-engine, per-year results on the FULL history (read-only)

Loads the complete candle history fresh from MongoDB (or a cache), runs the live-faithful simulator and
reports LISTING_BREAKOUT and SHELF_BREAKOUT separately, year by year, plus an account-level result for
each engine at Rs 1 lakh with Indian delivery costs.  Run under the current 60-day lifecycle and under the
old 730-day lifecycle (the rules six live positions were opened under).

  python research_engines.py [--cache data.pkl] [--refresh]
"""

import argparse
import logging
import os
import pickle
from dataclasses import replace

import numpy as np
import pandas as pd

logging.disable(logging.CRITICAL)

from core.execution_model import CostModel
from core.honest_backtest import LIVE_FAITHFUL, Params, simulate_symbol
from core.portfolio_sim import PortfolioConfig, run_portfolio

FRICTION = 1.0


def load(cache, refresh):
    if cache and os.path.exists(cache) and not refresh:
        return pickle.load(open(cache, "rb"))
    from run_latest_rules_backtest import load_dataset
    data = load_dataset()
    if cache:
        pickle.dump(data, open(cache, "wb"))
    return data


def pf(x):
    w, l = x[x > 0].sum(), -x[x < 0].sum()
    return w / l if l > 0 else np.inf


def row(label, d):
    if len(d) == 0:
        return f"  {label:<14} no trades"
    net = d.pnl_pct - FRICTION
    top = d.pnl_pct.nlargest(1).sum() / d.pnl_pct.sum() * 100 if d.pnl_pct.sum() > 0 else float("nan")
    return (f"  {label:<14} N={len(d):3d}  win(net)={100 * (net > 0).mean():5.1f}%  avg gross={d.pnl_pct.mean():+6.2f}%  "
            f"avg net={net.mean():+6.2f}%  PF(net)={pf(net):4.2f}  best trade={d.pnl_pct.max():+6.1f}%  "
            f"best share of profit={top:4.0f}%")


def report(title, trades, capital=100000.0):
    t = pd.DataFrame([{k: v for k, v in x.items() if k != "path"} for x in trades])
    print(f"\n{'=' * 110}\n{title}\n{'=' * 110}")
    if t.empty:
        print("no trades")
        return
    t["year"] = pd.to_datetime(t.entry_date).dt.year
    for eng in ("LISTING_BREAKOUT", "SHELF_BREAKOUT", "BASE_BREAKOUT", "ALL"):
        d = t if eng == "ALL" else t[t.type == eng]
        if eng == "BASE_BREAKOUT" and d.empty:
            continue
        print(f"\n{eng}")
        print(row("full history", d))
        for y, g in d.groupby("year"):
            print(row(str(y), g))
        # without the three best trades
        if len(d) > 6:
            rest = d.drop(d.pnl_pct.nlargest(3).index)
            print(row("ex top-3", rest))
    print("\nAccount level (Rs {:,.0f}, 5/7 slots, Indian delivery costs + 0.3%/side slippage):".format(capital))
    for eng, sub in (("LISTING only", [x for x in trades if x["type"] == "LISTING_BREAKOUT"]),
                     ("SHELF only", [x for x in trades if x["type"] == "SHELF_BREAKOUT"]),
                     ("BOTH", trades)):
        r = run_portfolio(sub, PortfolioConfig(capital=capital, cost=CostModel()))["summary"]
        if not r.get("trades_taken"):
            print(f"  {eng:<14} no trades taken")
            continue
        print(f"  {eng:<14} taken {r['trades_taken']:3d}/{r['signals']:3d}  return {r['total_return_pct']:+7.1f}%  "
              f"CAGR {r['cagr_pct']:+6.1f}%  maxDD {r.get('max_drawdown_pct', float('nan')):6.1f}%  "
              f"PF {r['profit_factor']:.2f}  top-3 share {r['top3_share_of_profit_pct']:.0f}%")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=None)
    ap.add_argument("--refresh", action="store_true")
    a = ap.parse_args()
    listing_map, candle_map = load(a.cache, a.refresh)
    last = max(pd.Timestamp(df.DATE.iloc[-1]) for df in candle_map.values())
    first = min(pd.Timestamp(df.DATE.iloc[0]) for df in candle_map.values())
    print(f"universe: {len(candle_map)} symbols with candles / {len(listing_map)} listing records;"
          f" candles {first.date()} -> {last.date()}")
    base = Params.from_live()
    for title, p in (("CURRENT RULES: listing breakout <= 60 days, shelf 3-30 days", base),
                     ("OLD LIFECYCLE: listing breakout <= 730 days (rules in force until v3.5.4)", replace(base, max_age=730))):
        trades = []
        for sym, df in candle_map.items():
            trades.extend(simulate_symbol(df, sym, listing_map.get(sym, {}), LIVE_FAITHFUL, p))
        report(title, trades)


if __name__ == "__main__":
    main()
