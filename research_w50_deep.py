# -*- coding: utf-8 -*-
"""
research_w50_deep.py -- a deeper look at the IPOs that rose 50%+ after listing: how they moved, what is common at listing, and what our setups held.

EXPLORATORY (written 2026-10-22; owner: "check more deeply ones that move 50% or more post listing").  Nothing here changes a rule; a feature is only
called a marker if it meets the repo bar used in research_winner_anatomy.py / research_big_movers.py: AUC outside 0.40..0.60, 90% IPO-bootstrap CI
excluding 0.50, same direction in at least 3 listing years with 25+ IPOs.  About 30 features are tested, so one or two chance passes are expected;
the year rule is the guard, and anything that passes is a hypothesis for pre-registration, not a finding.
Universe: clean_universe (real listing date, candles start at listing), listed 2022-06-01..2026-06-15, price >= Rs 25, median turnover of sessions
1-10 >= Rs 0.75 Cr.  Reference = listing-day close P0.  W50 = highest HIGH within 120 calendar days >= P0 x 1.5 (W100 likewise, descriptive only).
Information new to this study: (a) market capitalisation at listing (shares implied by the stored market cap and the price on its snapshot date,
times P0), (b) issue size / issue price / listing gain over issue (Trendlyne scrape, 298 matched), (c) listing-day behaviour from 1-minute bars
(first 15-minute move, time of the day high, close versus VWAP, last-hour move, share of volume in the first 15 minutes), (d) the first 10
sessions in daily bars.  Parts: 1 how the W50 names moved (timing, depth of the dip before the move); 2 markers; 3 what policy P and S3-X1 held.

  .venv\\Scripts\\python.exe research_w50_deep.py --cache data.pkl --out-dir reports/2026-10-22
"""
import argparse
import os
import pickle
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, ".")
from core.setup2 import clean_universe

PLACE = {pd.Timestamp("2026-08-17"), pd.Timestamp("2026-08-18")}


def auc(y, x):
    y = np.asarray(y, bool)
    x = np.asarray(x, float)
    ok = ~np.isnan(x)
    y, x = y[ok], x[ok]
    n1, n0 = y.sum(), (~y).sum()
    if n1 < 5 or n0 < 5:
        return np.nan
    r = pd.Series(x).rank().values
    return float((r[y].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def auc_ci(y, x, n=500, seed=0):
    y = np.asarray(y, bool)
    x = np.asarray(x, float)
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n):
        i = rng.integers(0, len(y), len(y))
        a = auc(y[i], x[i])
        if a == a:
            out.append(a)
    return (float(np.percentile(out, 5)), float(np.percentile(out, 95))) if out else (np.nan, np.nan)


def minute_feats(sym, ld):
    f = os.path.join("data_minute", f"{sym}_1m.pkl")
    if not os.path.exists(f):
        return {}
    m = pd.read_pickle(f)
    m = m[m.ts.dt.tz_localize(None).dt.normalize() == ld] if m.ts.dt.tz is not None else m[m.ts.dt.normalize() == ld]
    if len(m) < 150:
        return {}
    o, h, l, c, v = (m[k].values.astype(float) for k in ("open", "high", "low", "close", "volume"))
    tp = (h + l + c) / 3
    vwap = (tp * v).sum() / max(v.sum(), 1)
    n = len(m)
    return dict(m_open15=(c[14] / o[0] - 1) * 100, m_tod_high=float(np.argmax(h)) / n, m_close_vs_vwap=(c[-1] / vwap - 1) * 100,
                m_last60=(c[-1] / c[-61] - 1) * 100, m_vol15=v[:15].sum() / max(v.sum(), 1), m_hi_to_close=(c[-1] / h.max() - 1) * 100,
                m_open_to_vwap=(o[0] / vwap - 1) * 100, m_range=(h.max() / l.min() - 1) * 100)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--out-dir", default=None)
    a = ap.parse_args()
    buf = []

    def out(*x):
        s = " ".join(str(i) for i in x)
        print(s)
        buf.append(s)

    L, C = pickle.load(open(a.cache, "rb"))
    clean, _ = clean_universe(L, C)
    meta = pd.read_csv("reports/2026-10-17/ipo_meta_matched.csv")[["symbol", "issue_size_cr", "issue_price_n", "total_x", "list_gain_pct", "close_vs_issue"]]
    from db import listing_data_col
    mc = {d["symbol"]: d for d in listing_data_col.find({}, {"_id": 0, "symbol": 1, "market_cap_cr": 1, "last_updated": 1, "updated_at": 1})}
    rows = []
    for s, (df, li) in clean.items():
        ld = df.DATE.iloc[0].normalize()
        if ld < pd.Timestamp("2022-06-01") or ld > pd.Timestamp("2026-06-15") or ld in PLACE:
            continue
        o, h, l, c, v = (df[k].values.astype(float) for k in ("OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"))
        if c[0] < 25 or len(df) < 40 or np.median(c[1:11] * v[1:11]) / 1e7 < 0.75:
            continue
        days = (df.DATE - ld).dt.days.values
        w = (days > 0) & (days <= 120)
        if w.sum() < 60:
            continue
        P0, LH = c[0], h[0]
        mx = h[w].max()
        cross = np.where(w & (h >= 1.5 * P0))[0]
        k50 = int(cross[0]) if len(cross) else None
        pre_low = l[1:k50 + 1].min() if k50 else l[w].min()
        r = dict(symbol=s, year=ld.year, ld=ld, w50=mx >= 1.5 * P0, w100=mx >= 2.0 * P0, max_gain=(mx / P0 - 1) * 100,
                 days_to50=int(days[k50]) if k50 else np.nan, sess_to50=k50 if k50 else np.nan,
                 pre_dip=(pre_low / P0 - 1) * 100, pre_dip_vs_lh=(pre_low / LH - 1) * 100,
                 closed_above_lh_before=bool((c[1:k50 + 1] > LH).any()) if k50 else False,
                 # listing-day / early daily features
                 d1_range=(LH - l[0]) / LH * 100, d1_closeloc=(P0 - l[0]) / (LH - l[0]) if LH > l[0] else np.nan, d1_open_to_close=(P0 / o[0] - 1) * 100,
                 d1_turn_cr=P0 * v[0] / 1e7, ret5=(c[5] / P0 - 1) * 100, ret10=(c[10] / P0 - 1) * 100,
                 held_low=float(l[1:11].min() >= l[0]), above_p0_share=float((c[1:11] > P0).mean()), vol_decay=v[2:6].mean() / max(v[0], 1),
                 turn_1_10=np.median(c[1:11] * v[1:11]) / 1e7, price=P0)
        d = mc.get(s)
        if d and d.get("market_cap_cr"):
            snap = pd.Timestamp(d.get("last_updated") or d.get("updated_at")).tz_localize(None).normalize() if d.get("last_updated") or d.get("updated_at") else None
            if snap is not None:
                j = np.searchsorted(df.DATE.values, np.datetime64(snap), side="right") - 1
                if j >= 0:
                    shares_cr = float(d["market_cap_cr"]) / c[j]
                    r["mcap_cr"] = shares_cr * P0
                    r["d1_turn_over_mcap"] = r["d1_turn_cr"] / r["mcap_cr"] * 100
        r.update(minute_feats(s, ld))
        rows.append(r)
    D = pd.DataFrame(rows).merge(meta, on="symbol", how="left")
    D["log_mcap"] = np.log(D.mcap_cr)
    D["log_issue"] = np.log(D.issue_size_cr)
    D["log_total_sub"] = np.log(D.total_x.where(D.total_x > 0))
    out(f"research_w50_deep.py  eligible {len(D)}; W50 {int(D.w50.sum())} ({100 * D.w50.mean():.1f}%); W100 {int(D.w100.sum())} ({100 * D.w100.mean():.1f}%); minute features {int(D.m_open15.notna().sum())}; market cap {int(D.mcap_cr.notna().sum())}; meta {int(D.issue_size_cr.notna().sum())}")
    W = D[D.w50]

    out("\n" + "=" * 110 + "\n1. HOW THE W50 NAMES MOVED\n" + "=" * 110)
    out("  W50 rate by listing year:", {int(y): f"{100 * g.w50.mean():.0f}% of {len(g)}" for y, g in D.groupby("year")})
    t = W.days_to50
    out(f"  calendar days to reach +50%: median {t.median():.0f}; <=14d {100 * (t <= 14).mean():.0f}%, 15-30d {100 * ((t > 14) & (t <= 30)).mean():.0f}%, 31-60d {100 * ((t > 30) & (t <= 60)).mean():.0f}%, 61-120d {100 * (t > 60).mean():.0f}%")
    out(f"  dip BEFORE reaching +50% (lowest low after listing vs the listing close): median {W.pre_dip.median():+.1f}%; deeper than -5%: {100 * (W.pre_dip < -5).mean():.0f}%, -10%: {100 * (W.pre_dip < -10).mean():.0f}%, -15%: {100 * (W.pre_dip < -15).mean():.0f}%, -25%: {100 * (W.pre_dip < -25).mean():.0f}%")
    out(f"  dip vs the LISTING HIGH (what a listing-high buyer sees): median {W.pre_dip_vs_lh.median():+.1f}%; deeper than -12% (the stop cap): {100 * (W.pre_dip_vs_lh < -12).mean():.0f}%")
    out(f"  closed above the listing-day high at least once before reaching +50%: {100 * W.closed_above_lh_before.mean():.0f}%")
    for lab, g in (("FAST (<= 30 d)", W[W.days_to50 <= 30]), ("MEDIUM (31-60 d)", W[(W.days_to50 > 30) & (W.days_to50 <= 60)]), ("SLOW (61-120 d)", W[W.days_to50 > 60])):
        out(f"  {lab:<17} N={len(g):3d} max gain median {g.max_gain.median():+.0f}%  pre-dip median {g.pre_dip.median():+.1f}%  share dipping below -10%: {100 * (g.pre_dip < -10).mean():.0f}%  W100: {int(g.w100.sum())}")

    out("\n" + "=" * 110 + "\n2. MARKERS: W50 vs ALL other eligible IPOs (AUC, 90% bootstrap CI, by listing year with 25+ IPOs)\n" + "=" * 110)
    feats = ["price", "log_mcap", "log_issue", "issue_price_n", "log_total_sub", "list_gain_pct", "close_vs_issue", "d1_turn_cr", "d1_turn_over_mcap", "turn_1_10",
             "d1_range", "d1_closeloc", "d1_open_to_close", "ret5", "ret10", "held_low", "above_p0_share", "vol_decay",
             "m_open15", "m_tod_high", "m_close_vs_vwap", "m_last60", "m_vol15", "m_hi_to_close", "m_open_to_vwap", "m_range"]
    res = []
    hits = []
    for f in feats:
        d = D.dropna(subset=[f])
        if len(d) < 60 or d.w50.sum() < 15:
            continue
        a_ = auc(d.w50.values, d[f].values)
        lo, hi = auc_ci(d.w50.values, d[f].values)
        yr = {int(y): round(auc(g.w50.values, g[f].values), 2) for y, g in d.groupby("year") if len(g) >= 25 and g.w50.sum() >= 4}
        sgn = np.sign(a_ - 0.5)
        same = sum(1 for v in yr.values() if v == v and np.sign(v - 0.5) == sgn)
        q = pd.qcut(d[f].rank(method="first"), 3, labels=False)
        rate = [round(100 * float(d.w50[q == k].mean())) for k in range(3)]
        passed = (a_ < 0.40 or a_ > 0.60) and (hi < 0.5 or lo > 0.5) and same >= 3
        if passed:
            hits.append(f)
        res.append((f, len(d), a_, lo, hi, rate, yr, same, passed, d[f][d.w50].median(), d[f][~d.w50].median()))
    for f, n, a_, lo, hi, rate, yr, same, passed, mw, mr in res:
        out(f"  {f:<18} n={n:3d} AUC {a_:.2f} [{lo:.2f},{hi:.2f}] hit% by tercile low/mid/high {rate} medians W50 {mw:.2f} vs rest {mr:.2f} years {yr} same-sign {same} {'<== MEETS BAR' if passed else ''}")
    out(f"  features meeting the bar: {hits if hits else 'none'}  (of {len(res)} tested; about {0.1 * len(res):.0f} would pass by chance at the CI alone)")

    out("\n" + "=" * 110 + "\n3. WHAT POLICY P AND S3-X1 HELD ON THE W50 NAMES (first 120 days)\n" + "=" * 110)
    import logging
    logging.disable(logging.CRITICAL)
    from core.honest_backtest import Params
    from core.setup3 import Setup3Params, simulate_symbol_setup3
    from research_setup2 import CFG, run_p, to_df
    from research_setup3 import NO_TIME_EXITS
    P = to_df(run_p(L, C))
    S3 = []
    for s, (df, li) in clean.items():
        S3.extend(simulate_symbol_setup3(df, s, li, Params(exit_overrides=NO_TIME_EXITS), Setup3Params(), CFG, slip_pct=0.3, include_open=True))
    S3 = to_df(S3)
    cat = {"held_P": 0, "S3_only": 0}
    reasons = []
    for r in W.itertuples():
        df = clean[r.symbol][0]
        ld = r.ld
        cut = ld + pd.Timedelta(days=int(r.days_to50)) if r.days_to50 == r.days_to50 else ld + pd.Timedelta(days=120)
        p = P[(P.symbol == r.symbol) & (P.entry_date <= cut)]
        s3 = S3[(S3.symbol == r.symbol) & (S3.entry_date <= cut)] if len(S3) else S3
        o_, h_, c_ = (df[k].values.astype(float) for k in ("OPEN", "HIGH", "CLOSE"))
        days = (df.DATE - ld).dt.days.values
        LH = h_[0]
        cr = np.where((days >= 5) & (days <= 60) & (h_ >= LH * 1.015))[0]
        if len(p):
            why = "held by P before +50%"
        elif len(s3):
            why = "held by S3-X1 only"
        elif len(cr) == 0:
            why = "never crossed listing high +1.5% in age 5-60 d (and no S3 entry)"
        elif (o_[cr[0]] > LH * 1.025):
            why = "first cross gapped above the +2.5% limit (no fill)"
        else:
            why = "crossed, no P trade (gate, cooldown or exit before)"
        reasons.append(why)
    W = W.assign(why=reasons)
    out("  W50 names:", len(W), "|", {k: int(v) for k, v in W.why.value_counts().items()})
    W100 = W[W.w100]
    out("  W100 names:", len(W100), "|", {k: int(v) for k, v in W100.why.value_counts().items()})
    # money kept
    tot_av, tot_kept = [], []
    for r in W.itertuples():
        for nm, T in (("P", P), ("S3", S3)):
            g = T[(T.symbol == r.symbol)]
            if len(g):
                tot_av.append((nm, r.symbol, g.pnl_pct.max(), r.max_gain))
    k = pd.DataFrame(tot_av, columns=["setup", "symbol", "best_trade_pnl", "available"])
    for nm, g in k.groupby("setup"):
        out(f"  {nm}: trades on W50 names {len(g)} symbols; mean of best trade per symbol {g.best_trade_pnl.mean():+.1f}% vs mean available max gain from listing close {g.available.mean():+.0f}%")
    out("  W50 names with NO trade from either setup at any time:", int((~W.symbol.isin(set(P.symbol) | set(S3.symbol))).sum()))
    out("  largest misses (max gain, days to +50%, reason):")
    for r in W[W.why.str.startswith(("never", "first", "crossed"))].sort_values("max_gain", ascending=False).head(15).itertuples():
        out(f"    {r.symbol:<11} {r.max_gain:+6.0f}%  {r.days_to50:3.0f} d  pre-dip {r.pre_dip:+5.1f}%  {r.why}")
    if a.out_dir:
        os.makedirs(a.out_dir, exist_ok=True)
        D.to_csv(os.path.join(a.out_dir, "w50_deep_table.csv"), index=False)
        with open(os.path.join(a.out_dir, "w50_deep_output.txt"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(buf))


if __name__ == "__main__":
    main()
