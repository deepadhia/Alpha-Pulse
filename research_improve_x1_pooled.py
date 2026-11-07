# -*- coding: utf-8 -*-
"""
research_improve_x1_pooled.py -- POST-HOC: pooled paired gain of X1 (time-based exits off) over live exits on identical
entries, 2006-2026, with a year table.  Exploratory (written after research_improve_history.py output); a pass is only a
candidate for a forward paper test, never a live change.

  .venv\\Scripts\\python.exe research_improve_x1_pooled.py --atlas data_20261107.pkl --panel-dir data_panel_w1200
"""
import argparse
import logging
import pickle
from dataclasses import replace

import numpy as np
import pandas as pd

logging.disable(logging.CRITICAL)
import research_alpha_lab as lab
import research_improve_history as ih
import research_max_history as mh
from core.honest_backtest import Params
from core.setup3 import NO_TIME_EXITS


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--atlas", required=True)
    ap.add_argument("--panel-dir", default="data_panel_w1200")
    a = ap.parse_args()
    lab.POP.update(causal=True, vol_gate=False, slip=0.3, cap=2.5)
    lab.CFG = ih.LIVE_FAITHFUL_EOD
    p = Params()
    px1 = replace(p, exit_overrides=NO_TIME_EXITS)
    a_lm, a_cm = pickle.load(open(a.atlas, "rb"))
    p_lm, p_cm = mh.load_panel(a.panel_dir)
    parts = []
    for era, (lm, cm) in (("pre", mh.subset(p_lm, p_cm, lo=pd.Timestamp("2006-07-01"), hi=mh.ATLAS_START)),
                          ("atlas", mh.subset(a_lm, a_cm, lo=mh.ATLAS_START))):
        base, _ = ih.build_base(cm, lm, p)
        live = lab.rerun_exits(base, cm, p)
        x1 = lab.rerun_exits(base, cm, px1)
        d = base[["symbol", "year", "entry_date", "pnl_pct"]].copy()
        d["live"], d["x1"], d["era"] = live, x1, era
        d["diff"] = d.x1 - d.live
        parts.append(d.dropna(subset=["diff"]))
    D = pd.concat(parts, ignore_index=True)
    m, lo, hi = lab.cluster_boot(D["diff"].values, D.symbol.values)
    top = D["diff"].nlargest(3).index
    ex3 = D.drop(index=top)["diff"].mean()
    print(f"POOLED 2006-2026 X1 minus live exits, N={len(D)}: {m:+.2f}pp CI90 [{lo:+.2f},{hi:+.2f}]; dropping the 3 largest gains {ex3:+.2f}pp")
    print(f"  share of trades where X1 is better {100 * (D['diff'] > 1e-9).mean():.0f}%, equal {100 * (D['diff'].abs() <= 1e-9).mean():.0f}%, worse {100 * (D['diff'] < -1e-9).mean():.0f}%")
    print("  by year (N, mean gain pp):")
    row = []
    for y, g in D.groupby("year"):
        row.append(f"{int(y)}: {len(g)}, {g['diff'].mean():+.1f}")
    print("   " + " | ".join(row))
    big = D[D.year.map(D.groupby('year').size()) >= 8].groupby("year")["diff"].mean()
    print(f"  years with >= 8 trades: {len(big)}; X1 better in {int((big > 0).sum())}, worse in {int((big < 0).sum())}")
    for era, g in D.groupby("era"):
        mm, l, h = lab.cluster_boot(g["diff"].values, g.symbol.values)
        print(f"  {era}: {mm:+.2f}pp CI90 [{l:+.2f},{h:+.2f}] N={len(g)}")


if __name__ == "__main__":
    main()
