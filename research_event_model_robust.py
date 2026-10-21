# -*- coding: utf-8 -*-
"""
research_event_model_robust.py -- is the gradient-boosting selection signal in research_event_model.py real or luck?

Pre-registered checks (written before running; the headline result M2 d2 EXPLODE AUC 0.700, top-30% lift 1.60x passed the earlier bar):
  R1 permutation null: shuffle the EXPLODE labels within each year (keeps base rates), rerun the whole walk-forward pipeline 100 times,
     p-value = share of runs whose pooled out-of-sample AUC >= the real one.  Real signal needs p <= 0.05.
  R2 stability: 5 seeds x depth {1, 2, 3} (15 fits per setting): the median out-of-sample AUC and top-30% lift must stay >= 0.60 and >= 1.3x.
  R3 what drives it: mean feature importance across the three walk-forward models, and the univariate direction of the top features.
  R4 does it pay: link events to the policy-P trades (same symbol and entry date) and compare the actual NET trade result of the top 30% vs the rest,
     out of sample (2024-26), with a symbol-cluster bootstrap of the difference.  A selection signal that does not improve realised net is not useful.
Adoption bar: R1 p <= 0.05, R2 holds, and R4 shows top-30% mean net above the rest with the 90% cluster CI excluding 0 (or at minimum PF higher
and the same sign in every test year).  Otherwise the signal is NOT adopted.

  python research_event_model_robust.py --events reports/2026-10-17/event_model_events.csv --trades reports/2026-10-17/gate_replay_V0.csv
"""
import argparse

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import roc_auc_score

FEATS = ["age", "dist_lh", "gap", "ret5", "ret10", "atr10", "range10", "vdry3", "vprev", "turn20", "ld_range", "logp", "ldv_ratio", "supply30", "breadth", "med_r10"]


def walk(E, X, y, depth=2, seed=0, n_est=150):
    pred = pd.Series(np.nan, index=E.index)
    imps = []
    for ty in (2024, 2025, 2026):
        tr, te = (E.year < ty).values, (E.year == ty).values
        if te.sum() == 0 or y[tr].sum() < 10:
            continue
        m = GradientBoostingClassifier(n_estimators=n_est, max_depth=depth, learning_rate=0.05, subsample=0.8, random_state=seed).fit(X[tr], y[tr])
        pred[te] = m.predict_proba(X[te])[:, 1]
        imps.append(m.feature_importances_)
    return pred, np.mean(imps, axis=0) if imps else None


def stats(E, pred, y):
    ok = pred.notna().values & (E.year >= 2024).values
    p, yy = pred[ok].values, y[ok]
    auc = roc_auc_score(yy, p)
    cut = np.quantile(p, 0.70)
    top = p >= cut
    lift = yy[top].mean() / max(yy.mean(), 1e-9)
    return auc, lift, ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", required=True)
    ap.add_argument("--trades", required=True)
    a = ap.parse_args()
    E = pd.read_csv(a.events, parse_dates=["date"])
    X = E[FEATS].fillna(E[FEATS].median()).values
    y = E.y20.values
    pred, imp = walk(E, X, y)
    auc, lift, ok = stats(E, pred, y)
    print(f"real run: AUC {auc:.3f}, top-30% lift {lift:.2f}x on {int(ok.sum())} out-of-sample events ({int(y[ok].sum())} positives)")

    rng = np.random.default_rng(0)
    null = []
    for _ in range(100):
        ys = y.copy()
        for yr in E.year.unique():
            idx = np.where(E.year.values == yr)[0]
            ys[idx] = rng.permutation(ys[idx])
        pr, _ = walk(E, X, ys, seed=1)
        if pr.notna().any():
            null.append(stats(E, pr, ys)[0])
    null = np.array(null)
    print(f"R1 permutation null (100 shuffles within year): mean AUC {null.mean():.3f}, 95th pct {np.percentile(null, 95):.3f}, max {null.max():.3f}; "
          f"p-value = {(null >= auc).mean():.3f}")

    print("\nR2 stability across seeds and depth:")
    for depth in (1, 2, 3):
        aucs, lifts = [], []
        for seed in range(5):
            pr, _ = walk(E, X, y, depth=depth, seed=seed)
            au, li, _ = stats(E, pr, y)
            aucs.append(au); lifts.append(li)
        print(f"  depth {depth}: AUC median {np.median(aucs):.3f} (min {min(aucs):.3f}, max {max(aucs):.3f}); top-30% lift median {np.median(lifts):.2f}x (min {min(lifts):.2f})")

    print("\nR3 mean feature importance (3 walk-forward models) and univariate direction on all events:")
    order = np.argsort(-imp)
    for k in order[:8]:
        f = FEATS[k]
        corr = E[f].corr(E.gain, method="spearman")
        print(f"  {f:<9} importance {imp[k]:.3f}  spearman with 10-session max-high gain {corr:+.2f}")

    # R4 link to realised trade net
    T = pd.read_csv(a.trades, parse_dates=["entry_date"])
    T["key"] = T.symbol + "|" + T.entry_date.dt.strftime("%Y-%m-%d")
    E["key"] = E.symbol + "|" + E.date.dt.strftime("%Y-%m-%d")
    M = E.assign(score=pred).merge(T[["key", "net", "open", "max_runup_pct"]], on="key", how="inner")
    M = M[(M.year >= 2024) & M.score.notna()]
    cut = M.score.quantile(0.70)
    top, rest = M[M.score >= cut], M[M.score < cut]
    print(f"\nR4 events matched to policy-P trades (2024-26, out of sample): {len(M)} (top 30%: {len(top)}, rest: {len(rest)})")

    def pf(v):
        v = np.asarray(v, float)
        w, l = v[v > 0].sum(), -v[v < 0].sum()
        return w / l if l > 0 else float("inf")

    print(f"  mean net top {top.net.mean():+.2f}% vs rest {rest.net.mean():+.2f}%   PF top {pf(top.net):.2f} vs rest {pf(rest.net):.2f}   win rate top {100 * (top.net > 0).mean():.0f}% vs rest {100 * (rest.net > 0).mean():.0f}%")
    g = pd.factorize(M.symbol)[0]
    rng = np.random.default_rng(1)
    diffs = []
    ks = g.max() + 1
    topm = (M.score >= cut).values
    netv = M.net.values
    for _ in range(2000):
        pick = rng.integers(0, ks, ks)
        idx = np.concatenate([np.where(g == i)[0] for i in pick])
        t_, r_ = idx[topm[idx]], idx[~topm[idx]]
        if len(t_) and len(r_):
            diffs.append(netv[t_].mean() - netv[r_].mean())
    print(f"  difference top minus rest: {top.net.mean() - rest.net.mean():+.2f}pp, 90% symbol-cluster CI {np.percentile(diffs, 5):+.2f}..{np.percentile(diffs, 95):+.2f}")
    print("  by year (mean net top / rest, n top):", {int(yv): (round(float(gg[gg.score >= cut].net.mean()), 1), round(float(gg[gg.score < cut].net.mean()), 1), int((gg.score >= cut).sum())) for yv, gg in M.groupby("year")})
    s = top.sort_values("net", ascending=False)
    print("  largest top-bucket winners:", [(r.symbol, str(r.date.date()), round(r.net, 0)) for r in s.head(5).itertuples()])
    print(f"  top bucket net without its 3 best trades: {s.net.iloc[3:].mean():+.2f}%   (rest without its 3 best: {rest.sort_values('net', ascending=False).net.iloc[3:].mean():+.2f}%)")
    print(f"  GOOD-type winners (net >= 20%) captured by the top bucket: {int((top.net >= 20).sum())} of {int((M.net >= 20).sum())}")


if __name__ == "__main__":
    main()
