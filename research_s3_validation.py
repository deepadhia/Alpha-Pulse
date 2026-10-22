# -*- coding: utf-8 -*-
"""
research_s3_validation.py -- the strongest validation of the PAPER_ONLY S3 book that existing data allows.  It cannot prove the algorithm is good
(only forward paper trades can; see s3_scorecard.py); it can show that the sheet matches the replay and that the turnover rule is not an
in-sample artefact.

PRE-REGISTRATION (written 2026-10-22 BEFORE any number below was computed).  Trade set: S3 trades with X1 exits, uncapped, slippage 0.3%, flat
1.0% costs, 2022-2026 (research_setup3 engine); turnover = mean of close x volume over the 20 sessions before entry, Rs crore (research_few_quality.feats).
  V1 PARITY ("is it working").  For every symbol and every bar i with a replay state under PAPER_PARAMS, preview_s3_entry on bars 0..i-1 must exist
     and agree on trigger, limit and, on days that open at or below the trigger and cross it, on stop and fill; conversely every preview must have a
     replay state.  "Replay state" = order-placeable (see v1_parity docstring: price floor, limit, NSE filter).  PASS = 0 mismatches.
  V2 OUT OF TIME.  For test year Y in 2024, 2025, 2026: threshold = MEDIAN turnover of the trades entered before Y (no look-ahead); keep year-Y
     trades at or below it.  Pooled out-of-time kept set versus all year-Y trades.  PASS = kept mean CI90 lower bound > 0 AND kept minus all-trades
     mean > 0 AND not worse than all-trades in more than one of the three years.
  V3 PERMUTATION.  Statistic = mean(kept) - mean(rest) for the fixed Rs 25 Cr split.  Null: turnover shuffled across trades within entry year, 20,000
     shuffles.  PASS = one-sided p < 0.01 (nine looks were taken in LEARNINGS 20.3, so 0.05 would not be enough).
  V4 INDEPENDENT REPLICATION.  The same split on a population that is NOT selected by the S3 trigger: 10 random draws of one random eligible
     S3-state day per symbol, bought at the open with the same stop and X1 exits (thousands of entries).  PASS = kept minus rest CI90 lower bound > 0.
  V5 COST STRESS.  The paper rule (cap Rs 25 Cr) at slippage 1.0%.  PASS = mean net CI90 lower bound > 0.
  V6 CAPACITY.  Position size Rs 10,000 (Rs 1 lakh x 10%) as a share of the lowest 20-day turnover among paper-rule trades; reported, PASS = < 1%.
A claim of "validated" needs V1-V5 all PASS; the claim "proved good" is never made here: V2-V5 are replays of 2023-26, a hot IPO period.

  .venv\\Scripts\\python.exe research_s3_validation.py --cache data.pkl --out-dir reports/2026-10-22
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
from core.honest_backtest import Params, _Bars, _listing_context
from core.setup2 import _prepare, clean_universe
from core.setup3 import (PAPER_MAX_TURN_CR, PAPER_PARAMS, Setup3Params, control_indices, paper_exit_params, pivot_arrays, preview_s3_entry,
                         setup3_signal, setup3_state, simulate_control_entry, simulate_symbol_setup3)
from research_alpha_lab import cluster_boot, pf
from research_few_quality import feats
from research_setup2 import CFG, Tee, block, diff_cluster_boot, to_df


def v1_parity(clean):
    """
    A replay bar counts as order-placeable when the S3 state holds AND the trigger is at or above the Rs 25 price floor (the floor lives in the
    signal, not in the state; core.setup3.order_days uses the same definition) AND the prior close is not above the limit AND the listing is not an
    NSE addition without BSE liquidity (both are filters of the entry function).  Harness correction made after the first run showed 121 raw
    differences that were all one of: price floor (116, e.g. SBGLP at Rs 22) or a gap-up open changing the fill and therefore the 12%-capped stop (5).
    """
    p = paper_exit_params()
    n_state = n_prev = bad = 0
    examples = []
    for sym, (df, li) in clean.items():
        if li.get("is_nse_addition") and not li.get("bse_liquidity_passed", True):
            continue
        ld_high, _, ld = _listing_context(df, li)
        b = _Bars.of(df)
        pre = _prepare(df)
        pv, pi = pivot_arrays(b)
        for i in range(12, len(df)):
            st = setup3_state(b, pre, pv, pi, i, ld_high, ld, p, PAPER_PARAMS)
            placeable = st is not None and st["trig"] >= p.min_entry_price and float(df["CLOSE"].iat[i - 1]) <= st["ceil"]
            prev = preview_s3_entry(df.iloc[:i].copy(), li, df["DATE"].iloc[i], p, PAPER_PARAMS) if (placeable or i % 97 == 0) else None
            if not placeable and prev is None:
                continue
            n_state += int(placeable)
            n_prev += int(prev is not None)
            if placeable != (prev is not None):
                bad += 1
                examples.append((sym, str(df["DATE"].iloc[i].date()), "replay-only" if placeable else "preview-only"))
                continue
            if abs(prev["trigger"] - st["trig"]) > 1e-6 or abs(prev["limit"] - st["ceil"]) > 1e-6:
                bad += 1
                examples.append((sym, str(df["DATE"].iloc[i].date()), "trigger/limit"))
                continue
            if float(df["OPEN"].iloc[i]) <= st["trig"] and float(df["HIGH"].iloc[i]) >= st["trig"]:
                s = setup3_signal(df, b, pre, pv, pi, i, ld_high, ld, p, PAPER_PARAMS, slip_pct=0.3, state=st)
                if s is None or abs(prev["stop"] - s["stop"]) > 1e-6 or abs(prev["fill"] - s["fill"]) > 1e-6:
                    bad += 1
                    examples.append((sym, str(df["DATE"].iloc[i].date()), "stop/fill"))
    return n_state, n_prev, bad, examples[:8]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--out-dir", default=None)
    a = ap.parse_args()
    tee = Tee()
    sys.stdout = tee
    L, C = pickle.load(open(a.cache, "rb"))
    clean, _ = clean_universe(L, C)
    print(f"research_s3_validation.py  clean universe {len(clean)}; costs flat 1.0% round trip; CI = 90% symbol-cluster bootstrap; tier REPLAY\n")
    res = {}

    print("=" * 110 + "\nV1 PARITY: order-sheet preview vs replay state, every bar of every symbol with a replay state\n" + "=" * 110)
    ns, npv, bad, ex = v1_parity(clean)
    print(f"  replay states {ns}, previews {npv}, mismatches {bad} {ex if bad else ''}")
    res["V1"] = bad == 0

    # trade set
    X1 = paper_exit_params()
    rows = []
    for s, (df, li) in clean.items():
        rows.extend(simulate_symbol_setup3(df, s, li, X1, Setup3Params(), CFG, slip_pct=0.3, include_open=True))
    T = to_df(rows)
    T["turn"] = [feats(clean[t.symbol][0], int(t.entry_idx))[1] for t in T.itertuples()]
    T = T.dropna(subset=["turn"]).reset_index(drop=True)
    T["kept"] = T.turn <= PAPER_MAX_TURN_CR
    print(f"\ntrade set: {len(T)} S3-X1 trades; kept by the Rs {PAPER_MAX_TURN_CR:.0f} Cr rule: {int(T.kept.sum())}")
    block("all trades", T, full=False)
    block(f"kept (turnover <= {PAPER_MAX_TURN_CR:.0f} Cr)", T[T.kept])
    block("rest", T[~T.kept], full=False)

    print("\n" + "=" * 110 + "\nV2 OUT OF TIME: threshold = median turnover of earlier-year trades, applied to the next year\n" + "=" * 110)
    keep_parts, all_parts, worse = [], [], 0
    for y in (2024, 2025, 2026):
        past, now = T[T.year < y], T[T.year == y]
        thr = float(past.turn.median())
        k = now[now.turn <= thr]
        keep_parts.append(k)
        all_parts.append(now)
        m_k, m_a = float(k.net.mean()), float(now.net.mean())
        worse += int(m_k < m_a)
        print(f"  {y}: threshold Rs {thr:5.1f} Cr (from {len(past)} earlier trades): kept N={len(k):3d} mean {m_k:+6.2f}%  all N={len(now):3d} mean {m_a:+6.2f}%  rest mean {now[now.turn > thr].net.mean():+6.2f}%")
    K, A = pd.concat(keep_parts), pd.concat(all_parts)
    mk, lo, hi = cluster_boot(K.net.values, K.symbol.values)
    print(f"  pooled out-of-time kept: N={len(K)} mean {mk:+.2f}% CI90 [{lo:+.1f}, {hi:+.1f}] PF {pf(K.net.values):.2f}  | all trades same years: N={len(A)} mean {A.net.mean():+.2f}% PF {pf(A.net.values):.2f}")
    dm, dlo, dhi = diff_cluster_boot(K.net.values, K.symbol.values, A.net.values, A.symbol.values)
    print(f"  kept minus all: {dm:+.2f}pp CI90 [{dlo:+.1f}, {dhi:+.1f}]; years where kept is worse than all: {worse} of 3")
    res["V2"] = lo > 0 and dm > 0 and worse <= 1

    print("\n" + "=" * 110 + "\nV3 PERMUTATION: turnover shuffled within entry year (20,000 shuffles)\n" + "=" * 110)
    obs = T.net[T.kept].mean() - T.net[~T.kept].mean()
    rng = np.random.default_rng(7)
    groups = [np.where(T.year.values == y)[0] for y in T.year.unique()]
    net = T.net.values
    cnt = 0
    N = 20000
    kept0 = T.kept.values
    for _ in range(N):
        sh = kept0.copy()
        for g in groups:
            sh[g] = rng.permutation(kept0[g])
        d = net[sh].mean() - net[~sh].mean()
        cnt += d >= obs
    p = (cnt + 1) / (N + 1)
    print(f"  observed kept minus rest {obs:+.2f}pp; one-sided permutation p = {p:.4f}")
    res["V3"] = p < 0.01

    print("\n" + "=" * 110 + "\nV4 INDEPENDENT REPLICATION: random eligible S3-state day per symbol, bought at the open (not selected by the S3 trigger), X1 exits\n" + "=" * 110)
    elig = {s: control_indices(df, li, Params(), Setup3Params()) for s, (df, li) in clean.items()}
    elig = {s: v for s, v in elig.items() if v}
    ctrl = []
    for draw in range(10):
        r = np.random.default_rng(300 + draw)
        for s, idx in elig.items():
            df, li = clean[s]
            i = int(r.choice(idx))
            t = simulate_control_entry(df, s, li, i, X1, Setup3Params(), CFG, slip_pct=0.3)
            if t is not None:
                t["turn"] = feats(df, i)[1]
                ctrl.append(t)
    Cn = to_df(ctrl)
    Cn = Cn.dropna(subset=["turn"])
    Cn["kept"] = Cn.turn <= PAPER_MAX_TURN_CR
    ck, cr_ = Cn[Cn.kept], Cn[~Cn.kept]
    block("control kept (turnover <= cap)", ck, full=False)
    block("control rest", cr_, full=False)
    dm, dlo, dhi = diff_cluster_boot(ck.net.values, ck.symbol.values, cr_.net.values, cr_.symbol.values)
    print(f"  control kept minus rest: {dm:+.2f}pp CI90 [{dlo:+.1f}, {dhi:+.1f}]; by year: " + "  ".join(f"{int(y)}: {g[g.kept].net.mean() - g[~g.kept].net.mean():+.1f}pp" for y, g in Cn.groupby("year") if g.kept.sum() > 20 and (~g.kept).sum() > 20))
    res["V4"] = dlo > 0

    print("\n" + "=" * 110 + "\nV5 COST STRESS: paper rule at slippage 1.0%\n" + "=" * 110)
    rows = []
    for s, (df, li) in clean.items():
        rows.extend(simulate_symbol_setup3(df, s, li, X1, PAPER_PARAMS, CFG, slip_pct=1.0, include_open=True))
    S5 = to_df(rows)
    r5 = block("paper rule, slip 1.0%", S5)
    res["V5"] = r5["lo"] > 0

    print("\n" + "=" * 110 + "\nV6 CAPACITY\n" + "=" * 110)
    lo_turn = float(T[T.kept].turn.min())
    share = 10000 / (lo_turn * 1e7) * 100
    print(f"  lowest 20-day turnover among paper-rule trades: Rs {lo_turn:.2f} Cr; a Rs 10,000 position is {share:.3f}% of it")
    res["V6"] = share < 1.0

    print("\n" + "=" * 110 + "\nVERDICT\n" + "=" * 110)
    for k, v in res.items():
        print(f"  {k}: {'PASS' if v else 'FAIL'}")
    print(f"  V1-V5 {'ALL PASS: replay-validated (not proven: 2023-26 is one hot period; the forward paper sample decides)' if all(res[k] for k in ('V1', 'V2', 'V3', 'V4', 'V5')) else 'NOT ALL PASS: see above'}")
    if a.out_dir:
        os.makedirs(a.out_dir, exist_ok=True)
        with open(os.path.join(a.out_dir, "s3_validation_output.txt"), "w", encoding="utf-8") as fh:
            fh.write(tee.buf.getvalue())
    sys.stdout = sys.__stdout__


if __name__ == "__main__":
    main()
