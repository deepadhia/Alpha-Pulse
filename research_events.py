# -*- coding: utf-8 -*-
"""
research_events.py -- signal-level event study (research tooling, read-only)

Builds one row per candidate breakout signal under LOOSENED gates, runs each through the
live-faithful exit engine, and attaches the features known at signal time plus market-state
features built from the IPO cohort itself (no external index needed).

  python research_events.py --cache data.pkl --out events.csv

The loosened-gate event set has thousands of rows where the production book has ~80 trades, which
is the only way to get enough statistical power to ask "which features actually predict outcomes".
Nothing here changes the live scanners.
"""

import argparse
import logging
import os
import pickle
from dataclasses import replace

import numpy as np
import pandas as pd

logging.disable(logging.CRITICAL)

from core.honest_backtest import LIVE_FAITHFUL, Params, simulate_events

FRICTION_PCT = 1.0     # round-trip cost assumption used for "net" outcomes (about Rs 14k positions)


def loose_params(base: Params) -> Params:
    return replace(base, min_vol_mult=1.0, min_volume=30000, max_extension_pct=8.0, min_close_loc=0.0,
                   min_post_confirm_pct=-99.0, min_leader_score=-99, turnover_floor_cr=0.0,
                   circuit_day_threshold=999, shelf_min_turnover_cr=0.0, min_risk_reward=0.0,
                   tier_b_enabled=False)


def market_state(candle_map, listing_map) -> pd.DataFrame:
    """Per-date IPO-cohort features: breadth, momentum, supply."""
    rows = []
    first_dates = []
    for sym, df in candle_map.items():
        li = listing_map.get(sym, {})
        ld_high = li.get("listing_day_high") or float(df["HIGH"].iloc[:3].max())
        ld = pd.Timestamp(df["DATE"].iloc[0]).normalize()
        first_dates.append(ld)
        close = df["CLOSE"].astype(float)
        d = pd.DataFrame({
            "date": pd.to_datetime(df["DATE"]).dt.normalize(),
            "age": (pd.to_datetime(df["DATE"]).dt.normalize() - ld).dt.days,
            "ret10": close / close.shift(10) - 1.0,
            "ret20": close / close.shift(20) - 1.0,
            "above_ld": (close > float(ld_high)).astype(float),
        })
        d = d[(d.age >= 3) & (d.age <= 90)]
        rows.append(d)
    allr = pd.concat(rows, ignore_index=True)
    g = allr.groupby("date")
    ms = pd.DataFrame({
        "mkt_n_active": g.size(),
        "mkt_med_ret10": g["ret10"].median() * 100.0,
        "mkt_med_ret20": g["ret20"].median() * 100.0,
        "mkt_frac_above_ld": g["above_ld"].mean(),
    })
    fd = pd.Series(1, index=pd.DatetimeIndex(sorted(first_dates))).rename("n")
    idx = ms.index
    supply = [int(((fd.index > d - pd.Timedelta(days=30)) & (fd.index <= d)).sum()) for d in idx]
    ms["mkt_supply30"] = supply
    # smooth the noisy cohort series with a 5-day trailing mean
    for c in ("mkt_med_ret10", "mkt_med_ret20", "mkt_frac_above_ld"):
        ms[c + "_5d"] = ms[c].rolling(5, min_periods=1).mean()
    return ms


def add_features(ev: pd.DataFrame, candle_map, listing_map, ms: pd.DataFrame) -> pd.DataFrame:
    feats = []
    for _, r in ev.iterrows():
        df = candle_map[r["symbol"]]
        i = int(r["signal_idx"])
        o = float(df["OPEN"].iat[i]); h = float(df["HIGH"].iat[i]); l = float(df["LOW"].iat[i])
        c = float(df["CLOSE"].iat[i]); v = float(df["VOLUME"].iat[i])
        pc = float(df["CLOSE"].iat[i - 1])
        rng = h - l
        t20 = (df["CLOSE"].iloc[max(0, i - 19):i + 1] * df["VOLUME"].iloc[max(0, i - 19):i + 1]).mean() / 1e7
        ranges = ((df["HIGH"] - df["LOW"]) / df["CLOSE"]).iloc[max(0, i - 9):i + 1]
        ld_high = float(r["ld_high"]); ld_low = float(r["ld_low"]) if r["ld_low"] else np.nan
        sig_date = pd.Timestamp(r["signal_date"]).normalize()
        feats.append(dict(
            close_loc=(c - l) / rng if rng > 0 else np.nan,
            upper_wick=(h - max(o, c)) / rng if rng > 0 else np.nan,
            gap_pct=(o / pc - 1.0) * 100.0,
            ret5=(c / float(df["CLOSE"].iat[max(0, i - 5)]) - 1.0) * 100.0,
            ret10=(c / float(df["CLOSE"].iat[max(0, i - 10)]) - 1.0) * 100.0,
            day_range_pct=rng / c * 100.0,
            atr10_pct=float(ranges.mean() * 100.0),
            turnover_cr=c * v / 1e7,
            turnover20_cr=float(t20),
            dist_ld_pct=(c / ld_high - 1.0) * 100.0,
            listing_range_pct=(ld_high - ld_low) / ld_high * 100.0 if ld_low == ld_low else np.nan,
            prior_vol_ratio=float(v / max(1.0, df["VOLUME"].iloc[max(0, i - 10):i].mean())),
            date=sig_date,
        ))
    f = pd.DataFrame(feats, index=ev.index)
    out = pd.concat([ev, f], axis=1)
    out = out.merge(ms, left_on="date", right_index=True, how="left")
    out["net"] = out["pnl_pct"] - FRICTION_PCT
    out["year"] = pd.to_datetime(out["entry_date"]).dt.year
    out["month"] = pd.to_datetime(out["entry_date"]).dt.month
    out["age"] = out["days_since_listing"]
    return out


def build(cache_path, out_path=None):
    with open(cache_path, "rb") as fh:
        listing_map, candle_map = pickle.load(fh)
    p = loose_params(Params.from_live())
    events = []
    for sym, df in candle_map.items():
        events.extend(simulate_events(df, sym, listing_map.get(sym, {}), LIVE_FAITHFUL, p))
    ev = pd.DataFrame([{k: v for k, v in e.items() if k != "path"} for e in events])
    ms = market_state(candle_map, listing_map)
    ev = add_features(ev, candle_map, listing_map, ms)
    if out_path:
        ev.to_csv(out_path, index=False)
    return ev


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    ev = build(a.cache, a.out)
    print(f"events: {len(ev)}  symbols: {ev.symbol.nunique()}  years: {sorted(ev.year.unique())}")
    print(ev.groupby("type").net.agg(["count", "mean", "median"]).round(2))
