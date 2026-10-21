# -*- coding: utf-8 -*-
"""
run_honest_backtest.py -- Phase 0 parity harness

Answers: "how much of the backtest edge survives when the backtest does what the live
scanners actually do?"

  python run_honest_backtest.py --verify-legacy     # new engine == run_latest_rules_backtest, trade for trade
  python run_honest_backtest.py --bridge            # switch the divergences on one at a time, show the PF each costs
  python run_honest_backtest.py --portfolio         # account-level result: slots, sizing, costs, drawdown
  python run_honest_backtest.py --all

Read-only: loads candles from MongoDB once (cache with --cache) and never writes anywhere
except the optional --out CSV/JSON files.
"""

import argparse
import json
import logging
import os
import pickle
import sys
from dataclasses import replace

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.WARNING)

from core.honest_backtest import (BRIDGE_STEPS, LEGACY, LIVE_FAITHFUL, Params, SimConfig,
                                   simulate_symbol)
from core.portfolio_sim import PortfolioConfig, run_portfolio
from core.execution_model import CostModel, ZERO_COST


def load_data(cache_path):
    if cache_path and os.path.exists(cache_path):
        with open(cache_path, "rb") as fh:
            return pickle.load(fh)
    from run_latest_rules_backtest import load_dataset
    data = load_dataset()
    if cache_path:
        with open(cache_path, "wb") as fh:
            pickle.dump(data, fh)
    return data


def run_all(candle_map, listing_map, cfg, params):
    trades = []
    for sym, df in candle_map.items():
        trades.extend(simulate_symbol(df, sym, listing_map.get(sym, {}), cfg, params))
    return trades


def summarize(trades):
    if not trades:
        return dict(n=0)
    t = pd.DataFrame(trades)
    wins = t.loc[t.pnl_pct > 0, "pnl_pct"]
    losses = t.loc[t.pnl_pct < 0, "pnl_pct"]
    pf = wins.sum() / -losses.sum() if len(losses) and losses.sum() < 0 else float("inf")
    top3 = t.nlargest(3, "pnl_pct")["pnl_pct"].sum()
    return dict(
        n=len(t), win_pct=100.0 * (t.pnl_pct > 0).mean(), pf=pf, avg=t.pnl_pct.mean(),
        total=t.pnl_pct.sum(), avg_win=wins.mean() if len(wins) else 0.0,
        avg_loss=losses.mean() if len(losses) else 0.0, worst=t.pnl_pct.min(), best=t.pnl_pct.max(),
        top3_share=100.0 * top3 / t.pnl_pct.sum() if t.pnl_pct.sum() > 0 else float("nan"),
        avg_days=t.days_held.mean(),
        n_listing=int((t.type == "LISTING_BREAKOUT").sum()),
        n_shelf=int((t.type == "SHELF_BREAKOUT").sum()),
        n_base=int((t.type == "BASE_BREAKOUT").sum()),
        below_stop=int((t.pnl_pct < -12.0001).sum()),
    )


def fmt_row(label, s):
    if s.get("n", 0) == 0:
        return f"{label:<66} no trades"
    return (f"{label:<66} N={s['n']:3d} win={s['win_pct']:5.1f}% PF={s['pf']:5.2f} "
            f"avg={s['avg']:+5.2f}% tot={s['total']:+7.1f}% worst={s['worst']:+7.2f}% "
            f"top3={s['top3_share']:4.0f}%")


def verify_legacy(candle_map, listing_map):
    import run_latest_rules_backtest as legacy
    import listing_day_breakout_scanner as lscan
    p = Params.from_live()
    config = {
        "enable_vol_exit": True, "enable_stagnant_guard": True, "enable_speed_gates": True,
        "trail_pnl_ipo": 3.0, "vol_ratio": 0.45, "start_date": None, "min_volume": 150000,
        "min_vol_mult": lscan.MIN_VOLUME_MULTIPLIER, "min_close_loc": 0.50,
        "max_extension_pct": lscan.MAX_ENTRY_ABOVE_HIGH_PCT, "max_age": lscan.MAX_DAYS_SINCE_LISTING_FOR_BREAKOUT,
        "cooldown_days": 30, "min_risk_reward": lscan.MIN_RISK_REWARD, "enforce_winner_cushion": True,
    }
    old, new = [], []
    for sym, df in candle_map.items():
        li = listing_map.get(sym, {})
        old.extend(legacy.simulate_production_latest_system(df, sym, li, config))
        new.extend(simulate_symbol(df, sym, li, LEGACY, p))
    key = lambda x: (x["symbol"], x["entry_date"], x["exit_date"])
    old_m = {key(x): x["pnl_pct"] for x in old}
    new_m = {key(x): x["pnl_pct"] for x in new}
    missing = set(old_m) - set(new_m)
    extra = set(new_m) - set(old_m)
    diffs = [k for k in set(old_m) & set(new_m) if abs(old_m[k] - new_m[k]) > 1e-9]
    print(f"legacy trades: {len(old)}   new-engine (legacy flags): {len(new)}")
    print(f"missing in new: {len(missing)}  extra in new: {len(extra)}  pnl mismatches: {len(diffs)}")
    for k in sorted(missing)[:8]:
        print("   missing:", k)
    for k in sorted(extra)[:8]:
        print("   extra  :", k)
    ok = not missing and not extra and not diffs
    print("PARITY:", "EXACT MATCH" if ok else "MISMATCH")
    return ok


def bridge(candle_map, listing_map, params, show_exit_mix=False):
    cfg = LEGACY
    results = []
    trades = run_all(candle_map, listing_map, cfg, params)
    base = summarize(trades)
    results.append(("0. Legacy backtest (as run today)", base, trades))
    print(fmt_row(results[0][0], base))
    for k, (field, value, desc) in enumerate(BRIDGE_STEPS, start=1):
        cfg = replace(cfg, **{field: value})
        trades = run_all(candle_map, listing_map, cfg, params)
        s = summarize(trades)
        results.append((f"{k}. {desc}", s, trades))
        print(fmt_row(f"{k}. {desc}"[:66], s))
    return results


def costs_scenarios():
    return {
        "zero cost": ZERO_COST,
        "default Indian delivery costs + 0.3%/side slippage": CostModel(),
        "stress: 0.75%/side slippage": CostModel(entry_slippage_pct=0.75, exit_slippage_pct=0.75),
    }


def portfolio_report(label, trades, pcfg):
    r = run_portfolio(trades, pcfg)
    s = r["summary"]
    if s.get("trades_taken", 0) == 0:
        print(f"{label:<58} no trades taken")
        return r
    print(f"{label:<58} taken={s['trades_taken']:3d}/{s['signals']:3d} "
          f"ret={s['total_return_pct']:+7.1f}% cagr={s['cagr_pct']:+6.1f}% "
          f"maxDD={s.get('max_drawdown_pct', float('nan')):6.1f}% PF={s['profit_factor']:4.2f} "
          f"worst={s['worst_trade_pct']:+6.1f}% top3={s['top3_share_of_profit_pct']:4.0f}% skip={s['skipped']}")
    return r


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify-legacy", action="store_true")
    ap.add_argument("--bridge", action="store_true")
    ap.add_argument("--portfolio", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--cache", default=None, help="pickle path to cache the MongoDB candle load")
    ap.add_argument("--capital", type=float, default=100000.0)
    ap.add_argument("--out", default=None, help="write honest-config trades to this CSV")
    args = ap.parse_args(argv)
    if args.all:
        args.verify_legacy = args.bridge = args.portfolio = True
    if not (args.verify_legacy or args.bridge or args.portfolio):
        ap.print_help()
        return

    listing_map, candle_map = load_data(args.cache)
    params = Params.from_live()
    print(f"universe: {len(candle_map)} symbols with candles, {len(listing_map)} listing records")

    if args.verify_legacy:
        print("\n== PARITY CHECK: new engine with legacy flags vs run_latest_rules_backtest ==")
        verify_legacy(candle_map, listing_map)

    results = None
    if args.bridge or args.portfolio:
        print("\n== ATTRIBUTION BRIDGE: legacy -> live-faithful (cumulative, one switch at a time) ==")
        results = bridge(candle_map, listing_map, params)

    if args.portfolio and results:
        legacy_trades = results[0][2]
        honest_trades = results[-1][2]
        print("\n== ACCOUNT-LEVEL RESULTS (capital Rs {:,.0f}) ==".format(args.capital))
        for cname, cost in costs_scenarios().items():
            print(f"\n-- costs: {cname}")
            for tlabel, trades in (("legacy trades", legacy_trades), ("live-faithful trades", honest_trades)):
                for pol, soft, hard in (("as_coded", 5, 7), ("documented", 5, 7), ("flat", 5, 5), ("flat", 10, 10)):
                    pc = PortfolioConfig(capital=args.capital, soft_cap=soft, hard_cap=hard,
                                         cap_policy=pol, cost=cost)
                    portfolio_report(f"{tlabel} | {pol} {soft}/{hard}", trades, pc)
        print("\n-- slot-priority sensitivity (live-faithful, default costs, as_coded 5/7): 20 random orderings")
        rets = []
        for seed in range(20):
            r = run_portfolio(honest_trades, PortfolioConfig(capital=args.capital, priority="random", seed=seed))
            if r["summary"].get("trades_taken"):
                rets.append(r["summary"]["total_return_pct"])
        if rets:
            print(f"   total return across orderings: min={min(rets):+.1f}% median={float(np.median(rets)):+.1f}% max={max(rets):+.1f}%")

    if args.out and results:
        rows = [{k: v for k, v in t.items() if k != "path"} for t in results[-1][2]]
        pd.DataFrame(rows).to_csv(args.out, index=False)
        print(f"\nwrote {len(rows)} honest-config trades to {args.out}")


if __name__ == "__main__":
    main()
