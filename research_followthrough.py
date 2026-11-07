# -*- coding: utf-8 -*-
"""
research_followthrough.py -- the follow-through rule suggested by research_deep_dive.py part C (added 2026-11-07).

DISCOVERY (descriptive, both eras at once, so there is NO untouched holdout for this rule): 19 of 20 outliers (95%) in 2006-22 and 19 of 20 in
2022-26 were above their entry price at session 5 after entry, against 41% and 50% of all other trades; median follow-through at session 10:
+17.8% / +14.7% for outliers versus -1.8% / +0.5% for the rest.  Outliers move almost at once; a trade that has not moved by session 5 is almost
never one.  This is POST-ENTRY information, available when the exit decision is made, so using it is causal (unlike the entry features of
LEARNINGS 22.5-22.6, which carried no information).

RULE FAMILY (fixed before running; exploratory because the pattern came from the same data):
   FT(k, t): if the CLOSE at session k after the entry bar is at or below entry x (1 + t), sell at the NEXT open (the live close-based model).
   (k, t) in (3, 0%), (5, 0%), (5, +2%), (10, +2%).  Applied on top of X1 and on top of the live exits.  Entry, stops and every other exit unchanged.
   READING: a rule is a candidate for a PAPER shadow only if, versus ITS OWN base (X1 or live), it raises Calmar AND CAGR in BOTH eras without
   deepening the max drawdown by more than 2pp AND the paired per-trade gain is positive in both eras.  Because there is no untouched holdout,
   even a pass is only a forward-test candidate.  Not previously tested: LEARNINGS 12 tested "close <= -x% within N days" (a loss trigger), not
   "has not gone up by session k" (a progress trigger).

  .venv\\Scripts\\python.exe research_followthrough.py --atlas data_20261107.pkl --panel-dir data_panel_w1200
"""
import argparse
import logging
import pickle
from copy import copy
from dataclasses import replace

import numpy as np
import pandas as pd

logging.disable(logging.CRITICAL)
import research_alpha_lab as lab
import research_max_history as mh
from core.execution_model import CostModel
from core.honest_backtest import LIVE_FAITHFUL, Params
from core.portfolio_sim import PortfolioConfig, run_portfolio
from core.setup3 import NO_TIME_EXITS

COSTM = CostModel(entry_slippage_pct=0.0, exit_slippage_pct=0.3)
VARIANTS = [(3, 0.0), (5, 0.0), (5, 2.0), (10, 2.0)]


def apply_ft(t, df, k, thr_pct):
    """Return a copy of trade t with the follow-through exit applied (or t itself when it does not fire)."""
    i0 = int(t["entry_idx"])
    j = i0 + k
    n = len(df)
    open_trade = bool(t.get("open"))
    ex = int(t["exit_idx"]) if t.get("exit_idx") is not None and not pd.isna(t.get("exit_idx")) else n - 1
    if j >= n or (not open_trade and ex <= j):
        return t
    entry = float(t["entry_price"])
    c = float(df["CLOSE"].iat[j])
    if c > entry * (1 + thr_pct / 100.0):
        return t
    if j + 1 >= n:                                   # the sale would be the next session: not yet known, keep open at the last close
        return t
    px = float(df["OPEN"].iat[j + 1])
    d = pd.Timestamp(df["DATE"].iat[j + 1])
    u = copy(t)
    u.update(exit_idx=j + 1, exit_date=str(d.date()), exit_price=px, pnl_pct=(px / entry - 1) * 100.0, exit_reason="Follow-through fail",
             days_held=int(j + 1 - i0), open=False)
    u["path"] = [pt for pt in t["path"] if pd.Timestamp(pt[0]) <= d]
    return u


def account(trades):
    pc = PortfolioConfig(capital=100000.0, soft_cap=10, hard_cap=10, cap_policy="flat", cost=COSTM, priority="first")
    s = run_portfolio(trades, pc)["summary"]
    return s["cagr_pct"], s["max_drawdown_pct"], (s["cagr_pct"] / abs(s["max_drawdown_pct"]) if s.get("max_drawdown_pct") else float("nan")), s["trades_taken"], s["signals"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--atlas", required=True)
    ap.add_argument("--panel-dir", default="data_panel_w1200")
    a = ap.parse_args()
    lab.POP.update(causal=True, vol_gate=False, slip=0.3, cap=2.5)
    lab.CFG = replace(LIVE_FAITHFUL, exit_fill="eod_next_open")
    p = Params()
    bases = {"live": p, "X1": replace(p, exit_overrides=NO_TIME_EXITS)}
    a_lm, a_cm = pickle.load(open(a.atlas, "rb"))
    p_lm, p_cm = mh.load_panel(a.panel_dir)
    eras = {"A 2006-07..2022-05": mh.subset(p_lm, p_cm, lo=pd.Timestamp("2006-07-01"), hi=mh.ATLAS_START),
            "B 2022-05..2026-11": mh.subset(a_lm, a_cm, lo=mh.ATLAS_START)}
    res = {}
    for era, (lm, cm) in eras.items():
        print(f"\n=== {era} ===")
        print(f"{'base / rule':<22} {'N':>4} {'closed net%':>10} {'CI90':>14} {'PF c/all/ex3':>15} {'paired gain':>22} {'cut%':>5} {'CAGR%':>7} {'maxDD%':>8} {'Calmar':>7}")
        for bname, pp in bases.items():
            trades = lab.baseline_trades(cm, lm, pp, slip=0.3, fill_cap_pct=2.5)
            variants = [(f"{bname} (base)", None)] + [(f"{bname} + FT({k}, {t:+.0f}%)", (k, t)) for k, t in VARIANTS]
            base_net = None
            for name, kt in variants:
                tr = trades if kt is None else [apply_ft(t, cm[t["symbol"]], kt[0], kt[1]) for t in trades]
                d = pd.DataFrame([{k_: v for k_, v in t.items() if k_ != "path"} for t in tr])
                d["net"] = d.pnl_pct - lab.COST
                cl = d[~d.open]
                m, lo, hi = lab.cluster_boot(cl.net.values, cl.symbol.values)
                pc, pa, px = mh.pf3(d.net.values, d.open.values)
                cagr, dd, calmar, taken, sig = account(tr)
                if kt is None:
                    base_net = d.net.values.copy()
                    gain = "-"
                    cut = 0.0
                else:
                    diff = d.net.values - base_net
                    gm, glo, ghi = lab.cluster_boot(diff, d.symbol.values)
                    gain = f"{gm:+.2f} [{glo:+.2f},{ghi:+.2f}]"
                    cut = 100 * float((d.exit_reason == "Follow-through fail").mean())
                res[(era, name)] = dict(cagr=cagr, dd=dd, calmar=calmar, gain=(None if kt is None else gm), glo=(None if kt is None else glo))
                print(f"{name:<22} {len(d):4d} {m:+8.2f}  [{lo:+5.1f},{hi:+5.1f}] {pc:4.2f}/{pa:4.2f}/{px:4.2f} {gain:>22} {cut:5.0f} {cagr:+7.1f} {dd:8.1f} {calmar:7.2f}")
    print("\nVERDICT (each rule versus ITS OWN base: Calmar AND CAGR up in both eras, DD not worse by > 2pp, paired gain > 0 in both eras)")
    ea, eb = list(eras)
    for bname in bases:
        for k, t in VARIANTS:
            nm, b0 = f"{bname} + FT({k}, {t:+.0f}%)", f"{bname} (base)"
            ok, parts = True, []
            for e in (ea, eb):
                v, b = res[(e, nm)], res[(e, b0)]
                good = v["calmar"] > b["calmar"] and v["cagr"] > b["cagr"] and v["dd"] >= b["dd"] - 2.0 and v["gain"] > 0
                ok &= good
                parts.append(f"{e[0]}: Calmar {b['calmar']:.2f}->{v['calmar']:.2f} CAGR {b['cagr']:+.1f}->{v['cagr']:+.1f} DD {b['dd']:.1f}->{v['dd']:.1f} gain {v['gain']:+.2f}")
            print(f"  {nm:<22} {' | '.join(parts)} -> {'CANDIDATE' if ok else 'not adopted'}")


if __name__ == "__main__":
    main()
