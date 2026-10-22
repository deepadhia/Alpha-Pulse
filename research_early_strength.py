# -*- coding: utf-8 -*-
"""
research_early_strength.py -- S4: buy the IPOs that are still holding strength ten sessions after listing.

PRE-REGISTRATION (written 2026-10-22 BEFORE any S4 trade outcome was computed).
Why: research_w50_deep.py (exploratory) found that, among 430 liquid IPOs, W50 names (+50% within 120 days) differ from the rest only in their first
ten sessions: share of closes above the listing-day close (AUC 0.71), return at session 10 (0.79), listing-day low held (0.61); nothing at listing
(market cap, issue size / price / subscription, listing gain, minute-bar listing-day shape) passes.  The catch: 23% of W50 names reach +50% within
30 days, so ret10 partly CONTAINS the move (hindsight).  Only a forward test, entry AFTER the signal, can say whether it is tradable; LEARNINGS
17.10 saw the forward lift fall from 11% to 20% hit rate for session-20 strength.  Policy P already buys listing-high breakouts, so the question is
whether S4 adds anything it does not hold.

THE RULE (all information known at the close of session 10, session 0 = listing day, P0 = listing-day close; thresholds fixed here, not tuned):
  S4 signal if: (A) at least 8 of the 10 closes of sessions 1-10 are above P0; (B) close of session 10 > P0; (C) the lowest low of sessions 1-10 is
  at or above the listing-day low.  Liquidity gates as policy P (causal): fill >= Rs 25, prior-bar volume >= 150,000, 20-session mean turnover to the
  prior bar >= Rs 0.75 Cr, fewer than 3 circuit-like days in the last 15 sessions.
  Entry: market buy at the OPEN of session 11 (+0.3% slippage), no chase cap.  Stop: engine default (15-session swing low x 0.97, at most 12% below).
  One position per symbol, no re-entry.  Universe: core.setup2.clean_universe, listed 2022-06-01 .. 2026-06-15.  Costs: flat 1.0% round trip.
EXIT ARMS (both fixed in advance): P = policy P's exits; X1 = stop + trailing ratchet only (research_setup3.NO_TIME_EXITS).
CONTROL (same entry day, same gates, same stop and exits): ALL eligible IPOs entered at the open of session 11 regardless of A-C ("unconditional"),
  and the complement (fail A-C).  The signal must beat the control, otherwise the result is just "IPOs held for 11 sessions".
BAR (per arm): N >= 60; mean net (incl. open at MTM) 90% symbol-cluster CI lower bound > 0; mean > 0 without the largest trade; at most one negative
  year with N >= 8; AND signal minus unconditional control > 0 with CI lower bound > 0.  Reported: PF three ways, year table, overlap with policy P
  (P entry in sessions 1-10 or 11-30 of the same symbol), W50/W100 capture.  A pass means PAPER_ONLY candidate; two arms = two looks.

  .venv\\Scripts\\python.exe research_early_strength.py --cache data.pkl --out-dir reports/2026-10-22
"""
import argparse
import logging
import os
import pickle
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, ".")
logging.disable(logging.CRITICAL)
from core.honest_backtest import Params, _Bars, _listing_context, _make_open_trade, _run_trade, _stop_from_history
from core.setup2 import EOD, _prepare, clean_universe
from research_alpha_lab import COST, cluster_boot, pf
from research_setup2 import Tee, block, diff_cluster_boot, run_p, to_df, year_table
from research_setup3 import NO_TIME_EXITS

K = 10
CFG = EOD


def entry(df, li, i, p, slip=0.3):
    """Buy the open of bar i; None if a gate fails.  Returns a trade dict (open trades marked to market)."""
    ld_high, _, list_date = _listing_context(df, li)
    b = _Bars.of(df)
    pre = _prepare(df)
    if float(b.vols[i - 1]) < p.min_volume:
        return None
    turn, circ = float(pre.turn[i]), float(pre.circ[i])
    if (turn > 0 and turn < p.turnover_floor_cr) or circ >= p.circuit_day_threshold:
        return None
    fill = float(b.opens[i]) * (1 + slip / 100.0)
    if fill < p.min_entry_price:
        return None
    stop = _stop_from_history(df, i, fill, p)
    risk = (fill - stop) / fill
    if risk <= 0:
        return None
    age = int((pd.Timestamp(b.dates[i]) - list_date).days)
    sig = dict(type="S4", pivot=float(b.closes[i - 1]), post_confirm=0.0, max_chase=0.0, signal_price=fill, vol_ratio=0.0, ext_pct=0.0, rr=0.0, shelf_prng=None)
    cur = pd.Timestamp(b.dates[i])
    t = dict(entry_idx=i, entry_date=cur, entry_price=fill, signal=sig, signal_idx=i, signal_date=cur, trailing=stop, risk_pct=risk, max_runup=0.0,
             path=[], stop_initial=stop, age=age, missed=False)
    trade, ex = _run_trade(b, t, i, i, CFG, p, "")
    if trade is None:
        trade = _make_open_trade(t, b, "")
        trade["open"] = True
    else:
        trade["open"] = False
    trade["entry_idx"], trade["exit_idx"], trade["age_signal"] = i, ex, age
    return trade


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--out-dir", default=None)
    a = ap.parse_args()
    tee = Tee()
    sys.stdout = tee
    L, C = pickle.load(open(a.cache, "rb"))
    clean, _ = clean_universe(L, C)
    place = {pd.Timestamp("2026-08-17"), pd.Timestamp("2026-08-18")}
    arms = {"P": Params(), "X1": Params(exit_overrides=NO_TIME_EXITS)}
    rows = []
    for s, (df, li) in clean.items():
        ld = df.DATE.iloc[0].normalize()
        if ld < pd.Timestamp("2022-06-01") or ld > pd.Timestamp("2026-06-15") or ld in place or len(df) < 40:
            continue
        c, l = df.CLOSE.values.astype(float), df.LOW.values.astype(float)
        P0 = c[0]
        sig = bool((c[1:K + 1] > P0).sum() >= 8 and c[K] > P0 and l[1:K + 1].min() >= l[0])
        for arm, p in arms.items():
            t = entry(df, li, K + 1, p)
            if t is None:
                continue
            t.update(symbol=s, arm=arm, sig=sig)
            t.pop("path", None)
            rows.append(t)
    D = to_df(rows)
    D["sig"] = D.sig.astype(bool)
    print(f"research_early_strength.py  costs flat {COST}% round trip; CI = 90% symbol-cluster bootstrap; tier REPLAY\n"
          f"  eligible entries at session 11: P-arm {int((D.arm == 'P').sum())}, X1-arm {int((D.arm == 'X1').sum())}; with signal A-C: {int(((D.arm == 'P') & D.sig).sum())}\n")
    Pt = to_df(run_p(L, C))
    ok_all = {}
    for arm in ("P", "X1"):
        d = D[D.arm == arm]
        S, U, F = d[d.sig], d, d[~d.sig]
        print("=" * 120 + f"\nARM {arm} exits\n" + "=" * 120)
        rS = block(f"S4 signal ({arm})", S)
        yrs = year_table(S, f"S4 {arm}")
        block(f"Unconditional control, all eligible ({arm})", U, full=False)
        block(f"Fail A-C ({arm})", F, full=False)
        dm, lo, hi = diff_cluster_boot(S.net.values, S.symbol.values, U.net.values, U.symbol.values)
        print(f"  signal minus unconditional control: {dm:+.2f}pp CI90 [{lo:+.1f}, {hi:+.1f}]")
        dm2, lo2, hi2 = diff_cluster_boot(S.net.values, S.symbol.values, F.net.values, F.symbol.values)
        print(f"  signal minus fail group           : {dm2:+.2f}pp CI90 [{lo2:+.1f}, {hi2:+.1f}]")
        ps = set(Pt[(Pt.entry_idx <= 10)].symbol)
        pl = set(Pt[(Pt.entry_idx > 10) & (Pt.entry_idx <= 30)].symbol)
        print(f"  overlap with policy P: S4 symbols with a P entry in sessions 1-10: {int(S.symbol.isin(ps).sum())}, in 11-30: {int(S.symbol.isin(pl).sum())} of {len(S)}")
        neg = [y for y, (n_, m_, _p, _w) in yrs.items() if n_ >= 8 and m_ < 0]
        ok = {"N>=60": rS["n"] >= 60, "CI lower>0": rS["lo"] > 0, "ex-top1>0": rS["mean_ex1"] > 0, "<=1 negative year": len(neg) <= 1, "beats control (CI lower>0)": lo > 0}
        ok_all[arm] = all(ok.values())
        print("  BAR: " + " | ".join(f"{k} {'PASS' if v else 'FAIL'}" for k, v in ok.items()) + f"   negative years {neg}")
        print(f"  VERDICT {arm}: {'MEETS THE BAR (PAPER_ONLY candidate)' if ok_all[arm] else 'DOES NOT MEET THE BAR'}\n")
    if a.out_dir:
        os.makedirs(a.out_dir, exist_ok=True)
        D.to_csv(os.path.join(a.out_dir, "early_strength_trades.csv"), index=False)
        with open(os.path.join(a.out_dir, "early_strength_output.txt"), "w", encoding="utf-8") as fh:
            fh.write(tee.buf.getvalue())
    sys.stdout = sys.__stdout__


if __name__ == "__main__":
    main()
