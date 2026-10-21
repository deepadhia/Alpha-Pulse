# -*- coding: utf-8 -*-
"""
research_dead_money.py -- how much dead money costs, whether a setup pattern predicts it, whether the shelf breakout is a
better pattern than the listing-high breakout, and whether rotating capital out of dead money helps.

PRE-REGISTRATION (written 2026-10-14 before the variants were run).  Population: policy P (resting entry, causal gates, live
close-based stops, age >= 5; see research_risk_profile.py).  "Dead money" = a trade that ends in a time-type exit (IPO dead money,
velocity gate, volume exhaustion, stagnant guard).
  1. Size the drag (trades, capital-days, net) and tabulate dead-money and outlier rates by pattern buckets known before the entry
     (first cross vs re-cross, days pressing against the high, volume dry-up, age, listing-day close).
  2. Pattern comparison: SHELF_BREAKOUT vs LISTING_BREAKOUT under the same causal, resting, close-stop model; combined book.
  3. Is capital the constraint?  Average open positions, days at the cap, signals skipped for slots or cash and what they earned.
  4. R1 rotation: when a new signal cannot be funded, sell the worst open position held >= 7 days with peak close run-up < 4% and PnL
     <= +2% at the previous close (R2: 10 days).
Adoption bar for R1: account return up and max drawdown not worse in BOTH fill scenarios and BOTH capital sizes, at most one rotated
position that would have ended as a >= 25% outlier, and no dependence on a single replaced trade.

  python research_dead_money.py --cache data.pkl
"""

import argparse
import logging
import pickle
from dataclasses import replace

import numpy as np
import pandas as pd

logging.disable(logging.CRITICAL)

from core.execution_model import CostModel
from core.honest_backtest import LIVE_FAITHFUL, Params, simulate_symbol_cross
from core.portfolio_sim import PortfolioConfig, run_portfolio
from research_alpha_lab import COST, cluster_boot

P0 = Params()
EOD = replace(LIVE_FAITHFUL, exit_fill="eod_next_open")
COSTM = CostModel(entry_slippage_pct=0.0, exit_slippage_pct=0.3)
DEAD = "Dead|Velocity|Volume Exhaustion|Stagnant|Time Stop"


def pfv(x):
    x = np.asarray(x, float)
    w, l = x[x > 0].sum(), -x[x < 0].sum()
    return float(w / l) if l > 0 else float("inf")


def gen(cm, lm, setup="listing", **k):
    kw = dict(min_age=5, causal=True, vol_gate=False, slip_pct=0.3, fill_cap_pct=2.5)
    kw.update(k)
    tr = []
    for s, df in cm.items():
        tr += simulate_symbol_cross(df, s, lm.get(s, {}), P0, cfg=EOD, setup=setup, **kw)
    return tr


def fr(tr):
    d = pd.DataFrame([{k: v for k, v in t.items() if k != "path"} for t in tr])
    d["net"] = d.pnl_pct - COST
    d["entry_ts"] = pd.to_datetime(d.entry_date)
    d["exit_ts"] = pd.to_datetime(d.exit_date)
    d["days"] = (d.exit_ts - d.entry_ts).dt.days.clip(lower=1)
    d["dead"] = (~d.open) & d.exit_reason.str.contains(DEAD)
    return d


def show(lbl, d):
    m, lo, hi = cluster_boot(d.net.values, d.symbol.values)
    cl = d[~d.open]
    yrs = {int(y): (int(len(g)), round(float(g.net.mean()), 1)) for y, g in d.groupby(d.entry_ts.dt.year)}
    print(f"{lbl:<42} N={len(d):3d} mean {m:+5.2f}% CI90[{lo:+5.1f},{hi:+5.1f}] med {d.net.median():+5.2f} PF incl-open {pfv(d.net):4.2f} "
          f"closed {pfv(cl.net):4.2f} | dead-money {d.dead.mean():4.0%} (capital-days {d[d.dead].days.sum() / d.days.sum():4.0%}) "
          f"runners {(d.max_runup_pct >= 30).mean():4.0%} outliers {(d.pnl_pct >= 25).mean():4.1%} | {yrs}")


def pattern_features(row, cm):
    df = cm[row.symbol]
    i = int(row.entry_idx)
    LH = row.ld_high
    h, c, l, v = df.HIGH.values, df.CLOSE.values, df.LOW.values, df.VOLUME.values
    prior = h[1:i]
    n_cross = int((prior >= LH * 1.015).sum()) if i > 1 else 0
    last10 = c[max(1, i - 10):i]
    v5 = v[max(1, i - 5):i].mean() if i > 5 else np.nan
    v15 = v[max(1, i - 15):max(1, i - 5)].mean() if i > 8 else np.nan
    return pd.Series(dict(
        first_cross=n_cross == 0,
        near10=int(((last10 >= LH * 0.95) & (last10 <= LH * 1.03)).sum()),
        dryup=v5 / v15 if v15 and v15 > 0 else np.nan,
        day0_loc=(c[0] - l[0]) / (h[0] - l[0]) if h[0] > l[0] else np.nan,
        pct_above=float((c[1:i] > LH).mean()) if i > 1 else 0.0))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    args = ap.parse_args(argv)
    with open(args.cache, "rb") as fh:
        lm, cm = pickle.load(fh)

    # ------------------------------------------------------------------ 1. dead-money anatomy and patterns
    tr = gen(cm, lm)
    d = fr(tr)
    print("=" * 112)
    print("1. DEAD-MONEY ANATOMY (policy P)")
    print("=" * 112)
    d["cat"] = np.where(d.open, "open", np.where(d.dead, "time-stop (dead money)", "stop/trail"))
    g = d.groupby("cat").agg(n=("net", "size"), cap_days=("days", "sum"), mean=("net", "mean"), total=("net", "sum"))
    g["share_trades"] = g.n / g.n.sum()
    g["share_capital_days"] = g.cap_days / g.cap_days.sum()
    print(g.round(3).to_string())
    ts = d[d.dead]
    print(f"dead-money trades: median {ts.days.median():.0f} days held, mean peak run-up {ts.max_runup_pct.mean():.1f}%, mean net {ts.net.mean():+.2f}%, "
          f"{(ts.pnl_pct > 0).mean():.0%} still positive at exit; their total {ts.net.sum():+.0f}pp vs all trades {d.net.sum():+.0f}pp")
    print("share of trades ending as dead money by entry year:", d.groupby(d.entry_ts.dt.year).dead.mean().round(2).to_dict())

    # the listing high per trade for the pattern features
    ldh = {}
    for s, df in cm.items():
        li = lm.get(s, {})
        v = li.get("listing_day_high")
        ldh[s] = float(v) if v and v > 0 else float(df["HIGH"].iloc[:3].max())
    d["ld_high"] = d.symbol.map(ldh)
    d["age"] = d.days_since_listing
    F = d.apply(lambda r: pattern_features(r, cm), axis=1)
    d = pd.concat([d, F], axis=1)
    d["runner"] = (d.max_runup_pct >= 30).astype(int)
    d["deadi"] = d.dead.astype(int)

    def tab(col, bins=None, labels=None):
        x = d[col] if bins is None else pd.cut(d[col], bins, labels=labels)
        t = d.groupby(x, observed=True).agg(n=("net", "size"), dead=("deadi", "mean"), runner=("runner", "mean"), mean=("net", "mean"),
                                           med=("net", "median"), total=("net", "sum"))
        print(f"\n-- by {col}")
        print(t.round(2).to_string())

    tab("first_cross")
    tab("near10", [-1, 1, 3, 6, 11], ["0-1 closes pressing the high", "2-3", "4-6", "7-10"])
    tab("pct_above", [-0.01, 0.0, 0.3, 0.7, 1.0], ["never above LH", "up to 30% of days above", "30-70%", "70%+"])
    tab("age", [0, 10, 20, 40, 60], ["5-10d", "11-20d", "21-40d", "41-60d"])
    tab("dryup", [0, 0.6, 1.0, 1.5, 100], ["volume drying up (<0.6)", "0.6-1.0", "1.0-1.5", "rising (>1.5)"])
    tab("day0_loc", [-0.01, 0.33, 0.66, 1.0], ["listing day closed low", "middle", "closed high"])
    print("\nReading: dead-money rates sit between about 30% and 45% in almost every bucket and the buckets that look best have n of 20-45;"
          "\nno pattern separates dead money from the outliers that follow it.")

    # ------------------------------------------------------------------ 2. shelf vs listing
    print("\n" + "=" * 112)
    print("2. PATTERN COMPARISON: SHELF_BREAKOUT vs LISTING_BREAKOUT (causal, resting entry, live close-based stops, age >= 5)")
    print("=" * 112)
    L = d
    show("LISTING (policy P)", L)
    S25 = fr(gen(cm, lm, "shelf"))
    show("SHELF, resting cap pivot+2.5%", S25)
    S50 = fr(gen(cm, lm, "shelf", fill_cap_pct=5.0))
    show("SHELF, resting cap pivot+5.0% (live chase)", S50)
    show("SHELF, cap +5.0%, fill +1.0%", fr(gen(cm, lm, "shelf", fill_cap_pct=5.0, slip_pct=1.0)))
    print("reference with the live end-of-day gates (1.8x spike, Rs 10 Cr day turnover; look-ahead vs a resting order):")
    show("LISTING, EOD gates", fr(gen(cm, lm, "listing", causal=False, vol_gate=True)))
    show("SHELF, EOD gates, cap +5.0%", fr(gen(cm, lm, "shelf", causal=False, vol_gate=True, fill_cap_pct=5.0)))
    S = S50.copy()
    S["prng_b"] = pd.cut(S.shelf_prng.astype(float), [0, 8, 12, 18], labels=["shelf range <= 8%", "8-12%", "12-18%"])
    print("\nshelf by shelf tightness (cap +5.0%):")
    print(S.groupby("prng_b", observed=True).agg(n=("net", "size"), dead=("dead", "mean"), runner=("max_runup_pct", lambda x: (x >= 30).mean()),
                                                 mean=("net", "mean"), med=("net", "median")).round(2).to_string())
    trL, trS = tr, gen(cm, lm, "shelf", fill_cap_pct=5.0)
    spans = {}
    for t in trL:
        spans.setdefault(t["symbol"], []).append((pd.Timestamp(t["entry_date"]), pd.Timestamp(t["exit_date"])))
    trLS = list(trL) + [t for t in trS if not any(a <= pd.Timestamp(t["entry_date"]) <= b + pd.Timedelta(days=42) for a, b in spans.get(t["symbol"], []))]
    print("\naccount level (Rs 1 lakh, 10 slots of 10%, costs on):")
    for lbl, t_ in (("LISTING only", trL), ("SHELF only", trS), ("LISTING + SHELF (listing wins overlaps)", trLS)):
        r = run_portfolio(t_, PortfolioConfig(capital=100000.0, soft_cap=10, hard_cap=10, cap_policy="flat", cost=COSTM))["summary"]
        print(f"  {lbl:<42} signals {r['signals']:3d} taken {r['trades_taken']:3d} ret {r['total_return_pct']:+7.1f}% CAGR {r['cagr_pct']:+5.1f}% "
              f"maxDD {r['max_drawdown_pct']:6.1f}% Calmar {r['cagr_pct'] / abs(r['max_drawdown_pct']):4.2f} PF {r['profit_factor']:.2f}")

    # ------------------------------------------------------------------ 3. is capital the constraint
    print("\n" + "=" * 112)
    print("3. IS CAPITAL THE CONSTRAINT? (policy P)")
    print("=" * 112)
    spans = [(pd.Timestamp(t["entry_date"]), pd.Timestamp(t["exit_date"]), t["exit_reason"]) for t in tr]
    days = pd.date_range(min(s[0] for s in spans), max(s[1] for s in spans), freq="B")
    cnt = np.array([sum(1 for a, b, _ in spans if a <= x <= b) for x in days])
    dead_cnt = np.array([sum(1 for a, b, r in spans if a <= x <= b and ("Dead" in r or "Velocity" in r or "Time Stop" in r)) for x in days])
    print(f"business days {len(days)}: average open positions {cnt.mean():.1f}; days with >= 10 open {(cnt >= 10).mean():.1%}; >= 7 {(cnt >= 7).mean():.1%}")
    print(f"average dead-money positions open at a time {dead_cnt.mean():.1f}; days at the 10-slot cap that would not be without dead money: "
          f"{((cnt >= 10) & (cnt - dead_cnt < 10)).mean():.1%}")
    for cap in (7, 10):
        for capital in (100000.0, 1000000.0):
            res = run_portfolio(tr, PortfolioConfig(capital=capital, soft_cap=cap, hard_cap=cap, cap_policy="flat", cost=COSTM))
            log, s = res["log"], res["summary"]
            taken = set(zip(log.symbol, log.entry_date))
            miss = [t for t in tr if (t["symbol"], t["entry_date"]) not in taken]
            mn = np.array([t["pnl_pct"] - COST for t in miss]) if miss else np.array([0.0])
            out = [t["symbol"] for t in miss if t["pnl_pct"] >= 25]
            print(f"cap {cap:2d}, Rs {capital / 1e5:>4.0f} lakh: signals {s['signals']} taken {s['trades_taken']} skipped {s['skipped']} | missed N={len(miss)} "
                  f"mean net {mn.mean():+.2f}% sum {mn.sum():+.0f}pp, missed outliers (>=25%): {len(out)} {out[:6]}")

    # ------------------------------------------------------------------ 4. rotation
    print("\n" + "=" * 112)
    print("4. R1/R2 ROTATION OF DEAD-MONEY POSITIONS (account level, 10 slots)")
    print("=" * 112)
    sets = {"resting 0.3%/cap2.5": tr, "moderate 1.0%/cap3.0": gen(cm, lm, slip_pct=1.0, fill_cap_pct=3.0)}
    for nm, trs in sets.items():
        for capital in (100000.0, 1000000.0):
            print(f"\n== {nm}, Rs {capital / 1e5:.0f} lakh")

            def line(lbl, s):
                print(f"  {lbl:<26} taken {s['trades_taken']:3d} ret {s['total_return_pct']:+7.1f}% CAGR {s['cagr_pct']:+5.1f}% maxDD {s['max_drawdown_pct']:6.1f}% "
                      f"Calmar {s['cagr_pct'] / abs(s['max_drawdown_pct']):4.2f} PF {s['profit_factor']:.2f}")

            line("no rotation (today)", run_portfolio(trs, PortfolioConfig(capital=capital, soft_cap=10, hard_cap=10, cap_policy="flat", cost=COSTM))["summary"])
            for dd in (7, 10):
                s = run_portfolio(trs, PortfolioConfig(capital=capital, soft_cap=10, hard_cap=10, cap_policy="flat", cost=COSTM, rotate_after_days=dd))["summary"]
                line(f"rotate after {dd} days", s)
                rot = s["rotated"]
                if rot:
                    big = [(x["symbol"], round(x["would_have_ended"])) for x in rot if x["would_have_ended"] >= 25]
                    print(f"      rotated {len(rot)}: sold at avg {np.mean([x['sold_at_pct'] for x in rot]):+.1f}% vs would have ended at avg "
                          f"{np.mean([x['would_have_ended'] for x in rot]):+.1f}% (gross); would-have-been outliers: {big}")


if __name__ == "__main__":
    main()
