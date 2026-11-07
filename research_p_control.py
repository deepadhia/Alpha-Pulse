# -*- coding: utf-8 -*-
"""
research_p_control.py -- does the listing-high BREAKOUT TRIGGER add anything, or is the profit just IPO drift plus
the exit rules?  (policy P vs a matched random-entry control; added 2026-11-07)

PRE-REGISTRATION (2026-11-07, before running)
---------------------------------------------
Control: for every symbol that produced a policy-P trade, draw a random tradable bar of the SAME symbol that is
5-60 calendar days after listing, passes the listing-day range >= 5% gate, the 20-day turnover floor (Rs 0.75 Cr)
and the price floor, and buy that bar's OPEN + 0.3% slippage.  Same initial stop (12% cap / swing-low rule), same
live exit engine (close-based stop sold next open, dead-money exits), same 1.0% round-trip cost.  No trigger.
10 independent draws.  Paired difference D = mean(P net) - mean(control net) by symbol cluster bootstrap.
Reading: D > 0 with the 90% interval above 0 means the breakout timing adds edge beyond drift + exits;
D ~ 0 means the profit is exit design plus IPO drift and the entry signal is not what earns it.
Not a tuned rule: nothing is searched.

  .venv\\Scripts\\python.exe research_p_control.py --atlas data_20261107.pkl --panel-dir data_panel --out-dir reports/2026-11-07
"""

import argparse
import os
import pickle
from dataclasses import replace

import numpy as np
import pandas as pd

import research_alpha_lab as lab
import research_max_history as mh
from core.honest_backtest import LIVE_FAITHFUL, Params, _Bars, _listing_context

MIN_AGE, MAX_AGE = 5, 60
TURN_MIN_CR = 0.75


def eligible_bars(df, li, p):
    ld_high, ld_low, list_date = _listing_context(df, li)
    if not ld_low or ld_low <= 0 or (ld_high - ld_low) / ld_low * 100.0 < 5.0:
        return [], ld_high
    d = pd.to_datetime(df["DATE"]).values
    age = (pd.to_datetime(d) - list_date).days.values
    o = df["OPEN"].values.astype(float)
    c = df["CLOSE"].values.astype(float)
    v = df["VOLUME"].values.astype(float)
    turn = pd.Series(c * v / 1e7).rolling(20, min_periods=5).mean().shift(1).values
    out = []
    for i in range(1, len(df) - 1):
        if MIN_AGE <= age[i] <= MAX_AGE and o[i] >= p.min_entry_price and turn[i] >= TURN_MIN_CR:
            out.append(i)
    return out, ld_high


def control_rows(cmap, lmap, symbols, p, draws, seed=0):
    rng = np.random.default_rng(seed)
    elig = {}
    for s in sorted(set(symbols)):
        c = cmap[s]["CLOSE"].values
        if len(c) > 4 and (c[4:] / c[3:-1]).max() > mh.JUMP_UP:
            continue          # one-day close jump above +60% after the listing days: data artifact, never fed to the control
        e, ldh = eligible_bars(cmap[s], lmap.get(s, {}), p)
        if e:
            elig[s] = (e, ldh)
    rows = []
    for k in range(draws):
        for s, (e, ldh) in elig.items():
            i = int(rng.choice(e))
            df = cmap[s]
            ld_high, ld_low, list_date = _listing_context(df, lmap.get(s, {}))
            fill = float(df["OPEN"].iat[i]) * 1.003
            rows.append(dict(symbol=s, entry_idx=i, entry_price=fill, ld_high=ldh, ext=0.0, vr=1.0, rr=2.0,
                             age=int((pd.Timestamp(df["DATE"].iat[i]) - list_date).days), draw=k,
                             entry_ts=pd.Timestamp(df["DATE"].iat[i])))
    return pd.DataFrame(rows)


def run(label, p_trades, cmap, lmap, p, draws, out_dir):
    syms = p_trades.symbol.unique().tolist()
    cr = control_rows(cmap, lmap, syms, p, draws)
    pnl = lab.rerun_exits(cr, cmap, p)
    cr["net"] = pnl - lab.COST
    cr = cr.dropna(subset=["net"])
    cr["year"] = cr.entry_ts.dt.year
    n_draw = cr.draw.nunique()
    # same-symbol pairing: mean over draws per symbol -> compare with the symbol's P mean
    cs = cr.groupby("symbol").net.mean()
    ps = p_trades.groupby("symbol").net.mean()
    common = ps.index.intersection(cs.index)
    diff = (ps[common] - cs[common])
    # weight each symbol by its number of P trades (so the P side is the trade-level mean)
    w = p_trades.groupby("symbol").size()[common].values.astype(float)
    wd = (diff.values * w)
    rng = np.random.default_rng(1)
    idx = rng.integers(0, len(common), (4000, len(common)))
    bs = wd[idx].sum(1) / w[idx].sum(1)
    D = wd.sum() / w.sum()
    lo, hi = np.percentile(bs, [5, 95])
    pt = p_trades[p_trades.symbol.isin(common)]
    ct = cr[cr.symbol.isin(common)]
    print(f"{label}")
    print(f"   P trades (symbols with a control): N={len(pt)} mean net {pt.net.mean():+.2f}%  PF {mh.pf_of(pt.net.values):.2f}")
    print(f"   CONTROL (random eligible day, same symbols, {n_draw} draws): N={len(ct)} mean net {ct.net.mean():+.2f}%  "
          f"PF {mh.pf_of(ct.net.values):.2f}  median {ct.net.median():+.2f}%  win {100 * (ct.net > 0).mean():.1f}%")
    print(f"   P minus control (paired by symbol): {D:+.2f}pp  CI90 [{lo:+.1f}, {hi:+.1f}]")
    yrs = []
    for y in sorted(pt.year.unique()):
        a, b = pt[pt.year == y], ct[ct.year == y]
        if len(a) >= 5 and len(b) >= 5:
            yrs.append((int(y), len(a), a.net.mean(), b.net.mean()))
    print("   by year: " + "  ".join(f"{y}: P {a_:+.1f} vs ctl {b_:+.1f} (n={n})" for y, n, a_, b_ in yrs))
    if out_dir:
        cr.to_csv(os.path.join(out_dir, f"p_control_{label.split()[0].lower()}.csv"), index=False)
    return D, lo, hi


def run_universe(label, p_trades, cmap, lmap, p, draws, lo_date, hi_date):
    """Look-ahead-free control: every symbol listed in [lo, hi) with an eligible day (symbol choice does not use the
    future, unlike the matched control, whose symbols are by construction the ones that later broke out)."""
    syms = []
    for s, li in lmap.items():
        ld = pd.Timestamp(li["listing_date"])
        ld = ld.tz_localize(None) if ld.tzinfo else ld
        if s in cmap and lo_date <= ld < hi_date:
            syms.append(s)
    cr = control_rows(cmap, lmap, syms, p, draws, seed=7)
    pnl = lab.rerun_exits(cr, cmap, p)
    cr["net"] = pnl - lab.COST
    cr = cr.dropna(subset=["net"])
    m, lo, hi = lab.cluster_boot(cr.net.values, cr.symbol.values)
    pm, plo, phi = lab.cluster_boot(p_trades.net.values, p_trades.symbol.values)
    print(f"{label}: ALL-SYMBOL CONTROL (no look-ahead): {cr.symbol.nunique()} symbols x {draws} draws, "
          f"mean net {m:+.2f}% CI90 [{lo:+.1f},{hi:+.1f}] PF {mh.pf_of(cr.net.values):.2f} median {cr.net.median():+.2f}% win {100 * (cr.net > 0).mean():.1f}%")
    print(f"   policy P same period: mean net {pm:+.2f}% CI90 [{plo:+.1f},{phi:+.1f}] PF {mh.pf_of(p_trades.net.values):.2f}; "
          f"P minus all-symbol control {pm - m:+.2f}pp")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--atlas", required=True)
    ap.add_argument("--panel-dir", default="data_panel")
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--draws", type=int, default=10)
    a = ap.parse_args(argv)
    lab.POP.update(causal=True, vol_gate=False, slip=0.3, cap=2.5)
    lab.CFG = replace(LIVE_FAITHFUL, exit_fill="eod_next_open")
    p = Params()
    a_lm, a_cm = pickle.load(open(a.atlas, "rb"))
    p_lm, p_cm = mh.load_panel(a.panel_dir)
    d_at = pd.read_csv(os.path.join(a.out_dir, "maxhist_trades_atlas.csv"), parse_dates=["entry_ts"])
    d_pre = pd.read_csv(os.path.join(a.out_dir, "maxhist_trades_panelpre.csv"), parse_dates=["entry_ts"])
    print("MATCHED RANDOM-ENTRY CONTROL (same symbols, same stops and exits, no breakout trigger)\n")
    run("ATLAS 2022-05..2026-11", d_at, a_cm, a_lm, p, a.draws, a.out_dir)
    print()
    run("PANELPRE 2006-07..2022-05", d_pre, p_cm, p_lm, p, a.draws, a.out_dir)
    print()
    pre_ex06 = d_pre[d_pre.year != 2006]
    run("PANELPRE-ex2006 2007..2022-05", pre_ex06, p_cm, p_lm, p, a.draws, None)
    print("\nLOOK-AHEAD-FREE CONTROL (all listed symbols with an eligible day, not only the ones that later broke out)\n")
    run_universe("ATLAS 2022-05..2026-11", d_at, a_cm, a_lm, p, a.draws, pd.Timestamp("2022-05-27"), pd.Timestamp("2100-01-01"))
    run_universe("PANELPRE 2006-07..2022-05", d_pre, p_cm, p_lm, p, a.draws, pd.Timestamp("2006-07-01"), pd.Timestamp("2022-05-27"))
    run_universe("PANELPRE 2010..2022-05", d_pre[d_pre.year >= 2010], p_cm, p_lm, p, a.draws, pd.Timestamp("2010-01-01"), pd.Timestamp("2022-05-27"))


if __name__ == "__main__":
    main()
