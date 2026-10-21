# -*- coding: utf-8 -*-
"""
research_big_movers.py -- what is common to IPOs that rose 50% to 100%+ within 3-4 months of listing?

PRE-REGISTRATION (written before running, 2026-10-17; owner: moves of 50-100% in 3-4 months post listing, maybe subscription or something is common)
----------------------------------------------------------------------------------------------------------------------------------------------------
Population: the eligible IPOs of research_winner_anatomy.py (listed 2022-06-01 .. 2026-06-15 so 120 calendar days of data exist; price >= 25; median turnover
of sessions 1-10 >= Rs 0.75 Cr; real listing date).  Reference price = the listing-day close.
Winners: W50 = highest high within 120 calendar days >= +50% above the reference; W100 = >= +100%.  Always compared with ALL other eligible IPOs.
Tests (single feature, two-sided AUC, 90% bootstrap CI over IPOs, AUC by listing year, hit rate by tercile):
  A  listing-time features: listing-day range, close location, turnover, price, IPOs listed in the prior 30 days, and (matched subset, from the scraped data)
     log issue size, log issue price, log total / QIB / HNI / retail subscription, listing-day close versus issue price.
  B  checkpoint features at session k in 20, 40, 60: return since listing, share of closes above the listing high, drawdown from the running high, range of the
     last 10 sessions, volume trend (mean volume of the last 10 sessions / sessions 1-10), cohort-relative return (return minus the median return of IPOs
     listed in the same month at the same session).  Target: the next 60 sessions reach >= +50% above the close of session k.
  C  regime: W50 rate by listing quarter (hot cohorts?).
  D  engine capture: for W50 / W100 IPOs, how many had a policy-P trade in the first 120 days and what those trades earned versus the available move.
Bar for a signal (same as before): AUC outside 0.40..0.60 with a 90% CI excluding 0.50 and the same direction in 3 of the years with 25+ IPOs.
W100 will have few names: it is described, never used for a pass/fail.

  .venv\\Scripts\\python.exe research_big_movers.py --cache data.pkl
"""
import argparse
import pickle

import numpy as np
import pandas as pd

from research_winner_anatomy import auc, auc_ci

PLACEHOLDER = {pd.Timestamp("2026-08-17"), pd.Timestamp("2026-08-18")}


def build(listing_map, candle_map):
    S = {}
    for s, df in candle_map.items():
        li = listing_map.get(s)
        if li is None or len(df) < 40:
            continue
        ld = pd.Timestamp(li["listing_date"]).tz_localize(None).normalize()
        if ld < pd.Timestamp("2022-06-01") or ld > pd.Timestamp("2026-06-15") or ld in PLACEHOLDER:
            continue
        d = df.copy()
        d["DATE"] = pd.to_datetime(d.DATE).dt.tz_localize(None)
        d = d.sort_values("DATE").reset_index(drop=True)
        if abs((d.DATE.iloc[0] - ld).days) > 3:
            continue
        o, h, l, c, v = (d[k].values.astype(float) for k in ("OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"))
        if c[0] < 25 or len(d) < 12 or np.median(c[1:11] * v[1:11]) / 1e7 < 0.75:
            continue
        S[s] = dict(d=d, ld=d.DATE.iloc[0], o=o, h=h, l=l, c=c, v=v)
    lds = pd.Series({s: x["ld"] for s, x in S.items()}).sort_values()
    rowsA, rowsB = [], []
    # cohort median return at session k, by listing month
    month = {s: x["ld"].strftime("%Y-%m") for s, x in S.items()}
    for s, x in S.items():
        d, ld, h, l, c, v = x["d"], x["ld"], x["h"], x["l"], x["c"], x["v"]
        days = (d.DATE - ld).dt.days.values
        in120 = (days <= 120) & (days > 0)
        if in120.sum() < 60:
            continue
        P0, LH, LL, V0 = c[0], h[0], l[0], v[0]
        mx = h[in120].max()
        first50 = np.where(in120 & (h >= 1.5 * P0))[0]
        prior = lds[(lds >= ld - pd.Timedelta(days=30)) & (lds < ld)]
        rowsA.append(dict(symbol=s, year=ld.year, ld=ld, month=month[s], quarter=f"{ld.year}Q{(ld.month - 1) // 3 + 1}",
                          w50=mx >= 1.5 * P0, w100=mx >= 2.0 * P0, max_gain=(mx / P0 - 1) * 100,
                          days_to50=int(days[first50[0]]) if len(first50) else None,
                          ld_range=(LH - LL) / LH * 100, ld_closeloc=(P0 - LL) / (LH - LL) if LH > LL else np.nan,
                          ld_turn=V0 * P0 / 1e7, logp=np.log(P0), supply30=len(prior),
                          ret20=(c[20] / P0 - 1) * 100 if len(c) > 20 else np.nan, ret60=(c[60] / P0 - 1) * 100 if len(c) > 60 else np.nan,
                          above_lh_20=float((c[1:21] > LH).mean()) if len(c) > 21 else np.nan))
        for k in (20, 40, 60):
            if len(c) < k + 61:
                continue
            fut = h[k + 1:k + 61].max() / c[k] - 1
            vb = v[1:11]
            rowsB.append(dict(symbol=s, year=ld.year, k=k, month=month[s], target=fut >= 0.50, fwd_gain=fut * 100,
                              ret_k=(c[k] / P0 - 1) * 100, above_lh=float((c[1:k + 1] > LH).mean()),
                              dd_run=(c[k] / c[:k + 1].max() - 1) * 100,
                              range10=(h[k - 9:k + 1].max() - l[k - 9:k + 1].min()) / c[k] * 100,
                              vol_trend=v[k - 9:k + 1].mean() / vb.mean() if vb.mean() > 0 else np.nan))
    A, B = pd.DataFrame(rowsA), pd.DataFrame(rowsB)
    # cohort-relative return at the checkpoint: ret_k minus the median ret_k of IPOs listed in the same month
    B["rel_ret"] = B.ret_k - B.groupby(["month", "k"]).ret_k.transform("median")
    return A, B


def table(df, ycol, feats, title):
    print("\n" + "=" * 104 + f"\n{title}: n={len(df)}, positives {int(df[ycol].sum())} ({100 * df[ycol].mean():.1f}%)\n" + "=" * 104)
    rows = []
    for f in feats:
        d = df.dropna(subset=[f])
        if len(d) < 40 or d[ycol].sum() < 8:
            continue
        lo, hi = auc_ci(d[ycol].values, d[f].values, n=400)
        yr = {int(y): round(auc(g[ycol].values, g[f].values), 2) for y, g in d.groupby("year") if len(g) >= 25 and g[ycol].sum() >= 4}
        q = pd.qcut(d[f].rank(method="first"), 3, labels=["low", "mid", "high"])
        rows.append(dict(feature=f, n=len(d), med_pos=round(d[f][d[ycol]].median(), 2), med_rest=round(d[f][~d[ycol]].median(), 2),
                         AUC=round(auc(d[ycol].values, d[f].values), 3), ci=f"{lo:.2f}..{hi:.2f}",
                         hit_tercile={k: round(100 * float(d[ycol][q == k].mean()), 1) for k in ("low", "mid", "high")}, by_year=yr))
    pd.set_option("display.width", 240)
    print(pd.DataFrame(rows).to_string(index=False))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    a = ap.parse_args()
    with open(a.cache, "rb") as fh:
        listing_map, candle_map = pickle.load(fh)
    A, B = build(listing_map, candle_map)
    meta = pd.read_csv("reports/2026-10-17/ipo_meta_matched.csv")[["symbol", "log_size", "log_price", "log_total_sub", "log_qib", "log_hni", "log_retail", "close_vs_issue"]]
    A = A.merge(meta, on="symbol", how="left")
    print(f"eligible IPOs {len(A)}; W50 (>= +50% within 120 days) {int(A.w50.sum())} ({100 * A.w50.mean():.1f}%); W100 {int(A.w100.sum())} ({100 * A.w100.mean():.1f}%); "
          f"median days to +50% among W50: {A.days_to50[A.w50].median():.0f}; with scraped meta: {int(A.log_total_sub.notna().sum())}")
    print("W50 rate by listing year:", {int(y): (round(100 * g.w50.mean(), 1), len(g)) for y, g in A.groupby("year")})
    print("\nC. W50 rate by listing quarter (n, %):", {q: (len(g), round(100 * g.w50.mean())) for q, g in A.groupby("quarter")})
    lf = ["ld_range", "ld_closeloc", "ld_turn", "logp", "supply30", "log_size", "log_price", "log_total_sub", "log_qib", "log_hni", "log_retail", "close_vs_issue"]
    table(A, "w50", lf, "TEST A  listing-time features, W50 vs rest")
    table(A, "w100", [f for f in lf if f in ("log_size", "log_total_sub", "log_qib", "ld_range", "supply30", "close_vs_issue", "logp")], "TEST A100  listing-time features, W100 vs rest (descriptive)")
    bf = ["ret_k", "above_lh", "dd_run", "range10", "vol_trend", "rel_ret"]
    for k in (20, 40, 60):
        g = B[B.k == k]
        table(g, "target", bf, f"TEST B  checkpoint session {k}: next 60 sessions reach +50% above the close")
    # profile of the biggest movers
    print("\n" + "=" * 104 + "\nProfile of W100 IPOs (>= +100% within 120 days) vs the rest, medians\n" + "=" * 104)
    cols = ["max_gain", "days_to50", "ld_range", "ld_turn", "supply30", "ret20", "ret60", "above_lh_20", "log_total_sub", "log_size", "close_vs_issue"]
    print(A.groupby("w100")[cols].median().round(2).T.to_string())
    top = A[A.w100].sort_values("max_gain", ascending=False)
    print("\nW100 names (listing month, max gain %, return at session 20, subscription total x, issue size Cr):")
    for r in top.itertuples():
        sub = f"{np.exp(r.log_total_sub):.0f}x" if r.log_total_sub == r.log_total_sub else "n/a"
        size = f"{np.exp(r.log_size):.0f}" if r.log_size == r.log_size else "n/a"
        print(f"  {r.symbol:<12} {r.month}  +{r.max_gain:5.0f}%  ret20 {r.ret20:+6.1f}%  sub {sub:>6}  size {size:>6}")
    # D: engine capture
    T = pd.read_csv("reports/2026-10-17/gate_replay_V0.csv", parse_dates=["entry_date"])
    T["open"] = T.open.astype(bool)
    print("\n" + "=" * 104 + "\nD. Engine (policy P) capture of the big movers\n" + "=" * 104)
    for lab, mask in (("W50", A.w50), ("W100", A.w100)):
        names = set(A.symbol[mask])
        tt = T[T.symbol.isin(names)]
        avail = A[mask].set_index("symbol").max_gain
        cap = tt.assign(avail=tt.symbol.map(avail))
        print(f"{lab}: {len(names)} IPOs; with a policy-P trade {tt.symbol.nunique()} ({100 * tt.symbol.nunique() / max(len(names), 1):.0f}%); mean trade net {tt.net.mean():+.1f}%; "
              f"median available move {avail.median():.0f}% vs median realised net {tt.net.median():+.1f}%; sum of nets {tt.net.sum():+.0f}pp "
              f"= {100 * tt.net.sum() / max(T.net[T.net > 0].sum(), 1):.0f}% of all gross wins")


if __name__ == "__main__":
    main()
