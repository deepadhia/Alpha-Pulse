# -*- coding: utf-8 -*-
"""
research_improve_history.py -- can policy P be improved?  Pre-registered out-of-sample test on 2006-2022 (added 2026-11-07).

PRE-REGISTRATION (written before running; nothing below is searched or tuned here)
-----------------------------------------------------------------------------------
Why this is a fair test: the exit/structure ideas E1-E10 were first examined on 2022-26 (research_alpha_lab.py, none passed).
The 2006-2022 panel cohort (about 190 trades) has never been used to choose anything, so it is a HOLDOUT for each idea.
A. Exit variants, PAIRED on identical entries (same trades, only the exits differ):
   E1 trail 8% | E2 trail 13% | E3 winner cushion 0.85 | E4 cushion 0.75 | E5 breakeven after +8% peak
   E6 dead-money 10 d | E7 dead-money 21 d | E8 trail starts +5% | E9 initial stop cap 10% | E10 sell 1/3 at +20%
   X1 all time-based exits off (stop + trailing only; the S3 exit set)
   BAR (all must hold): holdout (2006-22) paired gain CI90 lower bound > 0; the 2022-26 gain also > 0; gain still > 0 after
   dropping the single largest contributing trade in the holdout; not worse in more than one year (>= 8 trades) per era.
   11 variants at 90% => about one false pass is expected by chance; a pass is only a candidate for the forward test.
B. Account level (not a hypothesis test, descriptive): slot count 8 / 10 / 15; adding the S3 paper rule to P in one account;
   share of time capital is idle and what a 6% liquid-fund yield on idle cash would add.
No claim of profitability is made from any of this; the tier is REPLAY.

  .venv\\Scripts\\python.exe research_improve_history.py --atlas data_20261107.pkl --panel-dir data_panel_w1200
"""
import argparse
import logging
import pickle
from dataclasses import replace

import numpy as np
import pandas as pd

logging.disable(logging.CRITICAL)
import research_alpha_lab as lab
import research_max_history as mh
from core.execution_model import CostModel
from core.honest_backtest import LIVE_FAITHFUL, Params, _listing_context
from core.portfolio_sim import PortfolioConfig, run_portfolio
from core.setup2 import clean_universe
from core.setup3 import NO_TIME_EXITS, PAPER_PARAMS, paper_exit_params, simulate_symbol_setup3

VARIANTS = [
    ("E1 trail 8%", lambda p: replace(p, exit_overrides=(("trail_pct_override", 0.08),)), None),
    ("E2 trail 13%", lambda p: replace(p, exit_overrides=(("trail_pct_override", 0.13),)), None),
    ("E3 cushion 0.85", lambda p: replace(p, exit_overrides=(("winner_cushion_mult", 0.85),)), None),
    ("E4 cushion 0.75", lambda p: replace(p, exit_overrides=(("winner_cushion_mult", 0.75),)), None),
    ("E5 breakeven after +8%", lambda p: replace(p, breakeven_runup_pct=8.0, breakeven_lock_pct=0.0), None),
    ("E6 dead-money 10 d", lambda p: replace(p, dead_money_days_ipo=10), None),
    ("E7 dead-money 21 d", lambda p: replace(p, dead_money_days_ipo=21), None),
    ("E8 trail starts +5%", lambda p: replace(p, trail_start_pnl=5.0), None),
    ("E9 stop cap 10%", lambda p: replace(p, stop_cap_pct=10.0), None),
    ("E10 sell 1/3 at +20%", lambda p: p, "partial"),
    ("X1 time exits off", lambda p: replace(p, exit_overrides=NO_TIME_EXITS), None),
]


def build_base(cm, lm, p):
    trades = lab.baseline_trades(cm, lm, p, slip=0.3, fill_cap_pct=2.5)
    d = pd.DataFrame([{k: v for k, v in t.items() if k != "path"} for t in trades])
    d["ld_high"] = [_listing_context(cm[s], lm.get(s, {}))[0] for s in d.symbol]
    d["ext"], d["vr"], d["age"] = d.ext_pct, d.vol_ratio, d.days_since_listing
    d["net"] = d.pnl_pct - lab.COST
    d["year"] = pd.to_datetime(d.entry_date).dt.year
    return d, trades


def paired(label, base, cm, p):
    b0 = lab.rerun_exits(base, cm, p)
    chk = float(np.nanmax(np.abs(b0 - base.pnl_pct.values)))
    print(f"\n[{label}] {len(base)} trades; control re-run of live exits max abs diff {chk:.1e}pp; live mean net {base.net.mean():+.2f}%")
    res = {}
    for name, fn, post in VARIANTS:
        pv = fn(p)
        v = lab.rerun_exits(base, cm, pv, post=lab.partial_posthoc(1 / 3, 20.0) if post == "partial" else None)
        ok = ~np.isnan(v) & ~np.isnan(b0)
        diff = (v - b0)[ok]
        sym = base.symbol.values[ok]
        yr = base.year.values[ok]
        m, lo, hi = lab.cluster_boot(diff, sym)
        ex1 = np.delete(diff, int(np.argmax(diff))).mean() if len(diff) > 1 else np.nan
        worse = 0
        for y in np.unique(yr):
            if (yr == y).sum() >= 8 and diff[yr == y].mean() < 0:
                worse += 1
        res[name] = dict(m=m, lo=lo, hi=hi, ex1=ex1, worse=worse, n=int(ok.sum()))
        print(f"  {name:<24} gain {m:+6.2f}pp CI90 [{lo:+6.2f},{hi:+6.2f}]  ex-largest {ex1:+6.2f}pp  worse years {worse}  (N={ok.sum()})")
    return res


def s3_trades(cm, lm, X1, lo_date, hi_date=None):
    sub_lm, sub_cm = mh.subset(lm, cm, lo=lo_date, hi=hi_date)
    clean, _ = clean_universe(sub_lm, sub_cm, min_listing="2006-01-01")
    rows = []
    for s, (df, li) in clean.items():
        rows.extend(simulate_symbol_setup3(df, s, li, X1, PAPER_PARAMS, LIVE_FAITHFUL_EOD, slip_pct=0.3, include_open=True))
    d = pd.DataFrame([{k: v for k, v in t.items() if k != "path"} for t in rows])
    up, _ = mh.flag_jumps(d, {s: df for s, (df, li) in clean.items()})
    keep = [t for t, u in zip(rows, up) if not u]
    for t in keep:
        t["entry_date"] = str(pd.Timestamp(t["entry_date"]).date()) if not isinstance(t["entry_date"], str) else t["entry_date"]
        t["exit_date"] = str(pd.Timestamp(t["exit_date"]).date()) if not isinstance(t["exit_date"], str) else t["exit_date"]
        t.setdefault("year", int(t["entry_date"][:4]))
        t.setdefault("type", "S3")
    return keep


def account(label, trades, slots, cm_cost):
    pc = PortfolioConfig(capital=100000.0, soft_cap=slots, hard_cap=slots, cap_policy="flat", cost=cm_cost, priority="first")
    r = run_portfolio(trades, pc)
    s = r["summary"]
    eq = r["equity"]
    util = np.nan
    if len(eq) and {"nav", "cash"} <= set(eq.columns):
        util = float(((eq.nav - eq.cash) / eq.nav).mean())
    calmar = s["cagr_pct"] / abs(s["max_drawdown_pct"]) if s.get("max_drawdown_pct") else np.nan
    print(f"  {label:<34} taken {s['trades_taken']:3d}/{s['signals']:3d} total {s['total_return_pct']:+7.1f}% CAGR {s['cagr_pct']:+5.1f}% "
          f"maxDD {s['max_drawdown_pct']:6.1f}% Calmar {calmar:4.2f} PF {s['profit_factor']:4.2f} avg invested {100 * util:4.0f}%")
    return s, eq


LIVE_FAITHFUL_EOD = replace(LIVE_FAITHFUL, exit_fill="eod_next_open")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--atlas", required=True)
    ap.add_argument("--panel-dir", default="data_panel_w1200")
    a = ap.parse_args()
    lab.POP.update(causal=True, vol_gate=False, slip=0.3, cap=2.5)
    lab.CFG = LIVE_FAITHFUL_EOD
    p = Params()
    a_lm, a_cm = pickle.load(open(a.atlas, "rb"))
    p_lm, p_cm = mh.load_panel(a.panel_dir)
    pre_lm, pre_cm = mh.subset(p_lm, p_cm, lo=pd.Timestamp("2006-07-01"), hi=mh.ATLAS_START)
    at_lm, at_cm = mh.subset(a_lm, a_cm, lo=mh.ATLAS_START)

    print("A. EXIT VARIANTS, paired on identical entries (gain = variant minus live exits, pp per trade, gross of cost, same cost both sides)")
    d_pre, t_pre = build_base(pre_cm, pre_lm, p)
    d_at, t_at = build_base(at_cm, at_lm, p)
    r_pre = paired("HOLDOUT 2006-07..2022-05", d_pre, pre_cm, p)
    r_at = paired("2022-05..2026-11 (where E1-E10 were first examined)", d_at, at_cm, p)
    print("\nVERDICT against the pre-registered bar")
    for name, _, _ in VARIANTS:
        h, t = r_pre[name], r_at[name]
        ok = h["lo"] > 0 and t["m"] > 0 and h["ex1"] > 0 and h["worse"] <= 1 and t["worse"] <= 1
        print(f"  {name:<24} holdout {h['m']:+6.2f}pp (lo {h['lo']:+6.2f}) | 2022-26 {t['m']:+6.2f}pp | ex-largest {h['ex1']:+6.2f} | worse yrs {h['worse']}/{t['worse']} -> {'CANDIDATE' if ok else 'not adopted'}")

    print("\nB. ACCOUNT LEVEL (Rs 1 lakh, flat equal weight, charges + 0.3% exit slip; descriptive)")
    cm_cost = CostModel(entry_slippage_pct=0.0, exit_slippage_pct=0.3)
    X1 = paper_exit_params()
    s3_pre = s3_trades(p_cm, p_lm, X1, pd.Timestamp("2006-07-01"), mh.ATLAS_START)
    s3_at = s3_trades(a_cm, a_lm, X1, mh.ATLAS_START)
    for era, tp, ts in (("2006-07..2022-05", t_pre, s3_pre), ("2022-05..2026-11", t_at, s3_at)):
        print(f"\n {era}: P signals {len(tp)}, S3 paper-rule signals {len(ts)}")
        for slots in (8, 10, 15):
            account(f"P alone, {slots} slots", tp, slots, cm_cost)
        for slots in (10, 15, 20):
            account(f"P + S3 shared, {slots} slots", list(tp) + list(ts), slots, cm_cost)
        account("S3 alone, 10 slots", ts, 10, cm_cost)
    print("\nIdle cash: a 6% liquid-fund yield on the uninvested share adds about 6% x (1 - avg invested) a year to the CAGRs above "
          "(estimate, not simulated).")


if __name__ == "__main__":
    main()
