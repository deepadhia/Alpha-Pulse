# -*- coding: utf-8 -*-
"""
research_ipo_meta_signal.py -- do issue size, subscription multiples and listing gain carry a quant signal for explosive post-listing moves?

PRE-REGISTRATION (written before the data was joined, 2026-10-17; owner: if subscription and issue size matter, we can think of integrating them)
-----------------------------------------------------------------------------------------------------------------------------------------------
Data: scraped Trendlyne monthly IPO pages (fetch_ipo_meta.py): issue size, issue price, QIB / HNI / retail / total subscription multiples, listing
open and close, listing gain.  Matched to database symbols by listing date (+-2 days) and normalised company-name similarity (>= 0.80).  Unmatched IPOs
are dropped and reported.  Subscription multiples are final figures known at the close of the IPO, i.e. BEFORE listing: no look-ahead.
Features (fixed in advance): log issue size, log issue price, log total subscription, log QIB, log HNI, log retail, listing gain versus the issue price
(listing-day close / issue price - 1; known at the listing-day close).
Tests (each feature, two-sided AUC, 90% bootstrap CI over IPOs, AUC by listing year):
  A  IPO level: WIN30 (high within 60 days >= +30% above the listing-day close) vs the rest   (population of research_winner_anatomy.py)
  B  checkpoint session 20: +30% high within the next 30 sessions vs not (same population)
  C  event level: policy-P trades, GOOD (net >= 20% or peak >= 25%) vs the rest, plus mean net by tercile of each feature
Bar to call a feature a quant signal: AUC >= 0.60 or <= 0.40 with a 90% CI that excludes 0.50, the same direction in at least 3 of the 4 listing years
with 25+ IPOs, AND a consistent direction in test C.  Seven features x three tests: one false positive is expected by chance, so a single borderline pass
is NOT a signal.  Only a pass is followed by a walk-forward model with the new features (not run here).

  .venv\\Scripts\\python.exe research_ipo_meta_signal.py --cache data.pkl
"""
import argparse
import difflib
import pickle
import re

import numpy as np
import pandas as pd

from research_winner_anatomy import auc, auc_ci, build

STOP = {"ltd", "limited", "pvt", "private", "the", "co", "company", "corp", "corporation", "inc"}


def norm(s):
    s = re.sub(r"[^a-z0-9 ]", " ", str(s).lower().replace("&", " and "))
    return " ".join(w for w in s.split() if w not in STOP)


def num(s):
    if s is None or (isinstance(s, float) and np.isnan(s)):
        return np.nan
    m = re.search(r"-?\d[\d,]*\.?\d*", str(s).replace("₹", " "))
    return float(m.group(0).replace(",", "")) if m else np.nan


def parse_date(s):
    m = re.match(r"(\d{1,2}) (\w{3}) `?(\d{2})", str(s).strip())
    return pd.Timestamp(f"{m.group(1)} {m.group(2)} 20{m.group(3)}") if m else pd.NaT


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--meta", default="data_ipo_meta/ipo_meta_raw.csv")
    ap.add_argument("--out", default="reports/2026-10-17/ipo_meta_matched.csv")
    a = ap.parse_args()
    import sys
    sys.path.insert(0, ".")
    from db import ipos_col
    names = {d["symbol"]: d.get("name", "") for d in ipos_col.find({}, {"_id": 0, "symbol": 1, "name": 1})}
    with open(a.cache, "rb") as fh:
        listing_map, candle_map = pickle.load(fh)
    M = pd.read_csv(a.meta)
    M["ldate"] = M.listing_date.map(parse_date)
    for c, src in (("issue_size_cr", "issue_size"), ("issue_price_n", "issue_price"), ("qib_x", "qib"), ("hni_x", "hni"), ("retail_x", "retail"), ("total_x", "total_sub")):
        M[c] = M[src].map(num)
    M["nname"] = M.company.map(norm)
    print(f"scraped rows {len(M)}; with listing date {int(M.ldate.notna().sum())}; with total subscription {int(M.total_x.notna().sum())}; with issue size {int(M.issue_size_cr.notna().sum())}")

    A, B = build(listing_map, candle_map)
    ld_by = {s: pd.Timestamp(v["listing_date"]).tz_localize(None).normalize() for s, v in listing_map.items()}
    matched, unmatched = [], []
    used = set()
    M["list_close_n"] = M.list_close.map(num)
    p0_db = {s: float(candle_map[s].CLOSE.iloc[0]) for s in A.symbol}
    for s in A.symbol:
        cand = M[(M.ldate - ld_by[s]).abs() <= pd.Timedelta(days=2)]
        best, bs = None, 0.0
        for i, r in cand.iterrows():
            if i in used:
                continue
            sc = difflib.SequenceMatcher(None, norm(names.get(s, s)), r.nname).ratio()
            # the listing-day close is an independent key (NSE vs BSE closes agree within ~1%): a price match with any name evidence is accepted
            if r.list_close_n and p0_db[s] and abs(r.list_close_n / p0_db[s] - 1) <= 0.01:
                sc = max(sc, 0.80 + 0.2 * sc) if sc >= 0.30 else (0.80 if sc >= 0.0 and len(cand) <= 3 else sc)
            if sc > bs:
                best, bs = i, sc
        if best is not None and bs >= 0.80:
            used.add(best)
            matched.append((s, best, bs))
        else:
            unmatched.append((s, names.get(s, ""), round(bs, 2)))
    print(f"eligible IPOs {len(A)}; matched {len(matched)} ({100 * len(matched) / len(A):.0f}%); unmatched {len(unmatched)}: {unmatched[:12]}")
    X = pd.DataFrame([dict(symbol=s, **M.loc[i, ["issue_size_cr", "issue_price_n", "qib_x", "hni_x", "retail_x", "total_x", "list_gain"]].to_dict()) for s, i, _ in matched])
    X["list_gain_pct"] = X.list_gain.map(num)
    A2 = A.merge(X, on="symbol")
    A2["log_size"] = np.log(A2.issue_size_cr)
    A2["log_price"] = np.log(A2.issue_price_n)
    for c, src in (("log_total_sub", "total_x"), ("log_qib", "qib_x"), ("log_hni", "hni_x"), ("log_retail", "retail_x")):
        A2[c] = np.log(A2[src].where(A2[src] > 0))
    P0 = {s: float(candle_map[s].CLOSE.iloc[0]) if len(candle_map[s]) else np.nan for s in A2.symbol}
    A2["close_vs_issue"] = [(P0[s] / p - 1) * 100 if p else np.nan for s, p in zip(A2.symbol, A2.issue_price_n)]
    A2.to_csv(a.out, index=False)
    feats = ["log_size", "log_price", "log_total_sub", "log_qib", "log_hni", "log_retail", "close_vs_issue"]
    print("coverage of features among matched:", {f: int(A2[f].notna().sum()) for f in feats})
    pd.set_option("display.width", 230)

    def table(df, ycol, title):
        print("\n" + "=" * 100 + f"\n{title}: n={len(df)}, positives {int(df[ycol].sum())} ({100 * df[ycol].mean():.1f}%)\n" + "=" * 100)
        rows = []
        for f in feats:
            d = df.dropna(subset=[f])
            if len(d) < 40:
                continue
            lo, hi = auc_ci(d[ycol].values, d[f].values, n=500)
            yr = {int(y): round(auc(g[ycol].values, g[f].values), 2) for y, g in d.groupby("year") if len(g) >= 25 and g[ycol].sum() >= 4}
            q = pd.qcut(d[f].rank(method="first"), 3, labels=["low", "mid", "high"])
            rate = {k: round(100 * float(d[ycol][q == k].mean()), 1) for k in ("low", "mid", "high")}
            rows.append(dict(feature=f, n=len(d), median_pos=round(d[f][d[ycol]].median(), 2), median_rest=round(d[f][~d[ycol]].median(), 2),
                             AUC=round(auc(d[ycol].values, d[f].values), 3), ci=f"{lo:.2f}..{hi:.2f}", hit_by_tercile=rate, by_year=yr))
        print(pd.DataFrame(rows).to_string(index=False))

    table(A2, "win30", "TEST A  IPO level, WIN30 vs rest")
    Bm = B.merge(A2[["symbol"] + feats], on="symbol")
    table(Bm[Bm.k == 20].rename(columns={"target": "t20"}).assign(t20=lambda d: d.t20.astype(bool)), "t20", "TEST B  checkpoint session 20, +30% in next 30 sessions")
    table(Bm[Bm.k == 10].rename(columns={"target": "t10"}).assign(t10=lambda d: d.t10.astype(bool)), "t10", "TEST B2 checkpoint session 10, +30% in next 30 sessions")

    # test C: policy-P trades
    T = pd.read_csv("reports/2026-10-17/gate_replay_V0.csv", parse_dates=["entry_date"])
    T["good"] = (T.net >= 20) | (T.max_runup_pct >= 25)
    T["year"] = T.entry_date.dt.year
    TC = T.merge(A2[["symbol"] + feats], on="symbol")
    print("\n" + "=" * 100 + f"\nTEST C  policy-P trades: n={len(TC)} of {len(T)} (matched), GOOD {int(TC.good.sum())}\n" + "=" * 100)
    rows = []
    for f in feats:
        d = TC.dropna(subset=[f])
        if len(d) < 40:
            continue
        lo, hi = auc_ci(d.good.values, d[f].values, n=500)
        q = pd.qcut(d[f].rank(method="first"), 3, labels=["low", "mid", "high"])
        rows.append(dict(feature=f, n=len(d), AUC=round(auc(d.good.values, d[f].values), 3), ci=f"{lo:.2f}..{hi:.2f}",
                         mean_net_by_tercile={k: round(float(d.net[q == k].mean()), 1) for k in ("low", "mid", "high")}))
    print(pd.DataFrame(rows).to_string(index=False))


if __name__ == "__main__":
    main()
