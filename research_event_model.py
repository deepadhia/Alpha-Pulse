# -*- coding: utf-8 -*-
"""
research_event_model.py -- can ANY combination of entry-time information pick the explosive breakouts out of sample?  (quality over quantity)

PRE-REGISTRATION (written before running, 2026-10-17; owner goal: take only trades that make money, missing others is fine)
--------------------------------------------------------------------------------------------------------------------------
Why this test: the earlier selection tests used 227 policy-P trades and single features.  Here the event set is every resting-order-eligible
breakout day (age 5-60, prior-day volume >= 150k, 20-day turnover >= Rs 0.75 Cr, price >= 25, open <= listing high +2.5%, intraday high >=
listing high +1.5%), at most one event per symbol per 10 sessions, which gives several times more events and so more statistical power.
Label (no exit model needed): EXPLODE = the highest high in the next 10 sessions, counting the entry day, reaches >= +20% above the fill
(trigger or open, plus 0.3%).  Secondary label: BIG = reaches >= +40%.  Also reported: the 10-session close return.
Features, all known BEFORE the entry bar (prior bars only, plus the opening gap): age, distance of the prior close from the listing high,
opening gap, 5- and 10-day return, 10-day average range, 10-day range, 3-day volume dry-up, prior-day volume / 20-day average, 20-day turnover,
listing-day range, log price, listing-day volume / 20-day average volume, number of IPOs listed in the prior 30 days, share of active IPOs
(age 3-60) closing above their own listing high on the prior day, median 10-day return of active IPOs on the prior day (market state).
Models: (M1) L2 logistic regression on standardised features; (M2) shallow gradient boosting (depth 2, 150 trees, learning rate 0.05).
Walk-forward: train on all events before the test year, predict the test year, test years 2024, 2025, 2026, pooled.
Adoption bar for a selection model: out-of-sample AUC >= 0.60 with the 90% symbol-cluster CI lower bound above 0.5, AND the top 30% of scores
has an EXPLODE rate at least 1.5x the base rate, AND the top 30% has a higher mean 10-session max-high gain than the rest with the cluster CI
of the difference excluding 0, AND the same direction in at least two of the three test years.  Otherwise: no selection model is justified.

  python research_event_model.py --cache data.pkl        (system python: needs scikit-learn)
"""
import argparse
import pickle

import numpy as np
import pandas as pd

MIN_AGE, MAX_AGE = 5, 60
# Two-step run because the venv and the system python have different pandas versions:
#   step 1 (venv):   .venv\Scripts\python.exe research_event_model.py --cache data.pkl --build-only
#   step 2 (system): python research_event_model.py --events-in reports/2026-10-17/event_model_events.csv


def prep(listing_map, candle_map):
    S = {}
    for s, df in candle_map.items():
        li = listing_map.get(s)
        if li is None or len(df) < 20:
            continue
        d = df.copy()
        d["DATE"] = pd.to_datetime(d.DATE).dt.tz_localize(None)
        d = d.sort_values("DATE").reset_index(drop=True)
        ld = pd.Timestamp(li["listing_date"]).tz_localize(None)
        ldh = float(li.get("listing_day_high") or d.HIGH.iloc[0])
        ldl = float(li.get("listing_day_low") or d.LOW.iloc[0])
        ldv = float(li.get("listing_day_volume") or d.VOLUME.iloc[0])
        S[s] = dict(d=d, ld=ld, ldh=ldh, ldl=ldl, ldv=ldv)
    # market state by date: breadth and median 10-day return of active IPOs, supply in prior 30 days
    recs = []
    for s, x in S.items():
        d = x["d"]
        age = (d.DATE - x["ld"]).dt.days.values
        c = d.CLOSE.values
        r10 = np.r_[np.full(10, np.nan), c[10:] / c[:-10] - 1]
        for i in range(len(d)):
            if 3 <= age[i] <= 60:
                recs.append((d.DATE.iloc[i], c[i] > x["ldh"], r10[i]))
    m = pd.DataFrame(recs, columns=["date", "above", "r10"])
    mk = m.groupby("date").agg(breadth=("above", "mean"), med_r10=("r10", "median"), n_active=("above", "size"))
    lds = pd.Series(sorted(x["ld"] for x in S.values()))
    return S, mk, lds


def events(S, mk, lds):
    rows = []
    dates = mk.index
    for s, x in S.items():
        d, ldh = x["d"], x["ldh"]
        o, h, l, c, v = (d[k].values.astype(float) for k in ("OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"))
        age = (d.DATE - x["ld"]).dt.days.values
        last = -99
        for i in range(12, len(d) - 11):
            if not (MIN_AGE <= age[i] <= MAX_AGE) or i - last < 10:
                continue
            trig, limit = ldh * 1.015, ldh * 1.025
            if h[i] < trig or o[i] > limit or v[i - 1] < 150000:
                continue
            turn20 = float((c[i - 20:i] * v[i - 20:i]).mean()) / 1e7 if i >= 20 else float((c[:i] * v[:i]).mean()) / 1e7
            fill = max(o[i], trig) * 1.003
            if fill < 25 or turn20 < 0.75:
                continue
            last = i
            dt = d.DATE.iloc[i]
            prev = mk.index[mk.index < dt]
            m = mk.loc[prev[-1]] if len(prev) else None
            vb = v[max(1, i - 13):i - 3]
            avg20 = v[max(0, i - 20):i].mean()
            rows.append(dict(
                symbol=s, date=dt, year=dt.year,
                gain=(h[i:i + 11].max() / fill - 1) * 100, ret10fwd=(c[min(i + 10, len(c) - 1)] / fill - 1) * 100,
                age=age[i], dist_lh=(c[i - 1] / ldh - 1) * 100, gap=(o[i] / c[i - 1] - 1) * 100,
                ret5=(c[i - 1] / c[i - 6] - 1) * 100, ret10=(c[i - 1] / c[i - 11] - 1) * 100,
                atr10=float(((h[i - 10:i] - l[i - 10:i]) / c[i - 10:i]).mean() * 100),
                range10=(h[i - 10:i].max() - l[i - 10:i].min()) / c[i - 1] * 100,
                vdry3=v[i - 3:i].mean() / vb.mean() if len(vb) >= 2 and vb.mean() > 0 else np.nan,
                vprev=v[i - 1] / avg20 if avg20 > 0 else np.nan, turn20=turn20,
                ld_range=(ldh - x["ldl"]) / ldh * 100, logp=np.log(fill), ldv_ratio=x["ldv"] / avg20 if avg20 > 0 else np.nan,
                supply30=int(((lds >= dt - pd.Timedelta(days=30)) & (lds < dt)).sum()),
                breadth=m.breadth if m is not None else np.nan, med_r10=m.med_r10 if m is not None else np.nan))
    return pd.DataFrame(rows)


def cluster_diff(a, b, groups_a, groups_b, n=2000, seed=0):
    ga = pd.factorize(np.r_[groups_a, groups_b])[0]
    k = ga.max() + 1
    va = np.r_[a, np.full(len(b), np.nan)]
    vb = np.r_[np.full(len(a), np.nan), b]
    sa = np.bincount(ga, weights=np.nan_to_num(va), minlength=k); ca = np.bincount(ga, weights=~np.isnan(va), minlength=k)
    sb = np.bincount(ga, weights=np.nan_to_num(vb), minlength=k); cb = np.bincount(ga, weights=~np.isnan(vb), minlength=k)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, k, (n, k))
    diff = sa[idx].sum(1) / np.maximum(ca[idx].sum(1), 1) - sb[idx].sum(1) / np.maximum(cb[idx].sum(1), 1)
    return float(np.percentile(diff, 5)), float(np.percentile(diff, 95))


def cluster_auc(y, p, groups, n=1000, seed=0):
    g = pd.factorize(groups)[0]
    k = g.max() + 1
    rng = np.random.default_rng(seed)
    members = [np.where(g == i)[0] for i in range(k)]
    out = []
    for _ in range(n):
        pick = rng.integers(0, k, k)
        idx = np.concatenate([members[i] for i in pick])
        if y[idx].min() == y[idx].max():
            continue
        out.append(roc_auc_score_fn(y[idx], p[idx]))
    return float(np.percentile(out, 5)), float(np.percentile(out, 95))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache")
    ap.add_argument("--out", default="reports/2026-10-17/event_model_events.csv")
    ap.add_argument("--build-only", action="store_true")
    ap.add_argument("--events-in")
    a = ap.parse_args()
    if a.events_in:
        E = pd.read_csv(a.events_in, parse_dates=["date"])
    else:
        with open(a.cache, "rb") as fh:
            listing_map, candle_map = pickle.load(fh)
        S, mk, lds = prep(listing_map, candle_map)
        E = events(S, mk, lds)
        E["y20"] = (E.gain >= 20).astype(int)
        E["y40"] = (E.gain >= 40).astype(int)
        E.to_csv(a.out, index=False)
        print(f"wrote {len(E)} events to {a.out}")
        if a.build_only:
            return
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score
    from sklearn.preprocessing import StandardScaler
    global roc_auc_score_fn
    roc_auc_score_fn = roc_auc_score
    feats = ["age", "dist_lh", "gap", "ret5", "ret10", "atr10", "range10", "vdry3", "vprev", "turn20", "ld_range", "logp", "ldv_ratio", "supply30", "breadth", "med_r10"]
    print(f"events {len(E)} across {E.symbol.nunique()} symbols; EXPLODE (>=+20% in 10 sessions) {100 * E.y20.mean():.1f}%; BIG (>=+40%) {100 * E.y40.mean():.1f}%; "
          f"by year events: {E.groupby('year').size().to_dict()}  explode rate: {E.groupby('year').y20.mean().round(3).to_dict()}")
    X = E[feats].copy()
    X = X.fillna(X.median())
    res = {}
    for name in ("M1 logistic L2", "M2 boosting d2"):
        for target in ("y20", "y40"):
            preds = pd.Series(np.nan, index=E.index)
            for ty in (2024, 2025, 2026):
                tr, te = E.year < ty, E.year == ty
                if te.sum() == 0 or E.loc[tr, target].sum() < 10:
                    continue
                if name.startswith("M1"):
                    sc = StandardScaler().fit(X[tr])
                    mdl = LogisticRegression(C=0.1, max_iter=500).fit(sc.transform(X[tr]), E.loc[tr, target])
                    preds[te] = mdl.predict_proba(sc.transform(X[te]))[:, 1]
                else:
                    mdl = GradientBoostingClassifier(n_estimators=150, max_depth=2, learning_rate=0.05, subsample=0.8, random_state=0).fit(X[tr], E.loc[tr, target])
                    preds[te] = mdl.predict_proba(X[te])[:, 1]
            res[(name, target)] = preds
    T = E[E.year >= 2024].copy()
    print(f"\nOut-of-sample events (2024-26): {len(T)}; base EXPLODE rate {100 * T.y20.mean():.1f}%, BIG {100 * T.y40.mean():.1f}%, mean 10-session max-high gain {T.gain.mean():.1f}%")
    for (name, target), p in res.items():
        T["p"] = p[T.index]
        ok = T.p.notna()
        if ok.sum() == 0:
            print(f"{name:<16} target {target}: fewer than 10 positive training events before each test year, not run")
            continue
        y = T.loc[ok, target].values
        auc = roc_auc_score(y, T.loc[ok, "p"])
        lo, hi = cluster_auc(y, T.loc[ok, "p"].values, T.loc[ok, "symbol"].values)
        cut = T.p.quantile(0.70)
        top = T[ok & (T.p >= cut)]
        rest = T[ok & (T.p < cut)]
        dlo, dhi = cluster_diff(top.gain.values, rest.gain.values, top.symbol.values, rest.symbol.values)
        yr = {int(yv): (round(100 * g.loc[g.p >= cut, target].mean(), 1), round(100 * g[target].mean(), 1)) for yv, g in T[ok].groupby("year")}
        print(f"{name:<16} target {target}: AUC {auc:.3f} (90% CI {lo:.3f}..{hi:.3f})  top-30% rate {100 * top[target].mean():.1f}% vs base {100 * T[target].mean():.1f}% "
              f"(lift {top[target].mean() / max(T[target].mean(), 1e-9):.2f}x)  top gain {top.gain.mean():.1f}% vs rest {rest.gain.mean():.1f}% (diff CI {dlo:+.1f}..{dhi:+.1f})  by year top/base: {yr}")
        top10 = T[ok & (T.p >= T.p.quantile(0.90))]
        print(f"{'':<16}   top-10% bucket: n={len(top10)}, {target} rate {100 * top10[target].mean():.1f}%, mean gain {top10.gain.mean():.1f}%, mean 10-session close return {top10.ret10fwd.mean():+.1f}% (all events {T.ret10fwd.mean():+.1f}%)")
    # single-feature reference: best univariate out-of-sample style AUC on the same pooled test events (in-sample, optimistic)
    print("\nUnivariate AUC on the test events (optimistic, no training):")
    ua = sorted(((roc_auc_score(T.y20, T[f].fillna(T[f].median())), f) for f in feats), key=lambda z: -abs(z[0] - 0.5))
    print("  " + ", ".join(f"{f} {a_:.3f}" for a_, f in ua[:8]))


if __name__ == "__main__":
    main()
