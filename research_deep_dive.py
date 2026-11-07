# -*- coding: utf-8 -*-
"""
research_deep_dive.py -- three NEW angles on the outlier question, each tested in two eras (added 2026-11-07, owner: "dive deeper").

  A  CONDITIONAL TIME STOP.  X1 (time exits off) gains +3.17pp per trade on average, but only some time-stopped trades go on to run.  Among
     trades that P's live exits close with a time stop, does anything known AT THE DECISION DAY predict which ones X1 rescues?  (Target: gain =
     X1 pnl - live pnl.  Features: pnl and peak so far, days held, close vs listing high, volume trend, 5-day range, up-day share, Nifty regime.)
     More data per era than the outlier label (about 70-85 time-stopped trades per era) and a directly actionable decision.
  B  COHORT / SERIAL STATE.  Outliers cluster by listing wave (LEARNINGS 22.6).  Is the state of the IPO market or of P's own recent results,
     known at the order date, related to the trade's outcome?  (Cohort breadth, active count, median 20-day return, listing-day sentiment, IPO
     supply, mean net of P's previous 10 closed trades, number of outliers in the previous 90 days.)
  C  OUTLIER ANATOMY (descriptive, no test): age at entry, gap, days to peak, adverse excursion before the run, follow-through in the first
     3/5/10 sessions, repeat entries, for outliers (net >= +25%) versus all other trades, both eras.
PRE-REGISTRATION: A and B use rank (Spearman) correlation with the target; a feature is a CANDIDATE only if the sign agrees in both eras and
|rho| >= 0.20 in both.  About 14 features per angle => expect false candidates; the standard of proof for any rule built on a candidate is the
two-era account test used in research_tail_levers.py.  Nothing is adopted here.

  .venv\\Scripts\\python.exe research_deep_dive.py --atlas data_20261107.pkl --panel-dir data_panel_w1200
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
import research_outlier_anatomy as oa
from core.honest_backtest import LIVE_FAITHFUL, Params, _listing_context
from core.setup3 import NO_TIME_EXITS


def spearman(x, y):
    x, y = pd.Series(x, dtype=float), pd.Series(y, dtype=float)
    ok = x.notna() & y.notna()
    if ok.sum() < 15:
        return float("nan")
    return float(x[ok].rank().corr(y[ok].rank()))


def decision_features(r, df, lm, nf):
    i0 = int(r.entry_idx)
    dec = int(r.exit_idx) - 1
    if dec <= i0:
        return None
    c, h, l, v = (df[k].values.astype(float) for k in ("CLOSE", "HIGH", "LOW", "VOLUME"))
    ld_high = _listing_context(df, lm.get(r.symbol, {}))[0]
    entry = float(r.entry_price)
    last5 = slice(max(i0, dec - 4), dec + 1)
    f = dict(
        pnl_dec=(c[dec] / entry - 1) * 100, peak_dec=(h[i0:dec + 1].max() / entry - 1) * 100, days=dec - i0,
        c_vs_ldhigh=(c[dec] / ld_high - 1) * 100,
        vol_trend=v[last5].mean() / max(v[max(0, i0 - 20):i0].mean(), 1.0),
        range5=(h[last5].max() / l[last5].min() - 1) * 100,
        up_share=float((np.diff(c[i0:dec + 1]) > 0).mean()) if dec > i0 else np.nan,
        mae=(l[i0:dec + 1].min() / entry - 1) * 100,
    )
    date = pd.Timestamp(df["DATE"].iat[dec])
    if date >= oa.NIFTY_START:
        k = nf.index.searchsorted(date, side="right") - 1
        f.update(n_above200=float(nf.iloc[k]["n_above200"]), n_ret63=float(nf.iloc[k]["n_ret63"]), n_dd252=float(nf.iloc[k]["n_dd252"]))
    else:
        f.update(n_above200=np.nan, n_ret63=np.nan, n_dd252=np.nan)
    return f


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
    nifty = pd.read_csv(oa.NIFTY, parse_dates=["Date"]).set_index("Date")["Close"]
    nf = oa.nifty_features(nifty)
    eras = {"A 2006-07..2022-05": mh.subset(p_lm, p_cm, lo=pd.Timestamp("2006-07-01"), hi=mh.ATLAS_START),
            "B 2022-05..2026-11": mh.subset(a_lm, a_cm, lo=mh.ATLAS_START)}
    D = {}
    for era, (lm, cm) in eras.items():
        base, _ = ih.build_base(cm, lm, p)
        base["x1_pnl"] = lab.rerun_exits(base, cm, px1)
        base["gain"] = base.x1_pnl - base.pnl_pct
        base["entry_ts"] = pd.to_datetime(base.entry_date)
        base["exit_ts"] = pd.to_datetime(base.exit_date)
        base["time_stop"] = base.exit_reason.astype(str).str.contains("Time Stop|Volume Exhaustion", regex=True)
        D[era] = (base, lm, cm)
        print(f"{era}: {len(base)} trades, time-stop exits {int(base.time_stop.sum())} ({100 * base.time_stop.mean():.0f}%), mean live net {base.net.mean():+.2f}%")

    # ------------------------------------------------------------------ A
    print("\n=== A. CONDITIONAL TIME STOP: what is known at the decision day among time-stopped trades, versus the X1 gain ===")
    A = {}
    for era, (b, lm, cm) in D.items():
        rows = []
        for r in b[b.time_stop].itertuples():
            f = decision_features(r, cm[r.symbol], lm, nf)
            if f is not None:
                f["gain"], f["symbol"] = r.gain, r.symbol
                rows.append(f)
        A[era] = pd.DataFrame(rows)
        g = A[era].gain
        print(f"  {era}: {len(g)} time-stopped trades; mean X1 gain {g.mean():+.2f}pp, share rescued (gain > +5pp) {100 * (g > 5).mean():.0f}%, share hurt (gain < -2pp) {100 * (g < -2).mean():.0f}%, median {g.median():+.2f}pp")
    feats = ["pnl_dec", "peak_dec", "days", "c_vs_ldhigh", "vol_trend", "range5", "up_share", "mae", "n_above200", "n_ret63", "n_dd252"]
    ks = list(A)
    print(f"  {'feature':<12} {'rho A':>7} {'rho B':>7}  candidate")
    candA = []
    for ft in feats:
        r1, r2 = spearman(A[ks[0]][ft], A[ks[0]].gain), spearman(A[ks[1]][ft], A[ks[1]].gain)
        flag = "CANDIDATE" if (not np.isnan(r1) and not np.isnan(r2) and np.sign(r1) == np.sign(r2) and abs(r1) >= 0.20 and abs(r2) >= 0.20) else ""
        if flag:
            candA.append(ft)
        print(f"  {ft:<12} {r1:+7.2f} {r2:+7.2f}  {flag}")
    print(f"  candidates: {candA if candA else 'NONE'}")

    # ------------------------------------------------------------------ B
    print("\n=== B. COHORT / SERIAL STATE at the order date versus the trade outcome (net, X1 net) ===")
    B = {}
    for era, (b, lm, cm) in D.items():
        cohort, all_ld, sent_df = lab.build_cohort(cm, lm)
        rows = []
        bs = b.sort_values("entry_ts")
        for r in bs.itertuples():
            st = lab.cohort_at(cohort, all_ld, sent_df, pd.Timestamp(r.entry_ts))
            prior = bs[bs.exit_ts < r.entry_ts]
            st["prev10_net"] = prior.net.tail(10).mean() if len(prior) >= 5 else np.nan
            st["outliers90"] = int(((prior.net >= 25) & (prior.exit_ts >= r.entry_ts - pd.Timedelta(days=90))).sum())
            st["signals90"] = int(((bs.entry_ts < r.entry_ts) & (bs.entry_ts >= r.entry_ts - pd.Timedelta(days=90))).sum())
            st["net"], st["x1net"] = r.net, r.x1_pnl - lab.COST
            rows.append(st)
        B[era] = pd.DataFrame(rows)
    bfeats = ["supply30", "n_active", "frac_above", "med_ret20", "ld_sent60", "prev10_net", "outliers90", "signals90"]
    print(f"  {'feature':<12} {'rho A net':>10} {'rho B net':>10} {'rho A X1':>9} {'rho B X1':>9}  candidate")
    for ft in bfeats:
        v = [spearman(B[k][ft], B[k][t]) for t in ("net", "x1net") for k in ks]
        v = [v[0], v[1], v[2], v[3]]
        flag = ""
        for lab_, (u, w) in (("net", v[0:2]), ("X1", v[2:4])):
            if not np.isnan(u) and not np.isnan(w) and np.sign(u) == np.sign(w) and abs(u) >= 0.20 and abs(w) >= 0.20:
                flag += f" {lab_}"
        print(f"  {ft:<12} {v[0]:+10.2f} {v[1]:+10.2f} {v[2]:+9.2f} {v[3]:+9.2f}  {('CANDIDATE' + flag) if flag else ''}")

    # ------------------------------------------------------------------ C
    print("\n=== C. OUTLIER ANATOMY (descriptive): outliers = live net >= +25% versus all other trades ===")
    for era, (b, lm, cm) in D.items():
        rows = []
        for r in b.itertuples():
            df = cm[r.symbol]
            i0 = int(r.entry_idx)
            c, h, l = df["CLOSE"].values.astype(float), df["HIGH"].values.astype(float), df["LOW"].values.astype(float)
            entry = float(r.entry_price)
            ex = int(r.exit_idx) if not pd.isna(r.exit_idx) else len(c) - 1
            seg_h = h[i0:ex + 1]
            pk = int(np.argmax(seg_h))
            rows.append(dict(
                age=r.days_since_listing, ext=r.ext_pct, days_to_peak=pk, peak=(seg_h.max() / entry - 1) * 100,
                mae_before_peak=(l[i0:i0 + pk + 1].min() / entry - 1) * 100,
                ft3=(c[min(i0 + 3, len(c) - 1)] / entry - 1) * 100, ft5=(c[min(i0 + 5, len(c) - 1)] / entry - 1) * 100,
                ft10=(c[min(i0 + 10, len(c) - 1)] / entry - 1) * 100, held=ex - i0))
        X = pd.DataFrame(rows, index=b.index)
        X["outlier"] = (b.net >= 25).values
        X["repeat"] = b.groupby("symbol").cumcount().values > 0
        print(f"  {era}: outliers {int(X.outlier.sum())} of {len(X)}")
        t = X.groupby("outlier").agg(age=("age", "median"), ext=("ext", "median"), held=("held", "median"), days_to_peak=("days_to_peak", "median"),
                                      mae_before_peak=("mae_before_peak", "median"), ft3=("ft3", "median"), ft5=("ft5", "median"), ft10=("ft10", "median"),
                                      repeat=("repeat", "mean"))
        print(t.round(2).to_string().replace("\n", "\n    "))
        o = X[X.outlier]
        print(f"    outliers: share that dipped below -5% before the run {100 * (o.mae_before_peak < -5).mean():.0f}%, below -8% {100 * (o.mae_before_peak < -8).mean():.0f}%; "
              f"share up at session 5 {100 * (o.ft5 > 0).mean():.0f}% (others {100 * (X[~X.outlier].ft5 > 0).mean():.0f}%); "
              f"entered at age <= 15 d {100 * (o.age <= 15).mean():.0f}% (others {100 * (X[~X.outlier].age <= 15).mean():.0f}%)")


if __name__ == "__main__":
    main()
