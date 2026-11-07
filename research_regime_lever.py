# -*- coding: utf-8 -*-
"""
research_regime_lever.py -- is there a market state in which policy P works properly, so that it can be traded only then and sat out otherwise?
(added 2026-11-07, owner request)

PRE-REGISTRATION (written before running; thresholds are round numbers, NOT fitted)
  State at the order date uses only information up to the PRIOR close.  ON/OFF rules:
    R0  Nifty 50 above its 200-day average (the rule already seen in LEARNINGS 22.5; shown for reference)
    R1  broad breadth: >= 50% of liquid NSE stocks above their own 200-day average
    R2  >= 50% of liquid stocks above their 50-day average
    R3  median 63-day return of liquid stocks > 0
    R4  >= 2% of liquid stocks at a 252-day high (new-high participation)
  (breadth built from the 20-year panel by build_market_breadth.py; liquid = 60-day mean turnover >= Rs 1 Cr.)
  Tests, both eras (2007-09..2022-05 panel proxy, 2022-05..2026-11 Atlas), on the X1 book and on the live-exit book:
    (a) trade level: mean net of ON entries versus OFF entries (symbol-cluster 90% interval of the difference) and the share of entries that are ON;
    (b) account level (Rs 1 lakh, 10 slots): the book restricted to ON entries versus the unfiltered book.
  A rule is a CANDIDATE only if in BOTH eras ON beats OFF at trade level (difference > 0) AND the filtered account has higher Calmar AND higher
  CAGR than the unfiltered one with max drawdown no worse than 2pp, on BOTH books.  5 rules x 2 books: about one false pass is expected; a pass is a
  candidate for a PAPER shadow only.  Nothing is adopted here.

  .venv\\Scripts\\python.exe research_regime_lever.py --atlas data_20261107.pkl --panel-dir data_panel_w1200
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
import research_outlier_anatomy as oa
from core.honest_backtest import LIVE_FAITHFUL, Params
from core.setup3 import NO_TIME_EXITS
from research_setup2 import diff_cluster_boot

START = pd.Timestamp("2007-09-17")


def state_at(series, dates):
    idx = series.index
    out = []
    for d in dates:
        k = idx.searchsorted(pd.Timestamp(d), side="left") - 1      # last row strictly before the order date
        out.append(series.iloc[k] if k >= 0 else np.nan)
    return np.array(out, float)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--atlas", required=True)
    ap.add_argument("--panel-dir", default="data_panel_w1200")
    a = ap.parse_args()
    lab.POP.update(causal=True, vol_gate=False, slip=0.3, cap=2.5)
    lab.CFG = replace(LIVE_FAITHFUL, exit_fill="eod_next_open")
    p = Params()
    books = {"live": p, "X1": replace(p, exit_overrides=NO_TIME_EXITS)}
    a_lm, a_cm = pickle.load(open(a.atlas, "rb"))
    p_lm, p_cm = mh.load_panel(a.panel_dir)
    br = pd.read_csv(a.panel_dir + "/breadth.csv", index_col=0, parse_dates=True)
    nifty = pd.read_csv(oa.NIFTY, parse_dates=["Date"]).set_index("Date")["Close"]
    n200 = (nifty > nifty.rolling(200).mean()).astype(float).where(nifty.rolling(200).mean().notna())
    rules = {
        "R0 Nifty50 > 200DMA": (n200, lambda x: x >= 0.5),
        "R1 >=50% above 200DMA": (br.pct_above200, lambda x: x >= 50),
        "R2 >=50% above 50DMA": (br.pct_above50, lambda x: x >= 50),
        "R3 median 63d return > 0": (br.med_ret63, lambda x: x > 0),
        "R4 >=2% at 252d high": (br.pct_high252, lambda x: x >= 2),
    }
    eras = {"A 2007-09..2022-05": mh.subset(p_lm, p_cm, lo=pd.Timestamp("2006-07-01"), hi=mh.ATLAS_START),
            "B 2022-05..2026-11": mh.subset(a_lm, a_cm, lo=mh.ATLAS_START)}
    trades = {}
    for era, (lm, cm) in eras.items():
        for bn, pp in books.items():
            tr = lab.baseline_trades(cm, lm, pp, slip=0.3, fill_cap_pct=2.5)
            if era.startswith("A"):
                tr = [t for t in tr if pd.Timestamp(t["entry_date"]) >= START]
            d = pd.DataFrame([{k: v for k, v in t.items() if k != "path"} for t in tr])
            d["net"] = d.pnl_pct - lab.COST
            trades[(era, bn)] = (tr, d)
        print(f"{era}: live book {len(trades[(era, 'live')][0])} trades, X1 book {len(trades[(era, 'X1')][0])} trades")
    ea, eb = list(eras)
    from core.portfolio_sim import PortfolioConfig, run_portfolio
    from core.execution_model import CostModel
    cost = CostModel(entry_slippage_pct=0.0, exit_slippage_pct=0.3)

    def account(tr):
        s = run_portfolio(tr, PortfolioConfig(capital=100000.0, soft_cap=10, hard_cap=10, cap_policy="flat", cost=cost, priority="first"))["summary"]
        return s["cagr_pct"], s["max_drawdown_pct"], s["cagr_pct"] / abs(s["max_drawdown_pct"]), s["trades_taken"]

    verdict = {}
    for rname, (ser, on_fn) in rules.items():
        print(f"\n=== {rname} ===")
        ok_all = True
        for bn in books:
            line = []
            ok = True
            for era in eras:
                tr, d = trades[(era, bn)]
                st = state_at(ser, d.entry_date)
                on = np.array([(not np.isnan(x)) and bool(on_fn(x)) for x in st])
                unknown = np.isnan(st)
                on_eff = on | unknown                        # no state before the series starts: keep the trade
                ton, toff = d[on_eff], d[~on_eff]
                dm, dlo, dhi = diff_cluster_boot(ton.net.values, ton.symbol.values, toff.net.values, toff.symbol.values) if len(toff) >= 5 else (np.nan, np.nan, np.nan)
                base = account(tr)
                filt = account([t for t, k in zip(tr, on_eff) if k])
                good = (not np.isnan(dm)) and dm > 0 and filt[2] > base[2] and filt[0] > base[0] and filt[1] >= base[1] - 2.0
                ok &= good
                line.append(f"{era[0]}: ON {len(ton)}/{len(d)} net {ton.net.mean():+.1f} vs OFF {toff.net.mean() if len(toff) else float('nan'):+.1f} diff {dm:+.1f}[{dlo:+.1f},{dhi:+.1f}] | "
                            f"CAGR {base[0]:+.1f}->{filt[0]:+.1f} DD {base[1]:.1f}->{filt[1]:.1f} Calmar {base[2]:.2f}->{filt[2]:.2f}")
            ok_all &= ok
            print(f"  {bn:<5} " + "\n        ".join(line) + f"  -> {'pass' if ok else 'no'}")
            verdict[(rname, bn)] = ok
        print(f"  {'CANDIDATE (both books, both eras)' if ok_all else 'not adopted'}")


if __name__ == "__main__":
    main()
