# -*- coding: utf-8 -*-
"""
research_minute_coverage.py -- coverage report for data_minute/ (output of fetch_minute_bars.py).

Per symbol, within the engine age window (listing date + 60 calendar days, capped at the latest candle):
  sessions with minute data vs sessions in the daily candles, share of sessions with >= 300 minutes, minute-vs-daily volume ratio.
Summaries by stage and by listing year, a list of problem symbols, and a check on the policy-P trade days.
Read-only.

  .venv\\Scripts\\python.exe research_minute_coverage.py --cache data.pkl --trades reports/2026-10-17/gate_replay_V0.csv
"""
import argparse
import os
import pickle

import numpy as np
import pandas as pd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--trades", required=True)
    ap.add_argument("--dir", default="data_minute")
    a = ap.parse_args()
    with open(a.cache, "rb") as fh:
        listing_map, candle_map = pickle.load(fh)
    man = pd.read_csv(os.path.join(a.dir, "manifest.csv")).drop_duplicates("symbol", keep="last")
    print("manifest status counts by stage:")
    print(man.groupby(["stage", "status"]).size().unstack(fill_value=0).to_string())
    rows = []
    for r in man.itertuples():
        if r.status != "ok":
            continue
        li = listing_map.get(r.symbol)
        ld = pd.Timestamp(li["listing_date"]).tz_localize(None)
        m = pd.read_pickle(os.path.join(a.dir, f"{r.symbol}_1m.pkl"))
        d = candle_map.get(r.symbol)
        rec = dict(symbol=r.symbol, stage=r.stage, year=ld.year, rows=len(m))
        if len(m):
            m["ts"] = pd.to_datetime(m.ts)
            m["day"] = m.ts.dt.strftime("%Y-%m-%d")
            per = m.groupby("day").agg(minutes=("ts", "size"), vol=("volume", "sum"))
        else:
            per = pd.DataFrame(columns=["minutes", "vol"])
        if d is not None and len(d):
            dd = d.copy()
            dd["day"] = pd.to_datetime(dd.DATE).dt.tz_localize(None)
            end = min(ld + pd.Timedelta(days=60), dd.day.max())
            dd = dd[(dd.day >= ld) & (dd.day <= end)]
            dd["dkey"] = dd.day.dt.strftime("%Y-%m-%d")
            rec["daily_sessions"] = len(dd)
            have = dd.dkey.isin(per.index)
            rec["covered_sessions"] = int(have.sum())
            j = dd[have].set_index("dkey").join(per)
            rec["full_sessions"] = int((j.minutes >= 300).sum())
            ratio = (j.vol / j.VOLUME.replace(0, np.nan)).dropna()
            rec["vol_ratio_median"] = float(ratio.median()) if len(ratio) else np.nan
            rec["vol_ratio_bad"] = int(((ratio < 0.9) | (ratio > 1.1)).sum()) if len(ratio) else 0
        rows.append(rec)
    c = pd.DataFrame(rows)
    c["cov"] = c.covered_sessions / c.daily_sessions
    c["full_share"] = c.full_sessions / c.daily_sessions
    pd.set_option("display.width", 200)
    print("\nCoverage of daily sessions by minute data (symbols with daily candles):")
    g = c.dropna(subset=["daily_sessions"]).groupby("stage").agg(symbols=("symbol", "size"), rows=("rows", "sum"), sessions=("daily_sessions", "sum"),
                                                                  covered=("covered_sessions", "sum"), full=("full_sessions", "sum"),
                                                                  vol_ratio_median=("vol_ratio_median", "median"), bad_vol_days=("vol_ratio_bad", "sum"))
    g["covered_pct"] = (100 * g.covered / g.sessions).round(1)
    g["full_pct"] = (100 * g.full / g.sessions).round(1)
    print(g.to_string())
    print("\nBy listing year (all stages):")
    gy = c.dropna(subset=["daily_sessions"]).groupby("year").agg(symbols=("symbol", "size"), sessions=("daily_sessions", "sum"), covered=("covered_sessions", "sum"),
                                                                  full=("full_sessions", "sum"), bad_vol_days=("vol_ratio_bad", "sum"), vol_ratio_median=("vol_ratio_median", "median"))
    gy["covered_pct"] = (100 * gy.covered / gy.sessions).round(1)
    gy["full_pct"] = (100 * gy.full / gy.sessions).round(1)
    print(gy.to_string())
    prob = c[(c["cov"] < 0.95) | (c.vol_ratio_bad > 2)].sort_values("cov")
    print(f"\nProblem symbols (covered < 95% of daily sessions or > 2 days with volume ratio outside 0.9-1.1): {len(prob)}")
    print(prob.head(25)[["symbol", "stage", "year", "daily_sessions", "covered_sessions", "vol_ratio_bad", "vol_ratio_median"]].to_string(index=False))
    bad = man[man.status != "ok"]
    print(f"\nSymbols not fetched: {len(bad)}")
    print(bad.head(30)[["stage", "symbol", "status", "http", "error"]].to_string(index=False))
    empties = c[c.rows == 0]
    print(f"\nSymbols fetched OK but with zero rows: {len(empties)}", list(empties.symbol)[:30])

    t = pd.read_csv(a.trades)
    t["entry_date"] = pd.to_datetime(t.entry_date).dt.strftime("%Y-%m-%d")
    have, miss = 0, []
    for r in t.itertuples():
        pth = os.path.join(a.dir, f"{r.symbol}_1m.pkl")
        if not os.path.exists(pth):
            miss.append((r.symbol, r.entry_date, "no file"))
            continue
        m = pd.read_pickle(pth)
        if len(m) and (pd.to_datetime(m.ts).dt.strftime("%Y-%m-%d") == r.entry_date).any():
            have += 1
        else:
            miss.append((r.symbol, r.entry_date, "no minutes that day"))
    print(f"\nPolicy-P trades with minute bars on the entry day: {have} of {len(t)}")
    print("missing:", miss[:20])
    c.to_csv(os.path.join(a.dir, "coverage_by_symbol.csv"), index=False)


if __name__ == "__main__":
    main()
