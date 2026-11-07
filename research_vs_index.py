# -*- coding: utf-8 -*-
"""
research_vs_index.py -- policy P (live exits) and P with X1 exits against the Nifty 50 PRICE index on identical dates (added 2026-11-07).

Account = Rs 1 lakh, 10 x 10% slots, charges + 0.3% exit slip, idle cash earns 0% (state both: an idle-cash yield is shown separately as an
ESTIMATE).  Nifty = price index only (no dividends, about +1.3% a year more as a total return).  Window starts 2007-09-17 (first Nifty row).
Replay tier, proxy IPO list before 2022-05-27; not a forecast.

  .venv\\Scripts\\python.exe research_vs_index.py --atlas data_20261107.pkl --panel-dir data_panel_w1200
"""
import argparse
import logging
import pickle
from dataclasses import replace

import numpy as np
import pandas as pd

logging.disable(logging.CRITICAL)
import research_alpha_lab as lab
import research_max_history as mh
from core.execution_model import CostModel
from core.honest_backtest import LIVE_FAITHFUL, Params
from core.portfolio_sim import PortfolioConfig, run_portfolio
from core.setup3 import NO_TIME_EXITS

NIFTY = r"D:\Personal Projects\Finance-Guru\research_archive_2026-10\studies\index_sleeve_2026-10\data\nifty50.csv"
COST = CostModel(entry_slippage_pct=0.0, exit_slippage_pct=0.3)


def stats(nav: pd.Series, lo, hi):
    s = nav.loc[lo:hi].dropna()
    yrs = (s.index[-1] - s.index[0]).days / 365.25
    cagr = ((s.iloc[-1] / s.iloc[0]) ** (1 / yrs) - 1) * 100
    dd = ((s / s.cummax()) - 1).min() * 100
    return cagr, dd, s.iloc[-1] / s.iloc[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--atlas", required=True)
    ap.add_argument("--panel-dir", default="data_panel_w1200")
    a = ap.parse_args()
    lab.POP.update(causal=True, vol_gate=False, slip=0.3, cap=2.5)
    lab.CFG = replace(LIVE_FAITHFUL, exit_fill="eod_next_open")
    p = Params()
    a_lm, a_cm = pickle.load(open(a.atlas, "rb"))
    p_lm, p_cm = mh.load_panel(a.panel_dir)
    pre = mh.subset(p_lm, p_cm, lo=pd.Timestamp("2006-07-01"), hi=mh.ATLAS_START)
    at = mh.subset(a_lm, a_cm, lo=mh.ATLAS_START)
    start = pd.Timestamp("2007-09-17")
    nifty = pd.read_csv(NIFTY, parse_dates=["Date"]).set_index("Date")["Close"]
    end = nifty.index[-1]
    navs = {}
    for name, pp in (("P live exits", p), ("P + X1 exits", replace(p, exit_overrides=NO_TIME_EXITS))):
        trades = []
        for lm, cm in (pre[0:2], at[0:2]):
            trades += lab.baseline_trades(cm, lm, pp, slip=0.3, fill_cap_pct=2.5)
        trades = [t for t in trades if pd.Timestamp(t["entry_date"]) >= start]
        r = run_portfolio(trades, PortfolioConfig(capital=100000.0, soft_cap=10, hard_cap=10, cap_policy="flat", cost=COST, priority="first"))
        eq = r["equity"]
        nav = eq["nav"] if "nav" in eq.columns else eq.iloc[:, 0]
        nav.index = pd.to_datetime(eq.index if "date" not in eq.columns else eq["date"])
        nav = nav.reindex(nifty.loc[start:end].index).ffill().fillna(100000.0)
        navs[name] = nav
        print(f"{name}: {len(trades)} signals from {start.date()}, equity columns {list(eq.columns)}")
    navs["Nifty 50 price"] = nifty.loc[start:end] / nifty.loc[start] * 100000.0
    cuts = (("2007-09 .. 2026-10 (19 yrs)", start, end), ("2007-09 .. 2022-05 (14.7 yrs, holdout era)", start, pd.Timestamp("2022-05-26")),
            ("2022-05 .. 2026-10 (4.4 yrs)", pd.Timestamp("2022-05-27"), end))
    print()
    for lab_, lo, hi in cuts:
        print(f"== {lab_}")
        base_cagr = None
        for name, nav in navs.items():
            c, dd, mult = stats(nav, lo, hi)
            print(f"   {name:<16} CAGR {c:+6.1f}%  maxDD {dd:6.1f}%  Calmar {c / abs(dd):4.2f}  growth x{mult:4.2f}")
        # idle-cash estimate not simulated: say so
    ann = {}
    for name, nav in navs.items():
        r = nav.pct_change().dropna()
        ann[name] = r.groupby(r.index.year).apply(lambda x: (1 + x).prod() - 1) * 100
    tab = pd.DataFrame(ann).round(1)
    print("\ncalendar-year returns %:")
    print(tab.to_string())
    pos = (tab["P + X1 exits"] > tab["Nifty 50 price"]).sum()
    pos2 = (tab["P live exits"] > tab["Nifty 50 price"]).sum()
    print(f"\nyears P live beat Nifty price: {pos2} of {len(tab)}; P + X1: {pos} of {len(tab)}")
    cor = tab.corr().round(2)
    print("\ncorrelation of yearly returns:\n" + cor.to_string())


if __name__ == "__main__":
    main()
