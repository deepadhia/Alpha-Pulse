# -*- coding: utf-8 -*-
"""
research_walkforward.py -- out-of-sample test of entry filters (research tooling)

For each test year Y the filter/model is chosen ONLY from events with entry year < Y and then scored on
year Y, so every reported number is out-of-sample.  Events are the loosened-gate signals restricted to
the live liquidity floor (turnover20 >= 0.75 Cr), outcome = live-exit return net of 1% friction.

Part A  single-feature threshold rules (pre-registered families; threshold picked on the training years)
Part B  a heavily regularised linear model on a small, pre-registered feature set

Selection effect and noise are handled by pooling all out-of-sample years and using a cluster (symbol)
bootstrap on "selected minus not-selected" mean outcome.

  python research_walkforward.py --events events.csv
"""

import argparse

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

MIN_TRAIN = 150
TEST_YEARS = (2024, 2025, 2026)

# family name -> (feature, direction)   direction "le": keep feature <= t ; "ge": keep feature >= t
RULES = {
    "low volatility (atr10_pct <= t)": ("atr10_pct", "le"),
    "narrow day range (day_range_pct <= t)": ("day_range_pct", "le"),
    "low IPO supply (mkt_supply30 <= t)": ("mkt_supply30", "le"),
    "cool cohort momentum (mkt_med_ret20_5d <= t)": ("mkt_med_ret20_5d", "le"),
    "higher liquidity (turnover20_cr >= t)": ("turnover20_cr", "ge"),
    "tight extension (ext_pct <= t)": ("ext_pct", "le"),
    "volume spike (vol_ratio >= t)": ("vol_ratio", "ge"),
    "strong close (close_loc >= t)": ("close_loc", "ge"),
}
MODEL_FEATURES = ["atr10_pct", "mkt_supply30", "mkt_med_ret20_5d", "ext_pct", "vol_ratio", "close_loc",
                  "log_turnover20", "age", "ret10", "mkt_frac_above_ld_5d"]


def pf(x):
    w, l = x[x > 0].sum(), -x[x < 0].sum()
    return w / l if l > 0 else np.inf


def cluster_diff(d, flag, B=4000, seed=5):
    """Cluster bootstrap of mean(net | flag) - mean(net | ~flag)."""
    rng = np.random.default_rng(seed)
    a = d.assign(f=flag.astype(int))
    g = a.groupby("symbol").apply(lambda x: pd.Series({
        "n1": (x.f == 1).sum(), "s1": x.loc[x.f == 1, "net"].sum(),
        "n0": (x.f == 0).sum(), "s0": x.loc[x.f == 0, "net"].sum()}), include_groups=False)
    S = g.values
    k = len(S)
    out = []
    for _ in range(B):
        t = S[rng.integers(0, k, k)].sum(axis=0)
        if t[0] > 0 and t[2] > 0:
            out.append(t[1] / t[0] - t[3] / t[2])
    out = np.array(out)
    base = g.s1.sum() / g.n1.sum() - g.s0.sum() / g.n0.sum()
    return base, np.percentile(out, [2.5, 97.5]), float((out <= 0).mean())


def pick_threshold(train, feat, direction):
    """Threshold maximising mean net on the training years, keeping at least 35% of events."""
    best, best_t = -1e9, None
    for q in np.linspace(0.25, 0.85, 13):
        t = float(train[feat].quantile(q if direction == "le" else 1 - q))
        keep = train[train[feat] <= t] if direction == "le" else train[train[feat] >= t]
        if len(keep) < 0.35 * len(train) or len(keep) < 60:
            continue
        m = keep.net.mean()
        if m > best:
            best, best_t = m, t
    return best_t


def part_a(ev):
    print("=" * 100)
    print("PART A - walk-forward single-feature rules (threshold chosen on earlier years only)")
    print("=" * 100)
    res = []
    for name, (feat, direction) in RULES.items():
        sel_all, flags = [], []
        yr_rows = []
        for Y in TEST_YEARS:
            train, test = ev[ev.year < Y], ev[ev.year == Y]
            if len(train) < MIN_TRAIN or len(test) < 20:
                continue
            t = pick_threshold(train, feat, direction)
            if t is None:
                continue
            keep = (test[feat] <= t) if direction == "le" else (test[feat] >= t)
            yr_rows.append((Y, t, int(keep.sum()), len(test), test.net[keep].mean(), test.net[~keep].mean(), test.net.mean()))
            sel_all.append(test.assign(sel=keep))
        if not sel_all:
            continue
        oos = pd.concat(sel_all)
        diff, ci, p = cluster_diff(oos, oos.sel)
        wins = sum(1 for r in yr_rows if r[4] > r[5])
        res.append(dict(rule=name, oos_n=len(oos), kept=int(oos.sel.sum()), kept_mean=oos.net[oos.sel].mean(),
                        kept_PF=pf(oos.net[oos.sel]), rest_mean=oos.net[~oos.sel].mean(), all_mean=oos.net.mean(),
                        diff=diff, lo=ci[0], hi=ci[1], p=p, yrs_better=f"{wins}/{len(yr_rows)}"))
        print(f"\n{name}")
        for r in yr_rows:
            print(f"   test {r[0]}: t={r[1]:8.3f}  kept {r[2]:4d}/{r[3]:4d}  kept mean {r[4]:+6.2f}  dropped {r[5]:+6.2f}  all {r[6]:+6.2f}")
    r = pd.DataFrame(res).sort_values("diff", ascending=False)
    pd.set_option("display.width", 220)
    print("\nPooled out-of-sample summary (kept = events the rule would have traded):")
    print(r.round(3).to_string(index=False))
    return r


def part_b(ev):
    print("\n" + "=" * 100)
    print("PART B - walk-forward ridge model (features standardised on the training years)")
    print("=" * 100)
    from numpy.linalg import solve
    ev = ev.copy()
    ev["log_turnover20"] = np.log1p(ev.turnover20_cr)
    rows = []
    allp = []
    for Y in TEST_YEARS:
        train, test = ev[ev.year < Y].dropna(subset=MODEL_FEATURES), ev[ev.year == Y].dropna(subset=MODEL_FEATURES)
        if len(train) < MIN_TRAIN or len(test) < 20:
            continue
        mu, sd = train[MODEL_FEATURES].mean(), train[MODEL_FEATURES].std().replace(0, 1)
        Xtr = ((train[MODEL_FEATURES] - mu) / sd).values
        ytr = train.net.clip(-15, 40).values            # winsorise so a single +190% trade cannot drive the fit
        ytr_c = ytr - ytr.mean()
        for alpha in (300.0,):
            beta = solve(Xtr.T @ Xtr + alpha * np.eye(Xtr.shape[1]), Xtr.T @ ytr_c)
        Xte = ((test[MODEL_FEATURES] - mu) / sd).values
        pred = Xte @ beta
        test = test.assign(pred=pred)
        rho = spearmanr(test.pred, test.net)[0]
        top = test.pred >= test.pred.quantile(0.5)
        rows.append((Y, len(test), rho, test.net[top].mean(), test.net[~top].mean(), test.net.mean()))
        allp.append(test.assign(sel=top))
        print(f"test {Y}: n={len(test):4d}  OOS rank-IC={rho:+.3f}  top-half mean {test.net[top].mean():+6.2f}  "
              f"bottom-half {test.net[~top].mean():+6.2f}  all {test.net.mean():+6.2f}")
        coefs = pd.Series(beta, index=MODEL_FEATURES).sort_values()
        print("     coefficients (std units): " + ", ".join(f"{k}={v:+.2f}" for k, v in coefs.items()))
    oos = pd.concat(allp)
    diff, ci, p = cluster_diff(oos, oos.sel)
    print(f"\npooled OOS: top-half mean {oos.net[oos.sel].mean():+.2f}  PF {pf(oos.net[oos.sel]):.2f}  "
          f"bottom-half {oos.net[~oos.sel].mean():+.2f}  diff {diff:+.2f}  CI95 [{ci[0]:+.2f},{ci[1]:+.2f}]  P(diff<=0)={p:.3f}")
    print(f"pooled OOS rank-IC: {spearmanr(oos.pred, oos.net)[0]:+.3f}")
    return oos


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", required=True)
    a = ap.parse_args()
    ev = pd.read_csv(a.events)
    ev = ev[(ev.type == "LISTING_BREAKOUT") & (ev.turnover20_cr >= 0.75)].copy()
    print(f"events used: {len(ev)}  (LISTING_BREAKOUT, turnover20 >= 0.75 Cr)   by year: {ev.year.value_counts().sort_index().to_dict()}")
    part_a(ev)
    part_b(ev)


if __name__ == "__main__":
    main()
