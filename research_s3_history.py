# -*- coding: utf-8 -*-
"""
research_s3_history.py -- the S3 paper rule (core/setup3.PAPER_PARAMS, X1 exits) replayed on 2006-2022 listings
it has never seen (added 2026-11-07).

The S3 rule and its Rs 25 Cr turnover cap were fitted / chosen on 2023-26 (LEARNINGS 20.3-20.5), so the replay
numbers there are in-sample for the cap.  2006-2022 is a genuinely out-of-sample test of the rule as it stands.

PRE-REGISTRATION (2026-11-07, before running): no parameter is changed.  Population = panel proxy listings
(build_panel_ipo_universe.py), flat 1.0% round-trip cost, slip 0.3%, X1 exits, eod_next_open fills.
  Pass mark for "S3 generalises": paper-rule closed-trade net mean > 0 with the 90% symbol-cluster interval above 0 on
  2006-2022, positive in at least half of the years with >= 8 trades, positive after dropping the top 3 trades, AND
  paper rule minus the matched random S3-state control > 0 (the trigger/cap adds something).
Caveat stated in advance: Rs 25 Cr of 20-day turnover is a NOMINAL cap; market turnover grew many-fold since 2006, so
in early years the cap admits far more of the market than in 2023-26.  The uncapped S3 result is shown beside it.

  .venv\\Scripts\\python.exe research_s3_history.py --atlas data_20261107.pkl --panel-dir data_panel_w1200
"""
import argparse
import logging
import pickle

import numpy as np
import pandas as pd

logging.disable(logging.CRITICAL)
import research_max_history as mh
from core.honest_backtest import Params
from core.setup2 import clean_universe
from core.setup3 import PAPER_PARAMS, Setup3Params, control_indices, paper_exit_params, simulate_control_entry, simulate_symbol_setup3
from research_alpha_lab import cluster_boot, pf
from research_setup2 import CFG, block, diff_cluster_boot, to_df


def run_set(label, clean, X1):
    rows_all, rows_paper = [], []
    for s, (df, li) in clean.items():
        rows_all.extend(simulate_symbol_setup3(df, s, li, X1, Setup3Params(), CFG, slip_pct=0.3, include_open=True))
        rows_paper.extend(simulate_symbol_setup3(df, s, li, X1, PAPER_PARAMS, CFG, slip_pct=0.3, include_open=True))
    A, P = to_df(rows_all), to_df(rows_paper)
    cmap = {s: df for s, (df, li) in clean.items()}
    print(f"\n--- {label}: clean universe {len(clean)} symbols")
    block("S3 uncapped (X1 exits) AS RUN", A)
    block("S3 PAPER RULE (turnover <= Rs 25 Cr) AS RUN", P)
    for nm, d in (("uncapped", A), ("paper", P)):
        d["jump_up"], d["jump_dn"] = mh.flag_jumps(d, cmap)
        if d.jump_up.any():
            print(f"   {nm}: {int(d.jump_up.sum())} up-jump trade(s) (> +60% in one day: cannot be real): "
                  + ", ".join(d[d.jump_up].symbol.astype(str) + " " + d[d.jump_up].pnl_pct.round(0).astype(int).astype(str) + "%"))
        if d.jump_dn.any():
            print(f"   {nm}: {int(d.jump_dn.sum())} down-jump trade(s) (< -40% in one day; kept): "
                  + ", ".join(d[d.jump_dn].symbol.astype(str) + " " + d[d.jump_dn].pnl_pct.round(0).astype(int).astype(str) + "%"))
    A, P = A[~A.jump_up].copy(), P[~P.jump_up].copy()
    block("S3 uncapped, up-jump trades removed", A)
    block("S3 PAPER RULE, up-jump trades removed", P)
    if len(P):
        yrs = []
        for y, g in P.groupby("year"):
            yrs.append(f"{int(y)}: N={len(g)} {g.net.mean():+.1f}%")
        print("   paper rule by year: " + " | ".join(yrs))
        good = [(int(y), g.net.mean()) for y, g in P.groupby("year") if len(g) >= 8]
        print(f"   years with >= 8 trades: {len(good)}; positive: {sum(1 for _, m in good if m > 0)}")
    return A, P


def control(label, clean, X1, s3p, draws=10, seed=300):
    elig = {s: control_indices(df, li, Params(), s3p) for s, (df, li) in sorted(clean.items())}
    elig = {s: v for s, v in elig.items() if v}
    out = []
    for k in range(draws):
        r = np.random.default_rng(seed + k)
        for s, idx in elig.items():
            df, li = clean[s]
            t = simulate_control_entry(df, s, li, int(r.choice(idx)), X1, s3p, CFG, slip_pct=0.3)
            if t is not None:
                out.append(t)
    C = to_df(out)
    cmap = {s: df for s, (df, li) in clean.items()}
    up, _ = mh.flag_jumps(C, cmap)
    print(f"   {label}: {C.symbol.nunique()} symbols x {draws} draws; {int(up.sum())} up-jump control trades removed")
    C = C[~up].copy()
    block(f"   control {label}", C, full=False)
    return C


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--atlas", required=True)
    ap.add_argument("--panel-dir", default="data_panel_w1200")
    a = ap.parse_args()
    X1 = paper_exit_params()
    a_lm, a_cm = pickle.load(open(a.atlas, "rb"))
    p_lm, p_cm = mh.load_panel(a.panel_dir)
    print("S3 PAPER RULE HISTORY REPLAY (tier REPLAY; flat 1.0% round-trip cost; 90% symbol-cluster intervals)")

    at_clean, _ = clean_universe(a_lm, a_cm)
    A_at, P_at = run_set("ATLAS 2022-26 (reference; cap chosen on this sample)", at_clean, X1)

    pp_lm, pp_cm = mh.subset(p_lm, p_cm, lo=mh.ATLAS_START)
    pp_clean, _ = clean_universe(pp_lm, pp_cm, min_listing="2022-05-27")
    run_set("PANEL PROXY 2022-26 (validation of the proxy)", pp_clean, X1)

    pre_lm, pre_cm = mh.subset(p_lm, p_cm, lo=pd.Timestamp("2006-07-01"), hi=mh.ATLAS_START)
    pre_clean, why = clean_universe(pre_lm, pre_cm, min_listing="2006-01-01")
    print(f"\npre-2022 clean universe {len(pre_clean)} of {len(pre_cm)}; dropped reasons: {pd.Series(why).value_counts().to_dict()}")
    A_pre, P_pre = run_set("PANEL PRE-2022 2006-07..2022-05 (OUT OF SAMPLE)", pre_clean, X1)

    if len(P_pre):
        for lab_, lo_, hi_ in (("2010-2022", 2010, 2023), ("2014-2022", 2014, 2023)):
            sub = P_pre[(P_pre.year >= lo_) & (P_pre.year < hi_)]
            block(f"paper rule {lab_}", sub)
    print("\nCONTROLS (random eligible S3-state day per symbol, no breakout trigger, same X1 exits)")
    C_pre_all = control("pre-2022 uncapped state", pre_clean, X1, Setup3Params())
    C_pre_cap = control("pre-2022 capped state (turnover <= 25 Cr)", pre_clean, X1, PAPER_PARAMS)
    if len(P_pre) and len(C_pre_cap):
        d, lo, hi = diff_cluster_boot(P_pre.net.values, P_pre.symbol.values, C_pre_cap.net.values, C_pre_cap.symbol.values)
        print(f"   paper rule minus capped control (pre-2022): {d:+.2f}pp CI90 [{lo:+.1f},{hi:+.1f}]")
    if len(A_pre) and len(C_pre_all):
        d, lo, hi = diff_cluster_boot(A_pre.net.values, A_pre.symbol.values, C_pre_all.net.values, C_pre_all.symbol.values)
        print(f"   S3 uncapped minus uncapped control (pre-2022): {d:+.2f}pp CI90 [{lo:+.1f},{hi:+.1f}]")
    C_at_cap = control("Atlas capped state", at_clean, X1, PAPER_PARAMS)
    d, lo, hi = diff_cluster_boot(P_at.net.values, P_at.symbol.values, C_at_cap.net.values, C_at_cap.symbol.values)
    print(f"   paper rule minus capped control (Atlas 2022-26): {d:+.2f}pp CI90 [{lo:+.1f},{hi:+.1f}]")


if __name__ == "__main__":
    main()
