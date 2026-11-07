# -*- coding: utf-8 -*-
"""
research_improve_followup.py -- POST-HOC follow-up to research_improve_history.py (written after seeing its output, so
EXPLORATORY and never adoptable by itself).  The only variant with the same sign in both eras was X1 (time-based exits
off).  A paired per-trade gain ignores that longer holds tie up slots, so this runs X1 through the account simulator
(10 and 15 slots) and reports capital utilisation.

  .venv\\Scripts\\python.exe research_improve_followup.py --atlas data_20261107.pkl --panel-dir data_panel_w1200
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
from core.honest_backtest import LIVE_FAITHFUL, Params
from core.portfolio_sim import PortfolioConfig, run_portfolio
from core.setup3 import NO_TIME_EXITS


def acct(label, trades, slots, cost):
    pc = PortfolioConfig(capital=100000.0, soft_cap=slots, hard_cap=slots, cap_policy="flat", cost=cost, priority="first")
    r = run_portfolio(trades, pc)
    s, eq = r["summary"], r["equity"]
    util = float("nan")
    for c in ("invested", "position_value", "pos_value", "exposure"):
        if c in eq.columns:
            util = float((eq[c] / eq["nav"]).mean()) if "nav" in eq.columns else float("nan")
    if np.isnan(util) and {"nav", "cash"} <= set(eq.columns):
        util = float(((eq.nav - eq.cash) / eq.nav).mean())
    calmar = s["cagr_pct"] / abs(s["max_drawdown_pct"]) if s.get("max_drawdown_pct") else float("nan")
    print(f"  {label:<30} taken {s['trades_taken']:3d}/{s['signals']:3d} total {s['total_return_pct']:+7.1f}% CAGR {s['cagr_pct']:+5.1f}% "
          f"maxDD {s['max_drawdown_pct']:6.1f}% Calmar {calmar:4.2f} PF {s['profit_factor']:4.2f} avg invested {100 * util:4.0f}%"
          f"  worst {s['worst_trade_pct']:+.1f}%")
    return eq


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--atlas", required=True)
    ap.add_argument("--panel-dir", default="data_panel_w1200")
    a = ap.parse_args()
    lab.POP.update(causal=True, vol_gate=False, slip=0.3, cap=2.5)
    lab.CFG = replace(LIVE_FAITHFUL, exit_fill="eod_next_open")
    p = Params()
    px1 = replace(p, exit_overrides=NO_TIME_EXITS)
    a_lm, a_cm = pickle.load(open(a.atlas, "rb"))
    p_lm, p_cm = mh.load_panel(a.panel_dir)
    cost = CostModel(entry_slippage_pct=0.0, exit_slippage_pct=0.3)
    first = True
    for era, (lm, cm) in (("2006-07..2022-05", mh.subset(p_lm, p_cm, lo=pd.Timestamp("2006-07-01"), hi=mh.ATLAS_START)),
                          ("2022-05..2026-11", mh.subset(a_lm, a_cm, lo=mh.ATLAS_START))):
        print(f"\n{era}")
        t_live = lab.baseline_trades(cm, lm, p, slip=0.3, fill_cap_pct=2.5)
        t_x1 = lab.baseline_trades(cm, lm, px1, slip=0.3, fill_cap_pct=2.5)
        for nm, tr in (("live exits", t_live), ("X1 (no time exits)", t_x1)):
            d = pd.DataFrame([{k: v for k, v in t.items() if k != "path"} for t in tr])
            d["net"] = d.pnl_pct - lab.COST
            cl = d[~d.open]
            m, lo, hi = lab.cluster_boot(cl.net.values, cl.symbol.values)
            print(f" {nm}: trades {len(d)} (open {int(d.open.sum())}), closed net {m:+.2f}% CI90 [{lo:+.1f},{hi:+.1f}], median days held {d.days_held.median():.0f}, "
                  f"mean days held {d.days_held.mean():.0f}, PF closed {mh.pf_of(cl.net.values):.2f} incl.open {mh.pf_of(d.net.values):.2f}")
            for slots in (10, 15):
                eq = acct(f"{nm}, {slots} slots", tr, slots, cost)
                if first:
                    print("   equity columns:", list(eq.columns))
                    first = False


if __name__ == "__main__":
    main()
