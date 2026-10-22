# -*- coding: utf-8 -*-
"""
research_setup3.py -- a THIRD entry setup for the slow IPO leaders that policy P cannot hold ("S3: late breakout of the post-listing high").

PRE-REGISTRATION (written 2026-10-22 BEFORE any S3 trade outcome was computed on this engine).
What was looked at before freezing: (a) the owner's four examples (RUBICON, CORONA, SUDEEPPHRM, MOLBIO: +200%, ~+100%, +70%, +80% after listing)
as price paths, not as trades: three of them (RUBICON, SUDEEPPHRM, CORONA) did NOT make their big move in the first 60 days; the +50% mark came
124-179 sessions after listing, after a base lasting months under or near the highest price printed since listing; policy P held RUBICON (stopped
+0.8%) and CORONA (dead-money exit -1.2%) in their first weeks and never again (max age 60 days), and held MOLBIO (+31% open); it never held
SUDEEPPHRM; (b) a healthcare-name tag (all four are pharma/diagnostics): healthcare IPOs are NOT more likely than others to reach +50% / +100%
over their history (55% vs 45% and 32% vs 29%, N = 31 vs 321), so sector is not used here; (c) OUTCOME-BLIND raw trigger-day counts of the setup
below (467 raw trigger days, 218 symbols, 145 LONG_BASE and 322 SHORT_BASE, none before 2023 in LONG_BASE).  No threshold was searched.

WHY: LEARNINGS 17.8/17.10 found nothing at listing that predicts the big movers, and 17.2/18 found that the explosive episodes policy P and S2
cannot reach start far below or long after the listing high.  A rule that needs no prediction is trend-following on the IPO universe: be in the
queue for a breakout of the highest price since listing, and let the existing exits ride whatever follows.  This is a different setup, not a
bolt-on: policy P, its exits, gates, slot caps and defaults are untouched.

THE SETUP, "S3" (all information known BEFORE the open of day i; engine code core/setup3.py)
  Universe hygiene: core.setup2.clean_universe (real listing date, first candle within 5 days of it, listing high consistent, listed 2022-05 or later).
  Eligible on day i if ALL of:
    - age (calendar days since listing) between 61 and 400;
    - pivot = highest HIGH of ALL completed sessions since listing (i-1 and earlier, listing day included); the pivot session index <= i-6;
    - prior close >= 0.90 x pivot (order-sheet feasibility rule, as S2);
    - policy P's causal liquidity gates: fill >= Rs 25, PRIOR-bar volume >= 150,000, 20-session mean turnover (to the prior bar) >= Rs 0.75 Cr,
      fewer than 3 circuit-like days in the last 15 sessions.  Same-day volume or candle shape is never used (rule 21).
  Entry: resting stop-limit BUY, trigger pivot x 1.015, limit pivot x 1.025, filled at max(open, trigger) x 1.003; no fill if the day opens above
  the limit.  Stop: engine default (15-session swing low x 0.97, at most 12% under the fill).  One position per symbol, 30-bar cooldown.
  Costs: flat 1.0% round trip for trade statistics; the Indian delivery cost model inside the account simulation.  Open trades are marked to market.

EXITS (two arms, fixed in advance)
  PRIMARY    policy P's exits exactly (core.exit_rules through the honest engine; close-based stops sold at the next open, 10% trail from +3%, winner
             lock above +15% (stop never below entry x 1.05 or 80% of the peak), dead-money / velocity / stagnant / volume-exhaustion time stops).
  SECONDARY  X1 "slow-leader exits": the same entries; the same stop and trailing ratchet; ALL time-based exits disabled (dead money, velocity, stagnant,
             volume exhaustion, the -5%/30d and -8%/60d floors).  Mechanism: a base breakout in a slow leader can sit flat for weeks before the move
             (CORONA, SUDEEPPHRM), and P's 14-day dead-money gate was built for listing breakouts.  X1 is a second look at the same entries, so a pass
             by chance is about twice as likely; it is judged by the same bar and its result is reported as such.

HYPOTHESES (a fixed, disjoint partition):
  LONG_BASE   pivot at least 40 sessions old (a base of two months or more under the high).
  SHORT_BASE  pivot 6-39 sessions old.
  PRIMARY test = S3 pooled with P's exits, one test.  LONG_BASE and SHORT_BASE are SECONDARY (cells need N >= 40 to be adoptable).

ADOPTION BAR (identical to research_setup2.py; every item must hold; otherwise the setup is reported and NOT adopted; adoption can only mean
"PAPER_ONLY forward-test candidate", never live capital):
  B1  Pooled N >= 100 trades.
  B2  Mean net per trade (incl. open at mark to market) has a 90% symbol-cluster bootstrap CI whose LOWER bound is above 0.
  B3  Mean net stays above 0 after dropping the single largest trade.
  B4  At most one entry year with N >= 8 has a negative mean net.
  B5  Account test (Rs 1 lakh, flat 10 slots x 10%, whole shares, costs on; P wins same-day ties and same-symbol overlaps; S3 at slippage 0.3% and
      again at 1.0%): P + S3 total return >= P alone AND Calmar >= P alone in BOTH cases, and the net contribution (sum of net% of S3 trades taken minus
      sum of net% of P trades displaced) above 0.
REPORTED, NOT GATING: PF three ways with symbol-cluster intervals; year by year; dead-money share; outliers (>= +25%) and their share of gross
  profit; the named cases (what each exit arm did on RUBICON, CORONA, SUDEEPPHRM and the share of the available move it kept); a CONTROL (same state,
  random eligible day's open, one day per symbol, 10 draws, same stop and exits) to separate the trigger from the state and from the 2024-26 tape;
  resting orders per day; slippage 1.0% sensitivity.  Evidence tier of every number: REPLAY of the honest engine (not live, not paper).

HONEST LIMITS: survivors only (delisted names absent); 2023-2026, no crash; most signals sit in 2023-26 hot IPO cohorts, so trades are clustered in
time and the symbol-cluster bootstrap understates that; resting-order fills are idealised; the named cases are the reason the setup was designed, so
they are illustrations, never evidence; a null or negative result is a valid result and is reported as such.

  .venv\\Scripts\\python.exe research_setup3.py --cache data.pkl --out-dir reports/2026-10-22
"""
import argparse
import logging
import os
import pickle
import sys
from dataclasses import replace

import numpy as np
import pandas as pd

logging.disable(logging.CRITICAL)
from core.honest_backtest import Params
from core.setup3 import (NO_TIME_EXITS, Setup3Params, clean_universe, control_indices, order_days, simulate_control_entry, simulate_symbol_setup3)
from research_alpha_lab import COST, cluster_boot, pf
import research_setup2 as r2
from research_setup2 import (CFG, Tee, account, block, diff_cluster_boot, drop_same_symbol_overlap, run_p, sum_cluster_boot, tag_priority,
                             to_df, year_table)

S3 = Setup3Params()
CELLS = ("LONG_BASE", "SHORT_BASE")
P_PRIMARY = Params()
P_X1 = Params(exit_overrides=NO_TIME_EXITS)
NAMED = ("RUBICON", "CORONA", "SUDEEPPHRM")


def run_s3(clean, slip, params):
    out = []
    for sym, (df, li) in clean.items():
        out.extend(simulate_symbol_setup3(df, sym, li, params, S3, CFG, slip_pct=slip, include_open=True))
    return out


def named_cases(label, D, clean):
    print(f"  named cases, {label}:")
    for sym in NAMED:
        g = D[D.symbol == sym].sort_values("entry_date")
        df = clean[sym][0] if sym in clean else None
        if g.empty:
            print(f"    {sym:<11} no S3 trade")
            continue
        for t in g.itertuples():
            hi = df.HIGH.values.astype(float)
            peak = hi[int(t.entry_idx):].max()
            avail = (peak / t.entry_price - 1) * 100
            kept = f"{100 * t.pnl_pct / avail:4.0f}% of the available +{avail:.0f}%" if avail > 5 else "n/a"
            ex = "OPEN" if t.open else str(pd.Timestamp(t.exit_date).date())
            print(f"    {sym:<11} entry {pd.Timestamp(t.entry_date).date()} (age {t.age_signal}d, {t.klass}) exit {ex}  pnl {t.pnl_pct:+6.1f}%  kept {kept}  reason: {str(t.exit_reason)[:40]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--draws", type=int, default=10)
    a = ap.parse_args()
    tee = Tee()
    sys.stdout = tee
    with open(a.cache, "rb") as fh:
        listing_map, candle_map = pickle.load(fh)
    clean, dropped = clean_universe(listing_map, candle_map)
    last = max(pd.to_datetime(c.DATE).max() for c in candle_map.values() if len(c))
    print(f"research_setup3.py  data to {last.date()}  symbols with candles {len(candle_map)}  clean universe {len(clean)}  dropped {len(dropped)}")
    print(f"  costs: flat {COST}% round trip for trade statistics; CI = 90% symbol-cluster bootstrap; evidence tier: REPLAY of the honest engine\n")

    p_trades = run_p(listing_map, candle_map)
    P = to_df(p_trades)
    print("=" * 128 + "\n0. HARNESS CHECK: policy P (causal resting, unchanged), expect about 228 trades, PF 2.43 / 1.71 / 1.91\n" + "=" * 128)
    block("Policy P, all symbols", P)

    s3_03 = run_s3(clean, 0.3, P_PRIMARY)
    s3_10 = run_s3(clean, 1.0, P_PRIMARY)
    x1_03 = run_s3(clean, 0.3, P_X1)
    x1_10 = run_s3(clean, 1.0, P_X1)
    D03, D10, X03, X10 = to_df(s3_03), to_df(s3_10), to_df(x1_03), to_df(x1_10)
    if D03.empty:
        print("S3 produced no trades")
        return
    print(f"\nS3 trades: primary slip 0.3% -> {len(D03)}, slip 1.0% -> {len(D10)}; X1 slip 0.3% -> {len(X03)}")

    print("\n" + "=" * 128 + "\n1. PRIMARY: S3 pooled with policy P's exits, resting order, slippage 0.3%\n" + "=" * 128)
    r03 = block("S3 pooled (slip 0.3%)", D03)
    yrs = year_table(D03, "S3 pooled")
    cell = {}
    print("\n  by base length (secondary hypotheses):")
    for k in CELLS:
        g = D03[D03.klass == k]
        cell[k] = block(f"  {k}", g)
        if len(g):
            year_table(g, k)
    print("\n  Stress: slippage 1.0%")
    block("S3 pooled (slip 1.0%)", D10)
    print("\n  Closed-only view:")
    block("S3 pooled closed only", D03[~D03.open], full=False)

    print("\n" + "=" * 128 + "\n2. SECONDARY: X1 exits (stop + trailing ratchet only, no time-based exits), slippage 0.3%\n" + "=" * 128)
    rx = block("S3 pooled, X1 exits", X03)
    yrs_x = year_table(X03, "S3 pooled X1")
    cell_x = {}
    for k in CELLS:
        g = X03[X03.klass == k]
        cell_x[k] = block(f"  {k} (X1)", g)
    print("\n  Stress: slippage 1.0%")
    block("S3 pooled X1 (slip 1.0%)", X10)
    print(f"  paired on the same entries: mean net X1 {X03.net.mean():+.2f}% vs P-exits {D03.net.mean():+.2f}%; median days held {X03.days_held_cal.median():.0f} vs {D03.days_held_cal.median():.0f}")

    print("\n" + "=" * 128 + "\n3. NAMED CASES (illustration only, not evidence): what S3 did on the owner's examples\n" + "=" * 128)
    named_cases("P exits", D03, clean)
    named_cases("X1 exits", X03, clean)

    print("\n" + "=" * 128 + f"\n4. CONTROL: same state, random eligible day's OPEN, one day per symbol, {a.draws} draws (not gating)\n" + "=" * 128)
    elig = {sym: control_indices(df, li, Params(), S3) for sym, (df, li) in clean.items()}
    elig = {s: v for s, v in elig.items() if v}
    ctrl = []
    for draw in range(a.draws):
        rng = np.random.default_rng(100 + draw)
        for sym, idx in elig.items():
            df, li = clean[sym]
            t = simulate_control_entry(df, sym, li, int(rng.choice(idx)), Params(), S3, CFG, slip_pct=0.3)
            if t is not None:
                t["draw"] = draw
                ctrl.append(t)
    C = to_df(ctrl)
    print(f"  symbols with an eligible state day: {len(elig)}; control trades {len(C)}")
    block("Control pooled (all draws)", C, full=False)
    d_m, d_lo, d_hi = diff_cluster_boot(D03.net.values, D03.symbol.values, C.net.values, C.symbol.values)
    print(f"  S3 pooled minus control: {d_m:+.2f}pp  CI90 [{d_lo:+.1f}, {d_hi:+.1f}]")
    for y, g in D03.groupby("year"):
        c = C[C.year == y]
        if len(c):
            print(f"    {int(y)}: S3 {g.net.mean():+6.2f}% (N={len(g)})  control {c.net.mean():+6.2f}% (N={len(c)})")

    od = {}
    for sym, (df, li) in clean.items():
        for d_ in order_days(df, li, Params(), S3):
            od[d_] = od.get(d_, 0) + 1
    ods = pd.Series(od).sort_index()
    full = ods.reindex(pd.date_range(ods.index.min(), last, freq="B"), fill_value=0)
    print(f"  resting orders per business day: mean {full.mean():.1f} median {full.median():.0f} p95 {full.quantile(0.95):.0f} max {int(full.max())}; last 250 days mean {full.iloc[-250:].mean():.1f}")

    print("\n" + "=" * 128 + "\n5. ACCOUNT SIMULATION: Rs 1 lakh, flat 10 slots x 10%, whole shares, costs on, P wins ties and same-symbol overlaps\n" + "=" * 128)
    pt = tag_priority(p_trades, 99.0)
    acc = {"P": account("P alone", pt)}
    contrib = {}
    arms = {"prim": (s3_03, s3_10), "X1": (x1_03, x1_10)}
    for arm, (t03, t10) in arms.items():
        for lbl, trades in (("0.3", t03), ("1.0", t10)):
            kept, drop = drop_same_symbol_overlap(tag_priority(trades, 0.0), p_trades)
            print(f"  [{arm}, S3 slip {lbl}%] same-symbol overlaps with P dropped: {len(drop)} of {len(trades)}")
            acc[f"S3{arm}_{lbl}_alone"] = account(f"S3 alone ({arm}, slip {lbl}%)", tag_priority(trades, 0.0))
            key = f"PS3{arm}_{lbl}"
            acc[key] = account(f"P + S3 ({arm}, slip {lbl}%)", pt + kept)
            base_log, comb_log = acc["P"]["log"], acc[key]["log"]
            is_s3 = comb_log.type.astype(str).str.startswith("S3_")
            s3_taken = comb_log[is_s3]
            p_taken_comb = set(zip(comb_log[~is_s3].symbol, comb_log[~is_s3].entry_date))
            displaced = base_log[[(s, e) not in p_taken_comb for s, e in zip(base_log.symbol, base_log.entry_date)]]
            vals = np.concatenate([s3_taken.net_pct.values, -displaced.net_pct.values])
            grp = np.concatenate([s3_taken.symbol.values, displaced.symbol.values])
            tot, lo, hi = sum_cluster_boot(vals, grp)
            contrib[(arm, lbl)] = (tot, lo, hi)
            print(f"    S3 taken {len(s3_taken)} (mean net {s3_taken.net_pct.mean() if len(s3_taken) else float('nan'):+.2f}%); P displaced {len(displaced)} "
                  f"(mean net {displaced.net_pct.mean() if len(displaced) else float('nan'):+.2f}%, outliers >= +25%: {int((displaced.net_pct >= 25).sum())}); "
                  f"net contribution {tot:+.1f}pp CI90 [{lo:+.1f}, {hi:+.1f}]")
            print("      by year P+S3: " + "  ".join(f"{y}: {v:+.0f}%" for y, v in acc[key]["summary"].get("by_year_pct", {}).items()))
    print("      by year P:    " + "  ".join(f"{y}: {v:+.0f}%" for y, v in acc["P"]["summary"].get("by_year_pct", {}).items()))

    print("\n" + "=" * 128 + "\n6. ADOPTION BAR (docstring), applied to both exit arms\n" + "=" * 128)
    P_s = acc["P"]["summary"]

    def neg_years(rows):
        return [y for y, (n_, m_, _p, _w) in rows.items() if n_ >= 8 and m_ < 0]

    def verdict(arm, r, yr):
        ok = {"B1": r["n"] >= 100, "B2": r["lo"] > 0, "B3": r["mean_ex1"] > 0, "B4": len(neg_years(yr)) <= 1}

        def b5(lbl):
            c = acc[f"PS3{arm}_{lbl}"]["summary"]
            c_cal = c["cagr_pct"] / abs(c["max_drawdown_pct"]) if c["max_drawdown_pct"] else float("nan")
            return c["total_return_pct"] >= P_s["total_return_pct"] and c_cal >= P_s["calmar"] and contrib[(arm, lbl)][0] > 0

        ok["B5"] = b5("0.3") and b5("1.0")
        print(f"  [{arm}] B1 N>=100 {'PASS' if ok['B1'] else 'FAIL'} (N={r['n']}) | B2 CI lower>0 {'PASS' if ok['B2'] else 'FAIL'} (mean {r['mean']:+.2f}%, CI [{r['lo']:+.2f}, {r['hi']:+.2f}])"
              f" | B3 ex-top1>0 {'PASS' if ok['B3'] else 'FAIL'} ({r['mean_ex1']:+.2f}%) | B4 <=1 negative year {'PASS' if ok['B4'] else 'FAIL'} ({neg_years(yr)}) | B5 account {'PASS' if ok['B5'] else 'FAIL'}")
        for lbl in ("0.3", "1.0"):
            c = acc[f"PS3{arm}_{lbl}"]["summary"]
            print(f"      B5 slip {lbl}%: P+S3 ret {c['total_return_pct']:+.1f}% vs P {P_s['total_return_pct']:+.1f}%; maxDD {c['max_drawdown_pct']:.1f}% vs {P_s['max_drawdown_pct']:.1f}%; "
                  f"contribution {contrib[(arm, lbl)][0]:+.1f}pp [{contrib[(arm, lbl)][1]:+.1f}, {contrib[(arm, lbl)][2]:+.1f}]")
        print(f"  [{arm}] VERDICT: {'MEETS THE BAR (forward-test candidate, PAPER_ONLY)' if all(ok.values()) else 'DOES NOT MEET THE BAR (not adopted)'}")

    verdict("prim", r03, yrs)
    verdict("X1", rx, yrs_x)
    for arm, cellmap, D in (("prim", cell, D03), ("X1", cell_x, X03)):
        for k in CELLS:
            c = cellmap[k]
            if c["n"] < 40:
                print(f"  {k} [{arm}]: UNDERPOWERED (N = {c['n']} < 40)")
                continue
            g = D[D.klass == k]
            y_ = {int(y): (len(x), float(x.net.mean()), 0, 0) for y, x in g.groupby("year")}
            print(f"  {k} [{arm}]: N = {c['n']} mean {c['mean']:+.2f}% CI [{c['lo']:+.2f}, {c['hi']:+.2f}] exTop1 {c['mean_ex1']:+.2f}% negative years {neg_years(y_)} -> "
                  f"{'meets B2-B4' if c['lo'] > 0 and c['mean_ex1'] > 0 and len(neg_years(y_)) <= 1 else 'does not meet B2-B4'}")

    if a.out_dir:
        os.makedirs(a.out_dir, exist_ok=True)
        for name, d in (("setup3_trades_slip0.3", D03), ("setup3_trades_slip1.0", D10), ("setup3_x1_trades_slip0.3", X03), ("setup3_control_trades", C)):
            d.drop(columns=[c for c in ("path",) if c in d.columns]).to_csv(os.path.join(a.out_dir, name + ".csv"), index=False)
        with open(os.path.join(a.out_dir, "setup3_output.txt"), "w", encoding="utf-8") as fh:
            fh.write(tee.buf.getvalue())
    sys.stdout = sys.__stdout__


if __name__ == "__main__":
    main()
