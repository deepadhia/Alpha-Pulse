# -*- coding: utf-8 -*-
"""
research_setup2_x1.py -- S2 (pivot breakout BELOW the listing high, unchanged) re-tested with the "slow-leader" exits X1.

PRE-REGISTRATION (written 2026-10-22 BEFORE any S2-with-X1 outcome was computed).
Why now: LEARNINGS 18 failed S2 under policy P's exits and said "retest only with a pre-registered redesign of exits for base setups".  Section 20
then found that disabling every time-based exit (X1: protective stop + trailing ratchet only) turns the late post-listing-high breakout (S3) from
+2.5% to +4.4% per trade and lets it pass the bar.  The same question applies to S2: is its failure the entries or the dead-money exits?
Also looked at before freezing (outcome-blind): a cross-sectional study of 33,231 weekly IPO-days (2022-2026): no daily price/volume feature
identifies a coming +50% run before it starts (walk-forward gradient boosting AUC 0.57-0.61, top-decile lift 1.3-1.5x; the only feature positive in
all four years is volatility, AUC 0.62, which is largely mechanical: volatile names reach +50% more easily; momentum features flip sign in 2025;
universe breadth does not predict the run rate).  So the lever left is catching runs AFTER they start, with exits that do not kill slow movers.

THE TEST: exactly the S2 setup of research_setup2.py / core/setup2.py (parameters unchanged: pivot 10 sessions, age 15-200, prior close > 10% below
the listing high, within 10% of the pivot, P's causal liquidity gates, trigger pivot +1.5%, limit +2.5%, fill +0.3%, stop = 15-session swing low
x 0.97 capped at 12%, 30-bar cooldown), with exits X1 = research_setup3.NO_TIME_EXITS (all time-based exits disabled).  Cells H1/H2/H3 as before.
ONE primary test: S2-X1 pooled.  ADOPTION BAR identical to research_setup2.py (B1 N>=100, B2 CI90 lower > 0, B3 mean > 0 without the largest trade,
B4 at most one negative year with N>=8, B5 account vs policy P at slip 0.3% and 1.0%).  Cells need N >= 40.
Looks in this family so far: S3-P-exits, S3-X1, now S2-X1 (a pass at 90% by chance is about 1 in 10 per look, so a pass is a forward-test candidate,
never adoption).  ADDITIONALLY reported (not gating): P + S2-X1 + S3-X1 together in the account, since the three setups were designed to be disjoint
(P age <= 60 at the listing high; S2 below the listing high; S3 at or above the post-listing high, age >= 61).

  .venv\\Scripts\\python.exe research_setup2_x1.py --cache data.pkl --out-dir reports/2026-10-22
"""
import argparse
import logging
import os
import pickle
import sys

import numpy as np
import pandas as pd

logging.disable(logging.CRITICAL)
from core.honest_backtest import Params
from core.setup2 import Setup2Params, clean_universe, simulate_symbol_setup2
from core.setup3 import Setup3Params, simulate_symbol_setup3
from research_alpha_lab import COST
from research_setup2 import (CFG, Tee, account, block, drop_same_symbol_overlap, run_p, sum_cluster_boot, tag_priority, to_df, year_table)
from research_setup3 import NO_TIME_EXITS

S2 = Setup2Params()
S3 = Setup3Params()
PX1 = Params(exit_overrides=NO_TIME_EXITS)
CELLS = ("H1_BASE_OLD", "H2_RECOVERY", "H3_BASE_YOUNG")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--out-dir", default=None)
    a = ap.parse_args()
    tee = Tee()
    sys.stdout = tee
    with open(a.cache, "rb") as fh:
        listing_map, candle_map = pickle.load(fh)
    clean, _ = clean_universe(listing_map, candle_map)
    print(f"research_setup2_x1.py  clean universe {len(clean)}  costs flat {COST}% round trip; CI = 90% symbol-cluster bootstrap; tier: REPLAY\n")
    p_trades = run_p(listing_map, candle_map)
    block("Policy P (harness check, expect N=228 PF 2.43/1.71/1.91)", to_df(p_trades))

    def run2(slip):
        out = []
        for sym, (df, li) in clean.items():
            out.extend(simulate_symbol_setup2(df, sym, li, PX1, S2, CFG, slip_pct=slip, include_open=True))
        return out

    def run3(slip):
        out = []
        for sym, (df, li) in clean.items():
            out.extend(simulate_symbol_setup3(df, sym, li, PX1, S3, CFG, slip_pct=slip, include_open=True))
        return out

    s2_03, s2_10, s3_03 = run2(0.3), run2(1.0), run3(0.3)
    D03, D10 = to_df(s2_03), to_df(s2_10)
    print(f"\nS2-X1 trades: slip 0.3% -> {len(D03)}, slip 1.0% -> {len(D10)}")
    print("\n" + "=" * 120 + "\n1. PRIMARY: S2 pooled with X1 exits\n" + "=" * 120)
    r = block("S2-X1 pooled (slip 0.3%)", D03)
    yrs = year_table(D03, "S2-X1 pooled")
    cell = {}
    for k in CELLS:
        g = D03[D03.klass == k]
        cell[k] = block(f"  {k}", g)
        if len(g):
            year_table(g, k)
    print("\n  Stress slip 1.0%:")
    block("S2-X1 pooled (slip 1.0%)", D10, full=False)
    block("S2-X1 closed only", D03[~D03.open], full=False)
    ex_top = D03.groupby("symbol").net.sum().sort_values(ascending=False).head(10).index
    block("S2-X1 without its 10 best symbols (post hoc)", D03[~D03.symbol.isin(ex_top)], full=False)

    print("\n" + "=" * 120 + "\n2. ACCOUNT: Rs 1 lakh, 10 x 10%, P wins ties and same-symbol overlaps\n" + "=" * 120)
    pt = tag_priority(p_trades, 99.0)
    acc = {"P": account("P alone", pt)}
    contrib = {}
    for lbl, trades in (("0.3", s2_03), ("1.0", s2_10)):
        kept, drop = drop_same_symbol_overlap(tag_priority(trades, 0.0), p_trades)
        acc["S2alone" + lbl] = account(f"S2-X1 alone (slip {lbl}%)", tag_priority(trades, 0.0))
        key = "PS2" + lbl
        acc[key] = account(f"P + S2-X1 (slip {lbl}%)", pt + kept)
        base_log, comb_log = acc["P"]["log"], acc[key]["log"]
        is_s2 = comb_log.type.astype(str).str.startswith("S2_")
        taken = comb_log[is_s2]
        p_comb = set(zip(comb_log[~is_s2].symbol, comb_log[~is_s2].entry_date))
        disp = base_log[[(s, e) not in p_comb for s, e in zip(base_log.symbol, base_log.entry_date)]]
        tot, lo, hi = sum_cluster_boot(np.concatenate([taken.net_pct.values, -disp.net_pct.values]), np.concatenate([taken.symbol.values, disp.symbol.values]))
        contrib[lbl] = (tot, lo, hi)
        print(f"    S2 taken {len(taken)} (mean net {taken.net_pct.mean() if len(taken) else float('nan'):+.2f}%); P displaced {len(disp)} (mean {disp.net_pct.mean() if len(disp) else float('nan'):+.2f}%); "
              f"contribution {tot:+.1f}pp CI90 [{lo:+.1f}, {hi:+.1f}]")
    # all three together
    s3k, _ = drop_same_symbol_overlap(tag_priority(s3_03, 0.0), p_trades)
    s2k, _ = drop_same_symbol_overlap(tag_priority(s2_03, 0.0), p_trades)
    acc["ALL"] = account("P + S2-X1 + S3-X1 (slip 0.3%)", pt + s2k + s3k)
    print("      by year P:           " + "  ".join(f"{y}: {v:+.0f}%" for y, v in acc["P"]["summary"].get("by_year_pct", {}).items()))
    print("      by year P+S2X1+S3X1: " + "  ".join(f"{y}: {v:+.0f}%" for y, v in acc["ALL"]["summary"].get("by_year_pct", {}).items()))

    print("\n" + "=" * 120 + "\n3. BAR\n" + "=" * 120)
    Ps = acc["P"]["summary"]

    def neg(rows):
        return [y for y, (n_, m_, _p, _w) in rows.items() if n_ >= 8 and m_ < 0]

    def b5(lbl):
        c = acc["PS2" + lbl]["summary"]
        cal = c["cagr_pct"] / abs(c["max_drawdown_pct"]) if c["max_drawdown_pct"] else float("nan")
        return c["total_return_pct"] >= Ps["total_return_pct"] and cal >= Ps["calmar"] and contrib[lbl][0] > 0

    ok = {"B1": r["n"] >= 100, "B2": r["lo"] > 0, "B3": r["mean_ex1"] > 0, "B4": len(neg(yrs)) <= 1, "B5": b5("0.3") and b5("1.0")}
    print("  " + " | ".join(f"{k} {'PASS' if v else 'FAIL'}" for k, v in ok.items()) + f"   (N={r['n']}, mean {r['mean']:+.2f}% CI [{r['lo']:+.2f}, {r['hi']:+.2f}], exTop1 {r['mean_ex1']:+.2f}%, negative years {neg(yrs)})")
    print(f"  VERDICT S2-X1: {'MEETS THE BAR (forward-test candidate, PAPER_ONLY)' if all(ok.values()) else 'DOES NOT MEET THE BAR (not adopted)'}")
    for k in CELLS:
        c = cell[k]
        print(f"  {k}: " + (f"UNDERPOWERED (N={c['n']})" if c["n"] < 40 else f"N={c['n']} mean {c['mean']:+.2f}% CI [{c['lo']:+.2f}, {c['hi']:+.2f}] exTop1 {c['mean_ex1']:+.2f}%"))
    if a.out_dir:
        os.makedirs(a.out_dir, exist_ok=True)
        D03.drop(columns=[c for c in ("path",) if c in D03.columns]).to_csv(os.path.join(a.out_dir, "setup2_x1_trades_slip0.3.csv"), index=False)
        with open(os.path.join(a.out_dir, "setup2_x1_output.txt"), "w", encoding="utf-8") as fh:
            fh.write(tee.buf.getvalue())
    sys.stdout = sys.__stdout__


if __name__ == "__main__":
    main()
