# -*- coding: utf-8 -*-
"""
research_coil.py -- does a volume dry-up / tight consolidation just under or above the listing high before the breakout help?

PRE-REGISTRATION (written before running, 2026-10-17; hypothesis from the owner)
-------------------------------------------------------------------------------
Mechanism: supply is absorbed while price coils near the listing-day high with shrinking volume; the breakout then meets little overhead.
All features use bars BEFORE the entry bar only (a pre-placed order can know them), n = number of bars before the entry bar.
  vdry3     mean volume of the 3 bars before entry / mean volume of the 10 bars before those (< 1 means drying up)
  vdry_ld   mean volume of the 3 bars before entry / listing-day volume
  rng5      (max high - min low of the 5 bars before entry) / last close, in %
  near_lh   share of the 5 bars before entry whose close is within 8% below the listing high or above it
  top_dist  (listing high - max close of the 5 bars before entry) / listing high, in % (negative = already closed above it)
FIXED RULE (not tuned): COIL = vdry3 < 0.80 AND rng5 <= 8% AND near_lh >= 0.8.
Variants of the same idea judged separately and also fixed in advance: COIL_V = vdry3 < 0.80 alone, COIL_R = rng5 <= 8% and near_lh >= 0.8 alone.
Walk-forward check: the single threshold per feature (direction fixed by the mechanism: vdry low, rng low, near_lh high) chosen on earlier years
to keep >= 80% of GOOD trades, applied to 2024, 2025, 2026.
Adoption bar: recall of GOOD >= 75%, BAD rejected >= 35% with lift over random rejection >= 10pp, the REJECTED trades mean net < 0 (or kept mean
higher with a symbol-cluster CI of the difference excluding 0), and no more than one worse year.  GOOD = net >= 20% or peak >= 25%; BAD = closed loss
with peak < 5%.  Input: reports/2026-10-17/gate_replay_V0.csv (policy P, 228 trades) and a data.pkl snapshot.

  .venv\\Scripts\\python.exe research_coil.py --cache data.pkl --trades reports/2026-10-17/gate_replay_V0.csv
"""
import argparse
import pickle

import numpy as np
import pandas as pd

from research_alpha_lab import cluster_boot, pf


def features(df, i, ld_high, ld_vol):
    # bar 0 is the listing day (huge volume, excluded from the dry-up baseline); young IPOs have few bars, so windows shrink to what exists
    if i < 6:
        return None
    v = df.VOLUME.values.astype(float)
    hi, lo, cl = df.HIGH.values, df.LOW.values, df.CLOSE.values
    v3 = v[i - 3:i].mean()
    base_slice = v[max(1, i - 13):i - 3]
    base = base_slice.mean() if len(base_slice) >= 2 else np.nan
    w0 = max(1, i - 5)
    rng5 = (hi[w0:i].max() - lo[w0:i].min()) / cl[i - 1] * 100
    near = float((cl[w0:i] >= ld_high * 0.92).mean())
    top = (ld_high - cl[w0:i].max()) / ld_high * 100
    return dict(vdry3=v3 / base if base > 0 else np.nan, vdry_ld=v3 / ld_vol if ld_vol else np.nan, rng5=rng5, near_lh=near, top_dist=top)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--trades", required=True)
    a = ap.parse_args()
    with open(a.cache, "rb") as fh:
        listing_map, candle_map = pickle.load(fh)
    t = pd.read_csv(a.trades)
    t["entry_date"] = pd.to_datetime(t.entry_date)
    t["open"] = t.open.astype(bool)
    rows = []
    for r in t.itertuples():
        df = candle_map.get(r.symbol)
        li = listing_map.get(r.symbol, {})
        if df is None:
            rows.append({})
            continue
        df = df.reset_index(drop=True)
        d = pd.to_datetime(df.DATE).dt.tz_localize(None)
        idx = int(np.where(d.values == np.datetime64(r.entry_date))[0][0]) if (d.values == np.datetime64(r.entry_date)).any() else None
        f = features(df, idx, float(li.get("listing_day_high", df.HIGH.iloc[0])), float(li.get("listing_day_volume", 0) or 0)) if idx is not None else None
        rows.append(f or {})
    F = pd.DataFrame(rows, index=t.index)
    t = pd.concat([t, F], axis=1)
    t["year"] = t.entry_date.dt.year
    t["good"] = (t.net >= 20) | (t.max_runup_pct >= 25)
    t["bad"] = (~t.open) & (t.net < 0) & (t.max_runup_pct < 5)
    t = t.dropna(subset=["vdry3", "rng5", "near_lh"])
    print(f"trades with features {len(t)}; GOOD {int(t.good.sum())}  BAD {int(t.bad.sum())}; full mean net {t.net.mean():+.2f}%  PF {pf(t.net.values):.2f}")
    print("feature medians  GOOD vs BAD vs ALL:")
    for f in ("vdry3", "vdry_ld", "rng5", "near_lh", "top_dist"):
        print(f"  {f:<9} GOOD {t[f][t.good].median():8.3f}  BAD {t[f][t.bad].median():8.3f}  ALL {t[f].median():8.3f}")

    def report(label, keep, te=None):
        te = t if te is None else te
        keep = keep.loc[te.index]
        g, b = te.good, te.bad
        rec = 100 * keep[g].mean()
        rej_bad = 100 * (~keep[b]).mean()
        rej_all = 100 * (~keep).mean()
        kept, rej = te[keep], te[~keep]
        diff = ""
        if len(kept) and len(rej):
            m, lo, hi = cluster_boot(np.r_[kept.net.values, -rej.net.values], np.r_[kept.symbol.values, rej.symbol.values])
            diff = f" kept-minus-rejected CI90 ~[{lo:+.1f},{hi:+.1f}]"
        print(f"{label:<44} kept {len(kept):3d}/{len(te):3d}  recall GOOD {rec:5.1f}%  BAD rejected {rej_bad:5.1f}% (all rejected {rej_all:4.1f}%, lift {rej_bad - rej_all:+5.1f})  "
              f"kept net {kept.net.mean():+5.2f}%  rejected net {rej.net.mean() if len(rej) else float('nan'):+5.2f}%  kept PF {pf(kept.net.values):4.2f}{diff}")

    print("\nFIXED RULES (all years pooled, not tuned):")
    k_v = t.vdry3 < 0.80
    k_r = (t.rng5 <= 8) & (t.near_lh >= 0.8)
    report("COIL_V  volume dry-up (vdry3 < 0.80)", k_v)
    report("COIL_R  tight 5-bar range <= 8%, near LH", k_r)
    report("COIL    both", k_v & k_r)
    print("  by year, COIL:", {int(y): (int(((k_v & k_r)[g.index]).sum()), int(len(g))) for y, g in t.groupby("year")})
    print("  mean net kept vs rejected by year (COIL):", {int(y): (round(g.net[(k_v & k_r)[g.index]].mean(), 1), round(g.net[~(k_v & k_r)[g.index]].mean(), 1)) for y, g in t.groupby("year")})
    print("  explosive winners the COIL rule would reject:", [(r.symbol, str(r.entry_date.date()), round(r.net, 0)) for r in t[(~(k_v & k_r)) & (t.net >= 50)].sort_values("net", ascending=False).itertuples()][:10])

    print("\nWALK-FORWARD single-feature thresholds (direction fixed by the mechanism), test 2024-26:")
    for f, direction in (("vdry3", "le"), ("vdry_ld", "le"), ("rng5", "le"), ("near_lh", "ge")):
        keeps = []
        for ty in (2024, 2025, 2026):
            tr, te = t[t.year < ty], t[t.year == ty]
            best = None
            for th in np.unique(tr[f].dropna().values):
                k = (tr[f] <= th) if direction == "le" else (tr[f] >= th)
                if k[tr.good].mean() < 0.80:
                    continue
                rb = (~k[tr.bad]).mean()
                if best is None or rb > best[0]:
                    best = (rb, th)
            keeps.append(pd.Series(True, index=te.index) if best is None else ((te[f] <= best[1]) if direction == "le" else (te[f] >= best[1])))
        te_all = t[t.year >= 2024]
        report(f"WF {f} keep {direction}", pd.concat(keeps), te_all)


if __name__ == "__main__":
    main()
