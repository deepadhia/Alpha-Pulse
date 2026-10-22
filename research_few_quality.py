# -*- coding: utf-8 -*-
"""
research_few_quality.py -- "I only need a few": can a small, high-quality subset of the policy-P and S3-X1 signals be isolated?

PRE-REGISTRATION (written 2026-10-22 BEFORE any kept-vs-rejected outcome was computed).
Owner: not every mover must be caught, a few are enough.  LEARNINGS 15, 17.4, 17.5 found that every quality filter lowered or did not raise OOS profit
factor.  The two markers found since that hold their sign in all test years are VOLATILITY (ATR20, cross-sectional AUC 0.62, 20.1) and LOWER TURNOVER
(AUC 0.46 cross-section, 0.38 among near-high IPO days, 20.0 / 20.1).  This tests exactly those two, on REALISED trades, with thresholds that have no
free parameter (the median of the pooled trade set), so nothing can be tuned.

TRADE SET: policy P trades (causal resting entry, P exits, as research_setup2.run_p) and S3 trades with X1 exits (research_setup3), flat 1.0% costs,
open trades marked to market.  Features known BEFORE the entry bar (bars up to entry_idx - 1): ATR20 = mean over the last 20 sessions of (high - low)
/ close, in %; TURN20 = mean of close x volume over the last 20 sessions, Rs crore.  Threshold = median of the pooled set (P and S3 pooled).
RULES (three, fixed): R1 = ATR20 >= median.  R2 = TURN20 <= median.  R3 = R1 AND R2 (the "few": about a quarter of the signals).
PRIMARY test = R3 on the pooled set.  R1, R2 and the by-setup splits (P only, S3 only) are secondary (disclosed look count: 3 rules x 3 sets = 9).
BAR (for the primary): kept N >= 60; kept mean net 90% symbol-cluster CI lower bound > 0; kept minus rejected mean difference with CI lower bound > 0;
kept mean > 0 without its largest trade; at most one negative entry year with N >= 8 among the kept; kept PF (incl. open) reported three ways.
A pass = PAPER_ONLY "A-grade" label, never a replacement of the order sheet (the rejected trades still earn money in the replay).
Reported: how many kept trades per year, the share of the whole book's gross profit the kept subset holds, outliers (>= +25%).

  .venv\\Scripts\\python.exe research_few_quality.py --cache data.pkl --out-dir reports/2026-10-22
"""
import argparse
import logging
import os
import pickle
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, ".")
logging.disable(logging.CRITICAL)
from core.honest_backtest import Params
from core.setup2 import clean_universe
from core.setup3 import Setup3Params, simulate_symbol_setup3
from research_alpha_lab import COST
from research_setup2 import CFG, Tee, block, diff_cluster_boot, run_p, to_df, year_table
from research_setup3 import NO_TIME_EXITS


def feats(df, i):
    h, l, c, v = (df[k].values.astype(float) for k in ("HIGH", "LOW", "CLOSE", "VOLUME"))
    lo = max(0, i - 20)
    if i - lo < 8:
        return np.nan, np.nan
    atr = float(np.mean((h[lo:i] - l[lo:i]) / c[lo:i]) * 100)
    turn = float(np.mean(c[lo:i] * v[lo:i]) / 1e7)
    return atr, turn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--out-dir", default=None)
    a = ap.parse_args()
    tee = Tee()
    sys.stdout = tee
    L, C = pickle.load(open(a.cache, "rb"))
    clean, _ = clean_universe(L, C)
    P = to_df(run_p(L, C))
    P["setup"] = "P"
    rows = []
    for s, (df, li) in clean.items():
        rows.extend(simulate_symbol_setup3(df, s, li, Params(exit_overrides=NO_TIME_EXITS), Setup3Params(), CFG, slip_pct=0.3, include_open=True))
    S = to_df(rows)
    S["setup"] = "S3"
    keep_cols = ["symbol", "setup", "entry_date", "exit_date", "entry_idx", "net", "pnl_pct", "open", "year", "exit_reason", "days_held_cal", "initial_risk_pct"]
    T = pd.concat([P[keep_cols], S[keep_cols]], ignore_index=True)
    atr, turn = [], []
    for t in T.itertuples():
        df = C[t.symbol] if t.setup == "P" else clean[t.symbol][0]
        x, y = feats(df, int(t.entry_idx))
        atr.append(x)
        turn.append(y)
    T["atr"], T["turn"] = atr, turn
    T = T.dropna(subset=["atr", "turn"]).reset_index(drop=True)
    m_atr, m_turn = T.atr.median(), T.turn.median()
    T["R1"] = T.atr >= m_atr
    T["R2"] = T.turn <= m_turn
    T["R3"] = T.R1 & T.R2
    print(f"research_few_quality.py  costs flat {COST}%; CI = 90% symbol-cluster bootstrap; tier REPLAY\n  trades {len(T)} (P {int((T.setup == 'P').sum())}, S3-X1 {int((T.setup == 'S3').sum())}); "
          f"thresholds (medians): ATR20 {m_atr:.2f}%  TURN20 Rs {m_turn:.1f} Cr\n")
    block("WHOLE SET (reference)", T)
    gp_all = T.net[T.net > 0].sum()
    res = {}
    for setup in ("POOLED", "P", "S3"):
        d = T if setup == "POOLED" else T[T.setup == setup]
        print("\n" + "=" * 120 + f"\n{setup}\n" + "=" * 120)
        for rule in ("R3", "R1", "R2"):
            k, r = d[d[rule]], d[~d[rule]]
            print(f"--- rule {rule} ---")
            rk = block(f"  kept ({rule})", k)
            block(f"  rejected ({rule})", r, full=False)
            if len(k) > 5 and len(r) > 5:
                dm, lo, hi = diff_cluster_boot(k.net.values, k.symbol.values, r.net.values, r.symbol.values)
                print(f"  kept minus rejected: {dm:+.2f}pp CI90 [{lo:+.1f}, {hi:+.1f}]; kept share of gross profit {100 * k.net[k.net > 0].sum() / d.net[d.net > 0].sum():.0f}%; kept trades per year {len(k) / 4.4:.0f}")
                yrs = year_table(k, f"kept {rule}") if rule == "R3" and setup == "POOLED" else None
                res[(setup, rule)] = (rk, dm, lo, hi, yrs)
    print("\n" + "=" * 120 + "\nPRIMARY: R3 on the pooled set\n" + "=" * 120)
    rk, dm, lo, hi, yrs = res[("POOLED", "R3")]
    neg = [y for y, (n_, m_, _p, _w) in yrs.items() if n_ >= 8 and m_ < 0]
    ok = {"kept N>=60": rk["n"] >= 60, "kept CI lower>0": rk["lo"] > 0, "kept-rejected CI lower>0": lo > 0, "kept ex-top1>0": rk["mean_ex1"] > 0, "<=1 negative year": len(neg) <= 1}
    print("  " + " | ".join(f"{k} {'PASS' if v else 'FAIL'}" for k, v in ok.items()) + f"   (kept N {rk['n']}, mean {rk['mean']:+.2f}% CI [{rk['lo']:+.2f},{rk['hi']:+.2f}], difference {dm:+.2f}pp [{lo:+.1f},{hi:+.1f}], negative years {neg})")
    print(f"  VERDICT: {'MEETS THE BAR (PAPER_ONLY A-grade label)' if all(ok.values()) else 'DOES NOT MEET THE BAR'}")
    if a.out_dir:
        os.makedirs(a.out_dir, exist_ok=True)
        T.to_csv(os.path.join(a.out_dir, "few_quality_trades.csv"), index=False)
        with open(os.path.join(a.out_dir, "few_quality_output.txt"), "w", encoding="utf-8") as fh:
            fh.write(tee.buf.getvalue())
    sys.stdout = sys.__stdout__


if __name__ == "__main__":
    main()
