# -*- coding: utf-8 -*-
"""
research_max_history_extras.py -- fill/slippage sensitivity of policy P on the max-history cohorts and the replay of the
most recent months (added 2026-11-07).  Companion to research_max_history.py (same inputs, same engine, no tuning).

  .venv\\Scripts\\python.exe research_max_history_extras.py --atlas data_20261107.pkl --panel-dir data_panel_w1200 --out-dir reports/2026-11-07
(needs reports/<out-dir>/maxhist_trades_atlas.csv written by research_max_history.py)
"""
import argparse
import logging
import os
import pickle
from dataclasses import replace

import numpy as np
import pandas as pd

logging.disable(logging.CRITICAL)
import research_alpha_lab as lab
import research_max_history as mh
from core.honest_backtest import LIVE_FAITHFUL, Params


def stats(trs):
    d = pd.DataFrame([{k: v for k, v in t.items() if k != "path"} for t in trs])
    d["net"] = d.pnl_pct - lab.COST
    cl = d[~d.open]
    m, lo, hi = lab.cluster_boot(cl.net.values, cl.symbol.values)
    ex3 = np.sort(cl.net.values)[:-3]
    return len(d), m, lo, hi, mh.pf_of(cl.net.values), mh.pf_of(d.net.values), mh.pf_of(ex3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--atlas", required=True)
    ap.add_argument("--panel-dir", default="data_panel_w1200")
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args()
    lab.CFG = replace(LIVE_FAITHFUL, exit_fill="eod_next_open")
    p = Params()
    a_lm, a_cm = pickle.load(open(a.atlas, "rb"))
    p_lm, p_cm = mh.load_panel(a.panel_dir)
    pre_lm, pre_cm = mh.subset(p_lm, p_cm, lo=pd.Timestamp("2006-07-01"), hi=mh.ATLAS_START)
    at_lm, at_cm = mh.subset(a_lm, a_cm, lo=mh.ATLAS_START)

    print("SLIPPAGE / FILL SENSITIVITY (closed-trade net after 1.0% costs; PF closed / incl.open / closed ex-top3)")
    for lab_, slip, cap in (("resting +0.3% cap 2.5", 0.3, 2.5), ("resting +1.0% cap 2.5", 1.0, 2.5),
                            ("chase +1.7% no cap", 1.7, None), ("chase +2.5% no cap", 2.5, None)):
        for era, lm, cm in (("pre-2022 panel", pre_lm, pre_cm), ("Atlas 2022-26", at_lm, at_cm)):
            trs = lab.baseline_trades(cm, lm, p, slip=slip, fill_cap_pct=cap, causal=True, vol_gate=False)
            n, m, lo, hi, pc, pa, px = stats(trs)
            print(f"{lab_:<24} {era:<16} N={n:3d} net={m:+5.2f}% CI90=[{lo:+5.1f},{hi:+5.1f}] PF {pc:4.2f}/{pa:4.2f}/{px:4.2f}")

    d = pd.read_csv(os.path.join(a.out_dir, "maxhist_trades_atlas.csv"), parse_dates=["entry_ts"])
    for lab_, start in (("entries since 2026-07-05 (clean cohort)", "2026-07-05"),
                        ("entries since 2026-09-29 (native v3.5+ cohort)", "2026-09-29")):
        x = d[d.entry_ts >= start]
        cl = x[~x.open]
        print(f"\nREPLAY {lab_}: N={len(x)} (open {int(x.open.sum())}) closed net {cl.net.mean():+.2f}% win {100 * (cl.net > 0).mean():.0f}% "
              f"PF closed {mh.pf_of(cl.net.values):.2f} incl.open {mh.pf_of(x.net.values):.2f}")
    x = d[d.entry_ts >= "2026-09-29"].sort_values("entry_ts")
    print(x[["symbol", "entry_date", "exit_date", "entry_price", "exit_price", "net", "exit_reason", "open"]]
          .assign(exit_reason=lambda z: z.exit_reason.str[:28]).to_string())


if __name__ == "__main__":
    main()
