# -*- coding: utf-8 -*-
"""
build_market_breadth.py -- daily broad-market breadth from the 20-year NSE panel (added 2026-11-07), for research_regime_lever.py.

For every date: among LIQUID stocks (60-day mean turnover >= Rs 1 Cr, at least 200 sessions of history) the share above their own 200-day and
50-day average, the share at a 252-day high, and the median 63-day return.  Raw bhavcopy prices are split-unadjusted, so any single-day move
beyond +/-45% is treated as a corporate action (return set to 0) before the price path is rebuilt.  Values are shifted by one day by the
consumer (only information up to the prior close).  System python (pyarrow).

  python build_market_breadth.py --out data_panel_w1200/breadth.csv
"""
import argparse

import numpy as np
import pandas as pd

PANEL = r"D:\Personal Projects\Learning-Lab\external\nse_archive\clean_panel.parquet"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data_panel_w1200/breadth.csv")
    a = ap.parse_args()
    df = pd.read_parquet(PANEL, columns=["symbol", "date", "series", "close", "turnover"])
    df = df[df.series.astype(str).isin(["EQ", "BE"])].drop_duplicates(["symbol", "date"])
    close = df.pivot(index="date", columns="symbol", values="close").sort_index()
    turn = df.pivot(index="date", columns="symbol", values="turnover").sort_index() / 1e7
    ret = close.pct_change(fill_method=None)
    ret = ret.where(ret.abs() <= 0.45, 0.0)
    adj = (1 + ret.fillna(0.0)).cumprod().where(close.notna())
    n_hist = close.notna().cumsum()
    liquid = (turn.rolling(60, min_periods=20).mean() >= 1.0) & (n_hist >= 200)
    above200 = (adj > adj.rolling(200, min_periods=150).mean()) & liquid
    above50 = (adj > adj.rolling(50, min_periods=40).mean()) & liquid
    high252 = (adj >= adj.rolling(252, min_periods=150).max()) & liquid
    r63 = adj / adj.shift(63) - 1
    n = liquid.sum(axis=1)
    out = pd.DataFrame({
        "n_liquid": n,
        "pct_above200": above200.sum(axis=1) / n.replace(0, np.nan) * 100,
        "pct_above50": above50.sum(axis=1) / n.replace(0, np.nan) * 100,
        "pct_high252": high252.sum(axis=1) / n.replace(0, np.nan) * 100,
        "med_ret63": r63.where(liquid).median(axis=1) * 100,
    })
    out = out[out.n_liquid >= 100]
    out.to_csv(a.out)
    print(out.describe().round(1).to_string())
    print("rows", len(out), out.index.min(), out.index.max())


if __name__ == "__main__":
    main()
