# -*- coding: utf-8 -*-
"""
research_quiet_day.py -- does a quiet day (volume dry-up AND a very narrow range) near the listing high precede the 20-30% moves?

PRE-REGISTRATION (written before running, 2026-10-17; owner observation: most moves came after a quiet day with volume dry-up and price contraction in a
very narrow range near the listing price, mostly 20-30% moves, not 50-100%)
-----------------------------------------------------------------------------------------------------------------------------------------------------------
Earlier tests used multi-day dry-up and 5-day ranges at fixed checkpoints and the policy-P trade set; this one tests a SINGLE compression day immediately before
the move, on every liquid IPO day.
Population: IPO days with age 5-60 calendar days, prior-day volume >= 150k, 20-day turnover >= Rs 0.75 Cr, close >= 25.  NEAR = the close is within -10% .. +5% of
the listing-day high.  One setup per symbol per 5 sessions.
Flags on day i (all from bars up to and including day i): NR7 = the day range (high-low)/close is the smallest of the last 7 sessions; VOLDRY = day volume <= 0.6 x the
mean volume of the previous 10 sessions (excluding the listing day); QUIET = NR7 and VOLDRY.
(1) CASE-CONTROL: of all days followed by a >= +20% high within 10 sessions (from the day-i close), the share that were QUIET (and NR7, VOLDRY alone), versus the
    share of all other days; the hit rate when QUIET vs when not (lift), all days and NEAR days, with a symbol-cluster 90% CI and by year.
(2) TRADABLE SETUP: a buy stop at the quiet day high x 1.002 valid for the next 3 sessions (entry = max(open, trigger) x 1.003); stop = the quiet-day low, limited to
    3%-12% below the entry; exit at +20% (target) or the stop (stop assumed first when both touch on a day) or the close of session 15 after entry; costs 1.0%.
    Compared with the CONTROL setup: the same mechanics on NEAR days that are not QUIET.  Also the +30% target in 20 sessions.
Bar: the QUIET hit rate lift over the NEAR control >= 1.3x with a 90% CI excluding 1, the mean net of the QUIET setup above the control with a CI of the difference
excluding 0, the same sign in at least 3 of 4 years with 20+ setups.  Otherwise the observation is not supported as a rule.

  .venv\\Scripts\\python.exe research_quiet_day.py --cache data.pkl
"""
import argparse
import pickle

import numpy as np
import pandas as pd


def prep(listing_map, candle_map):
    out = {}
    for s, df in candle_map.items():
        li = listing_map.get(s)
        if li is None or len(df) < 25:
            continue
        ld = pd.Timestamp(li["listing_date"]).tz_localize(None).normalize()
        if ld in (pd.Timestamp("2026-08-17"), pd.Timestamp("2026-08-18")) or ld < pd.Timestamp("2022-06-01"):
            continue
        d = df.copy()
        d["DATE"] = pd.to_datetime(d.DATE).dt.tz_localize(None)
        d = d.sort_values("DATE").reset_index(drop=True)
        if abs((d.DATE.iloc[0] - ld).days) > 3:
            continue
        out[s] = (d, float(li.get("listing_day_high") or d.HIGH.iloc[0]), d.DATE.iloc[0])
    return out


def cboot(vals, groups, n=1500, seed=0):
    v = np.asarray(vals, float)
    g = pd.factorize(np.asarray(groups))[0]
    k = g.max() + 1
    sums = np.bincount(g, weights=v, minlength=k)
    cnt = np.bincount(g, minlength=k).astype(float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, k, (n, k))
    m = sums[idx].sum(1) / np.maximum(cnt[idx].sum(1), 1)
    return float(v.mean()), float(np.percentile(m, 5)), float(np.percentile(m, 95))


def cdiff(a, ga, b, gb, n=1500, seed=1):
    keys = np.r_[ga, gb]
    g = pd.factorize(keys)[0]
    k = g.max() + 1
    va = np.r_[np.asarray(a, float), np.zeros(len(b))]
    ma = np.r_[np.ones(len(a)), np.zeros(len(b))]
    vb = np.r_[np.zeros(len(a)), np.asarray(b, float)]
    mb = 1 - ma
    sa, ca = np.bincount(g, weights=va, minlength=k), np.bincount(g, weights=ma, minlength=k)
    sb, cb = np.bincount(g, weights=vb, minlength=k), np.bincount(g, weights=mb, minlength=k)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, k, (n, k))
    d = sa[idx].sum(1) / np.maximum(ca[idx].sum(1), 1) - sb[idx].sum(1) / np.maximum(cb[idx].sum(1), 1)
    return float(np.percentile(d, 5)), float(np.percentile(d, 95))


def pf(v):
    v = np.asarray(v, float)
    w, l = v[v > 0].sum(), -v[v < 0].sum()
    return w / l if l > 0 else float("inf")


def days(S):
    rows = []
    for s, (d, ldh, ld0) in S.items():
        o, h, l, c, v = (d[k].values.astype(float) for k in ("OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"))
        age = (d.DATE - ld0).dt.days.values
        rng = (h - l) / c
        last = -99
        for i in range(12, len(d) - 16):
            if not (5 <= age[i] <= 60) or v[i - 1] < 150000 or c[i] < 25:
                continue
            turn20 = (c[i - 20:i] * v[i - 20:i]).mean() / 1e7 if i >= 20 else (c[1:i] * v[1:i]).mean() / 1e7
            if turn20 < 0.75:
                continue
            nr7 = bool(rng[i] <= rng[i - 6:i].min())
            vd = bool(v[i] <= 0.6 * v[max(1, i - 10):i].mean())
            near = bool(-10 <= (c[i] / ldh - 1) * 100 <= 5)
            fwd20 = h[i + 1:i + 11].max() / c[i] - 1
            fwd30 = h[i + 1:i + 16].max() / c[i] - 1
            rows.append(dict(symbol=s, i=i, date=d.DATE.iloc[i], year=d.DATE.iloc[i].year, nr7=nr7, voldry=vd, quiet=nr7 and vd, near=near,
                             hit20=fwd20 >= 0.20, hit30=fwd30 >= 0.30, fwd20=fwd20 * 100))
    return pd.DataFrame(rows)


def setups(S, D, target, horizon):
    """buy stop above the day-i high for the next 3 sessions; returns one row per setup day in D (already thinned)."""
    res = []
    for r in D.itertuples():
        d = S[r.symbol][0]
        o, h, l, c = (d[k].values.astype(float) for k in ("OPEN", "HIGH", "LOW", "CLOSE"))
        i = r.i
        trig = h[i] * 1.002
        entry = None
        for j in range(i + 1, i + 4):
            if j >= len(d) - horizon - 1:
                break
            if h[j] >= trig:
                entry = max(o[j], trig) * 1.003
                ej = j
                break
        if entry is None:
            res.append(dict(symbol=r.symbol, year=r.year, filled=False))
            continue
        stop = min(l[i], entry * 0.97)
        stop = max(stop, entry * 0.88)
        exit_px, outcome = None, "time"
        for k in range(ej, ej + horizon + 1):
            if k > ej and l[k] <= stop:
                exit_px, outcome = min(stop, o[k]), "stop"
                break
            if k == ej and l[k] <= stop and c[k] < stop:
                exit_px, outcome = stop, "stop"
                break
            if h[k] >= entry * (1 + target):
                exit_px, outcome = entry * (1 + target), "target"
                break
        if exit_px is None:
            exit_px = c[min(ej + horizon, len(c) - 1)]
        res.append(dict(symbol=r.symbol, year=r.year, filled=True, net=(exit_px / entry - 1) * 100 - 1.0, outcome=outcome))
    return pd.DataFrame(res)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    a = ap.parse_args()
    with open(a.cache, "rb") as fh:
        listing_map, candle_map = pickle.load(fh)
    S = prep(listing_map, candle_map)
    D = days(S)
    print(f"IPO days {len(D)} across {D.symbol.nunique()} symbols; NEAR days {int(D.near.sum())}; QUIET {int(D.quiet.sum())} ({100 * D.quiet.mean():.1f}%), "
          f"QUIET&NEAR {int((D.quiet & D.near).sum())}; base hit20 {100 * D.hit20.mean():.1f}%, hit30 {100 * D.hit30.mean():.1f}%")
    print("\n" + "=" * 112 + "\n(1) CASE-CONTROL: is a quiet day over-represented before +20% moves?\n" + "=" * 112)
    hit = D[D.hit20]
    for lab in ("nr7", "voldry", "quiet"):
        p_case, p_rest = hit[lab].mean() * 100, D[~D.hit20][lab].mean() * 100
        for pop_lab, P in (("all days", D), ("NEAR days", D[D.near])):
            f = P[P[lab]]
            nf = P[~P[lab]]
            r1, lo1, hi1 = cboot(f.hit20.astype(float), f.symbol)
            lift = f.hit20.mean() / max(nf.hit20.mean(), 1e-9)
            yrs = {int(y): (int(len(g[g[lab]])), round(100 * g[g[lab]].hit20.mean()), round(100 * g[~g[lab]].hit20.mean())) for y, g in P.groupby("year") if len(g[g[lab]]) >= 15}
            print(f"{lab:<7} {pop_lab:<10} days flagged {len(f):5d}: hit20 {100 * f.hit20.mean():5.1f}% (CI {100 * lo1:.1f}..{100 * hi1:.1f}) vs not flagged {100 * nf.hit20.mean():5.1f}%  lift {lift:.2f}x  by year (n, flagged%, rest%): {yrs}")
        print(f"        share of +20% episodes-days that were {lab}: {p_case:.1f}%  vs {p_rest:.1f}% of non-hit days")

    # one setup per symbol per 5 sessions
    D = D.sort_values(["symbol", "i"])
    keep, last = [], {}
    for r in D.itertuples():
        if r.i - last.get(r.symbol, -99) >= 5:
            keep.append(r.Index)
            last[r.symbol] = r.i
    DT = D.loc[keep]
    print("\n" + "=" * 112 + "\n(2) TRADABLE SETUP: buy stop above the quiet-day high (3 sessions), stop = quiet-day low, exit at target / stop / session 15\n" + "=" * 112)
    for target, horizon, lab in ((0.20, 15, "+20% target, 15 sessions"), (0.30, 20, "+30% target, 20 sessions")):
        Q = setups(S, DT[DT.quiet & DT.near], target, horizon)
        C = setups(S, DT[DT.near & ~DT.quiet], target, horizon)
        print(f"\n-- {lab} --")
        out = {}
        for nm, X in (("QUIET & NEAR", Q), ("NEAR, not quiet (control)", C)):
            F = X[X.filled]
            out[nm] = F
            if len(F) == 0:
                print(f"{nm}: no fills")
                continue
            m, lo, hi = cboot(F.net.values, F.symbol.values)
            print(f"{nm:<26} setups {len(X):4d} filled {len(F):4d}  target {100 * (F.outcome == 'target').mean():4.1f}%  stop {100 * (F.outcome == 'stop').mean():4.1f}%  time {100 * (F.outcome == 'time').mean():4.1f}%  "
                  f"mean net {m:+5.2f}% (CI {lo:+.1f}..{hi:+.1f})  median {F.net.median():+5.2f}%  PF {pf(F.net.values):.2f}")
        q, c = out["QUIET & NEAR"], out["NEAR, not quiet (control)"]
        if len(q) >= 5 and len(c) >= 5:
            dlo, dhi = cdiff(q.net.values, q.symbol.values, c.net.values, c.symbol.values)
            yrs = {int(y): (int(len(g)), round(float(g.net.mean()), 1), round(float(c[c.year == y].net.mean()), 1)) for y, g in q.groupby("year") if len(g) >= 8}
            print(f"   difference quiet minus control: {q.net.mean() - c.net.mean():+.2f}pp (90% CI {dlo:+.2f}..{dhi:+.2f}); by year (n quiet, quiet net, control net): {yrs}")


if __name__ == "__main__":
    main()
