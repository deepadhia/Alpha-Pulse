# -*- coding: utf-8 -*-
"""
research_small_account.py -- can a Rs 50,000 to Rs 1,00,000 account run policy P without blowing up?

PRE-REGISTRATION (written 2026-10-15 before the runs).  Population: policy P (resting entry, causal gates, live close-based stops, 12%
stop cap, age >= 5; research_risk_profile.py) in three fill scenarios: resting (+0.3% fill), moderate (+1.0%, cap +3%) and chase (+1.7%,
today's measured fills).  Account model: core.portfolio_sim with the full Indian delivery cost model (Rs 20 brokerage per order, STT,
stamp duty, exchange and SEBI charges, GST, Rs 13.5 depository charge on each sale, 0.3% exit slippage), whole shares, positions
skipped when whole shares reach less than half of the target, open positions marked to market.

Questions and the definitions used (fixed in advance):
  1. Frictions: round-trip cost as a percentage of the position at Rs 5k, 10k, 20k, 50k.
  2. Real experience: every 12-month window that starts on the first of a month in the history (about 40 overlapping starts).
  3. Resampled experience: 250 synthetic histories of 12 and 24 months built from randomly chosen 3-month blocks of real trades (dates
     shifted, so concurrency and cross-trade correlation inside a block are preserved).
  4. Blowup = NAV at or below 50% of the starting capital at any time.  Also reported: probability of a loss at the horizon, of a drawdown
     of 20%, 30% and 40% from the peak, median and 5th-percentile return, median maximum drawdown.
  5. Configurations: 10 slots x 10%, 7 x 14.3%, 5 x 20%, and 10 slots x 6% (about 60% deployed).

  python research_small_account.py --cache data.pkl --sims 250
"""

import argparse
import logging
import pickle
from dataclasses import replace

import numpy as np
import pandas as pd

logging.disable(logging.CRITICAL)

from core.execution_model import CostModel, round_trip_cost_pct
from core.honest_backtest import LIVE_FAITHFUL, Params, simulate_symbol_cross
from core.portfolio_sim import PortfolioConfig, run_portfolio

P0 = Params()
EOD = replace(LIVE_FAITHFUL, exit_fill="eod_next_open")
COST = CostModel()      # the full default model: fixed charges + 0.3% slippage per side (entry slip is in the fill, so set 0 here)
COST_ACCT = CostModel(entry_slippage_pct=0.0, exit_slippage_pct=0.3)

CONFIGS = [("10 slots x 10%", 10, None), ("7 slots x 14.3%", 7, None), ("5 slots x 20%", 5, None), ("10 slots x 6%", 10, 0.06)]
SCEN = {"resting (+0.3%)": {}, "moderate (+1.0%)": dict(slip_pct=1.0, fill_cap_pct=3.0), "chase (+1.7%, today)": dict(slip_pct=1.7, fill_cap_pct=None)}


def gen(cm, lm, **k):
    kw = dict(min_age=5, causal=True, vol_gate=False, slip_pct=0.3, fill_cap_pct=2.5)
    kw.update(k)
    tr = []
    for s, df in cm.items():
        tr += simulate_symbol_cross(df, s, lm.get(s, {}), P0, cfg=EOD, **kw)
    return tr


def shift(t, delta):
    u = dict(t)
    u["entry_date"] = str((pd.Timestamp(t["entry_date"]) + delta).date())
    u["exit_date"] = str((pd.Timestamp(t["exit_date"]) + delta).date())
    u["path"] = [(pd.Timestamp(d) + delta, c, tr) for d, c, tr in t["path"]]
    return u


def account(trades, capital, slots, weight, end):
    """Run the account on `trades`; return (return, max drawdown, min NAV ratio, trades taken) marked to market at `end`."""
    if not trades:
        return 0.0, 0.0, 1.0, 0
    cfg = PortfolioConfig(capital=capital, soft_cap=slots, hard_cap=slots, cap_policy="flat", weight=weight, cost=COST_ACCT)
    res = run_portfolio(trades, cfg)
    eq = res["equity"]
    taken = res["summary"].get("trades_taken", 0)
    if eq is None or not len(eq):
        return 0.0, 0.0, 1.0, taken
    nav = eq["nav"]
    nav = nav[nav.index <= end]
    if not len(nav):
        return 0.0, 0.0, 1.0, taken
    nav = pd.concat([pd.Series([capital], index=[nav.index[0] - pd.Timedelta(days=1)]), nav])
    dd = (nav / nav.cummax() - 1.0).min()
    return float(nav.iloc[-1] / capital - 1.0), float(dd), float(nav.min() / capital), taken


def stats(lbl, rets, dds, mins):
    r, d, m = np.array(rets), np.array(dds), np.array(mins)
    print(f"  {lbl:<18} median ret {np.median(r) * 100:+6.1f}% | 5th pct {np.percentile(r, 5) * 100:+6.1f}% | P(loss) {np.mean(r < 0):4.0%} | "
          f"median max DD {np.median(d) * 100:6.1f}% | P(DD>=20%) {np.mean(d <= -0.20):4.0%} P(DD>=30%) {np.mean(d <= -0.30):4.0%} "
          f"P(DD>=40%) {np.mean(d <= -0.40):4.0%} | BLOWUP (NAV<=50%) {np.mean(m <= 0.5):4.1%}")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--sims", type=int, default=250)
    args = ap.parse_args(argv)
    with open(args.cache, "rb") as fh:
        lm, cm = pickle.load(fh)
    rng = np.random.default_rng(11)

    print("=" * 112)
    print("1. FRICTIONS: round-trip cost (charges + 0.3% exit slippage, excl. entry fill) as a % of the position")
    print("=" * 112)
    for pos in (5000, 10000, 20000, 50000):
        print(f"  position Rs {pos:>6,}: {round_trip_cost_pct(COST_ACCT, pos, 0.0):.2f}%   (the flat assumption used elsewhere is 1.0% in total)")

    trade_sets = {nm: gen(cm, lm, **kw) for nm, kw in SCEN.items()}
    t0 = min(pd.Timestamp(t["entry_date"]) for t in trade_sets["resting (+0.3%)"])
    t1 = max(pd.Timestamp(t["entry_date"]) for t in trade_sets["resting (+0.3%)"])

    # ------------------------------------------------------------------ 2. real rolling 12-month windows
    print("\n" + "=" * 112)
    print("2. REAL 12-MONTH WINDOWS (start on the 1st of each month; trades entering inside the window; NAV marked to market at the window end)")
    print("=" * 112)
    starts = [s for s in pd.date_range(t0.replace(day=1), t1, freq="MS") if s + pd.DateOffset(months=12) <= t1 + pd.Timedelta(days=1)]
    print(f"{len(starts)} overlapping window starts, {starts[0].date()} to {starts[-1].date()}")
    for scn, trs in trade_sets.items():
        print(f"\n-- fills: {scn}")
        for capital in (50000.0, 100000.0):
            for lbl, slots, w in CONFIGS[:3]:
                rets, dds, mins = [], [], []
                for s in starts:
                    e = s + pd.DateOffset(months=12)
                    sub = [t for t in trs if s <= pd.Timestamp(t["entry_date"]) < e]
                    r, d, m, _ = account(sub, capital, slots, w, e)
                    rets.append(r)
                    dds.append(d)
                    mins.append(m)
                stats(f"Rs {capital / 1000:.0f}k {lbl}", rets, dds, mins)

    # ------------------------------------------------------------------ 3. resampled histories
    def synth(trs, months, block=3):
        n_blocks = months // block
        out = []
        t_start = pd.Timestamp("2030-01-01")
        for b in range(n_blocks):
            bs = pd.Timestamp(rng.choice(pd.date_range(t0.replace(day=1), t1 - pd.DateOffset(months=block), freq="MS")))
            be = bs + pd.DateOffset(months=block)
            target = t_start + pd.DateOffset(months=b * block)
            delta = target - bs
            out += [shift(t, delta) for t in trs if bs <= pd.Timestamp(t["entry_date"]) < be]
        return out, t_start + pd.DateOffset(months=months)

    print("\n" + "=" * 112)
    print(f"3. RESAMPLED HISTORIES ({args.sims} per cell; random 3-month blocks of real trades)")
    print("=" * 112)
    for months in (12, 24):
        for scn, trs in trade_sets.items():
            if months == 24 and scn != "moderate (+1.0%)":
                continue
            print(f"\n-- {months} months, fills: {scn}")
            histories = [synth(trs, months) for _ in range(args.sims)]
            for capital in (50000.0, 100000.0):
                for lbl, slots, w in CONFIGS:
                    rets, dds, mins = [], [], []
                    for h, e in histories:
                        r, d, m, _ = account(h, capital, slots, w, e)
                        rets.append(r)
                        dds.append(d)
                        mins.append(m)
                    stats(f"Rs {capital / 1000:.0f}k {lbl}", rets, dds, mins)

    # ------------------------------------------------------------------ 4. structural bound
    print("\n" + "=" * 112)
    print("4. STRUCTURAL LOSS BOUND PER TRADE (policy P: 12% stop cap, close-based exit; worst replayed loss)")
    print("=" * 112)
    gross = np.array([t["pnl_pct"] for t in trade_sets["moderate (+1.0%)"] if not t.get("open")])
    print(f"worst closed trade {gross.min():.1f}%, 1st percentile {np.percentile(gross, 1):.1f}%, share below -12%: {(gross < -12).mean():.1%}, below -15%: {(gross < -15).mean():.1%}")
    for w in (0.06, 0.10, 0.143, 0.20):
        print(f"  position weight {w * 100:4.1f}%: worst replayed single-trade loss = {gross.min() * w:.1f}% of the account; all positions at the worst case at once = {gross.min() * w * min(10, int(round(1 / w))):.1f}%")


if __name__ == "__main__":
    main()
