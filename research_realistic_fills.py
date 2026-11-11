# -*- coding: utf-8 -*-
"""
research_realistic_fills.py -- how much of policy P's replay edge survives realistic fills?  (research, tier REPLAY; nothing live changes)

Takes the SAME policy-P trades as the standing replay (core.px1_shadow.live_entries: resting stop-limit entries, trigger +1.5%, limit +2.5%, no
volume gate, causal, close-based stop sold at the next open, flat 1.0% round trip + 0.3% entry slip) and re-prices each one with
core.realistic_friction (Learning-Lab style liquidity-tiered spread + sqrt impact, tick rounding, limit check after the spread, upper-circuit entry
block, lower-circuit exit deferral, statutory charges on the actual whole-share value).  Paired by trade; symbol-cluster bootstrap.

PRE-REGISTERED before the first run (do not edit after seeing numbers): primary scenario = capital Rs 1,00,000 (10 slots of 10%), cost-model mult 1.0,
broker charges as core.execution_model.CostModel defaults.  Stress grid = capital {1,00,000; 50,000} x mult {1.0, 1.5, 2.0} x brokerage {Rs 20/order, Rs 0}.
The spread tiers, impact coefficient and the 4.5% lock heuristic are ASSUMPTIONS (ported, not measured); no parameter is tuned here.

  .venv/Scripts/python.exe research_realistic_fills.py --cache data_20261107.pkl --out reports/2026-11-11/realistic_fills.txt
"""

import argparse
import os
import pickle
import sys
from dataclasses import replace

import numpy as np
import pandas as pd

from core.execution_model import CostModel
from core.honest_backtest import _listing_context
from core.px1_shadow import boot_mean_ci, live_entries, profit_factor
from core.realistic_friction import reprice_trade

REPLAY_COST_PCT = 1.0
SLOTS = 10


def pf3(net, is_open):
    """Profit factor three ways (AGENTS rule 15): closed only, incl. open at MTM, closed excluding the 3 best."""
    net, is_open = np.asarray(net, float), np.asarray(is_open, bool)
    closed = net[~is_open]
    s = np.sort(closed)[::-1]
    return profit_factor(closed) if len(closed) else float("nan"), profit_factor(net) if len(net) else float("nan"), \
        (profit_factor(s[3:]) if len(s) > 3 else float("nan"))


def summarize(label, net, is_open, groups):
    net = np.asarray(net, float)
    if len(net) == 0:
        return f"{label:<44} no trades"
    lo, hi = boot_mean_ci(net, groups=groups)
    c, a, e = pf3(net, is_open)
    return (f"{label:<44} N={len(net):3d} net={net.mean():+6.2f}% CI90=[{lo:+5.1f},{hi:+5.1f}] med={np.median(net):+6.2f}% "
            f"win={100 * (net > 0).mean():4.1f}% PF closed/inclOpen/exTop3 = {c:4.2f}/{a:4.2f}/{e:4.2f}")


def build_trades(candle_map, listing_map):
    rows = []
    for sym, df in candle_map.items():
        li = listing_map.get(sym, {})
        if len(df) < 15:
            continue
        ld_high = _listing_context(df, li)[0]
        for t in live_entries(df, sym, li):
            t = dict(t)
            t["_sym"], t["_ld_high"] = sym, float(ld_high)
            rows.append(t)
    return rows


def run_scenario(trades, candle_map, capital, mult, brokerage, cost_base=CostModel()):
    cost = replace(cost_base, brokerage_per_order=brokerage)
    pos_value = capital / SLOTS
    out = []
    for t in trades:
        df = candle_map[t["_sym"]]
        r = reprice_trade(df, t, t["_ld_high"], pos_value, mult=mult, cost=cost)
        out.append(dict(symbol=t["_sym"], year=int(t["year"]), open=bool(t.get("open")), replay_net=float(t["pnl_pct"]) - REPLAY_COST_PCT,
                        taken=r.taken, reason=r.reason, real_net=r.net_pct, es=r.entry_slip_bps, xs=r.exit_slip_bps, fees_pct=r.fee_pct,
                        deferred=r.exit_deferred, shares=r.shares))
    return pd.DataFrame(out)


def report(df, title, detail=True):
    lines = [title]
    tk = df[df.taken]
    lines.append(summarize("replay net (flat 1.0%), all trades", df.replay_net.values, df.open.values, df.symbol.values))
    lines.append(summarize("replay net, trades that survive", tk.replay_net.values, tk.open.values, tk.symbol.values))
    lines.append(summarize("REALISTIC net, trades taken", tk.real_net.values, tk.open.values, tk.symbol.values))
    real_all = np.where(df.taken, df.real_net, 0.0)
    lines.append(f"{'REALISTIC incl. missed trades as 0 (per signal)':<44} N={len(df):3d} net={real_all.mean():+6.2f}%")
    if len(tk):
        d = (tk.real_net - tk.replay_net).values
        lo, hi = boot_mean_ci(d, groups=tk.symbol.values)
        lines.append(f"{'paired diff (realistic - replay), taken':<44} mean={d.mean():+6.2f}pp CI90=[{lo:+5.2f},{hi:+5.2f}]")
    if detail:
        miss = df[~df.taken]
        lines.append(f"not taken: {len(miss)} of {len(df)}  " + str(miss.reason.value_counts().to_dict()) +
                     f" | exits deferred by a lower-circuit lock: {int((tk.deferred > 0).sum())}")
        if len(tk):
            lines.append(f"mean entry slip {tk["es"].mean():.0f} bps, exit slip {tk["xs"].mean():.0f} bps, charges {tk.fees_pct.mean():.2f}% of position, "
                         f"median shares {int(tk.shares.median())}")
        yrs = {int(y): (int(len(g)), round(float(g.replay_net.mean()), 2), round(float(g.real_net.mean()), 2)) for y, g in tk.groupby("year")}
        lines.append("by year (n, replay net, realistic net): " + str(yrs))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    with open(a.cache, "rb") as fh:
        listing_map, candle_map = pickle.load(fh)
    trades = build_trades(candle_map, listing_map)
    out = [f"policy-P replay trades: {len(trades)} (open at data end: {sum(1 for t in trades if t.get('open'))}), "
           f"{len(candle_map)} symbols; CI = 90% symbol-cluster bootstrap; tier REPLAY; spread/impact/lock numbers are ASSUMPTIONS", ""]
    out.append("=" * 110)
    out.append(report(run_scenario(trades, candle_map, 100000.0, 1.0, CostModel().brokerage_per_order),
                      "PRIMARY: capital Rs 1,00,000, mult 1.0, Rs 20/order brokerage"))
    out.append("")
    out.append("=" * 110)
    out.append("STRESS GRID (taken trades, realistic net per trade; replay net on the same trades in brackets)")
    for cap in (100000.0, 50000.0):
        for brk in (20.0, 0.0):
            for mult in (1.0, 1.5, 2.0):
                d = run_scenario(trades, candle_map, cap, mult, brk)
                tk = d[d.taken]
                c, ai, e = pf3(tk.real_net.values, tk.open.values)
                out.append(f"cap {cap:>7,.0f} brokerage {brk:>4.0f} mult {mult:.1f}: taken {len(tk):3d}/{len(d)} "
                           f"net {tk.real_net.mean():+6.2f}% (replay {tk.replay_net.mean():+6.2f}%) PF closed/inclOpen/exTop3 {c:4.2f}/{ai:4.2f}/{e:4.2f}")
    text = "\n".join(out)
    print(text)
    if a.out:
        os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
