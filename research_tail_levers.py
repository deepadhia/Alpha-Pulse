# -*- coding: utf-8 -*-
"""
research_tail_levers.py -- do entry/exit levers that favour the right tail (outliers) improve CAGR and drawdown?  (added 2026-11-07)

Owner premise (2026-11-07): "less winners but outliers; most of the money comes from about 10% of the trades".  First the premise is MEASURED
(share of gross profit from the best 10% of trades), then a small pre-registered family of tail levers is run at ACCOUNT level (Rs 1 lakh,
10 slots, charges, 0.3% exit slip) on both eras, 2006-07..2022-05 (panel proxy, holdout for everything tuned on 2022-26) and 2022-05..2026-11.

PRE-REGISTRATION (written before running; nothing is searched):
  Base for every lever = X1 (all time-based exits off), the one candidate that survived research_improve_history.py.
  L1 cushion 0.75   winners (peak run-up >= 15%) keep a stop 25% below the peak instead of 20% (lets leaders breathe)
  L2 cushion 0.70   30% below the peak
  L3 winner start 10%   the peak-based cushion starts at +10% run-up instead of +15%
  L4 winner start 25%   starts at +25%
  L5 trail 13%      the pre-winner trailing distance 13% instead of 10%
  L6 cooldown 10    re-entry in the same symbol after 10 bars instead of 30 (more draws on a leader that was stopped out)
  L7 resting cap 3.5%  accept fills up to listing high +3.5% (catch more gap-ups) instead of +2.5%
  Reading: a lever is a CANDIDATE only if, versus X1 alone, it raises Calmar AND CAGR in BOTH eras and does not deepen the max drawdown by
  more than 2pp in either.  7 levers => one false pass is expected by chance, so a pass is only a forward-test candidate.  No lever is adopted.
Pyramiding, conviction sizing, E11 and re-entry cooldown 20/5 were already tested in LEARNINGS 13 (2022-26 only) and failed; they are not repeated.

  .venv\\Scripts\\python.exe research_tail_levers.py --atlas data_20261107.pkl --panel-dir data_panel_w1200
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

COST = CostModel(entry_slippage_pct=0.0, exit_slippage_pct=0.3)


def with_ov(p, *extra):
    return replace(p, exit_overrides=tuple(NO_TIME_EXITS) + tuple(extra))


def build_variants():
    p = Params()
    x1 = with_ov(p)
    return [
        ("V0 live exits", p, 2.5),
        ("V1 X1 (reference)", x1, 2.5),
        ("L1 X1 + cushion 0.75", with_ov(p, ("winner_cushion_mult", 0.75)), 2.5),
        ("L2 X1 + cushion 0.70", with_ov(p, ("winner_cushion_mult", 0.70)), 2.5),
        ("L3 X1 + winner start 10%", with_ov(p, ("winner_runup_pct", 10.0)), 2.5),
        ("L4 X1 + winner start 25%", with_ov(p, ("winner_runup_pct", 25.0)), 2.5),
        ("L5 X1 + trail 13%", with_ov(p, ("trail_pct_override", 0.13)), 2.5),
        ("L6 X1 + cooldown 10 bars", replace(x1, cooldown_bars=10), 2.5),
        ("L7 X1 + resting cap 3.5%", x1, 3.5),
    ]


def outlier_share(net):
    net = np.sort(np.asarray(net, float))[::-1]
    k = max(1, int(round(0.10 * len(net))))
    gross = net[net > 0].sum()
    return k, (net[:k].sum() / gross * 100.0 if gross > 0 else float("nan")), float(net[:k].mean())


def run_variant(cm, lm, p, cap):
    trades = lab.baseline_trades(cm, lm, p, slip=0.3, fill_cap_pct=cap)
    d = pd.DataFrame([{k: v for k, v in t.items() if k != "path"} for t in trades])
    net = (d.pnl_pct - lab.COST).values
    acc = {}
    for slots in (10,):
        pc = PortfolioConfig(capital=100000.0, soft_cap=slots, hard_cap=slots, cap_policy="flat", cost=COST, priority="first")
        s = run_portfolio(trades, pc)["summary"]
        acc[slots] = s
    return trades, d, net, acc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--atlas", required=True)
    ap.add_argument("--panel-dir", default="data_panel_w1200")
    a = ap.parse_args()
    lab.POP.update(causal=True, vol_gate=False, slip=0.3, cap=2.5)
    lab.CFG = replace(LIVE_FAITHFUL, exit_fill="eod_next_open")
    a_lm, a_cm = pickle.load(open(a.atlas, "rb"))
    p_lm, p_cm = mh.load_panel(a.panel_dir)
    eras = (("2006-07..2022-05 (holdout)", mh.subset(p_lm, p_cm, lo=pd.Timestamp("2006-07-01"), hi=mh.ATLAS_START)),
            ("2022-05..2026-11", mh.subset(a_lm, a_cm, lo=mh.ATLAS_START)))
    res = {}
    for era, (lm, cm) in eras:
        print(f"\n=== {era} ===")
        print(f"{'variant':<28} {'N':>4} {'closed net%':>11} {'PF c/all/ex3':>16} {'top10% share':>12} {'CAGR%':>7} {'maxDD%':>8} {'Calmar':>7} {'taken':>9}")
        for name, p, cap in build_variants():
            trades, d, net, acc = run_variant(cm, lm, p, cap)
            cl = d[~d.open]
            pc, pa, px = mh.pf3(net, d.open.values)
            m, lo, hi = lab.cluster_boot(net[~d.open.values], cl.symbol.values)
            k, share, topmean = outlier_share(net)
            s = acc[10]
            calmar = s["cagr_pct"] / abs(s["max_drawdown_pct"]) if s.get("max_drawdown_pct") else float("nan")
            res[(era, name)] = dict(cagr=s["cagr_pct"], dd=s["max_drawdown_pct"], calmar=calmar)
            print(f"{name:<28} {len(d):4d} {m:+7.2f} [{lo:+.1f},{hi:+.1f}] {pc:4.2f}/{pa:4.2f}/{px:4.2f} {share:6.0f}% ({k} tr) "
                  f"{s['cagr_pct']:+7.1f} {s['max_drawdown_pct']:8.1f} {calmar:7.2f} {s['trades_taken']:4d}/{s['signals']:3d}")
    print("\nVERDICT against the pre-registered rule (versus V1 X1 alone: Calmar AND CAGR up in BOTH eras, max drawdown not worse by more than 2pp)")
    names = [n for n, _, _ in build_variants() if n.startswith("L")]
    e1, e2 = eras[0][0], eras[1][0]
    for n in names:
        ok = True
        parts = []
        for e in (e1, e2):
            b, v = res[(e, "V1 X1 (reference)")], res[(e, n)]
            good = v["calmar"] > b["calmar"] and v["cagr"] > b["cagr"] and (v["dd"] >= b["dd"] - 2.0)
            ok &= good
            parts.append(f"{e[:9]} Calmar {b['calmar']:.2f}->{v['calmar']:.2f} CAGR {b['cagr']:+.1f}->{v['cagr']:+.1f} DD {b['dd']:.1f}->{v['dd']:.1f}")
        print(f"  {n:<28} {' | '.join(parts)} -> {'CANDIDATE' if ok else 'not adopted'}")


if __name__ == "__main__":
    main()
