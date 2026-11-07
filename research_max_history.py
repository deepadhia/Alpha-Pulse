# -*- coding: utf-8 -*-
"""
research_max_history.py -- policy P replay on the longest history available (2007-2026), same engine, same rules.

PRE-REGISTRATION (written 2026-11-07, before the panel cohort was replayed)
---------------------------------------------------------------------------
Question: does the listing-high breakout (policy P: resting stop-limit at listing high +1.5%, limit +2.5%,
live gates, close-based stop sold next open, no volume gate, flat 1.0% round-trip cost, open trades marked to
market) show the same net expectancy on IPO-like NSE listings of 2007-2022, a period the Atlas cache cannot
reach, as on the 2022-26 Atlas cohort?
  * Rule: NO parameter is changed or searched.  policy P exactly as in research_alpha_lab.py
    (--causal --no-vol-gate --base-slip 0.3 --base-cap 2.5 --exit-mode eod_alert).
  * Validation of the proxy universe BEFORE trusting it: replay the same panel-derived universe on 2022-05-27..2026
    and compare with the Atlas replay (true IPO list).  If the panel cohort does not reproduce the Atlas result
    within noise, the pre-2022 result is reported as unreliable.
  * Pass mark for "the edge generalises": pre-2022 closed-trade net mean > 0 with the 90% symbol-cluster interval
    above 0, positive in at least half of the years with >= 8 trades, and not driven by the top 3 trades.
Caveats stated in advance: the universe is a PROXY (first NSE row of a symbol; includes cross-listings and
relistings, excludes BSE-only listings); prices are raw bhavcopy with split/bonus/demerger factors applied inside
each window; volume gates and turnover floors use exchange-wide volume; earlier years have thinner IPO supply.

  .venv\\Scripts\\python.exe research_max_history.py --atlas data_20261107.pkl --panel-dir data_panel --out-dir reports/2026-11-07
(first build the CSVs with: python build_panel_ipo_universe.py --out-dir data_panel)
"""

import argparse
import logging
import os
import pickle
from dataclasses import replace

import numpy as np
import pandas as pd

logging.disable(logging.CRITICAL)

import research_alpha_lab as lab
from core.honest_backtest import LIVE_FAITHFUL, Params
from core.execution_model import CostModel
from core.portfolio_sim import PortfolioConfig, run_portfolio

ATLAS_START = pd.Timestamp("2022-05-27")


def load_panel(panel_dir):
    pk = os.path.join(panel_dir, "panel.pkl")
    if os.path.exists(pk):
        return pickle.load(open(pk, "rb"))
    rows = pd.read_csv(os.path.join(panel_dir, "panel_rows.csv.gz"), parse_dates=["date"])
    lst = pd.read_csv(os.path.join(panel_dir, "panel_listings.csv"), parse_dates=["listing_date"])
    listing_map, candle_map = {}, {}
    for s, g in rows.groupby("symbol", sort=False):
        g = g.sort_values("date")
        if len(g) < 10:
            continue
        df = pd.DataFrame({"DATE": g.date.values, "OPEN": g.open.values, "HIGH": g.high.values, "LOW": g.low.values,
                           "CLOSE": g.close.values, "VOLUME": g.volume.values})
        df["LTP"] = df["CLOSE"]
        candle_map[s] = df.reset_index(drop=True)
    for r in lst.itertuples():
        if r.symbol in candle_map:
            listing_map[r.symbol] = dict(symbol=r.symbol, listing_date=r.listing_date, listing_day_high=float(r.ld_high),
                                         listing_day_low=float(r.ld_low), listing_day_close=float(r.ld_close),
                                         listing_day_volume=float(r.ld_volume), is_nse_addition=False,
                                         bse_liquidity_passed=True)
    pickle.dump((listing_map, candle_map), open(pk, "wb"))
    return listing_map, candle_map


def subset(listing_map, candle_map, lo=None, hi=None):
    lm, cm = {}, {}
    for s, li in listing_map.items():
        ld = pd.Timestamp(li["listing_date"])
        ld = ld.tz_localize(None) if ld.tzinfo else ld
        if (lo is None or ld >= lo) and (hi is None or ld < hi) and s in candle_map:
            lm[s], cm[s] = li, candle_map[s]
    return lm, cm


def pf_of(x):
    x = np.asarray(x, float)
    w, l = x[x > 0].sum(), -x[x < 0].sum()
    return float(w / l) if l > 0 else float("inf")


def pf3(net, is_open):
    """PF closed-only, incl. open at MTM, closed excluding the 3 best trades."""
    net = np.asarray(net, float)
    is_open = np.asarray(is_open, bool)
    cl = net[~is_open]
    ex3 = np.sort(cl)[:-3] if len(cl) > 3 else cl
    return pf_of(cl), pf_of(net), pf_of(ex3)


JUMP_UP, JUMP_DOWN = 1.6, 0.6


def flag_jumps(d, cmap):
    """True for trades whose entry-to-exit window (plus one bar either side) contains a one-day CLOSE move above +60% or
    below -40%.  On NSE a stock cannot rise more than 20% in a day outside a listing day, so these are corporate-action
    artifacts (consolidations, missed or wrong adjustments, relistings) or real crashes; either way they dominate means
    (one +2,900% 'winner' moved an S3 mean by 11pp).  Rule written 2026-11-07 after the S3 proxy check failed."""
    up, dn = [], []
    for r in d.itertuples():
        c = cmap[r.symbol]["CLOSE"].values
        i0 = int(r.entry_idx)
        i1 = int(r.exit_idx) if not pd.isna(r.exit_idx) else len(c) - 1
        seg = c[max(i0 - 1, 0): i1 + 2]
        ratio = seg[1:] / seg[:-1] if len(seg) > 1 else np.array([1.0])
        up.append(bool(ratio.max() > JUMP_UP))
        dn.append(bool(ratio.min() < JUMP_DOWN))
    return np.array(up, bool), np.array(dn, bool)


def headline(label, d):
    cl = d[~d.open]
    pc, pa, px = pf3(d.net.values, d.open.values)
    m, lo, hi = lab.cluster_boot(cl.net.values, cl.symbol.values)
    print(f"{label:<44} signals-N={len(d):4d} (open {int(d.open.sum())}) win={100 * (cl.net > 0).mean():4.1f}% "
          f"closed net={m:+5.2f}% CI90=[{lo:+5.1f},{hi:+5.1f}] med={cl.net.median():+5.2f}% | PF closed={pc:4.2f} incl.open={pa:4.2f} exTop3={px:4.2f}")


def run_cohort(label, lm, cm, p, out_dir=None):
    trades = lab.baseline_trades(cm, lm, p, slip=0.3, fill_cap_pct=2.5)
    if not trades:
        print(f"{label:<44} no trades")
        return pd.DataFrame(), trades
    d = pd.DataFrame([{k: v for k, v in t.items() if k != "path"} for t in trades])
    d["jump_up"], d["jump_dn"] = flag_jumps(d, cm)
    d["net"] = d.pnl_pct - lab.COST
    d["entry_ts"] = pd.to_datetime(d.entry_date)
    d["year"] = d.entry_ts.dt.year
    headline(label, d)
    if d.jump_up.any():
        print(f"   up-jump trades (> +60% in one day, cannot be real): "
              f"{', '.join(d[d.jump_up].symbol.astype(str) + ' ' + d[d.jump_up].pnl_pct.round(0).astype(int).astype(str) + '%')}")
        headline("   -> same, up-jump trades removed", d[~d.jump_up])
    if d.jump_dn.any():
        print(f"   down-jump trades (< -40% in one day: real crash or unadjusted split, kept in the headline): "
              f"{', '.join(d[d.jump_dn].symbol.astype(str) + ' ' + d[d.jump_dn].pnl_pct.round(0).astype(int).astype(str) + '%')}")
    if out_dir:
        d.to_csv(os.path.join(out_dir, f"maxhist_trades_{label.split()[0].lower()}.csv"), index=False)
    return d, trades


def by_year(d):
    rows = []
    for y, g in d.groupby("year"):
        c = g[~g.open]
        rows.append((int(y), len(g), g.net.mean(), pf_of(g.net.values), 100 * (c.net > 0).mean() if len(c) else np.nan))
    print("   year   N   mean-net%   PF   win%")
    for y, n, mn, pf, w in rows:
        print(f"   {y}  {n:3d}  {mn:+8.2f}  {pf:5.2f}  {w:5.1f}")
    return rows


def account(label, trades, capital=100000.0):
    cm_cost = CostModel(entry_slippage_pct=0.0, exit_slippage_pct=0.3)
    for soft, hard in ((10, 10),):
        pc = PortfolioConfig(capital=capital, soft_cap=soft, hard_cap=hard, cap_policy="flat", cost=cm_cost)
        r = run_portfolio(trades, pc)["summary"]
        if not r.get("trades_taken"):
            print(f"   {label}: no trades taken")
            continue
        calmar = r["cagr_pct"] / abs(r["max_drawdown_pct"]) if r.get("max_drawdown_pct") else float("nan")
        print(f"   {label} {soft}x{100 // hard}% slots: taken {r['trades_taken']}/{r['signals']} total {r['total_return_pct']:+.1f}% "
              f"CAGR {r['cagr_pct']:+.1f}% maxDD {r['max_drawdown_pct']:.1f}% Calmar {calmar:.2f} PF {r['profit_factor']:.2f} "
              f"worst {r['worst_trade_pct']:+.1f}% top3-share {r['top3_share_of_profit_pct']:.0f}%")
        print("     by year %:", {k: round(v, 1) for k, v in r.get("by_year_pct", {}).items()})


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--atlas", required=True)
    ap.add_argument("--panel-dir", default="data_panel")
    ap.add_argument("--out-dir", default=None)
    args = ap.parse_args(argv)
    lab.POP.update(causal=True, vol_gate=False, slip=0.3, cap=2.5)
    lab.CFG = replace(LIVE_FAITHFUL, exit_fill="eod_next_open")
    p = Params()

    a_lm, a_cm = pickle.load(open(args.atlas, "rb"))
    p_lm, p_cm = load_panel(args.panel_dir)
    print(f"Atlas: {len(a_cm)} symbols | panel proxy: {len(p_cm)} symbols\n")
    if args.out_dir:
        os.makedirs(args.out_dir, exist_ok=True)

    # universe overlap 2022-05-27+ (is the proxy list the real list?)
    a_set = {s for s, li in a_lm.items() if s in a_cm and pd.Timestamp(li["listing_date"]).tz_localize(None) >= ATLAS_START}
    pp_lm, pp_cm = subset(p_lm, p_cm, lo=ATLAS_START)
    p_set = set(pp_cm)
    print(f"2022-05-27+ universe check: Atlas {len(a_set)} listings, panel proxy {len(p_set)}, overlap {len(a_set & p_set)} "
          f"(proxy precision {100 * len(a_set & p_set) / max(1, len(p_set)):.0f}%, share of Atlas covered {100 * len(a_set & p_set) / max(1, len(a_set)):.0f}%)\n")

    print("=" * 130)
    print("POLICY P REPLAY (resting stop-limit, close-based stop sold next open, no volume gate, 1.0% round-trip cost)")
    print("=" * 130)
    d_atlas, t_atlas = run_cohort("ATLAS 2022-05..2026-11 (true IPO list, Upstox)", *subset(a_lm, a_cm, lo=ATLAS_START), p, args.out_dir)
    by_year(d_atlas)
    print()
    d_pp, t_pp = run_cohort("PANELPROXY 2022-05..2026-10 (validation of proxy)", pp_lm, pp_cm, p, args.out_dir)
    by_year(d_pp)
    print()
    pre_lm, pre_cm = subset(p_lm, p_cm, lo=pd.Timestamp("2006-07-01"), hi=ATLAS_START)
    d_pre, t_pre = run_cohort("PANELPRE 2006-07..2022-05 (older history)", pre_lm, pre_cm, p, args.out_dir)
    by_year(d_pre)
    print()

    # split the old era in two, to see whether it is one regime
    for lab_, lo, hi in (("PANELPRE-A 2006-07..2013", "2006-07-01", "2014-01-01"), ("PANELPRE-B 2014..2022-05", "2014-01-01", "2022-05-27")):
        lm_, cm_ = subset(p_lm, p_cm, lo=pd.Timestamp(lo), hi=pd.Timestamp(hi))
        run_cohort(lab_, lm_, cm_, p)

    # all years together: panel pre-2022 + Atlas 2022-05+
    allt = list(t_pre) + list(t_atlas)
    d_all = pd.concat([d_pre, d_atlas], ignore_index=True)
    print()
    pc, pa, px = pf3(d_all.net.values, d_all.open.values)
    cl = d_all[~d_all.open]
    m, lo, hi = lab.cluster_boot(cl.net.values, cl.symbol.values)
    print(f"{'COMBINED 2006-2026 (panel pre-2022 + Atlas)':<44} N={len(d_all)} closed net={m:+.2f}% CI90=[{lo:+.1f},{hi:+.1f}] "
          f"win={100 * (cl.net > 0).mean():.1f}% | PF closed={pc:.2f} incl.open={pa:.2f} exTop3={px:.2f}")
    print("\nACCOUNT LEVEL (Rs 1,00,000, Indian delivery charges + 0.3% exit slippage, 10 x 10% slots, flat sizing)")
    account("pre-2022 panel", t_pre)
    account("Atlas 2022-26", t_atlas)
    account("combined 2006-26", allt)
    if args.out_dir:
        d_all.to_csv(os.path.join(args.out_dir, "maxhist_trades_combined.csv"), index=False)


if __name__ == "__main__":
    main()
