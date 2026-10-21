# -*- coding: utf-8 -*-
"""
research_winner_anatomy.py -- what do IPOs that rose 30%+ after listing have in common, compared with the IPOs that did not?

PRE-REGISTRATION (written before running, 2026-10-17; owner request: find the pattern common to the explosive post-listing movers)
-----------------------------------------------------------------------------------------------------------------------------------
Winners alone cannot show a pattern: a trait is only informative if non-winners lack it, so every comparison is winners vs ALL other eligible IPOs.
Data limit: the database holds only listing-day and daily OHLCV (no issue price, subscription, GMP or fundamentals), so only price, volume and
market-state patterns can be tested.
Eligible IPO: listed 2022-06-01 .. 2026-08-15 (so 60 calendar days of data exist), real listing date (not the 2026-08-17/18 placeholder cluster),
candles start on the listing day, price >= 25, median turnover of sessions 1-10 >= Rs 0.75 Cr.

LENS A (listing-time, one row per IPO): WIN30 = the highest high within 60 calendar days after listing is >= +30% above the listing-day close.
  Also WIN30_FAST (reached within 30 calendar days) and WIN50.  Features known at the listing-day close: listing-day range, close location in the
  range, upper wick, open-to-close move, turnover, price, number of IPOs listed in the prior 30 days, median return of those recent IPOs.
LENS B (checkpoint, one row per IPO and checkpoint session k in 5, 10, 20): TARGET = the highest high in the next 30 sessions is >= +30% above the
  close of session k (session 0 = listing day).  Features from sessions 0..k: distance of the close from the listing high, deepest pullback below the
  listing high, range of the last 5 sessions, volume dry-up (mean volume of the last 3 sessions / mean of sessions 1..k-3), mean volume vs listing-day
  volume, share of closes above the listing high, return since the listing close, whether the listing-day low held, closeness to the running high.
OWNER PATTERN (fixed before running, not tuned): DRYCOIL = volume dry-up < 0.8 AND last-5-session range <= 8% AND close within -10% .. +5% of the
  listing high AND the listing-day low held.

Reported: base rates, winner-vs-rest medians, AUC of each single feature (90% bootstrap CI over IPOs), AUC by listing year for the strongest, the hit rate of
DRYCOIL vs the base rate with counts and by year.  "Always works" would need a rule whose hit rate is near 100% in every year; the report states how
far the best rule is from that.

  .venv\\Scripts\\python.exe research_winner_anatomy.py --cache data.pkl
"""
import argparse
import pickle

import numpy as np
import pandas as pd

PLACEHOLDER = {pd.Timestamp("2026-08-17"), pd.Timestamp("2026-08-18")}


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


def auc_ci(y, x, n=600, seed=0):
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


def build(listing_map, candle_map):
    S = {}
    for s, df in candle_map.items():
        li = listing_map.get(s)
        if li is None or len(df) < 25:
            continue
        ld = pd.Timestamp(li["listing_date"]).tz_localize(None).normalize()
        if ld < pd.Timestamp("2022-06-01") or ld > pd.Timestamp("2026-08-15") or ld in PLACEHOLDER:
            continue
        d = df.copy()
        d["DATE"] = pd.to_datetime(d.DATE).dt.tz_localize(None)
        d = d.sort_values("DATE").reset_index(drop=True)
        if abs((d.DATE.iloc[0] - ld).days) > 3:
            continue
        S[s] = dict(d=d, ld=d.DATE.iloc[0])
    rowsA, rowsB = [], []
    lds = pd.Series({s: x["ld"] for s, x in S.items()}).sort_values()
    p0s = {s: float(x["d"].CLOSE.iloc[0]) for s, x in S.items()}
    for s, x in S.items():
        d, ld = x["d"], x["ld"]
        o, h, l, c, v = (d[k].values.astype(float) for k in ("OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"))
        dates = d.DATE
        if c[0] < 25 or len(d) < 12:
            continue
        turn = np.median(c[1:11] * v[1:11]) / 1e7
        if turn < 0.75:
            continue
        LH, LL, P0, V0, O0 = h[0], l[0], c[0], v[0], o[0]
        in60 = (dates - ld).dt.days.values <= 60
        in60[0] = False
        if in60.sum() < 20:
            continue
        mx = h[in60].max()
        first30 = np.where(in60 & (h >= 1.30 * P0))[0]
        days_to = int((dates.iloc[first30[0]] - ld).days) if len(first30) else None
        prior = lds[(lds >= ld - pd.Timedelta(days=30)) & (lds < ld)].index
        rec_ret = []
        for q in prior:
            dq = S[q]["d"]
            m = dq[dq.DATE < ld]
            if len(m):
                rec_ret.append(float(m.CLOSE.iloc[-1]) / p0s[q] - 1)
        rowsA.append(dict(symbol=s, year=ld.year, ld=ld, win30=mx >= 1.30 * P0, win50=mx >= 1.50 * P0, fast30=(days_to is not None and days_to <= 30),
                          max_gain=(mx / P0 - 1) * 100, days_to30=days_to,
                          ld_range=(LH - LL) / LH * 100, ld_closeloc=(P0 - LL) / (LH - LL) if LH > LL else np.nan, ld_upperwick=(LH - P0) / LH * 100,
                          ld_oc=(P0 / O0 - 1) * 100, ld_turn=V0 * P0 / 1e7, logp=np.log(P0), supply30=len(prior),
                          recent_ret=float(np.median(rec_ret)) if rec_ret else np.nan, post_turn=turn))
        for k in (5, 10, 20):
            if len(d) < k + 31:
                continue
            fut = h[k + 1:k + 31].max() / c[k] - 1
            vb = v[1:k - 2]
            rowsB.append(dict(symbol=s, year=ld.year, k=k, target=fut >= 0.30, fwd_gain=fut * 100,
                              dist_lh=(c[k] / LH - 1) * 100, pullback=(l[1:k + 1].min() / LH - 1) * 100,
                              range5=(h[k - 4:k + 1].max() - l[k - 4:k + 1].min()) / c[k] * 100,
                              vdry=v[k - 2:k + 1].mean() / vb.mean() if len(vb) >= 2 and vb.mean() > 0 else np.nan,
                              vol_vs_ld=v[1:k + 1].mean() / V0, above_lh=float((c[1:k + 1] > LH).mean()),
                              ret_k=(c[k] / P0 - 1) * 100, held_ldlow=float(l[1:k + 1].min() >= LL),
                              near_high=c[k] / c[:k + 1].max()))
    return pd.DataFrame(rowsA), pd.DataFrame(rowsB)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--out-dir", default="reports/2026-10-17")
    a = ap.parse_args()
    with open(a.cache, "rb") as fh:
        listing_map, candle_map = pickle.load(fh)
    A, B = build(listing_map, candle_map)
    A.to_csv(f"{a.out_dir}/winner_anatomy_ipos.csv", index=False)
    pd.set_option("display.width", 220)
    print(f"eligible IPOs {len(A)}  WIN30 {int(A.win30.sum())} ({100 * A.win30.mean():.1f}%)  WIN50 {int(A.win50.sum())} ({100 * A.win50.mean():.1f}%)  "
          f"reached +30% within 30 days {int(A.fast30.sum())} ({100 * A.fast30.mean():.1f}%)")
    print("WIN30 rate by listing year:", {int(y): (round(100 * g.win30.mean(), 1), len(g)) for y, g in A.groupby("year")})
    print("median days to +30% among winners:", A.days_to30[A.win30].median())

    print("\n" + "=" * 100 + "\nLENS A - at the listing-day close: winners vs the rest\n" + "=" * 100)
    feats = ["ld_range", "ld_closeloc", "ld_upperwick", "ld_oc", "ld_turn", "logp", "supply30", "recent_ret", "post_turn"]
    rows = []
    for f in feats:
        lo, hi = auc_ci(A.win30.values, A[f].values)
        yr = {int(y): round(auc(g.win30.values, g[f].values), 2) for y, g in A.groupby("year") if len(g) >= 30 and g.win30.sum() >= 5}
        rows.append(dict(feature=f, median_win=round(A[f][A.win30].median(), 2), median_rest=round(A[f][~A.win30].median(), 2),
                         AUC=round(auc(A.win30.values, A[f].values), 3), ci=f"{lo:.2f}..{hi:.2f}", by_year=yr))
    print(pd.DataFrame(rows).to_string(index=False))

    print("\n" + "=" * 100 + "\nLENS B - at session k after listing: P(+30% high within the next 30 sessions)\n" + "=" * 100)
    bf = ["dist_lh", "pullback", "range5", "vdry", "vol_vs_ld", "above_lh", "ret_k", "held_ldlow", "near_high"]
    for k, g in B.groupby("k"):
        print(f"\n-- checkpoint session {k}: IPOs {len(g)}, hit rate (base) {100 * g.target.mean():.1f}%")
        rows = []
        for f in bf:
            lo, hi = auc_ci(g.target.values, g[f].values, n=300)
            yr = {int(y): round(auc(gg.target.values, gg[f].values), 2) for y, gg in g.groupby("year") if len(gg) >= 30 and gg.target.sum() >= 5}
            rows.append(dict(feature=f, median_hit=round(g[f][g.target].median(), 2), median_rest=round(g[f][~g.target].median(), 2),
                             AUC=round(auc(g.target.values, g[f].values), 3), ci=f"{lo:.2f}..{hi:.2f}", by_year=yr))
        print(pd.DataFrame(rows).to_string(index=False))
        pat = (g.vdry < 0.8) & (g.range5 <= 8) & (g.dist_lh.between(-10, 5)) & (g.held_ldlow == 1)
        n = int(pat.sum())
        print(f"   OWNER PATTERN DRYCOIL: {n} IPOs match; hit rate {100 * g.target[pat].mean() if n else float('nan'):.1f}% vs base {100 * g.target.mean():.1f}% "
              f"vs non-matching {100 * g.target[~pat].mean():.1f}%;  share of all hits that match: {100 * (pat & g.target).sum() / max(g.target.sum(), 1):.0f}%;  "
              f"by year (n, hit%): {({int(y): (int(pat[gg.index].sum()), round(100 * gg.target[pat[gg.index]].mean(), 0) if pat[gg.index].sum() else None) for y, gg in g.groupby('year')})}")
        top = g[(g.dist_lh.between(-10, 5)) & (g.held_ldlow == 1)]
        print(f"   simpler: close within -10..+5% of the listing high and listing-day low held: n={len(top)}, hit {100 * top.target.mean():.1f}%")

    # what the explosive IPOs looked like (descriptive, with the rest alongside)
    print("\nWinners vs rest at checkpoint 10 (medians):")
    g = B[B.k == 10]
    print(g.groupby("target")[bf + ["fwd_gain"]].median().round(2).T.to_string())
    print("\nShare of winners whose checkpoint-10 state was each of these (vs share of the rest):")
    for lab, m in (("close above listing high", g.dist_lh > 0), ("close within -10..0% of LH", g.dist_lh.between(-10, 0)), ("close below -10% of LH", g.dist_lh < -10),
                   ("volume drying (<0.8)", g.vdry < 0.8), ("tight 5-session range (<=8%)", g.range5 <= 8), ("listing-day low held", g.held_ldlow == 1)):
        print(f"  {lab:<32} winners {100 * m[g.target].mean():5.1f}%   rest {100 * m[~g.target].mean():5.1f}%")


if __name__ == "__main__":
    main()
