# -*- coding: utf-8 -*-
"""
research_early_cut.py -- can losers be cut EARLY without cutting the winners?  (paired replay on policy-P trade paths)

PRE-REGISTRATION (written before running, 2026-10-17; hypothesis from the owner: cut losers early)
-------------------------------------------------------------------------------------------------
Rule R(N, x): if the CLOSE on any of trading days 1..N after the entry day is at or below entry x (1 - x%), sell at the NEXT open
(close-based, as the live 6:30 PM alert works).  Otherwise the trade runs unchanged under the live exits.  Day 0 is exempt (live rule).
Grid fixed in advance, nothing else is searched: N in {2, 3, 5}, x in {4, 6, 8}%.  Costs 1.0% round trip as in all replays.
Pairing: same trades, only the exit differs; a trade whose original exit comes on or before the trigger day is unchanged.
Adoption bar (the repo standard): paired mean gain per trade with a 90% symbol-cluster CI excluding 0, positive after dropping the single
largest contributor, at most one worse year, and fewer than 15% of GOOD trades (net >= 20% or peak >= 25%) cut.
Reported for every variant: trades cut, GOOD trades cut (false cuts), BAD trades cut (peak < 5%, closed loss), mean saved on cuts, PF three ways.

  .venv\\Scripts\\python.exe research_early_cut.py --cache data.pkl --trades reports/2026-10-17/gate_replay_V0.csv
"""
import argparse
import pickle

import numpy as np
import pandas as pd

from research_alpha_lab import cluster_boot, pf

COST = 1.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--trades", required=True)
    a = ap.parse_args()
    with open(a.cache, "rb") as fh:
        listing_map, candle_map = pickle.load(fh)
    t = pd.read_csv(a.trades)
    t["open"] = t.open.astype(bool)
    t["entry_date"] = pd.to_datetime(t.entry_date)
    t["year"] = t.entry_date.dt.year
    t["good"] = (t.net >= 20) | (t.max_runup_pct >= 25)
    t["bad"] = (~t.open) & (t.net < 0) & (t.max_runup_pct < 5)
    print(f"trades {len(t)}  GOOD {int(t.good.sum())}  BAD {int(t.bad.sum())}  baseline mean net {t.net.mean():+.2f}%  PF incl open {pf(t.net.values):.2f}")

    # path of closes and next-open fills for each trade
    paths = []
    for r in t.itertuples():
        df = candle_map[r.symbol].reset_index(drop=True)
        i = int(r.entry_idx)
        paths.append((df.CLOSE.values.astype(float), df.OPEN.values.astype(float), i, int(r.exit_idx) if r.exit_idx == r.exit_idx else None))

    rows, res_net = [], {}
    for N in (2, 3, 5):
        for x in (4, 6, 8):
            net = t.net.values.copy()
            cut = np.zeros(len(t), bool)
            for n_, (cl, op, i, ex) in enumerate(paths):
                ep = t.entry_price.values[n_]
                for k in range(1, N + 1):
                    j = i + k
                    if j + 1 >= len(cl):
                        break
                    if ex is not None and ex <= j:
                        break
                    if cl[j] <= ep * (1 - x / 100.0):
                        net[n_] = (op[j + 1] / ep - 1) * 100.0 - COST
                        cut[n_] = True
                        break
            diff = net - t.net.values
            m, lo, hi = cluster_boot(diff, t.symbol.values)
            top = np.argsort(-np.abs(diff))[:1]
            keep = np.ones(len(t), bool); keep[top] = False
            yrs = {int(y): round(float(diff[(t.year == y).values].mean()), 2) for y in sorted(t.year.unique())}
            worse = sum(v < 0 for v in yrs.values())
            s = np.sort(net)[::-1]
            cl_only = net[~t.open.values]
            rows.append(dict(rule=f"R({N}d,-{x}%)", cut=int(cut.sum()), good_cut=int((cut & t.good.values).sum()), bad_cut=int((cut & t.bad.values).sum()),
                             saved_on_cuts=round(float(diff[cut].mean()), 2) if cut.any() else 0.0,
                             gain_pp=round(m, 2), ci_lo=round(lo, 2), ci_hi=round(hi, 2), gain_ex_top=round(float(diff[keep].mean()), 2),
                             worse_years=worse, pf_incl_open=round(pf(net), 2), pf_closed=round(pf(cl_only), 2), pf_ex_top3=round(pf(s[3:]), 2)))
            res_net[f"R({N}d,-{x}%)"] = (net, cut, yrs)
    r = pd.DataFrame(rows)
    pd.set_option("display.width", 220)
    print(r.to_string(index=False))
    bar = r[(r.ci_lo > 0) & (r.gain_ex_top > 0) & (r.worse_years <= 1) & (r.good_cut < 0.15 * t.good.sum())]
    print("\nVariants passing the adoption bar:", list(bar.rule) or "NONE")
    best = r.sort_values("gain_pp", ascending=False).iloc[0]
    print(f"\nBest by mean gain: {best.rule}  by year (paired gain pp):", res_net[best.rule][2])
    net, cut, _ = res_net[best.rule]
    print("GOOD trades it would have cut:", [(s_, str(d.date()), round(n_, 0)) for s_, d, n_ in zip(t.symbol[cut & t.good.values], t.entry_date[cut & t.good.values], t.net[cut & t.good.values])][:12])
    print("\nWhy: path of GOOD vs BAD trades in the first 5 sessions (share whose close was ever <= -x% of entry):")
    for x in (4, 6, 8):
        out = {}
        for lab, mask in (("GOOD", t.good.values), ("BAD", t.bad.values), ("ALL", np.ones(len(t), bool))):
            hit = []
            for n_, (cl, op, i, ex) in enumerate(paths):
                if not mask[n_]:
                    continue
                ep = t.entry_price.values[n_]
                hit.append(any(i + k < len(cl) and cl[i + k] <= ep * (1 - x / 100.0) for k in range(1, 6) if ex is None or ex > i + k))
            out[lab] = round(100 * float(np.mean(hit)), 1)
        print(f"  close <= -{x}%: {out}")


if __name__ == "__main__":
    main()
