# -*- coding: utf-8 -*-
"""
research_risk_profile.py -- stop style, loss streaks, profit-factor variability, account-level risk and capacity for the
recommended execution policy (LEARNINGS.md section 12).

Population ("policy P"): LISTING_BREAKOUT, age >= 5 days, resting stop-limit entry at listing high +1.5% (fill +0.3%,
no fill above +2.5%), NO same-day volume information anywhere (causal), live exit engine, trades still open at the
end of the data kept and marked to market.  Costs: flat 1.0% round trip per trade for trade-level statistics; Indian
delivery charges + 0.3% exit slippage for the account simulation.

Sections
  1. Stop style: resting intraday stop vs close-based alert (the live 6:30 PM scan) vs close alert + disaster stop
  2. Profit factor: closed-only vs incl. open runners vs ex-top-3, by year; what payoff a PF of 2 needs
  3. Losing streaks and rolling profit factor (bootstrap of the closed trades)
  4. Account level: slot count / weights under three fill assumptions, plus the closed-only view
  5. Capital capacity vs liquidity

  python research_risk_profile.py --cache data.pkl
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
from research_alpha_lab import cluster_boot

COST = 1.0
EOD = replace(LIVE_FAITHFUL, exit_fill="eod_next_open")     # live: decide on the close, sell at the next open
P0 = Params()


def pf(x):
    x = np.asarray(x, float)
    w, l = x[x > 0].sum(), -x[x < 0].sum()
    return float(w / l) if l > 0 else float("inf")


def gen(cm, lm, cfg=EOD, p=P0, **k):
    kw = dict(min_age=5, causal=True, vol_gate=False, slip_pct=0.3, fill_cap_pct=2.5)
    kw.update(k)
    tr = []
    for s, df in cm.items():
        tr += simulate_symbol_cross(df, s, lm.get(s, {}), p, cfg=cfg, **kw)
    return tr


def frame(tr):
    d = pd.DataFrame([{k: v for k, v in t.items() if k != "path"} for t in tr])
    d["net"] = d.pnl_pct - COST
    d["entry_ts"] = pd.to_datetime(d.entry_date)
    d["exit_ts"] = pd.to_datetime(d.exit_date)
    return d


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    args = ap.parse_args(argv)
    with open(args.cache, "rb") as fh:
        lm, cm = pickle.load(fh)
    rng = np.random.default_rng(1)

    # ------------------------------------------------------------------ 1. stop style
    print("=" * 112)
    print("1. STOP STYLE (same entries; paired on symbol+entry bar; diff = variant minus resting intraday stop)")
    print("=" * 112)
    variants = {
        "A resting intraday stop (gap-aware fill)": (LIVE_FAITHFUL, P0),
        "B close-based alert, sell next open  [LIVE]": (EOD, P0),
        "C B + disaster stop 3% below the level": (EOD, replace(P0, cat_stop_pct=3.0)),
        "D B + disaster stop 5% below the level": (EOD, replace(P0, cat_stop_pct=5.0)),
        "E B + disaster stop 8% below the level": (EOD, replace(P0, cat_stop_pct=8.0)),
    }
    for fl, fk in (("RESTING ENTRY (fill +0.3%, cap +2.5%)", {}), ("MODERATE ENTRY (fill +1.0%, cap +3.0%)", dict(slip_pct=1.0, fill_cap_pct=3.0))):
        print(f"\n-- {fl}")
        res = {}
        for lab, (cfg, p) in variants.items():
            d = frame(gen(cm, lm, cfg, p, **fk))
            res[lab] = d
            cl = d[~d.open]
            m, lo, hi = cluster_boot(d.net.values, d.symbol.values)
            print(f"{lab:<46} N={len(d)} net {m:+5.2f}% CI90[{lo:+5.1f},{hi:+5.1f}] PF {pf(d.net):.2f} med {d.net.median():+5.2f} "
                  f"worst {d.pnl_pct.min():6.1f}% below -15%: {(cl.pnl_pct < -15).mean():5.1%} avg loser {cl[cl.net < 0].net.mean():+.2f}")
        A = res["A resting intraday stop (gap-aware fill)"]
        for lab in list(variants)[1:]:
            j = res[lab].merge(A, on=["symbol", "entry_idx"], suffixes=("_b", "_a"))
            diff = (j.net_b - j.net_a).values
            m, lo, hi = cluster_boot(diff, j.symbol.values)
            o = np.argsort(-np.abs(diff))
            m2, lo2, hi2 = cluster_boot(np.delete(diff, o[:3]), np.delete(j.symbol.values, o[:3]))
            yy = {int(y): round(float(diff[(j.entry_ts_b.dt.year == y).values].mean()), 2) for y in sorted(j.entry_ts_b.dt.year.unique())}
            print(f"   paired vs A: {lab[:44]:<44} {m:+5.2f}pp CI90[{lo:+5.2f},{hi:+5.2f}] n={len(j)} | ex-top3 {m2:+5.2f}[{lo2:+5.2f},{hi2:+5.2f}] "
                  f"| years better {sum(v > 0 for v in yy.values())}/{len(yy)}")

    # ------------------------------------------------------------------ 2/3. PF and streaks
    print("\n" + "=" * 112)
    print("2-3. PROFIT FACTOR, LOSING STREAKS (policy stops = live close-based alert)")
    print("=" * 112)
    for name, kw in (("RESTING 0.3%/cap2.5", {}), ("MODERATE 1.0%/cap3.0", dict(slip_pct=1.0, fill_cap_pct=3.0)),
                     ("CHASE +1.7% (today's measured fills)", dict(slip_pct=1.7, fill_cap_pct=None))):
        d = frame(gen(cm, lm, **kw))
        cl = d[~d.open].sort_values("exit_ts")
        x = cl.net.values
        print(f"\n== {name} | closed {len(cl)} open {int(d.open.sum())}")
        print(f"  PF closed-only {pf(x):.2f} | incl. open runners at MTM {pf(d.net):.2f} | closed ex-top-3 winners {pf(cl.net.drop(cl.net.nlargest(3).index)):.2f}")
        print("  PF by entry year (incl. open):", {int(y): round(pf(g.net), 2) for y, g in d.groupby(d.entry_ts.dt.year)})
        run = best = 0
        runs = []
        for v in x < 0:
            if v:
                run += 1
            else:
                if run:
                    runs.append(run)
                run = 0
            best = max(best, run)
        if run:
            runs.append(run)
        print(f"  longest losing streak (closed, chronological): {best}; streak lengths: {dict(pd.Series(runs).value_counts().sort_index())}")
        for k in (5, 8, 10):
            roll = pd.Series(x).rolling(k).sum().min()
            print(f"  worst {k} consecutive closed trades: {roll:+.1f}pp -> {roll * 0.10:+.1f}% of NAV at 10% weights, {roll * 0.143:+.1f}% at 14%")
        L, Pf, P100 = [], [], []
        for _ in range(4000):
            s = rng.choice(x, 40)
            r = b = 0
            for v in s < 0:
                r = r + 1 if v else 0
                b = max(b, r)
            L.append(b)
            Pf.append(pf(s))
            P100.append(pf(rng.choice(x, 100)))
        L, Pf, P100 = np.array(L), np.array(Pf), np.array(P100)
        print(f"  next 40 trades (bootstrap): longest losing streak median {int(np.median(L))}, p90 {int(np.percentile(L, 90))}, "
              f"P(streak >= 8) {100 * (L >= 8).mean():.0f}% | PF median {np.median(Pf):.2f}, p10 {np.percentile(Pf, 10):.2f}, "
              f"P(PF < 1) {100 * (Pf < 1).mean():.0f}%, P(PF >= 2) {100 * (Pf >= 2).mean():.0f}%")
        print(f"  next 100 trades (bootstrap): PF median {np.median(P100):.2f}, p10 {np.percentile(P100, 10):.2f}, P(PF >= 2) {100 * (P100 >= 2).mean():.0f}%")
        w, aw, al = (x > 0).mean(), x[x > 0].mean(), -x[x < 0].mean()
        print(f"  win rate {100 * w:.1f}%, avg win {aw:.2f}, avg loss {al:.2f}, payoff {aw / al:.2f}; a PF of 2.0 at this win rate needs payoff {2 * (1 - w) / w:.2f}")

    # ------------------------------------------------------------------ 4. account level
    print("\n" + "=" * 112)
    print("4. ACCOUNT LEVEL (Rs 1 lakh; Indian delivery charges + 0.3% exit slippage; entry slip is in the fill)")
    print("=" * 112)
    cost = CostModel(entry_slippage_pct=0.0, exit_slippage_pct=0.3)
    sets = {"chase +1.7% (today)": gen(cm, lm, slip_pct=1.7, fill_cap_pct=None), "resting 0.3%/cap2.5": gen(cm, lm),
            "moderate 1.0%/cap3.0": gen(cm, lm, slip_pct=1.0, fill_cap_pct=3.0)}
    cfgs = [("1/7 weights, 7 slots", dict(soft_cap=7, hard_cap=7)),
            ("1/10 weights, 10 slots", dict(soft_cap=10, hard_cap=10)),
            ("1/7 + halve size at 10% DD", dict(soft_cap=7, hard_cap=7, dd_half_pct=10.0)),
            ("1/10 + halve size at 10% DD", dict(soft_cap=10, hard_cap=10, dd_half_pct=10.0)),
            ("1/7, risk-sized 1.0% per trade", dict(soft_cap=7, hard_cap=7, risk_budget_pct=1.0))]

    def show(lab, trs, **k):
        r = run_portfolio(trs, PortfolioConfig(capital=100000.0, cap_policy="flat", cost=cost, **k))["summary"]
        print(f"   {lab:<32} taken {r['trades_taken']:3d} ret {r['total_return_pct']:+7.1f}% CAGR {r['cagr_pct']:+5.1f}% "
              f"maxDD {r['max_drawdown_pct']:6.1f}% Calmar {r['cagr_pct'] / abs(r['max_drawdown_pct']):4.2f} PF {r['profit_factor']:.2f} "
              f"worst {r['worst_trade_pct']:+.1f}%")

    for nm, trs in sets.items():
        print(f"== {nm}  (open trades marked to market: {sum(1 for t in trs if t.get('open'))})")
        for lab, k in cfgs:
            show(lab, trs, **k)
    print("== resting, CLOSED trades only (conservative: drops the still-running trades)")
    for lab, k in cfgs[:2]:
        show(lab, [t for t in sets["resting 0.3%/cap2.5"] if not t.get("open")], **k)

    # ------------------------------------------------------------------ 5. capacity
    print("\n" + "=" * 112)
    print("5. CAPACITY: position size vs the stock's 20-day average daily turnover at entry (policy P, 14% weights)")
    print("=" * 112)
    tr = gen(cm, lm)
    turn = []
    for t in tr:
        df = cm[t["symbol"]]
        i = int(t["entry_idx"])
        up = df.iloc[:i]
        turn.append(float((up["CLOSE"] * up["VOLUME"]).tail(20).mean()) / 1e7)
    turn = np.array(turn)
    print("turnover20 (Rs Cr/day) percentiles 5/10/25/50:", np.percentile(turn, [5, 10, 25, 50]).round(1))
    for cap_l in (1, 10, 25, 50, 100, 250):
        pos_cr = cap_l * 1e5 * 0.143 / 1e7
        part = pos_cr / turn * 100
        print(f"capital Rs {cap_l:>4} lakh: position Rs {pos_cr:6.2f} Cr | participation of avg daily turnover: median {np.median(part):5.2f}% "
              f"p90 {np.percentile(part, 90):6.2f}% | trades above 2%: {np.mean(part > 2):5.1%}, above 5%: {np.mean(part > 5):5.1%}")


if __name__ == "__main__":
    main()
