# -*- coding: utf-8 -*-
"""
research_alpha_lab.py -- pre-registered search for changes that raise LISTING_BREAKOUT expectancy
under a REALISTIC entry model (intraday-crossing fills, still-open trades kept, costs charged).

PRE-REGISTRATION (written before any variant below was run on this engine, 2026-10-14)
----------------------------------------------------------------------------------------
Baseline B0: listing breakout, age >= 5 calendar days (the live scanner needs an end-of-day close check
below that, so only age >= 5 is intraday-fillable), trigger at listing high +1.5%, volume gate 1.8x,
every live gate, live exit engine, fill = trigger + 1.7% slip (the measured native fill extension is
+3.2% over the listing high vs the +1.5% trigger), costs 1.0% round trip, open trades marked to market.

Hypotheses, each with a stated mechanism.  NO threshold is searched on the full sample: the fixed rule is
stated, and every adaptive version chooses its threshold on EARLIER YEARS ONLY (walk-forward, test 2024-26).

  Entry / selection (a rule is adopted only if the trades it REMOVES have a negative net expectancy):
    F1  IPO supply regime: skip when many IPOs listed in the prior 30 days (attention/liquidity drain).
    F2  Cohort breadth: skip when few active IPOs sit above their own listing high (weak IPO tape).
    F3  Gap-up open: skip when the stock opens > +2% above yesterday's close (worse fill, exhaustion).
    F4  Liquidity floor: raise the 20-day turnover floor from Rs 0.75 Cr (only prior-study gate that helped).
    F5  Drop the volume gate (earlier studies: it adds nothing; removing it adds trades).
  Exit / structure (paired on the same entries; same trades, different exits):
    E1/E2  trail 10% -> 8% / 13%       E3/E4  winner cushion 0.80 -> 0.85 / 0.75
    E5     breakeven stop after +8% peak     E6  dead-money 14d -> 10d     E7  dead-money 14d -> 21d
    E8     trail start +3% -> +5%      E9  initial stop cap 12% -> 10%   E10 sell 1/3 at +20%
  Execution (not a market hypothesis; measured, because fill price dominates):
    X1  resting stop-limit at the trigger (slip 0.3%, no fill above listing high +2.5%) vs chasing (+1.7%)

ADOPTION BAR (all must hold): mechanism stated above; pooled out-of-sample improvement whose 90%
symbol-cluster-bootstrap bound excludes zero; not worse in more than one test year; trade count kept
>= 60%; no worse median or worst-year result.  Anything else is reported and NOT adopted.

Honest limits: the 60-minute hold and the 2.5% pullback ejection are not modelled (no intraday bars);
end-of-day volume is generous vs volume at trigger time; ~170 events means only effects of about 3pp or more
are detectable; ten-odd hypotheses means one false positive is expected by chance, hence the year-consistency
rule.  All numbers are net of the flat 1.0% round-trip cost.

  python research_alpha_lab.py --cache data.pkl --out-dir reports/2026-10-14
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

from core.honest_backtest import (LIVE_FAITHFUL, Params, _Bars, _listing_context, _make_open_trade,
                                   _make_trade, _run_trade, _stop_from_history, simulate_symbol_cross)

POP = dict(causal=False, vol_gate=True)   # entry population: set from the CLI
CFG = LIVE_FAITHFUL                        # exit-fill model: set from the CLI (--exit-mode)
COST = 1.0            # round-trip percent, same flat assumption as the audit
BASE_SLIP = 1.7       # measured native fill extension (+3.2% over listing high) minus the +1.5% trigger
MIN_AGE = 5
CI_LEVEL = 90.0


# ----------------------------------------------------------------------------- statistics

def cluster_boot(values, groups, n=4000, seed=0):
    """Mean and symbol-cluster bootstrap CI of `values` (ratio-of-sums per resampled cluster set)."""
    v = np.asarray(values, float)
    g = pd.factorize(np.asarray(groups))[0]
    k = g.max() + 1
    sums = np.bincount(g, weights=v, minlength=k)
    cnt = np.bincount(g, minlength=k).astype(float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, k, (n, k))
    means = sums[idx].sum(1) / cnt[idx].sum(1)
    lo, hi = np.percentile(means, [(100 - CI_LEVEL) / 2, 100 - (100 - CI_LEVEL) / 2])
    return float(v.mean()), float(lo), float(hi)


def pf(net):
    net = np.asarray(net, float)
    w, l = net[net > 0].sum(), -net[net < 0].sum()
    return float(w / l) if l > 0 else float("inf")


def line(label, net, groups=None, extra=""):
    net = np.asarray(net, float)
    if len(net) == 0:
        return f"{label:<58} no trades"
    m, lo, hi = cluster_boot(net, groups if groups is not None else np.arange(len(net)))
    s = np.sort(net)[::-1]
    ex3 = s[3:].mean() if len(s) > 3 else float("nan")
    return (f"{label:<58} N={len(net):3d} win={100 * (net > 0).mean():4.1f}% net={m:+5.2f}% "
            f"CI{CI_LEVEL:.0f}=[{lo:+5.1f},{hi:+5.1f}] med={np.median(net):+5.2f}% PF={pf(net):4.2f} "
            f"exTop3={ex3:+5.2f}% {extra}")


# ----------------------------------------------------------------------- cohort / market features

def build_cohort(candle_map, listing_map):
    """Per-date IPO-market state, built only from data available at the close of that date."""
    rows = []
    sent = []   # (listing date, day-0 close/open - 1)
    for sym, df in candle_map.items():
        li = listing_map.get(sym, {})
        ld_high, ld_low, list_date = _listing_context(df, li)
        d = pd.to_datetime(df["DATE"]).values
        c = df["CLOSE"].values.astype(float)
        age = (pd.to_datetime(d) - list_date).days.values
        keep = (age >= 0) & (age <= 60)
        ret20 = np.full(len(c), np.nan)
        if len(c) > 20:
            ret20[20:] = c[20:] / c[:-20] - 1.0
        rows.append(pd.DataFrame({"date": d[keep], "above": (c > ld_high)[keep], "ret20": ret20[keep]}))
        o0 = float(df["OPEN"].iat[0]) if "OPEN" in df.columns else 0.0
        if o0 > 0:
            sent.append((list_date, float(df["CLOSE"].iat[0]) / o0 - 1.0))
    panel = pd.concat(rows)
    g = panel.groupby("date")
    cohort = pd.DataFrame({"n_active": g.size(), "frac_above": g["above"].mean(),
                           "med_ret20": g["ret20"].median()})
    sent_df = pd.DataFrame(sent, columns=["ld", "sent"])
    # supply counts every listing record, including symbols without candles
    dates = []
    for s, li in listing_map.items():
        v = li.get("listing_date")
        if v is not None and not pd.isna(v):
            ts = pd.Timestamp(v)
            dates.append(ts.tz_localize(None).normalize() if ts.tzinfo else ts.normalize())
    all_ld = pd.Series(sorted(dates)).reset_index(drop=True)
    return cohort.sort_index(), all_ld, sent_df


def cohort_at(cohort, all_ld, sent_df, date):
    """State known BEFORE the open of `date` (last cohort row strictly before it)."""
    prior = cohort.loc[cohort.index < date]
    last = prior.iloc[-1] if len(prior) else None
    d0 = date - pd.Timedelta(days=30)
    supply30 = int(((all_ld >= d0) & (all_ld < date)).sum())
    d60 = date - pd.Timedelta(days=60)
    m = sent_df[(sent_df.ld >= d60) & (sent_df.ld < date)]
    return dict(
        supply30=supply30,
        n_active=float(last["n_active"]) if last is not None else np.nan,
        frac_above=float(last["frac_above"]) if last is not None else np.nan,
        med_ret20=float(last["med_ret20"]) if last is not None else np.nan,
        ld_sent60=float(m.sent.mean()) if len(m) else np.nan,
    )


# ------------------------------------------------------------------------ baseline event table

def baseline_trades(candle_map, listing_map, p, slip=None, fill_cap_pct="pop", vol_gate=None,
                    min_age=MIN_AGE, signal_filter=None, causal=None):
    slip = POP.get("slip", BASE_SLIP) if slip is None else slip
    fill_cap_pct = POP.get("cap") if fill_cap_pct == "pop" else fill_cap_pct
    vol_gate = POP["vol_gate"] if vol_gate is None else vol_gate
    causal = POP["causal"] if causal is None else causal
    out = []
    for sym, df in candle_map.items():
        out.extend(simulate_symbol_cross(df, sym, listing_map.get(sym, {}), p, vol_gate=vol_gate,
                                         slip_pct=slip, fill_cap_pct=fill_cap_pct, min_age=min_age,
                                         include_open=True, signal_filter=signal_filter, causal=causal, cfg=CFG))
    return out


def attach_features(trades, candle_map, listing_map, cohort_state, feat_store):
    rows = []
    for t in trades:
        f = feat_store.get((t["symbol"], t["entry_idx"]), {})
        r = dict(t)
        r.pop("path", None)
        r.update(f)
        rows.append(r)
    df = pd.DataFrame(rows)
    df["net"] = df["pnl_pct"] - COST
    df["entry_ts"] = pd.to_datetime(df["entry_date"])
    df["year"] = df["entry_ts"].dt.year
    return df


def make_feature_filter(feat_store, cohort, all_ld, sent_df, listing_map):
    """signal_filter that records features for every signal that passed the live gates, and keeps all."""
    def f(ctx):
        df, i = ctx["df"], ctx["i"]
        c = df["CLOSE"].values.astype(float)
        h = df["HIGH"].values.astype(float)
        l = df["LOW"].values.astype(float)
        o = float(df["OPEN"].iat[i]) if "OPEN" in df.columns else c[i]
        prev = c[i - 1]
        feat = dict(
            age=ctx["age"], vr=ctx["vr"], ext=ctx["ext"], risk_pct=ctx["risk"] * 100.0, rr=ctx["rr"],
            turn20=ctx["turn"], ld_range=ctx["rng_pct"],
            gap=(o / prev - 1.0) * 100.0 if prev > 0 else 0.0,
            ret5=(prev / c[i - 6] - 1.0) * 100.0 if i >= 6 else np.nan,
            ret10=(prev / c[i - 11] - 1.0) * 100.0 if i >= 11 else np.nan,
            atr10=float(np.mean((h[max(0, i - 10):i] - l[max(0, i - 10):i]) / c[max(0, i - 10):i])) * 100.0,
            range10=(h[max(0, i - 10):i].max() / l[max(0, i - 10):i].min() - 1.0) * 100.0,
            dist_prev=(prev / ctx["ld_high"] - 1.0) * 100.0,
            ld_high=ctx["ld_high"],
        )
        feat.update(cohort_at(cohort, all_ld, sent_df, ctx["date"]))
        feat_store[(ctx["symbol"], i)] = feat
        return True
    return f


# ------------------------------------------------------------------------------ exit variants

def rerun_exits(base, candle_map, p_var, post=None):
    """Same entries, different exit/stop rules.  Returns per-trade gross pnl list aligned to `base`."""
    out = []
    bars = {}
    for r in base.itertuples():
        df = candle_map[r.symbol]
        b = bars.setdefault(r.symbol, _Bars.of(df))
        i = int(r.entry_idx)
        fill = float(r.entry_price)
        stop = _stop_from_history(df, i, fill, p_var)
        risk = (fill - stop) / fill
        if risk <= 0:
            out.append(np.nan)
            continue
        cur = pd.Timestamp(b.dates[i])
        sig = dict(type="LISTING_BREAKOUT", pivot=float(r.ld_high), post_confirm=float(r.ext),
                   signal_price=fill, vol_ratio=float(r.vr), ext_pct=float(r.ext), rr=float(r.rr))
        t = dict(entry_idx=i, entry_date=cur, entry_price=fill, signal=sig, signal_idx=i, signal_date=cur,
                 trailing=stop, risk_pct=risk, max_runup=0.0, path=[], stop_initial=stop,
                 age=int(r.age), missed=False)
        trade, ex = _run_trade(b, t, i, i, CFG, p_var, r.symbol)
        if trade is None:
            trade = _make_open_trade(t, b, r.symbol)
            ex = len(b.closes) - 1
        pnl = float(trade["pnl_pct"])
        if post is not None:
            pnl = post(pnl, fill, i, ex, b, trade)
        out.append(pnl)
    return np.array(out)


def partial_posthoc(frac, target_pct):
    """Sell `frac` at +target_pct (limit fill at max(open, target)); the rest follows the baseline exit."""
    def f(pnl, fill, i, ex, b, trade):
        tgt = fill * (1 + target_pct / 100.0)
        for j in range(i + 1, ex + 1):
            if b.highs[j] >= tgt:
                if j == ex and trade["exit_reason"] != "OPEN" and b.opens[j] < tgt and b.lows[j] <= trade["exit_price"]:
                    break   # stop and target on the same bar: assume the stop came first
                px = max(float(b.opens[j]), tgt)
                return frac * (px / fill - 1.0) * 100.0 + (1 - frac) * pnl
        return pnl
    return f


# ------------------------------------------------------------------------------ walk-forward

def wf_filter(df, col, direction, grid, label, keep_frac_min=0.40, test_years=(2024, 2025, 2026)):
    """Choose the threshold on earlier years only; keep rows with col <= t (direction 'le') or >= t ('ge')."""
    kept_all, drop_all, rows = [], [], []
    for Y in test_years:
        tr, te = df[df.year < Y].dropna(subset=[col]), df[df.year == Y].dropna(subset=[col])
        if len(tr) < 30 or len(te) == 0:
            continue
        best = None
        for t in grid:
            keep = tr[col] <= t if direction == "le" else tr[col] >= t
            if keep.mean() < keep_frac_min or keep.sum() < 15:
                continue
            m = tr.loc[keep, "net"].mean() - tr.loc[~keep, "net"].mean() if (~keep).sum() >= 10 else -9
            if best is None or m > best[0]:
                best = (m, t)
        if best is None:
            continue
        t = best[1]
        keep = te[col] <= t if direction == "le" else te[col] >= t
        kept_all.append(te[keep])
        drop_all.append(te[~keep])
        rows.append((Y, t, int(keep.sum()), float(te.loc[keep, "net"].mean()) if keep.any() else np.nan,
                     int((~keep).sum()), float(te.loc[~keep, "net"].mean()) if (~keep).any() else np.nan,
                     float(te["net"].mean())))
    return rows, (pd.concat(kept_all) if kept_all else pd.DataFrame()), (pd.concat(drop_all) if drop_all else pd.DataFrame())


def report_filter(df, col, direction, grid, label, fixed=None):
    print(f"\n{label}")
    rows, kept, dropped = wf_filter(df, col, direction, grid, label)
    for Y, t, nk, mk, nd, md, ma in rows:
        print(f"   test {Y}: threshold {t:g} chosen on earlier years | kept N={nk:3d} mean {mk:+6.2f} | "
              f"dropped N={nd:3d} mean {md:+6.2f} | all {ma:+6.2f}")
    if len(kept):
        allt = pd.concat([kept, dropped])
        print("   " + line("pooled OOS: kept", kept.net.values, kept.symbol.values))
        if len(dropped) >= 5:
            print("   " + line("pooled OOS: DROPPED (must be negative to adopt)", dropped.net.values, dropped.symbol.values))
        print("   " + line("pooled OOS: all (no filter)", allt.net.values, allt.symbol.values))
        yrs = [(Y, mk - ma) for Y, t, nk, mk, nd, md, ma in rows if not np.isnan(mk)]
        print(f"   kept-minus-all by year: {[(Y, round(d, 2)) for Y, d in yrs]}  years better: "
              f"{sum(d > 0 for _, d in yrs)}/{len(yrs)}   kept share {len(kept) / len(allt):.0%}")
    if fixed is not None:
        keep = df[col] <= fixed if direction == "le" else df[col] >= fixed
        sub = df.dropna(subset=[col])
        k = sub[(sub[col] <= fixed) if direction == "le" else (sub[col] >= fixed)]
        d = sub.drop(k.index)
        print(f"   fixed rule {col} {'<=' if direction == 'le' else '>='} {fixed:g} (full sample, descriptive only): "
              f"kept N={len(k)} mean {k.net.mean():+.2f} | dropped N={len(d)} mean {d.net.mean():+.2f}")
    return kept, dropped


# ----------------------------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--out-dir", default=None, help="also write base_trades.csv here")
    ap.add_argument("--causal", action="store_true",
                    help="no same-day volume anywhere (what a pre-placed resting order can know)")
    ap.add_argument("--no-vol-gate", action="store_true")
    ap.add_argument("--base-slip", type=float, default=BASE_SLIP)
    ap.add_argument("--base-cap", type=float, default=None, help="no fill above listing high + this %% (resting order)")
    ap.add_argument("--tag", default="", help="suffix for output csv")
    ap.add_argument("--exit-mode", choices=["intraday", "eod_alert"], default="intraday",
                    help="intraday = resting stop fills when the low touches it (gap-aware); eod_alert = stop checked on the "
                         "CLOSE, sold at the next open (what the live 6:30 PM stop_loss_update scan does)")
    args = ap.parse_args(argv)
    global CFG
    if args.exit_mode == "eod_alert":
        CFG = replace(LIVE_FAITHFUL, exit_fill="eod_next_open")
    POP.update(causal=args.causal, vol_gate=not args.no_vol_gate, slip=args.base_slip, cap=args.base_cap)
    with open(args.cache, "rb") as fh:
        listing_map, candle_map = pickle.load(fh)
    p = Params()
    print(f"universe: {len(candle_map)} symbols with candles, {len(listing_map)} listing records; "
          f"costs {COST}% round trip; CI = {CI_LEVEL:.0f}% symbol-cluster bootstrap\n")

    cohort, all_ld, sent_df = build_cohort(candle_map, listing_map)
    feat_store = {}
    ff = make_feature_filter(feat_store, cohort, all_ld, sent_df, listing_map)

    # ------------------------------------------------------------------ 0. baseline and execution
    print("=" * 110)
    print("0. BASELINE and EXECUTION (listing breakouts, age >= 5d, intraday-crossing fills, open trades kept at MTM)")
    print("=" * 110)
    tr0 = baseline_trades(candle_map, listing_map, p, signal_filter=ff)
    base = attach_features(tr0, candle_map, listing_map, cohort, feat_store)
    op = base[base.open]
    cl = base[~base.open]
    print(line(f"B0 baseline (slip +{POP['slip']}%, all trades)", base.net.values, base.symbol.values, f"open={len(op)}"))
    print(line("B0 closed trades only", cl.net.values, cl.symbol.values))
    print("   by year (n, mean net):", {int(y): (int(len(g)), round(float(g.net.mean()), 2))
                                       for y, g in base.groupby("year")})
    if args.out_dir:
        os.makedirs(args.out_dir, exist_ok=True)
        base.to_csv(os.path.join(args.out_dir, f"data_base_trades{args.tag}.csv"), index=False)

    print("\nX1 execution: what the fill discipline is worth (same signals, age >= 5)")
    exec_rows = {}
    for lab, kw in [("fill at trigger, no slip (ideal)", dict(slip=0.0)),
                    ("resting stop-limit: slip 0.3%, no fill > LH+2.5%", dict(slip=0.3, fill_cap_pct=2.5)),
                    ("resting stop-limit: slip 0.3%, no fill > LH+3.5%", dict(slip=0.3, fill_cap_pct=3.5)),
                    ("slip +1.0%", dict(slip=1.0)),
                    ("slip +1.7%  (today's measured native fill)  [B0]", dict(slip=1.7)),
                    ("slip +2.5%", dict(slip=2.5)),
                    ("slip +3.5%", dict(slip=3.5))]:
        tt = baseline_trades(candle_map, listing_map, p, **{**dict(fill_cap_pct=None), **{("slip" if k == "slip" else k): v for k, v in kw.items()}})
        d = pd.DataFrame(tt)
        d["net"] = d.pnl_pct - COST
        exec_rows[lab] = d
        print(line(lab, d.net.values, d.symbol.values, f"open={int(d.open.sum())}"))
    ideal, chase = exec_rows["resting stop-limit: slip 0.3%, no fill > LH+2.5%"], exec_rows["slip +1.7%  (today's measured native fill)  [B0]"]
    print(f"   -> resting order vs chase: {ideal.net.mean() - chase.net.mean():+.2f}pp per trade, "
          f"N {len(ideal)} vs {len(chase)} (the gap cap skips gap-ups that a chase would still have bought)")

    # ------------------------------------------------------------------ F. selection filters
    print("\n" + "=" * 110)
    print("F. ENTRY / SELECTION FILTERS - walk-forward (threshold chosen on earlier years, applied to the next year)")
    print("=" * 110)
    f_results = {}
    f_results["F1 supply30"] = report_filter(base, "supply30", "le", [6, 8, 10, 12, 15, 20, 30],
                                             "F1 low IPO supply (supply30 <= t)", fixed=12)
    f_results["F2 breadth"] = report_filter(base, "frac_above", "ge", [0.10, 0.20, 0.30, 0.40, 0.50],
                                            "F2 cohort breadth (share of active IPOs above own listing high >= t)", fixed=0.30)
    f_results["F3 gap"] = report_filter(base, "gap", "le", [0.5, 1.0, 2.0, 3.0, 5.0],
                                        "F3 no gap-up chase (open gap <= t %)", fixed=2.0)
    f_results["F4 liquidity"] = report_filter(base, "turn20", "ge", [0.75, 1.5, 3.0, 5.0, 8.0],
                                              "F4 liquidity floor (20d turnover Rs Cr >= t)", fixed=2.0)

    print("\nF5 drop the volume gate (adds trades; judged on the whole book, same slip +1.7%)")
    nv = pd.DataFrame(baseline_trades(candle_map, listing_map, p, vol_gate=False))
    nv["net"] = nv.pnl_pct - COST
    print("   " + line("with volume gate (B0)", base.net.values, base.symbol.values))
    print("   " + line("without volume gate", nv.net.values, nv.symbol.values))
    only_new = nv.merge(base[["symbol", "entry_idx"]], on=["symbol", "entry_idx"], how="left", indicator=True)
    only_new = only_new[only_new._merge == "left_only"]
    print("   " + line("   trades ONLY present without the gate", only_new.net.values, only_new.symbol.values))

    # ------------------------------------------------------------------ E. exit variants (paired)
    print("\n" + "=" * 110)
    print("E. EXIT / STRUCTURE VARIANTS - paired on the same entries (diff = variant minus live exits, pp per trade)")
    print("=" * 110)
    b0 = rerun_exits(base, candle_map, p)
    chk = float(np.nanmax(np.abs(b0 - base.pnl_pct.values)))
    print(f"control: re-run of live exits reproduces baseline pnl, max abs diff {chk:.2e}pp")
    variants = [
        ("E1 trail 8%", lambda: rerun_exits(base, candle_map, replace(p, exit_overrides=(("trail_pct_override", 0.08),)))),
        ("E2 trail 13%", lambda: rerun_exits(base, candle_map, replace(p, exit_overrides=(("trail_pct_override", 0.13),)))),
        ("E3 winner cushion 0.85 of peak", lambda: rerun_exits(base, candle_map, replace(p, exit_overrides=(("winner_cushion_mult", 0.85),)))),
        ("E4 winner cushion 0.75 of peak", lambda: rerun_exits(base, candle_map, replace(p, exit_overrides=(("winner_cushion_mult", 0.75),)))),
        ("E5 breakeven stop after +8% peak", lambda: rerun_exits(base, candle_map, replace(p, breakeven_runup_pct=8.0, breakeven_lock_pct=0.0))),
        ("E6 dead-money 10 days", lambda: rerun_exits(base, candle_map, replace(p, dead_money_days_ipo=10))),
        ("E7 dead-money 21 days", lambda: rerun_exits(base, candle_map, replace(p, dead_money_days_ipo=21))),
        ("E8 trail starts at +5% (not +3%)", lambda: rerun_exits(base, candle_map, replace(p, trail_start_pnl=5.0))),
        ("E9 initial stop cap 10% (not 12%)", lambda: rerun_exits(base, candle_map, replace(p, stop_cap_pct=10.0))),
        ("E10 sell 1/3 at +20%", lambda: rerun_exits(base, candle_map, p, post=partial_posthoc(1 / 3, 20.0))),
    ]
    e_results = {}
    for lab, fn in variants:
        v = fn()
        ok = ~np.isnan(v)
        d = (v - b0)[ok]
        m, lo, hi = cluster_boot(d, base.symbol.values[ok])
        yrs = {int(y): round(float(d[(base.year.values[ok] == y)].mean()), 2) for y in sorted(base.year.unique())
               if (base.year.values[ok] == y).any()}
        net_v = v[ok] - COST
        e_results[lab] = (m, lo, hi, yrs)
        print(f"{lab:<36} diff={m:+5.2f}pp CI{CI_LEVEL:.0f}=[{lo:+5.2f},{hi:+5.2f}] | net {net_v.mean():+5.2f}% "
              f"win {100 * (net_v > 0).mean():4.1f}% PF {pf(net_v):4.2f} med {np.median(net_v):+5.2f} | "
              f"years better {sum(x > 0 for x in yrs.values())}/{len(yrs)} {yrs}")

    # ------------------------------------------------------------------ X2. execution robustness
    print("\n" + "=" * 110)
    print("X2. EXECUTION ROBUSTNESS - resting stop-limit (slip 0.3%, cap LH+2.5%) vs chase (+1.7%), same signals")
    print("=" * 110)
    rest = exec_rows["resting stop-limit: slip 0.3%, no fill > LH+2.5%"]
    chase = exec_rows["slip +1.7%  (today's measured native fill)  [B0]"]
    j = rest.merge(chase, on=["symbol", "entry_idx"], suffixes=("_r", "_c"))
    d = (j.net_r - j.net_c).values
    m, lo, hi = cluster_boot(d, j.symbol.values)
    print(f"paired on {len(j)} common signals: resting minus chase = {m:+.2f}pp per trade, CI{CI_LEVEL:.0f}=[{lo:+.2f},{hi:+.2f}]")
    yy = {int(y): (int((j.year_r == y).sum()), round(float((j.net_r - j.net_c)[j.year_r == y].mean()), 2))
          for y in sorted(j.year_r.unique())}
    print("   by year (n, resting minus chase):", yy)
    for lab, s in (("resting", rest), ("chase", chase)):
        print(f"   {lab:8} by year (n, net):", {int(y): (int(len(g)), round(float(g.net.mean()), 2)) for y, g in s.groupby('year')})
    for slip in (0.3, 0.8, 1.2):
        t2 = pd.DataFrame(baseline_trades(candle_map, listing_map, p, slip=slip, fill_cap_pct=2.5))
        t2["net"] = t2.pnl_pct - COST
        print("   " + line(f"resting order with {slip}% adverse fill (jump-through allowance)", t2.net.values, t2.symbol.values))
    print("   NOTE: daily bars cannot see price jumping through the limit intraday; the true value of a resting order")
    print("   sits between the +0.8% and +0.3% rows above and the chase row, i.e. roughly +0.8 to +2.6pp per trade.")

    # ------------------------------------------------------------------ G. exploratory
    print("\n" + "=" * 110)
    print("G. EXPLORATORY (NOT pre-registered, NOT adoptable): trigger level and feature screens, discovery 2022-24 / holdout 2025-26")
    print("=" * 110)
    print("G1 trigger level above the listing high (resting order, slip 0.3%, cap = trigger + 1.0%)")
    for trig in (0.5, 1.0, 1.5, 2.0, 3.0):
        pv = replace(p, min_post_confirm_pct=trig)
        t3 = pd.DataFrame(baseline_trades(candle_map, listing_map, pv, slip=0.3, fill_cap_pct=trig + 1.0))
        if not len(t3):
            continue
        t3["net"] = t3.pnl_pct - COST
        yr = {int(y): round(float(g.net.mean()), 1) for y, g in t3.groupby("year")}
        print("   " + line(f"trigger LH+{trig}%", t3.net.values, t3.symbol.values, f"yrs {yr}"))
    print("\nG2 feature screens (direction fixed on 2022-24 discovery, shown on the 2025-26 holdout)")
    disc, hold = base[base.year <= 2024], base[base.year >= 2025]
    for col in ("age", "vr", "ext", "risk_pct", "rr", "turn20", "ld_range", "gap", "ret5", "ret10", "atr10",
                "range10", "dist_prev", "supply30", "n_active", "frac_above", "med_ret20", "ld_sent60"):
        dd, hh = disc.dropna(subset=[col]), hold.dropna(subset=[col])
        if len(dd) < 30 or len(hh) < 30:
            continue
        med = dd[col].median()
        d_hi, d_lo = dd[dd[col] > med].net.mean(), dd[dd[col] <= med].net.mean()
        h_hi, h_lo = hh[hh[col] > med].net.mean(), hh[hh[col] <= med].net.mean()
        agree = "AGREE" if (d_hi - d_lo) * (h_hi - h_lo) > 0 else "flip"
        print(f"   {col:<10} median(disc)={med:8.2f} | discovery hi-lo {d_hi - d_lo:+6.2f} | holdout hi-lo {h_hi - h_lo:+6.2f} "
              f"(n {len(hh[hh[col] > med])}/{len(hh[hh[col] <= med])}) {agree}")

    # ------------------------------------------------------------------ S. account level
    from core.execution_model import CostModel
    from core.portfolio_sim import PortfolioConfig, run_portfolio
    print("\n" + "=" * 110)
    print("S. ACCOUNT LEVEL (Rs 1,00,000; Indian delivery charges + 0.3% exit slippage; entry slip is in the fill; 5/7 slots)")
    print("=" * 110)
    cm_cost = CostModel(entry_slippage_pct=0.0, exit_slippage_pct=0.3)
    scenarios = {
        "chase (+1.7% fill) flat 1/7 sizing": (baseline_trades(candle_map, listing_map, p, slip=1.7), None),
        "resting order (0.3% fill, cap +2.5%) flat 1/7": (baseline_trades(candle_map, listing_map, p, slip=0.3, fill_cap_pct=2.5), None),
        "resting order, risk-sized 0.5% per trade": (None, 0.5),
        "resting order, risk-sized 0.75% per trade": (None, 0.75),
        "resting order, risk-sized 1.0% per trade": (None, 1.0),
    }
    rest_tr = scenarios["resting order (0.3% fill, cap +2.5%) flat 1/7"][0]
    scenarios["resting order, risk-sized 0.5% per trade"] = (rest_tr, 0.5)
    scenarios["resting order, risk-sized 0.75% per trade"] = (rest_tr, 0.75)
    scenarios["resting order, risk-sized 1.0% per trade"] = (rest_tr, 1.0)
    for lab, (trs, rb) in scenarios.items():
        for cap_lab, soft, hard in (("5/7 slots", 5, 7),):
            pc = PortfolioConfig(capital=100000.0, soft_cap=soft, hard_cap=hard, cap_policy="flat", cost=cm_cost,
                                 risk_budget_pct=rb)
            r = run_portfolio(trs, pc)["summary"]
            if not r.get("trades_taken"):
                print(f"{lab:<46} no trades taken")
                continue
            calmar = r["cagr_pct"] / abs(r["max_drawdown_pct"]) if r.get("max_drawdown_pct") else float("nan")
            print(f"{lab:<46} taken {r['trades_taken']:3d}/{r['signals']:3d} ret {r['total_return_pct']:+7.1f}% "
                  f"CAGR {r['cagr_pct']:+6.1f}% maxDD {r['max_drawdown_pct']:6.1f}% Calmar {calmar:5.2f} PF {r['profit_factor']:4.2f} "
                  f"by year {({k: round(v, 1) for k, v in r.get('by_year_pct', {}).items()})}")

    # ------------------------------------------------------------------ verdict table
    print("\n" + "=" * 110)
    print("VERDICT against the pre-registered adoption bar")
    print("=" * 110)
    for k, (kept, dropped) in f_results.items():
        if not len(kept):
            print(f"{k:<14} no OOS result")
            continue
        allt = pd.concat([kept, dropped])
        d_mean, d_lo, d_hi = (cluster_boot(dropped.net.values, dropped.symbol.values) if len(dropped) >= 5 else (np.nan,) * 3)
        share = len(kept) / len(allt)
        yr_better = None
        passes = (len(dropped) >= 20 and d_hi < 0 and share >= 0.60)
        print(f"{k:<14} dropped N={len(dropped):3d} mean {d_mean:+6.2f} CI{CI_LEVEL:.0f}=[{d_lo:+6.1f},{d_hi:+6.1f}] "
              f"kept share {share:.0%} -> {'PASS' if passes else 'not adopted'}")
    for lab, (m, lo, hi, yrs) in e_results.items():
        worse = sum(x < 0 for x in yrs.values())
        passes = lo > 0 and worse <= 1
        print(f"{lab:<36} diff {m:+5.2f}pp lower bound {lo:+5.2f} worse years {worse} -> {'PASS' if passes else 'not adopted'}")


if __name__ == "__main__":
    main()
