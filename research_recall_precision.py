# -*- coding: utf-8 -*-
"""
research_recall_precision.py -- can any entry-time feature keep most explosive winners while rejecting bad trades?

PRE-REGISTRATION (written before running, 2026-10-17)
-----------------------------------------------------
Population: policy P trades (reports/2026-10-14/data_base_trades_causal_resting_eod.csv, 227 trades, features known BEFORE entry).
Labels: GOOD = net >= +20% or peak run-up >= 25%; BAD = closed trade with net < 0 and peak run-up < 5% (no follow-through at all).
For each numeric feature and each direction (keep high / keep low) the rule keeps trades on one side of a threshold.  The threshold is
chosen on EARLIER YEARS ONLY as the one that keeps >= 80% of GOOD trades while rejecting the most BAD trades; it is then applied to the next year
(walk-forward, test years 2024, 2025, 2026).  Adoption bar: pooled out-of-sample recall of GOOD >= 75%, share of BAD rejected >= 35%,
AND the kept set has a higher mean net than the full set with a symbol-cluster CI of the difference excluding 0 or the rejected set
has a negative mean.  A random rejection at the same rate is the null (reported: bad rejected would equal the rejection rate).
Ten-odd features x 2 directions means a few false positives are expected; only a feature that passes in pooled test years counts.

  .venv\\Scripts\\python.exe research_recall_precision.py
"""
import numpy as np
import pandas as pd

PATH = "reports/2026-10-14/data_base_trades_causal_resting_eod.csv"
FEATS = ["vr", "ext", "risk_pct", "turn20", "ld_range", "gap", "ret5", "ret10", "atr10", "range10", "dist_prev", "ld_high",
         "supply30", "n_active", "frac_above", "med_ret20", "age", "ld_sent60"]
MIN_RECALL = 0.80


def load():
    d = pd.read_csv(PATH)
    d["good"] = (d.net >= 20) | (d.max_runup_pct >= 25)
    d["bad"] = (~d.open.astype(bool)) & (d.net < 0) & (d.max_runup_pct < 5)
    d["entry_date"] = pd.to_datetime(d.entry_date)
    d["year"] = d.entry_date.dt.year
    return d


def pick_threshold(tr, f, direction):
    """Threshold on earlier years: keeps >= MIN_RECALL of GOOD, rejects the most BAD."""
    best = None
    vals = np.unique(tr[f].dropna().values)
    for t in vals:
        keep = (tr[f] >= t) if direction == "ge" else (tr[f] <= t)
        keep = keep | tr[f].isna()
        g = tr[tr.good]
        if len(g) == 0:
            continue
        recall = keep[tr.good].mean()
        if recall < MIN_RECALL:
            continue
        rej_bad = (~keep[tr.bad]).mean() if tr.bad.sum() else 0.0
        if best is None or rej_bad > best[0]:
            best = (rej_bad, t)
    return None if best is None else best[1]


def main():
    d = load()
    print(f"trades {len(d)}  GOOD {int(d.good.sum())} ({100 * d.good.mean():.1f}%)  BAD {int(d.bad.sum())} ({100 * d.bad.mean():.1f}%)  "
          f"open {int(d.open.sum())}; GOOD share of total net profit: {100 * d.net[d.good].sum() / d.net[d.net > 0].sum():.0f}% of gross wins")
    print(f"full-set mean net {d.net.mean():+.2f}%   BAD mean {d.net[d.bad].mean():+.2f}%   GOOD mean {d.net[d.good].mean():+.2f}%\n")
    rows = []
    for f in FEATS:
        if f not in d.columns:
            continue
        for direction in ("ge", "le"):
            kept_all, test_all = [], []
            for ty in (2024, 2025, 2026):
                tr = d[d.year < ty]
                te = d[d.year == ty]
                if len(tr) < 30 or len(te) == 0:
                    continue
                t = pick_threshold(tr, f, direction)
                if t is None:
                    keep = pd.Series(True, index=te.index)
                else:
                    keep = ((te[f] >= t) if direction == "ge" else (te[f] <= t)) | te[f].isna()
                test_all.append(te)
                kept_all.append(keep)
            te = pd.concat(test_all)
            keep = pd.concat(kept_all)
            g, b = te.good, te.bad
            recall = keep[g].mean() if g.sum() else np.nan
            rej_bad = (~keep[b]).mean() if b.sum() else np.nan
            rej_all = (~keep).mean()
            kept_mean = te.net[keep].mean()
            rej_mean = te.net[~keep].mean() if (~keep).any() else np.nan
            rows.append(dict(feature=f, keep=direction, test_n=len(te), good=int(g.sum()), bad=int(b.sum()),
                             recall_good=round(100 * recall, 1), rej_bad=round(100 * rej_bad, 1), rej_all=round(100 * rej_all, 1),
                             lift=round(100 * (rej_bad - rej_all), 1), kept_net=round(kept_mean, 2), full_net=round(te.net.mean(), 2),
                             rej_net=round(rej_mean, 2) if rej_mean == rej_mean else np.nan))
    r = pd.DataFrame(rows).sort_values("lift", ascending=False)
    pd.set_option("display.width", 220)
    print("Out-of-sample (2024-26 pooled), threshold chosen on prior years to keep >= 80% of GOOD.")
    print("lift = share of BAD rejected minus share of ALL trades rejected (0 = no better than random rejection)\n")
    print(r.to_string(index=False))
    ok = r[(r.recall_good >= 75) & (r.rej_bad >= 35) & (r.lift >= 10) & (r.kept_net > r.full_net)]
    print("\nFeatures passing recall >= 75%, bad rejected >= 35%, lift >= 10pp and higher kept net:", list(zip(ok.feature, ok.keep)) or "NONE")
    # explicit example: best single rule in-sample on all years, to show what it would reject
    print("\nIn-sample reference (all years, thresholds fitted on everything, OPTIMISTIC):")
    for f, direction in [(x.feature, x.keep) for x in r.head(3).itertuples()]:
        t = pick_threshold(d, f, direction)
        if t is None:
            continue
        keep = ((d[f] >= t) if direction == "ge" else (d[f] <= t)) | d[f].isna()
        print(f"  {f} keep {direction} {t:.3g}: recall GOOD {100 * keep[d.good].mean():.0f}%  BAD rejected {100 * (~keep[d.bad]).mean():.0f}%  "
              f"trades rejected {100 * (~keep).mean():.0f}%  kept net {d.net[keep].mean():+.2f}%  rejected net {d.net[~keep].mean():+.2f}%")


if __name__ == "__main__":
    main()
