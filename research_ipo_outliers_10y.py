# -*- coding: utf-8 -*-
"""
research_ipo_outliers_10y.py -- what do the IPO OUTLIERS of the last ten years have in common (every liquid IPO, not only the ones policy P
traded), and does the shelf breakout work?  (added 2026-11-07, at the owner's request: "something must be common in outliers")

PRE-REGISTRATION (written before running)
 Population: NSE listings 2016-07-01..2026-06-30 with listing-day range >= 5%, price >= Rs 25 and 20-day turnover >= Rs 0.75 Cr at
   session 5.  Era A = 2016-07..2022-05 (panel proxy), era B = 2022-05-27.. (Atlas true list).  Decision point = close of session 5 (age >= 5 d),
   so only sessions 0-5 and market data up to that close are used (no look-ahead).
 Outlier labels, measured from the session-5 close over the NEXT 250 calendar days (needs the history; listings that are too recent are dropped):
   O100  max high >= +100%      O50  max high >= +50% within 120 calendar days (the repo's W50 definition)
 Features (13): listing-day range, close location, body (close/open-1), session-1 gap, return session 0->5, close vs listing high at session 5,
   drawdown from the post-listing high, turnover rank within the listing year, price rank within the listing year, 5-session turnover vs listing-day
   turnover, 5-session volume vs listing-day volume, IPO supply in the prior 30 days, Nifty 63-day return / above 200-day average / drawdown (2007+).
 A feature is a CANDIDATE MARKER only if its rank AUC is on the same side of 0.5 and >= 0.60 (or <= 0.40) in BOTH eras for the same label.
   Multi-feature: gradient boosting trained on one era and scored on the other (both directions); reported AUC needs > 0.60 in both directions.
 SHELF: setup="shelf" (engine as in research_capture_gaps.py: slip 0.3, cap 2.5, min age 3, causal, no volume gate), live exits and X1 exits,
   flat 1.0% cost, era A (holdout; the shelf rule was built on 2022-26) and era B.  Pass mark: holdout closed net CI90 lower bound > 0 and
   positive in at least half of the years with >= 8 trades.  Shelf stays PAPER_ONLY whatever happens here.
 Nothing is adopted from this script.

  .venv\\Scripts\\python.exe research_ipo_outliers_10y.py --atlas data_20261107.pkl --panel-dir data_panel_w1200 --out-dir reports/2026-11-07
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
import research_outlier_anatomy as oa
from core.honest_backtest import LIVE_FAITHFUL, Params, _listing_context, simulate_symbol_cross
from core.setup3 import NO_TIME_EXITS

FEATS = ["ld_range", "ld_close_loc", "ld_body", "gap1", "ret_0_5", "c5_vs_ldhigh", "dd_from_high", "turn_rank", "price_rank",
         "turn_ratio", "vol_ratio5", "supply30", "n_ret63", "n_above200", "n_dd252"]
LAST_LISTING = pd.Timestamp("2026-06-30")


def build_events(cm, lm, nf, ld_dates, lo, hi):
    rows = []
    for s, df in cm.items():
        li = lm.get(s)
        if li is None or len(df) < 15:
            continue
        ld = pd.Timestamp(li["listing_date"])
        ld = ld.tz_localize(None).normalize() if ld.tzinfo else ld.normalize()
        if not (lo <= ld < hi) or ld > LAST_LISTING:
            continue
        ld_high, ld_low, _ = _listing_context(df, li)
        d = pd.to_datetime(df["DATE"]).values
        o, h, l, c, v = (df[k].values.astype(float) for k in ("OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"))
        age = (pd.to_datetime(d) - ld).days.values
        i5 = int(np.argmax(age >= 5)) if (age >= 5).any() else None
        if i5 is None or i5 < 4 or i5 >= len(df) - 1 or not ld_low or ld_low <= 0:
            continue
        rng = (ld_high - ld_low) / ld_low * 100
        turn = c[:i5 + 1] * v[:i5 + 1] / 1e7
        if rng < 5.0 or c[0] < 25 or np.mean(turn) < 0.75:
            continue
        end = pd.Timestamp(d[-1])
        date5 = pd.Timestamp(d[i5])
        horizon = {"O100": 250, "O50": 120}
        f = dict(symbol=s, listing_date=ld, year=ld.year, rng=rng)
        for lab_, days in horizon.items():
            if (end - date5).days < days:
                f[lab_] = np.nan
                continue
            m = (pd.to_datetime(d) > date5) & (pd.to_datetime(d) <= date5 + pd.Timedelta(days=days))
            f[lab_] = float(h[m].max() / c[i5] - 1 >= (1.0 if lab_ == "O100" else 0.5)) if m.any() else np.nan
        post_high = h[1:i5 + 1].max() if i5 >= 1 else h[0]
        f.update(
            ld_range=rng, ld_close_loc=(c[0] - l[0]) / (h[0] - l[0]) if h[0] > l[0] else np.nan, ld_body=(c[0] / o[0] - 1) * 100,
            gap1=(o[1] / c[0] - 1) * 100, ret_0_5=(c[i5] / c[0] - 1) * 100, c5_vs_ldhigh=(c[i5] / ld_high - 1) * 100,
            dd_from_high=(c[i5] / max(ld_high, post_high) - 1) * 100,
            turn_level=np.log(np.mean(turn[1:]) + 1e-9), price_level=np.log(c[0]),
            turn_ratio=np.mean(turn[1:]) / turn[0] if turn[0] > 0 else np.nan, vol_ratio5=np.mean(v[1:i5 + 1]) / v[0] if v[0] > 0 else np.nan,
            supply30=int(((ld_dates >= ld - pd.Timedelta(days=30)) & (ld_dates < ld)).sum()))
        if date5 >= oa.NIFTY_START:
            k = nf.index.searchsorted(date5, side="right") - 1
            f.update({c_: float(nf.iloc[k][c_]) for c_ in ("n_ret63", "n_above200", "n_dd252")})
        else:
            f.update(n_ret63=np.nan, n_above200=np.nan, n_dd252=np.nan)
        rows.append(f)
    E = pd.DataFrame(rows)
    E["turn_rank"] = E.groupby("year").turn_level.rank(pct=True)
    E["price_rank"] = E.groupby("year").price_level.rank(pct=True)
    return E


def shelf_report(label, lm, cm, p):
    out = {}
    for nm, pp in (("live exits", p), ("X1 exits", replace(p, exit_overrides=NO_TIME_EXITS))):
        tr = []
        for sym, df in cm.items():
            tr.extend(simulate_symbol_cross(df, sym, lm.get(sym, {}), pp, slip_pct=0.3, fill_cap_pct=2.5, min_age=3, include_open=True,
                                            cfg=lab.CFG, causal=True, vol_gate=False, setup="shelf"))
        if not tr:
            print(f"  {label} shelf {nm}: no trades")
            continue
        d = pd.DataFrame([{k: v for k, v in t.items() if k != "path"} for t in tr])
        d["net"] = d.pnl_pct - lab.COST
        d["year"] = pd.to_datetime(d.entry_date).dt.year
        cl = d[~d.open]
        m, lo, hi = lab.cluster_boot(cl.net.values, cl.symbol.values)
        pc, pa, px = mh.pf3(d.net.values, d.open.values)
        ys = [(int(y), len(g), g.net.mean()) for y, g in d.groupby("year") if len(g) >= 8]
        print(f"  {label} shelf {nm:<11} N={len(d):3d} (open {int(d.open.sum())}) win {100 * (cl.net > 0).mean():4.1f}% closed net {m:+5.2f}% CI90 [{lo:+5.1f},{hi:+5.1f}] "
              f"median {cl.net.median():+5.2f}% PF {pc:4.2f}/{pa:4.2f}/{px:4.2f}  years>=8: {sum(1 for _, _, x in ys if x > 0)}/{len(ys)} positive")
        out[nm] = d
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--atlas", required=True)
    ap.add_argument("--panel-dir", default="data_panel_w1200")
    ap.add_argument("--out-dir", default=None)
    a = ap.parse_args()
    lab.CFG = replace(LIVE_FAITHFUL, exit_fill="eod_next_open")
    p = Params()
    a_lm, a_cm = pickle.load(open(a.atlas, "rb"))
    p_lm, p_cm = mh.load_panel(a.panel_dir)
    nifty = pd.read_csv(oa.NIFTY, parse_dates=["Date"]).set_index("Date")["Close"]
    nf = oa.nifty_features(nifty)
    lds = [pd.Timestamp(li["listing_date"]) for li in list(p_lm.values()) + list(a_lm.values())]
    ld_dates = pd.DatetimeIndex(sorted(t.tz_localize(None).normalize() if t.tzinfo else t.normalize() for t in lds))
    EA = build_events(p_cm, p_lm, nf, ld_dates, pd.Timestamp("2016-07-01"), mh.ATLAS_START)
    EB = build_events(a_cm, a_lm, nf, ld_dates, mh.ATLAS_START, pd.Timestamp("2100-01-01"))
    for nm, E in (("A 2016-07..2022-05", EA), ("B 2022-05..", EB)):
        print(f"{nm}: {len(E)} liquid IPOs; O100 base rate {100 * E.O100.mean():.1f}% (n={int(E.O100.notna().sum())}), O50 base rate {100 * E.O50.mean():.1f}% (n={int(E.O50.notna().sum())})")
        print("   by listing year (n, O50 %, O100 %): " + " | ".join(f"{int(y)}: {len(g)}, {100 * g.O50.mean():.0f}, {100 * g.O100.mean():.0f}" for y, g in E.groupby("year")))
    if a.out_dir:
        pd.concat([EA.assign(era="A"), EB.assign(era="B")]).to_csv(os.path.join(a.out_dir, "ipo_outlier_events.csv"), index=False)

    print("\nRANK AUC for the outlier labels (0.50 = no information); candidate = same side and >= 0.60 / <= 0.40 in BOTH eras")
    print(f"{'feature':<14} {'A O100':>7} {'B O100':>7} {'A O50':>7} {'B O50':>7}  candidate")
    cands = []
    for ft in FEATS:
        r = []
        for lab_ in ("O100", "O50"):
            for E in (EA, EB):
                m = E[lab_].notna()
                r.append(oa.auc(E.loc[m, ft].values, E.loc[m, lab_].astype(bool).values))
        flag = ""
        for lab_, (u, v) in (("O100", r[0:2]), ("O50", r[2:4])):
            if not np.isnan(u) and not np.isnan(v) and ((u >= 0.60 and v >= 0.60) or (u <= 0.40 and v <= 0.40)):
                flag += f" {lab_}:{'high' if u > 0.5 else 'low'}"
        if flag:
            cands.append((ft, flag.strip()))
        print(f"{ft:<14} {r[0]:7.2f} {r[1]:7.2f} {r[2]:7.2f} {r[3]:7.2f}  {flag.strip()}")
    print(f"\ncandidate markers: {cands if cands else 'NONE'}")

    print("\nSHELF BREAKOUT (setup='shelf'; PAPER_ONLY in the live system)")
    shelf_report("A 2006-07..2022-05 (holdout)", *mh.subset(p_lm, p_cm, lo=pd.Timestamp("2006-07-01"), hi=mh.ATLAS_START), p)
    shelf_report("B 2022-05..2026-11", *mh.subset(a_lm, a_cm, lo=mh.ATLAS_START), p)


if __name__ == "__main__":
    main()
