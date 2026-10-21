# -*- coding: utf-8 -*-
"""
research_setup2.py -- a SECOND, separate entry setup for the explosive post-listing moves that policy P cannot reach.

PRE-REGISTRATION (written 2026-10-21 BEFORE any trade outcome of the setups below was computed on this engine).
What was looked at before freezing: data hygiene (113 symbols carry placeholder listing dates 2026-08-17/18 and 42 truncated candles;
12 more have a first candle far from the record listing date) and OUTCOME-BLIND event counts (raw signal days, no price after the signal
bar).  The counts showed the setup is rare (about 200 raw signal days over 4.4 years), so low power is expected and is part of the
verdict.  Variants that came out EMPTY on counts alone were dropped before any outcome was seen: a 20-session pivot (only 13-20 signals in the
deep and young cells), a listing-day-close relative-strength split (empty by construction: a stock more than 10% below its listing high is
below its listing-day close), and a 40-session full-pivot reclaim (6 signals).  Nothing else was searched.  No threshold below was tuned.

WHY (LEARNINGS 17.2): policy P is a listing-high breakout.  43% of the +20% episodes (age 5-60) begin more than 10% BELOW the listing high,
so a listing-high order can never hold them.  The owner's lever is more independent outlier draws (77% of profit is outliers, nothing predicts
them).  This is a different setup, not a bolt-on: policy P, its exits, gates, slot caps and defaults are untouched.

THE SETUP, "S2: pivot breakout below the listing high"  (all information known BEFORE the open of day i; engine code core/setup2.py)
  Universe hygiene: real listing date (not the 2026-08-17/18 placeholder), first candle within 5 days of the listing date, listing-day high
  within 2% of the first candle high, listing 2022-05 or later.
  Eligible on day i if ALL of:
    - age (calendar days since listing) between 15 and 200;
    - prior close <= 0.90 x listing-day high (more than 10% below it);
    - pivot = highest HIGH of the last 10 completed sessions (i-10..i-1); the pivot session is at least 5 sessions old (index <= i-6), i.e. price
      has stayed under the pivot for 5+ sessions (a base, not a run);
    - prior close >= 0.90 x pivot (within 10% of the trigger; this is the order-sheet feasibility rule, the sheet cannot carry hundreds of orders);
    - the same liquidity gates as policy P in causal mode: fill price >= Rs 25, PRIOR-bar volume >= 150,000, 20-session mean turnover (to the prior
      bar) >= Rs 0.75 Cr, fewer than 3 circuit-like days in the last 15 sessions.  Same-day volume or candle shape is never used (rule 21).
  Entry: resting stop-limit BUY, trigger pivot x 1.015, limit pivot x 1.025; filled at max(open, trigger) x 1.003 (slippage 0.3%); no fill if the
  day opens above the limit.  Stop: engine default (15-session swing low x 0.97, at most 12% under the fill).  Exits: EXACTLY policy P's
  (core.exit_rules through the honest engine; close-based stops sold at the next open, trailing, dead-money gates, exit_fill="eod_next_open").
  One position per symbol, 30-bar cooldown after an exit.  Costs: flat 1.0% round trip for trade statistics, the Indian delivery cost model
  (entry slippage 0, exit slippage 0.3%) inside the account simulation.  Open trades at the end of the data are marked to market and kept.
  NOT applied (policy-P specific): listing-day range cushion, leader score (R:R gate is vacuous: target >= +20%, risk <= 12%).
  Caveat on exits: the live consolidation engine uses different dead-money and trail constants; P's exit set is used here so the comparison with P
  isolates the ENTRY.

HYPOTHESES (a fixed, disjoint partition of the setup; mechanism for each):
  H1 BASE_OLD   age 61-200 and drawdown DD60 < 30%, DD60 = 1 - prior close / highest HIGH of the last 60 completed sessions.
                Mechanism: after a correction of more than 10% from the listing high, a stock that has absorbed supply under a 10-session pivot for 5+
                sessions and breaks it follows the same demand mechanism as the listing-high breakout, one level lower.
  H2 RECOVERY   age 61-200 and DD60 >= 30%.  Mechanism: a washed-out stock reclaiming its most recent pivot (short covering, bottom fishing
                turned momentum).  Different risk: the stop cap (12%) sits inside typical washout volatility.
  H3 BASE_YOUNG age 15-60, any DD60.  Mechanism: the region where the unreachable +20% episodes of LEARNINGS 17.2 live (all episodes were age 5-60).
  PRIMARY test = S2 pooled (H1 + H2 + H3), one test.  H1, H2, H3 are SECONDARY (three more tests: expect about one false positive in ten bars
  of 90% by chance; so a cell is adoptable only if it also meets N >= 40).

ADOPTION BAR (every item must hold; otherwise the setup is reported and NOT adopted; adoption can only mean "PAPER_ONLY forward-test candidate",
never live capital):
  B1  Pooled N >= 100 trades (SD per trade about 17%, so SE about 1.7pp; below 100 the result cannot settle anything).  Cells need N >= 40; a cell below
      that is UNDERPOWERED (no claim either way).
  B2  Mean net per trade (incl. open at mark to market) has a 90% symbol-cluster bootstrap CI whose LOWER bound is above 0.
  B3  Mean net stays above 0 after dropping the single largest trade.
  B4  At most one entry year with N >= 8 has a negative mean net.
  B5  Account test (Rs 1 lakh, flat 10 slots x 10%, whole shares, costs on; policy P wins same-day ties and same-symbol overlaps; S2 at slippage 0.3%
      and again at 1.0%): P + S2 must have total return >= P alone AND Calmar >= P alone in BOTH slippage cases, and the net contribution
      (sum of net% of S2 trades taken minus sum of net% of P trades displaced, equal weights) must be above 0.  Single account paths flip sign
      (LEARNINGS 12.3), so B5 is a necessary check, never evidence on its own.
  Thresholds are fixed above, so there is nothing to walk forward; all of 2022-2026 is out of sample with respect to the rule.

REPORTED, NOT GATING: PF three ways (closed only, incl. open at mark to market, closed excluding the top 3) with symbol-cluster 90% intervals;
  year-by-year; dead-money share (trades, capital-days); outliers (>= +25%) and their share of gross profit; capture of the +20% episodes that begin
  more than 10% below the listing high (S2 vs P vs both); overlap with P (same symbol within 42 days, concurrent holding, monthly cohort correlation);
  resting orders per day the sheet would need; slippage 1.0% sensitivity; and a CONTROL: same state (age band, below the listing high, near the pivot,
  liquid) entered at a random eligible day's open (one random day per symbol, 10 draws), same stop and exits, to show how much of the result is the
  state or the 2024-26 market rather than the trigger.  Evidence tier of every number here: REPLAY of the honest engine (not live, not paper).

HONEST LIMITS: about 100-250 trades, so only effects of about 3-4pp or more are detectable; trades cluster in hot IPO cohorts and years (2025-26 carry
  most signals); survivors only (delisted names are absent); 4.4 years, no crash; resting-order fills are idealised (jump-throughs invisible in daily
  bars); exits were designed for listing-high breakouts.  A null or negative result is a valid result and is reported as such.

  .venv\\Scripts\\python.exe research_setup2.py --cache data.pkl --out-dir reports/2026-10-21
"""
import argparse
import io
import logging
import os
import pickle
import sys
from dataclasses import replace

import numpy as np
import pandas as pd

logging.disable(logging.CRITICAL)
from core.execution_model import CostModel
from core.honest_backtest import LIVE_FAITHFUL, Params, simulate_symbol_cross
from core.portfolio_sim import PortfolioConfig, run_portfolio
from core.setup2 import (DEAD_MONEY_RE, Setup2Params, clean_universe, control_indices, order_days, simulate_control_entry,
                         simulate_symbol_setup2)
from research_alpha_lab import CI_LEVEL, COST, cluster_boot, pf
from research_capture_gaps import episode_rows
from research_gate_replay import captured

CFG = replace(LIVE_FAITHFUL, exit_fill="eod_next_open")
COSTM = CostModel(entry_slippage_pct=0.0, exit_slippage_pct=0.3)     # same account cost model as research_dead_money.py
S2 = Setup2Params()
CELLS = ("H1_BASE_OLD", "H2_RECOVERY", "H3_BASE_YOUNG")
OUTLIER = 25.0


# --------------------------------------------------------------------------------------------- helpers

class Tee:
    def __init__(self):
        self.buf = io.StringIO()

    def write(self, s):
        sys.__stdout__.write(s)
        self.buf.write(s)

    def flush(self):
        sys.__stdout__.flush()


def to_df(trades):
    d = pd.DataFrame(trades)
    if d.empty:
        return d
    d["net"] = d.pnl_pct - COST
    d["entry_date"] = pd.to_datetime(d.entry_date)
    d["exit_date"] = pd.to_datetime(d.exit_date)
    d["year"] = d.entry_date.dt.year
    d["open"] = d.open.astype(bool)
    return d


def cluster_pf(d, n=2000, seed=0):
    """PF three ways with symbol-cluster bootstrap 90% intervals.  Returns dict name -> (point, lo, hi)."""
    groups = {s: g for s, g in d.groupby("symbol")}
    keys = list(groups)
    arr_all = {s: g.net.values for s, g in groups.items()}
    arr_cl = {s: g[~g.open].net.values for s, g in groups.items()}

    def f_all(x):
        return pf(x)

    def f_closed(x):
        return pf(x)

    def f_ex3(x):
        return pf(np.sort(x)[:-3]) if len(x) > 3 else float("nan")

    cl = d[~d.open].net.values
    point = {"incl_open": pf(d.net.values), "closed_only": pf(cl), "ex_top3_all": f_ex3(d.net.values), "ex_top3_closed": f_ex3(cl)}
    rng = np.random.default_rng(seed)
    out = {k: [] for k in point}
    for _ in range(n):
        pick = rng.integers(0, len(keys), len(keys))
        a = np.concatenate([arr_all[keys[j]] for j in pick])
        c = np.concatenate([arr_cl[keys[j]] for j in pick])
        out["incl_open"].append(f_all(a))
        out["closed_only"].append(f_closed(c) if len(c) else np.nan)
        out["ex_top3_all"].append(f_ex3(a))
        out["ex_top3_closed"].append(f_ex3(c) if len(c) else np.nan)
    res = {}
    for k, v in out.items():
        v = np.asarray(v, float)
        v = v[np.isfinite(v)]
        res[k] = (point[k], float(np.percentile(v, 5)), float(np.percentile(v, 95))) if len(v) else (point[k], np.nan, np.nan)
    return res


def diff_cluster_boot(a, ga, b, gb, n=4000, seed=1):
    """Mean(a) - mean(b) with a symbol-cluster bootstrap over the union of symbols (ratio of sums within each set)."""
    syms = sorted(set(ga) | set(gb))
    ix = {s: k for k, s in enumerate(syms)}
    ka = np.array([ix[s] for s in ga]); kb = np.array([ix[s] for s in gb])
    k = len(syms)
    sa = np.bincount(ka, weights=np.asarray(a, float), minlength=k); ca = np.bincount(ka, minlength=k).astype(float)
    sb = np.bincount(kb, weights=np.asarray(b, float), minlength=k); cb = np.bincount(kb, minlength=k).astype(float)
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, k, (n, k))
    with np.errstate(divide="ignore", invalid="ignore"):
        diff = sa[draws].sum(1) / ca[draws].sum(1) - sb[draws].sum(1) / cb[draws].sum(1)
    diff = diff[np.isfinite(diff)]
    pa, pb = float(np.mean(a)), float(np.mean(b))
    lo, hi = np.percentile(diff, [(100 - CI_LEVEL) / 2, 100 - (100 - CI_LEVEL) / 2])
    return pa - pb, float(lo), float(hi)


def sum_cluster_boot(vals, groups, n=4000, seed=2):
    """Total of `vals` with a symbol-cluster bootstrap interval."""
    v = np.asarray(vals, float)
    g = pd.factorize(np.asarray(groups))[0]
    k = g.max() + 1
    sums = np.bincount(g, weights=v, minlength=k)
    rng = np.random.default_rng(seed)
    tot = sums[rng.integers(0, k, (n, k))].sum(1)
    lo, hi = np.percentile(tot, [(100 - CI_LEVEL) / 2, 100 - (100 - CI_LEVEL) / 2])
    return float(v.sum()), float(lo), float(hi)


def block(label, d, full=True):
    """Standard statistics block.  Returns a dict used by the bar evaluation."""
    n = len(d)
    r = dict(n=n)
    if n == 0:
        print(f"{label:<44} no trades")
        return r
    m, lo, hi = cluster_boot(d.net.values, d.symbol.values)
    s = np.sort(d.net.values)[::-1]
    top = d.net.idxmax()
    rest = d.drop(index=top)
    m1 = float(rest.net.mean()) if len(rest) else float("nan")
    r.update(mean=m, lo=lo, hi=hi, mean_ex1=m1, symbols=int(d.symbol.nunique()))
    cl = d[~d.open]
    print(f"{label:<44} N={n:3d} (open {int(d.open.sum()):2d}, symbols {d.symbol.nunique():3d}) win={100 * (d.net > 0).mean():4.1f}% "
          f"net={m:+5.2f}% CI90=[{lo:+5.1f},{hi:+5.1f}] median={d.net.median():+5.2f}% exTop1={m1:+5.2f}% exTop3={s[3:].mean() if n > 3 else float('nan'):+5.2f}%")
    if full:
        p = cluster_pf(d)
        r["pf"] = p
        print(f"    PF  incl open {p['incl_open'][0]:.2f} [{p['incl_open'][1]:.2f}, {p['incl_open'][2]:.2f}] | closed only {p['closed_only'][0]:.2f} "
              f"[{p['closed_only'][1]:.2f}, {p['closed_only'][2]:.2f}] | ex-top3 all {p['ex_top3_all'][0]:.2f} [{p['ex_top3_all'][1]:.2f}, {p['ex_top3_all'][2]:.2f}] "
              f"| closed ex-top3 {p['ex_top3_closed'][0]:.2f} [{p['ex_top3_closed'][1]:.2f}, {p['ex_top3_closed'][2]:.2f}]   (n closed {len(cl)})")
        dead = d[d.exit_reason.astype(str).str.contains(DEAD_MONEY_RE)]
        days = d.days_held_cal.clip(lower=1)
        big = d[d.net >= OUTLIER]
        gp = d.net[d.net > 0].sum()
        print(f"    dead money: {len(dead)} trades = {100 * len(dead) / n:.0f}% | capital-days {100 * days[dead.index].sum() / days.sum():.0f}% | mean {dead.net.mean() if len(dead) else float('nan'):+.2f}%"
              f" || outliers (net >= +{OUTLIER:.0f}%): {len(big)} = {100 * len(big) / n:.1f}% of trades, {100 * big.net.sum() / gp if gp > 0 else float('nan'):.0f}% of gross profit"
              f" || median days held {d.days_held_cal.median():.0f}, median initial risk {d.initial_risk_pct.median():.1f}%")
    return r


def year_table(d, label):
    print(f"  by entry year ({label}):")
    rows = {}
    for y, g in d.groupby("year"):
        m = float(g.net.mean())
        rows[int(y)] = (len(g), m, pf(g.net.values), 100 * float((g.net > 0).mean()))
        print(f"    {int(y)}  N={len(g):3d}  mean {m:+6.2f}%  PF {pf(g.net.values):5.2f}  win {100 * (g.net > 0).mean():4.1f}%  open {int(g.open.sum())}")
    return rows


def account(label, trades):
    r = run_portfolio(trades, PortfolioConfig(capital=100000.0, soft_cap=10, hard_cap=10, cap_policy="flat", cost=COSTM))
    s = r["summary"]
    if not s.get("trades_taken"):
        print(f"  {label:<34} no trades")
        return r
    s["calmar"] = s["cagr_pct"] / abs(s["max_drawdown_pct"]) if s["max_drawdown_pct"] else float("nan")
    print(f"  {label:<34} signals {s['signals']:3d} taken {s['trades_taken']:3d} skipped {s['skipped']} ret {s['total_return_pct']:+7.1f}% "
          f"CAGR {s['cagr_pct']:+5.1f}% maxDD {s['max_drawdown_pct']:6.1f}% Calmar {s['calmar']:4.2f} PF {s['profit_factor']:.2f}")
    return r


def run_s2(clean, slip):
    out = []
    for sym, (df, li) in clean.items():
        out.extend(simulate_symbol_setup2(df, sym, li, Params(), S2, CFG, slip_pct=slip, include_open=True))
    return out


def run_p(listing_map, candle_map):
    p = Params()
    out = []
    for sym, df in candle_map.items():
        out.extend(simulate_symbol_cross(df, sym, listing_map.get(sym, {}), p, slip_pct=0.3, fill_cap_pct=2.5, min_age=5,
                                         include_open=True, cfg=CFG, causal=True, vol_gate=False))
    return out


def tag_priority(trades, prio):
    out = []
    for t in trades:
        u = dict(t)
        u["vol_ratio"] = prio
        out.append(u)
    return out


def drop_same_symbol_overlap(s2_trades, p_trades):
    """S2 trades that overlap in time a policy-P position in the same symbol are dropped (P keeps priority; one position per symbol)."""
    spans = {}
    for t in p_trades:
        spans.setdefault(t["symbol"], []).append((pd.Timestamp(t["entry_date"]), pd.Timestamp(t["exit_date"])))
    kept, dropped = [], []
    for t in s2_trades:
        a, b = pd.Timestamp(t["entry_date"]), pd.Timestamp(t["exit_date"])
        if any(a <= pb and pa <= b for pa, pb in spans.get(t["symbol"], [])):
            dropped.append(t)
        else:
            kept.append(t)
    return kept, dropped


def rank_corr(a, b):
    ra, rb = pd.Series(a).rank(), pd.Series(b).rank()
    return float(ra.corr(rb)) if len(ra) > 2 else float("nan")


# --------------------------------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--draws", type=int, default=10, help="random-entry control draws")
    a = ap.parse_args()
    tee = Tee()
    sys.stdout = tee
    with open(a.cache, "rb") as fh:
        listing_map, candle_map = pickle.load(fh)
    clean, dropped = clean_universe(listing_map, candle_map)
    last = max(pd.to_datetime(c.DATE).max() for c in candle_map.values() if len(c))
    print(f"research_setup2.py  data to {last.date()}  symbols with candles {len(candle_map)}  clean universe {len(clean)}  dropped {len(dropped)}")
    reasons = pd.Series(dropped).value_counts().to_dict()
    print(f"  dropped by reason: {reasons}")
    print(f"  costs: flat {COST}% round trip for trade statistics; account cost model entry slip 0, exit slip 0.3 plus delivery charges; CI = 90% symbol-cluster bootstrap")
    print("  evidence tier: REPLAY of the honest engine (causal resting order, close-based stops sold at the next open)\n")

    # ---- policy P (reference, unchanged) and S2
    p_trades = run_p(listing_map, candle_map)
    P = to_df(p_trades)
    print("=" * 128 + "\n0. HARNESS CHECK: policy P (causal resting, unchanged) must reproduce the repo numbers (about 228 trades, PF 2.43 / 1.71 / 1.91)\n" + "=" * 128)
    bp = block("Policy P, all symbols", P)
    s2_03 = run_s2(clean, 0.3)
    s2_10 = run_s2(clean, 1.0)
    D03, D10 = to_df(s2_03), to_df(s2_10)
    if D03.empty:
        print("S2 produced no trades")
        return
    print(f"\nS2 trades: slip 0.3% -> {len(D03)}, slip 1.0% -> {len(D10)}")

    # ---- 1. primary test
    print("\n" + "=" * 128 + "\n1. PRIMARY: S2 pooled (H1 + H2 + H3), resting order, slippage 0.3%\n" + "=" * 128)
    r03 = block("S2 pooled (slip 0.3%)", D03)
    yrs = year_table(D03, "S2 pooled")
    print("\n  by class (secondary hypotheses):")
    cell = {}
    for k in CELLS:
        g = D03[D03.klass == k]
        cell[k] = block(f"  {k}", g)
        if len(g):
            year_table(g, k)
    print("\n  Stress: slippage 1.0%")
    r10 = block("S2 pooled (slip 1.0%)", D10)
    year_table(D10, "S2 pooled slip 1.0%")
    for k in CELLS:
        block(f"  {k} (slip 1.0%)", D10[D10.klass == k], full=False)
    print("\n  Closed-only view (open runners excluded):")
    block("S2 pooled closed only", D03[~D03.open], full=False)

    # ---- 2. control
    print("\n" + "=" * 128 + f"\n2. CONTROL: same state, random eligible day's OPEN, one day per symbol, {a.draws} draws (not gating)\n" + "=" * 128)
    elig = {sym: control_indices(df, li) for sym, (df, li) in clean.items()}
    elig = {s: v for s, v in elig.items() if v}
    ctrl = []
    for draw in range(a.draws):
        rng = np.random.default_rng(100 + draw)
        for sym, idx in elig.items():
            df, li = clean[sym]
            t = simulate_control_entry(df, sym, li, int(rng.choice(idx)), Params(), S2, CFG, slip_pct=0.3)
            if t is not None:
                t["draw"] = draw
                ctrl.append(t)
    C = to_df(ctrl)
    print(f"  symbols with an eligible state day: {len(elig)}; control trades {len(C)} ({len(C) / a.draws:.0f} per draw)")
    block("Control pooled (all draws)", C, full=False)
    per_draw = C.groupby("draw").net.mean()
    print(f"    per-draw mean net: min {per_draw.min():+.2f}% median {per_draw.median():+.2f}% max {per_draw.max():+.2f}%")
    d_m, d_lo, d_hi = diff_cluster_boot(D03.net.values, D03.symbol.values, C.net.values, C.symbol.values)
    print(f"  S2 pooled minus control: {d_m:+.2f}pp  CI90 [{d_lo:+.1f}, {d_hi:+.1f}]")
    for k in CELLS:
        g, c = D03[D03.klass == k], C[C.klass == k]
        if len(g) and len(c):
            m_, lo_, hi_ = diff_cluster_boot(g.net.values, g.symbol.values, c.net.values, c.symbol.values)
            print(f"    {k}: S2 {g.net.mean():+.2f}% (N={len(g)}) vs control {c.net.mean():+.2f}% (N={len(c)}) -> {m_:+.2f}pp CI90 [{lo_:+.1f}, {hi_:+.1f}]")
    for y, g in D03.groupby("year"):
        c = C[C.year == y]
        if len(c):
            print(f"    {int(y)}: S2 {g.net.mean():+6.2f}% (N={len(g)})  control {c.net.mean():+6.2f}% (N={len(c)})")

    # ---- 3. overlap with P and capture
    print("\n" + "=" * 128 + "\n3. OVERLAP WITH POLICY P, CAPTURE OF THE EPISODES P CANNOT REACH, ORDER LOAD\n" + "=" * 128)
    psyms = P.groupby("symbol")
    same_sym = within42 = concurrent = 0
    for t in D03.itertuples():
        g = psyms.get_group(t.symbol) if t.symbol in psyms.groups else None
        if g is None:
            continue
        same_sym += 1
        gap = ((t.entry_date - g.exit_date).dt.days.abs() <= 42) | ((g.entry_date - t.entry_date).dt.days.abs() <= 42)
        within42 += int(gap.any())
        concurrent += int(((g.entry_date <= t.exit_date) & (g.exit_date >= t.entry_date)).any())
    print(f"  S2 trades whose symbol ever had a P trade: {same_sym} of {len(D03)} ({100 * same_sym / len(D03):.0f}%); P entry or exit within 42 days of the S2 entry: {within42}; "
          f"time-overlapping holding in the same symbol: {concurrent}")
    D03["ym"] = D03.entry_date.dt.to_period("M")
    P["ym"] = P.entry_date.dt.to_period("M")
    mp, ms = P.groupby("ym").net.mean(), D03.groupby("ym").net.mean()
    both = mp.index.intersection(ms.index)
    cp, cs = P.groupby("ym").size(), D03.groupby("ym").size()
    allm = cp.index.union(cs.index)
    print(f"  monthly cohorts: months with both {len(both)}; rank correlation of mean net (months with both) {rank_corr(mp[both].values, ms[both].values):+.2f}; "
          f"rank correlation of trade counts per month (all months) {rank_corr(cp.reindex(allm, fill_value=0).values, cs.reindex(allm, fill_value=0).values):+.2f}")
    eps = episode_rows({s: v[0] for s, v in clean.items()}, {s: v[1] for s, v in clean.items()}, 0.20)
    far = eps[eps.dist_lh < -10]
    ep_all = list(zip(eps.symbol, eps.start, eps.end))
    ep_far = list(zip(far.symbol, far.start, far.end))
    Pc = P[P.symbol.isin(clean.keys())]
    both_df = pd.concat([Pc, D03], ignore_index=True)
    print(f"  +20% episodes in the clean universe (age 5-60): {len(eps)}; more than 10% below the listing high: {len(far)} ({100 * len(far) / max(1, len(eps)):.0f}%)")
    print(f"    capture of ALL episodes     : P {captured(ep_all, Pc)} ({100 * captured(ep_all, Pc) / len(ep_all):.0f}%)  S2 {captured(ep_all, D03)}  P+S2 {captured(ep_all, both_df)} ({100 * captured(ep_all, both_df) / len(ep_all):.0f}%)")
    print(f"    capture of the FAR episodes : P {captured(ep_far, Pc)} ({100 * captured(ep_far, Pc) / max(1, len(ep_far)):.0f}%)  S2 {captured(ep_far, D03)} ({100 * captured(ep_far, D03) / max(1, len(ep_far)):.0f}%)  P+S2 {captured(ep_far, both_df)}")
    od = {}
    for sym, (df, li) in clean.items():
        for d_ in order_days(df, li, Params(), S2):
            od[d_] = od.get(d_, 0) + 1
    ods = pd.Series(od).sort_index()
    cal = pd.date_range(ods.index.min(), last, freq="B")
    full = ods.reindex(cal, fill_value=0)
    print(f"  resting orders per business day the sheet would carry (state passes pre-open): mean {full.mean():.1f} median {full.median():.0f} p95 {full.quantile(0.95):.0f} max {int(full.max())}; "
          f"last 250 business days mean {full.iloc[-250:].mean():.1f}")

    # ---- 4. account
    print("\n" + "=" * 128 + "\n4. ACCOUNT SIMULATION: Rs 1 lakh, flat 10 slots x 10%, whole shares, costs on, P wins ties and same-symbol overlaps\n" + "=" * 128)
    pt = tag_priority(p_trades, 99.0)
    acc = {}
    acc["P"] = account("P alone", pt)
    contrib = {}
    for lbl, trades in (("0.3", s2_03), ("1.0", s2_10)):
        kept, drop = drop_same_symbol_overlap(tag_priority(trades, 0.0), p_trades)
        print(f"  [S2 slip {lbl}%] same-symbol overlaps with P dropped: {len(drop)} of {len(trades)}")
        acc["S2_" + lbl] = account(f"S2 alone (slip {lbl}%)", tag_priority(trades, 0.0))
        acc["PS2_" + lbl] = account(f"P + S2 (S2 slip {lbl}%)", pt + kept)
        base_log, comb_log = acc["P"]["log"], acc["PS2_" + lbl]["log"]
        taken_p_alone = set(zip(base_log.symbol, base_log.entry_date))
        is_s2 = comb_log.type.astype(str).str.startswith("S2_")
        s2_taken = comb_log[is_s2]
        p_taken_comb = set(zip(comb_log[~is_s2].symbol, comb_log[~is_s2].entry_date))
        displaced = base_log[[(s, e) not in p_taken_comb for s, e in zip(base_log.symbol, base_log.entry_date)]]
        vals = np.concatenate([s2_taken.net_pct.values, -displaced.net_pct.values])
        grp = np.concatenate([s2_taken.symbol.values, displaced.symbol.values])
        tot, lo, hi = sum_cluster_boot(vals, grp)
        contrib[lbl] = (tot, lo, hi)
        print(f"    S2 trades taken {len(s2_taken)} (mean net {s2_taken.net_pct.mean() if len(s2_taken) else float('nan'):+.2f}%); P trades displaced {len(displaced)} "
              f"(mean net {displaced.net_pct.mean() if len(displaced) else float('nan'):+.2f}%, outliers >= +25%: {int((displaced.net_pct >= 25).sum())})")
        print(f"    net contribution = sum(S2 taken net%) - sum(P displaced net%) = {tot:+.1f}pp  CI90 [{lo:+.1f}, {hi:+.1f}]  (equal-weight approximation)")
        for nm, key in (("P", "P"), ("P+S2", "PS2_" + lbl)):
            print(f"      by year {nm}: " + "  ".join(f"{y}: {v:+.0f}%" for y, v in acc[key]["summary"].get("by_year_pct", {}).items()))

    # ---- 5. bar
    print("\n" + "=" * 128 + "\n5. ADOPTION BAR (research_setup2.py docstring)\n" + "=" * 128)

    def neg_years(rows):
        return [y for y, (n_, m_, _p, _w) in rows.items() if n_ >= 8 and m_ < 0]

    ok = {}
    ok["B1"] = r03["n"] >= 100
    ok["B2"] = r03["lo"] > 0
    ok["B3"] = r03["mean_ex1"] > 0
    nyr = neg_years(yrs)
    ok["B4"] = len(nyr) <= 1
    P_s = acc["P"]["summary"]

    def b5(lbl):
        c = acc["PS2_" + lbl]["summary"]
        return (c["total_return_pct"] >= P_s["total_return_pct"] and c["calmar"] >= P_s["calmar"] and contrib[lbl][0] > 0)

    ok["B5"] = b5("0.3") and b5("1.0")
    print(f"  B1 N >= 100                                   : {'PASS' if ok['B1'] else 'FAIL'}  (N = {r03['n']})")
    print(f"  B2 CI90 lower bound of mean net > 0           : {'PASS' if ok['B2'] else 'FAIL'}  (mean {r03['mean']:+.2f}%, CI [{r03['lo']:+.2f}, {r03['hi']:+.2f}])")
    print(f"  B3 mean net > 0 without the single largest    : {'PASS' if ok['B3'] else 'FAIL'}  ({r03['mean_ex1']:+.2f}%)")
    print(f"  B4 at most one negative year (N >= 8)         : {'PASS' if ok['B4'] else 'FAIL'}  (negative years: {nyr})")
    for lbl in ("0.3", "1.0"):
        c = acc["PS2_" + lbl]["summary"]
        print(f"  B5 account, S2 slip {lbl}%: P+S2 ret {c['total_return_pct']:+.1f}% vs P {P_s['total_return_pct']:+.1f}%; Calmar {c['calmar']:.2f} vs {P_s['calmar']:.2f}; "
              f"contribution {contrib[lbl][0]:+.1f}pp [{contrib[lbl][1]:+.1f}, {contrib[lbl][2]:+.1f}]  -> {'PASS' if b5(lbl) else 'FAIL'}")
    print(f"  B5 overall                                    : {'PASS' if ok['B5'] else 'FAIL'}")
    print(f"\n  PRIMARY VERDICT (S2 pooled): {'MEETS THE BAR (forward-test candidate, PAPER_ONLY)' if all(ok.values()) else 'DOES NOT MEET THE BAR (not adopted)'}")
    for k in CELLS:
        c = cell[k]
        if c["n"] < 40:
            print(f"  {k}: UNDERPOWERED (N = {c['n']} < 40), no claim either way")
            continue
        g = D03[D03.klass == k]
        y_ = {int(y): (len(x), float(x.net.mean()), 0, 0) for y, x in g.groupby("year")}
        passed = c["lo"] > 0 and c["mean_ex1"] > 0 and len(neg_years(y_)) <= 1
        print(f"  {k}: N = {c['n']} mean {c['mean']:+.2f}% CI [{c['lo']:+.2f}, {c['hi']:+.2f}] exTop1 {c['mean_ex1']:+.2f}% negative years {neg_years(y_)} -> "
              f"{'meets B2-B4' if passed else 'does not meet B2-B4'}")

    # ---- 6. POST-HOC, descriptive only (added after the first run showed the bar failed; it does not enter the bar or the verdict)
    print("\n" + "=" * 128 + "\n6. POST-HOC DESCRIPTIVE (not pre-registered, not gating): entry quality independent of the exit rules\n"
          "   peak HIGH within 20 sessions after the entry bar, relative to the fill (trades with 20 later bars only)\n" + "=" * 128)
    hi_clean = {s: v[0].HIGH.values.astype(float) for s, v in clean.items()}
    hi_raw = {s: c.HIGH.values.astype(float) for s, c in candle_map.items()}

    def mfe(label, d, hmap, h=20):
        vals = []
        for t in d.itertuples():
            hi = hmap[t.symbol]
            i = int(t.entry_idx)
            if i + h >= len(hi):
                continue
            vals.append(hi[i:i + h + 1].max() / t.entry_price - 1.0)
        v = np.asarray(vals)
        if len(v) == 0:
            print(f"  {label:<26} no trades with 20 later bars")
            return
        print(f"  {label:<26} N={len(v):4d}  median peak {100 * np.median(v):+5.1f}%  reached +10% {100 * (v >= 0.10).mean():4.1f}%  +20% {100 * (v >= 0.20).mean():4.1f}%  +40% {100 * (v >= 0.40).mean():4.1f}%")

    mfe("Policy P (clean symbols)", Pc, hi_raw)
    mfe("S2 pooled", D03, hi_clean)
    for k in CELLS:
        mfe(f"  {k}", D03[D03.klass == k], hi_clean)
    mfe("Control (all draws)", C, hi_clean)
    mfe("Control H1", C[C.klass == "H1_BASE_OLD"], hi_clean)
    mfe("Control H3", C[C.klass == "H3_BASE_YOUNG"], hi_clean)

    if a.out_dir:
        os.makedirs(a.out_dir, exist_ok=True)
        drop_cols = ["path"]
        D03.drop(columns=[c for c in drop_cols if c in D03.columns]).to_csv(os.path.join(a.out_dir, "setup2_trades_slip0.3.csv"), index=False)
        D10.drop(columns=[c for c in drop_cols if c in D10.columns]).to_csv(os.path.join(a.out_dir, "setup2_trades_slip1.0.csv"), index=False)
        C.drop(columns=[c for c in drop_cols if c in C.columns]).to_csv(os.path.join(a.out_dir, "setup2_control_trades.csv"), index=False)
        P.drop(columns=[c for c in drop_cols if c in P.columns]).to_csv(os.path.join(a.out_dir, "setup2_policyP_reference_trades.csv"), index=False)
        with open(os.path.join(a.out_dir, "setup2_output.txt"), "w", encoding="utf-8") as fh:
            fh.write(tee.buf.getvalue())
    sys.stdout = sys.__stdout__


if __name__ == "__main__":
    main()
