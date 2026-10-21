# -*- coding: utf-8 -*-
"""
research_anchor_unlock_audit.py -- POST-HOC audit of research_anchor_unlock.py (written 2026-10-21 AFTER its Stage 1 result was seen).

Status of this file: robustness checks of a result that passed its pre-registered bar; it is NOT part of that bar and cannot adopt anything.  It asks whether the
"unlock effect" is specific to the unlock dates or is just the age profile of newly listed stocks (post-listing drift differs by age, and the original
placebo windows are all LATER than the event).

Checks (all fixed before running this file):
  A  Age-profile null: the same contrast as Stage 1 (event window centred at listing + K days vs the same stock's windows centred at K+14, K+22, K+30, K+38,
     placebo centres within 10 days of 28 or 88 dropped, at least 2 valid) for K = 14, 16, ..., 130.  If the unlock dates are special, D(28) and D(88)
     must stand out from D(K) at ages with no unlock.  Reported: the table, and the share of non-unlock K whose D is as negative as D(28) / D(88).
     Neighbouring K overlap (windows are 10 sessions), so about 10 independent looks exist: read the table, not a p-value.
  B  Two-sided placebo for U90: D computed against placebo windows BEFORE the event (centres 50, 58, 66, 74) instead of after.  Both must be negative.
  C  Robustness of the U30 / U90 mean: median, 5% trimmed mean, and the mean without the 5 most negative stocks.

  .venv\\Scripts\\python.exe research_anchor_unlock_audit.py --cache data.pkl --out-dir reports/2026-10-21
"""
import argparse
import io
import os
import pickle
import sys

import numpy as np
import pandas as pd

import research_anchor_unlock as ru
from core.setup2 import clean_universe
from research_alpha_lab import cluster_boot

UNLOCKS = (28, 88)


def contrast(syms, mkt_log, k, centres):
    rows = []
    for s in syms:
        ev = ru.window(s, k, mkt_log)
        if ev is None:
            continue
        pl = [w["ar"] for w in (ru.window(s, c, mkt_log) for c in centres) if w is not None]
        if len(pl) < ru.MIN_PLACEBOS:
            continue
        rows.append(dict(symbol=s.sym, year=s.year, D=(ev["ar"] - float(np.mean(pl))) * 100))
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--out-dir", default=None)
    a = ap.parse_args()
    with open(a.cache, "rb") as fh:
        listing_map, candle_map = pickle.load(fh)
    clean, _ = clean_universe(listing_map, candle_map)
    syms = [ru.Sym(k, df, li) for k, (df, li) in clean.items()]
    mkt = ru.build_market(syms)
    mkt_log = np.log1p(mkt).cumsum()

    buf = io.StringIO()

    def out(x=""):
        print(x)
        buf.write(x + "\n")

    out("research_anchor_unlock_audit.py  (POST-HOC robustness audit, not part of the pre-registered bar)")
    out("\nA. AGE-PROFILE NULL: D(K) = event window centred at listing+K vs the stock's own windows at K+14, K+22, K+30, K+38 (mean abnormal return difference, pp)")
    out(f"   {'K':>4} {'N':>4} {'D':>7} {'CI90 low':>9} {'CI90 high':>9}   note")
    prof = {}
    for k in range(14, 131, 2):
        centres = [c for c in (k + 14, k + 22, k + 30, k + 38) if all(abs(c - u) > 10 for u in UNLOCKS)]
        d = contrast(syms, mkt_log, k, centres)
        if len(d) < 30:
            continue
        m, lo, hi = cluster_boot(d.D.values, d.symbol.values, n=1000)
        near = [u for u in UNLOCKS if abs(k - u) <= 10]
        prof[k] = (len(d), m, lo, hi, bool(near))
        out(f"   {k:>4} {len(d):>4} {m:>+7.2f} {lo:>+9.2f} {hi:>+9.2f}   {'<-- nominal unlock window' if near else ''}{' (exact nominal date)' if k in UNLOCKS else ''}")
    null = {k: v[1] for k, v in prof.items() if not v[4]}
    for u in UNLOCKS:
        if u in prof:
            share = np.mean([v <= prof[u][1] for v in null.values()])
            out(f"   D({u}) = {prof[u][1]:+.2f}pp; share of {len(null)} non-unlock ages with D at least that negative: {100 * share:.0f}%   "
                f"(non-unlock mean {np.mean(list(null.values())):+.2f}, min {min(null.values()):+.2f}, max {max(null.values()):+.2f})")

    out("\nB. TWO-SIDED PLACEBO for U90 (placebo windows BEFORE the event: centres 50, 58, 66, 74)")
    d = contrast(syms, mkt_log, 88, (50, 58, 66, 74))
    m, lo, hi = cluster_boot(d.D.values, d.symbol.values)
    out(f"   N={len(d)}  D = {m:+.2f}pp  CI90 [{lo:+.2f}, {hi:+.2f}]   by listing year: " +
        "  ".join(f"{int(y)}: {g.D.mean():+.2f} (N={len(g)})" for y, g in d.groupby('year')))

    out("\nC. ROBUSTNESS of the pre-registered D (from reports/2026-10-21/anchor_unlock_U30.csv and _U90.csv)")
    for nm in ("U30", "U90"):
        p = os.path.join(a.out_dir or ".", f"anchor_unlock_{nm}.csv")
        e = pd.read_csv(p)
        x = np.sort(e.D.values)
        k5 = int(len(x) * 0.05)
        out(f"   {nm}: N={len(x)} mean {x.mean():+.2f} median {np.median(x):+.2f} 5%-trimmed mean {x[k5:len(x) - k5].mean():+.2f} "
            f"mean without the 5 most negative {x[5:].mean():+.2f} without the 5 most positive {x[:-5].mean():+.2f}")

    if a.out_dir:
        with open(os.path.join(a.out_dir, "anchor_unlock_audit_output.txt"), "w", encoding="utf-8") as fh:
            fh.write(buf.getvalue())


if __name__ == "__main__":
    main()
