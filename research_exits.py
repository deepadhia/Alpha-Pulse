# -*- coding: utf-8 -*-
"""
research_exits.py -- paired exit-rule study (research tooling, read-only)

Every exit variant is run on the SAME set of ~900 loosened-gate signals as the live exit
configuration, so each variant is compared with the baseline event by event (a paired test,
far more powerful than comparing two 80-trade books).  Inference is clustered by symbol because
consecutive-day signals on one stock are not independent.

The variant list below is PRE-REGISTERED (written before looking at any result).  With ~40
variants some will look good by chance, so a variant is only called robust when ALL of:
  * the clustered 95% CI of the paired difference excludes zero,
  * it beats baseline in at least 4 of the 5 calendar years, and
  * its neighbours on the same parameter axis are also non-negative (a plateau, not a spike).

  python research_exits.py --cache data.pkl
"""

import argparse
import logging
import pickle
from dataclasses import replace

import numpy as np
import pandas as pd

logging.disable(logging.CRITICAL)

from core.honest_backtest import LIVE_FAITHFUL, Params, precompute_signals, simulate_events
from research_events import FRICTION_PCT, loose_params

BIG = 10_000

# (label, axis, Params overrides, exit overrides)
VARIANTS = []


def add(label, axis, p_over=None, exit_over=None):
    VARIANTS.append((label, axis, p_over or {}, exit_over or {}))


for v in (0.05, 0.06, 0.07, 0.08, 0.12, 0.15):
    add(f"trail distance {v:.0%} below price", "trail_pct", exit_over={"trail_pct_override": v})
for v in (0.0, 2.0, 5.0, 8.0):
    add(f"stage-1 trail starts at +{v:g}%", "trail_start", p_over={"trail_start_pnl": v})
for v in (10.0, 20.0, 25.0):
    add(f"winner threshold +{v:g}% run-up", "winner_thr", p_over={"winner_runup_pct": v})
for v in (0.70, 0.75, 0.85, 0.90):
    add(f"winner cushion {v:.2f} x peak", "cushion", exit_over={"winner_cushion_mult": v})
for v in (1.00, 1.10):
    add(f"winner min lock {v:.2f} x entry", "min_lock", exit_over={"winner_min_lock_mult": v})
for d, s in ((10, 7), (21, 15), (28, 20)):
    add(f"velocity gate {d}d / {s} sessions", "velocity",
        p_over={"dead_money_days_ipo": d}, exit_over={"velocity_days": d, "velocity_sessions": s})
for v in (2.0, 6.0, 8.0):
    add(f"dead-money peak run-up < {v:g}%", "dead_runup", p_over={"dead_money_runup_ipo": v})
add("velocity + dead-money gates OFF", "gates_off", p_over={"dead_money_days_ipo": BIG},
    exit_over={"velocity_days": BIG, "velocity_sessions": BIG, "dead_money_days_consol": BIG})
add("min trail move 0% (always ratchet)", "min_move", p_over={"min_trail_move_pct": 0.0})
add("min trail move 2%", "min_move", p_over={"min_trail_move_pct": 2.0})


# stop-construction variants change WHICH signals exist (risk filter), so signals are rebuilt per variant
STOP_VARIANTS = [
    ("initial stop cap 8%", {"stop_cap_pct": 8.0}),
    ("initial stop cap 10%", {"stop_cap_pct": 10.0}),
    ("initial stop cap 15%", {"stop_cap_pct": 15.0}),
    ("swing-low buffer 5%", {"stop_buffer": 0.95}),
    ("swing-low buffer 1%", {"stop_buffer": 0.99}),
    ("swing lookback 10d", {"swing_lookback": 10}),
    ("swing lookback 25d", {"swing_lookback": 25}),
]


def run(candle_map, listing_map, signals, p):
    rows = []
    for sym, df in candle_map.items():
        sg = signals.get(sym)
        if not sg:
            continue
        for e in simulate_events(df, sym, listing_map.get(sym, {}), LIVE_FAITHFUL, p, signals=sg):
            rows.append({k: v for k, v in e.items() if k != "path"})
    out = pd.DataFrame(rows)
    if len(out):
        out["net"] = out["pnl_pct"] - FRICTION_PCT
    return out


def pf(x):
    w, l = x[x > 0].sum(), -x[x < 0].sum()
    return w / l if l > 0 else np.inf


def cluster_ci(d, B=3000, seed=11):
    """Cluster (symbol) bootstrap of the mean paired difference."""
    rng = np.random.default_rng(seed)
    g = d.groupby("symbol")["diff"].agg(["sum", "count"])
    S, N = g["sum"].values, g["count"].values
    k = len(S)
    means = np.empty(B)
    for b in range(B):
        idx = rng.integers(0, k, k)
        means[b] = S[idx].sum() / N[idx].sum()
    return np.percentile(means, [2.5, 97.5]), float((means <= 0).mean())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--anyday", action="store_true",
                    help="population = every liquid IPO-day (large N) instead of the loosened breakout events")
    ap.add_argument("--step", type=int, default=3, help="with --anyday: use every Nth eligible day per symbol")
    ap.add_argument("--stops", action="store_true", help="also run the stop-construction variants")
    a = ap.parse_args()
    listing_map, candle_map = pickle.load(open(a.cache, "rb"))

    if a.anyday:
        from research_trigger import any_day_signals
        base_p = Params.from_live()

        def build_signals(p):
            out = {}
            for sym, df in candle_map.items():
                sg = any_day_signals(df, listing_map.get(sym, {}), p)
                if a.step > 1:
                    sg = {i: v for k, (i, v) in enumerate(sorted(sg.items())) if k % a.step == 0}
                out[sym] = sg
            return out
    else:
        base_p = loose_params(Params.from_live())

        def build_signals(p):
            return {s: precompute_signals(df, listing_map.get(s, {}), LIVE_FAITHFUL, p) for s, df in candle_map.items()}

    signals = build_signals(base_p)
    base = run(candle_map, listing_map, signals, base_p)
    if not a.anyday:
        tmp = {}
        for sym, df in candle_map.items():
            for i, sg in signals[sym].items():
                c = df["CLOSE"].iloc[max(0, i - 19):i + 1]; v = df["VOLUME"].iloc[max(0, i - 19):i + 1]
                tmp[(sym, i)] = float((c * v).mean() / 1e7)
        base["t20"] = [tmp.get((s, i), np.nan) for s, i in zip(base.symbol, base.signal_idx)]
        base = base[base.t20 >= 0.75].copy()
    key = ["symbol", "signal_idx"]
    print(f"baseline (live exits) on {len(base)} signals ({'any liquid IPO-day' if a.anyday else 'loosened breakout events'}): "
          f"mean net {base.net.mean():+.2f}%  PF {pf(base.net):.2f}  win {100 * (base.net > 0).mean():.1f}%  "
          f"avg days {base.days_held.mean():.1f}")
    n_var = len(VARIANTS) + (len(STOP_VARIANTS) if a.stops else 0)
    print(f"pre-registered variants: {n_var}  (Bonferroni one-sided threshold p < {0.05 / n_var:.4f})\n")

    def compare(label, axis, var):
        m = base.merge(var[key + ["net", "days_held"]], on=key, suffixes=("_b", "_v"))
        m["diff"] = m.net_v - m.net_b
        (lo, hi), pval = cluster_ci(m)
        yrs = m.groupby("year")["diff"].mean()
        n_changed = int((m["diff"].abs() > 1e-9).sum())
        return dict(label=label, axis=axis, n=len(m), changed=n_changed, mean_net=m.net_v.mean(), PF=pf(m.net_v),
                    win=100 * (m.net_v > 0).mean(), diff=m["diff"].mean(), lo=lo, hi=hi, p=pval,
                    yrs_pos=int((yrs > 0).sum()), yrs=len(yrs), days=m.days_held_v.mean())

    rows = []
    for label, axis, p_over, e_over in VARIANTS:
        p = replace(base_p, exit_overrides=tuple(e_over.items()), **p_over)
        rows.append(compare(label, axis, run(candle_map, listing_map, signals, p)))
    if a.stops:
        for label, p_over in STOP_VARIANTS:
            p = replace(base_p, **p_over)
            sg = build_signals(p)
            rows.append(compare(label, "stop", run(candle_map, listing_map, sg, p)))
    r = pd.DataFrame(rows)
    pd.set_option("display.width", 240)
    pd.set_option("display.max_colwidth", 44)
    print(r.sort_values("diff", ascending=False).round(3).to_string(index=False))
    r.to_csv("research_exits_results.csv", index=False)


if __name__ == "__main__":
    main()
