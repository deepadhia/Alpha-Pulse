# -*- coding: utf-8 -*-
"""
research_trigger.py -- does the breakout TRIGGER add anything over buying an arbitrary IPO-day?

Every liquid IPO-day aged 3..60 calendar days is treated as a hypothetical entry (next-open fill,
live exit engine, same stop construction).  Days that the breakout engine would have fired on are
then compared with the days it would not, within the same age band and year, using a symbol-clustered
bootstrap.  If the trigger carries information, signal days must beat the baseline.

  python research_trigger.py --cache data.pkl --events events.csv
"""

import argparse
import logging
import pickle

import numpy as np
import pandas as pd

logging.disable(logging.CRITICAL)

from core.honest_backtest import (LIVE_FAITHFUL, Params, _listing_context, _stop_from_history,
                                   simulate_events)
from research_events import FRICTION_PCT


def any_day_signals(df, listing_info, p):
    ld_high, ld_low, list_date = _listing_context(df, listing_info)
    out = {}
    dates = pd.to_datetime(df["DATE"]).dt.normalize()
    for i in range(3, len(df)):
        age = int((dates.iat[i] - list_date).days)
        if age > p.max_age:
            break
        if age < 3:
            continue
        close = float(df["CLOSE"].iat[i])
        vol20 = float((df["CLOSE"].iloc[max(0, i - 19):i + 1] * df["VOLUME"].iloc[max(0, i - 19):i + 1]).mean()) / 1e7
        if close < p.min_entry_price or vol20 < 0.75:
            continue
        stop = _stop_from_history(df, i, close, p)
        risk = (close - stop) / close
        if risk <= 0 or risk > p.stop_cap_pct / 100.0:
            continue
        out[i] = dict(type="ANY_DAY", signal_price=close, stop=stop, risk_pct=risk, pivot=close,
                      max_chase=5.0, vol_ratio=0.0, ext_pct=0.0, rr=0.0, post_confirm=0.0, shelf_prng=None)
    return out


def pf(x):
    w, l = x[x > 0].sum(), -x[x < 0].sum()
    return w / l if l > 0 else np.inf


def cluster_diff(d, flag, B=4000, seed=9):
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--events", required=True)
    a = ap.parse_args()
    listing_map, candle_map = pickle.load(open(a.cache, "rb"))
    p = Params.from_live()
    rows = []
    for sym, df in candle_map.items():
        sg = any_day_signals(df, listing_map.get(sym, {}), p)
        if not sg:
            continue
        for e in simulate_events(df, sym, listing_map.get(sym, {}), LIVE_FAITHFUL, p, signals=sg):
            rows.append({k: v for k, v in e.items() if k not in ("path",)})
    allr = pd.DataFrame(rows)
    allr["net"] = allr.pnl_pct - FRICTION_PCT
    allr["year"] = pd.to_datetime(allr.entry_date).dt.year
    ev = pd.read_csv(a.events)
    keys_all = set(zip(ev.symbol, ev.signal_idx))
    keys_lb = set(zip(ev[ev.type == "LISTING_BREAKOUT"].symbol, ev[ev.type == "LISTING_BREAKOUT"].signal_idx))
    keys_sh = set(zip(ev[ev.type == "SHELF_BREAKOUT"].symbol, ev[ev.type == "SHELF_BREAKOUT"].signal_idx))
    allr["is_lb"] = [(s, i) in keys_lb for s, i in zip(allr.symbol, allr.signal_idx)]
    allr["is_sh"] = [(s, i) in keys_sh for s, i in zip(allr.symbol, allr.signal_idx)]
    allr["age_band"] = pd.cut(allr.days_since_listing, [2, 7, 14, 21, 30, 45, 60])
    print(f"hypothetical liquid IPO-day entries: {len(allr)}  symbols: {allr.symbol.nunique()}")
    print(f"baseline (any day): mean net {allr.net.mean():+.2f}%  median {allr.net.median():+.2f}  PF {pf(allr.net):.2f}  "
          f"win {100 * (allr.net > 0).mean():.1f}%")
    for label, flag in (("LISTING_BREAKOUT days", allr.is_lb), ("SHELF_BREAKOUT days", allr.is_sh)):
        d = allr
        diff, ci, pv = cluster_diff(d, flag)
        print(f"\n{label}: n={int(flag.sum())}  mean net {allr.net[flag].mean():+.2f}  PF {pf(allr.net[flag]):.2f} "
              f"| non-signal days n={int((~flag).sum())} mean {allr.net[~flag].mean():+.2f}  "
              f"| diff {diff:+.2f}  CI95 [{ci[0]:+.2f},{ci[1]:+.2f}]  P(diff<=0)={pv:.3f}")
        print("   by year (signal mean / non-signal mean / n signal):")
        for y, g in allr.groupby("year"):
            f = flag.loc[g.index]
            if f.sum() >= 5:
                print(f"     {y}: {g.net[f].mean():+6.2f} / {g.net[~f].mean():+6.2f} / {int(f.sum())}")
        print("   by age band (signal mean / non-signal mean / n signal):")
        for b, g in allr.groupby("age_band", observed=True):
            f = flag.loc[g.index]
            if f.sum() >= 5:
                print(f"     {str(b):>10}: {g.net[f].mean():+6.2f} / {g.net[~f].mean():+6.2f} / {int(f.sum())}")
    # live-gate subset approximated from event features
    live = ev[(ev.type == "LISTING_BREAKOUT") & (ev.vol_ratio >= 1.8) & (ev.close_loc >= 0.5) &
              (ev.post_confirm >= 1.5) & (ev.ext_pct <= 3.5) & (ev.turnover20_cr >= 0.75)]
    keys_live = set(zip(live.symbol, live.signal_idx))
    flag = pd.Series([(s, i) in keys_live for s, i in zip(allr.symbol, allr.signal_idx)], index=allr.index)
    diff, ci, pv = cluster_diff(allr, flag)
    print(f"\nLIVE-GATED breakout days (all five quality gates): n={int(flag.sum())} mean net {allr.net[flag].mean():+.2f} "
          f"PF {pf(allr.net[flag]):.2f}  vs non-signal {allr.net[~flag].mean():+.2f}  diff {diff:+.2f}  "
          f"CI95 [{ci[0]:+.2f},{ci[1]:+.2f}]  P(diff<=0)={pv:.3f}")


if __name__ == "__main__":
    main()
