# -*- coding: utf-8 -*-
"""
research_gate_replay.py -- pre-registered replay: do the live partial-day gates cost us explosive moves?

PRE-REGISTRATION (written before running, 2026-10-17)
-----------------------------------------------------
Question: the live listing scanner gates on values read mid-session (volume so far vs a 150k floor, close location of a candle
still forming).  HTEL (rejected at 140k shares, finished 14.2M, +40% in 3 days) and DEEPA (close location 46.6% mid-day) were lost to them.
Daily bars cannot reproduce a mid-session snapshot, so the replay uses the MOST GENEROUS end-of-day versions of the gates.  If even the
end-of-day versions remove profitable trades, the mid-session versions (noisier, biased low for volume) can only do worse.

Variants (same exits = close-based stops sold at next open, same fills = resting stop-limit slip 0.3%, no fill above LH+2.5%, costs 1.0%):
  V0  policy P: causal, no volume gate, no body gate            (what resting orders do)
  V1  EOD volume gate only (1.8x spike and 150k floor on the breakout day own volume)
  V2  EOD body gate only (close in upper 50% of the day range)
  V3  both gates at EOD (best case for the live scanner)
Adoption bar for removing a gate: the trades the gate removes have a NEGATIVE net mean whose 90% symbol-cluster CI excludes 0, and
that holds in all but one year.  Otherwise the gate is not justified.
Capture metric: explosive episodes = a liquid IPO-day (age 5-60) whose next 10 sessions reach a high >= +20% (and >= +40%) above
that day close, one episode per symbol per 20 sessions.  A variant captures an episode if it held a position on any day of those 10 sessions.

  .venv\\Scripts\\python.exe research_gate_replay.py --cache data.pkl --out-dir reports/2026-10-17
"""
import argparse
import logging
import os
import pickle
from dataclasses import replace

import numpy as np
import pandas as pd

logging.disable(logging.CRITICAL)
from core.honest_backtest import LIVE_FAITHFUL, Params, simulate_symbol_cross
from research_alpha_lab import cluster_boot, line, pf, COST

CFG = replace(LIVE_FAITHFUL, exit_fill="eod_next_open")
VARIANTS = {
    "V0 policy P (causal, no gates)":      dict(causal=True,  vol_gate=False, body_gate=False),
    "V1 EOD volume gate only":             dict(causal=False, vol_gate=True,  body_gate=False),
    "V2 EOD body gate only":               dict(causal=False, vol_gate=False, body_gate=True),
    "V3 both EOD gates (live best case)":  dict(causal=False, vol_gate=True,  body_gate=True),
}


def run_variant(candle_map, listing_map, p, kw):
    out = []
    for sym, df in candle_map.items():
        out.extend(simulate_symbol_cross(df, sym, listing_map.get(sym, {}), p, slip_pct=0.3, fill_cap_pct=2.5, min_age=5,
                                         include_open=True, cfg=CFG, **kw))
    d = pd.DataFrame(out)
    if d.empty:
        return d
    d["net"] = d.pnl_pct - COST
    d["entry_date"] = pd.to_datetime(d.entry_date)
    d["exit_date"] = pd.to_datetime(d.exit_date)
    d["year"] = d.entry_date.dt.year
    return d


def episodes(candle_map, listing_map, thr):
    eps = []
    for s, c in candle_map.items():
        r = listing_map.get(s)
        if r is None or len(c) < 15:
            continue
        ld = pd.Timestamp(r["listing_date"]).tz_localize(None)
        c = c.copy()
        c["DATE"] = pd.to_datetime(c.DATE).dt.tz_localize(None)
        c = c.sort_values("DATE").reset_index(drop=True)
        hi, cl, v, dt = c.HIGH.values, c.CLOSE.values, c.VOLUME.values, c.DATE.values
        last = -99
        for i in range(len(c) - 10):
            age = (pd.Timestamp(dt[i]) - ld).days
            if not (5 <= age <= 60) or v[i] < 150000 or i - last < 20:
                continue
            if hi[i + 1:i + 11].max() / cl[i] - 1 >= thr:
                eps.append((s, pd.Timestamp(dt[i + 1]), pd.Timestamp(dt[i + 10])))
                last = i
    return eps


def captured(eps, trades):
    n = 0
    far = pd.Timestamp("2100-01-01")
    by = {s: g for s, g in trades.groupby("symbol")}
    for s, a, b in eps:
        g = by.get(s)
        if g is None:
            continue
        end = g.exit_date.where(~g.open.astype(bool), far).fillna(far)
        if ((g.entry_date <= b) & (end >= a)).any():
            n += 1
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--since", default=None, help="also report entries on/after this date, e.g. 2026-08-15")
    a = ap.parse_args()
    with open(a.cache, "rb") as fh:
        listing_map, candle_map = pickle.load(fh)
    p = Params()
    last_dates = [pd.to_datetime(c.DATE).max() for c in candle_map.values() if len(c)]
    print(f"universe {len(candle_map)} symbols with candles; latest candle {max(last_dates).date()}; costs {COST}% RT; "
          f"CI 90% symbol-cluster bootstrap\n")
    res = {k: run_variant(candle_map, listing_map, p, kw) for k, kw in VARIANTS.items()}
    if a.out_dir:
        os.makedirs(a.out_dir, exist_ok=True)
        for k, d in res.items():
            d.to_csv(os.path.join(a.out_dir, "gate_replay_" + k.split()[0] + ".csv"), index=False)

    print("=" * 112 + "\n1. BOOK PER VARIANT (open trades marked to market)\n" + "=" * 112)
    for k, d in res.items():
        cl = d[~d.open.astype(bool)]
        print(line(k, d.net.values, d.symbol.values, f"open={int(d.open.sum())}"))
        print(line("   closed only", cl.net.values, cl.symbol.values))
        s = np.sort(d.net.values)[::-1]
        print(f"   PF incl open {pf(d.net.values):.2f} | closed {pf(cl.net.values):.2f} | ex-top3 {pf(s[3:]):.2f} | "
              f"outliers >=25%: {int((d.net >= 25).sum())}  >=75%: {int((d.net >= 75).sum())}")
        print("   by year (n, mean, PF):", {int(y): (int(len(g)), round(float(g.net.mean()), 1), round(pf(g.net.values), 2))
                                          for y, g in d.groupby("year")})

    print("\n" + "=" * 112 + "\n2. WHAT EACH GATE REMOVES (V0 trades absent from the gated variant; bar needs a NEGATIVE mean)\n" + "=" * 112)
    v0 = res["V0 policy P (causal, no gates)"]
    v0_keys = list(zip(v0.symbol, v0.entry_date.dt.strftime("%Y-%m-%d")))
    for k in list(VARIANTS)[1:]:
        d = res[k]
        kept = set(zip(d.symbol, d.entry_date.dt.strftime("%Y-%m-%d")))
        mask = np.array([key not in kept for key in v0_keys])
        rem, kp = v0[mask], v0[~mask]
        print(line(f"removed by {k.split(' (')[0]}", rem.net.values, rem.symbol.values,
                   f"| outliers>=25%: {int((rem.net >= 25).sum())}"))
        print(line("   kept (V0 exits)", kp.net.values, kp.symbol.values))
        if len(rem):
            print("   removed by year (n, mean):", {int(y): (int(len(g)), round(float(g.net.mean()), 1))
                                                    for y, g in rem.groupby("year")})
            top = rem.sort_values("net", ascending=False).head(8)
            print("   biggest removed winners:", [(r.symbol, str(r.entry_date.date()), round(r.net, 1)) for r in top.itertuples()])

    print("\n" + "=" * 112 + "\n3. EXPLOSIVE-EPISODE CAPTURE (liquid IPO-day whose next 10 sessions reach the threshold)\n" + "=" * 112)
    for thr in (0.20, 0.40):
        eps = episodes(candle_map, listing_map, thr)
        print(f"episodes with 10-session high >= +{int(thr * 100)}%: {len(eps)}")
        for k, d in res.items():
            c = captured(eps, d)
            print(f"   {k:<40} captured {c:4d} = {100 * c / max(len(eps), 1):5.1f}%")

    if a.since:
        print("\n" + "=" * 112 + f"\n4. RECENT WINDOW: entries since {a.since}\n" + "=" * 112)
        for k, d in res.items():
            w = d[d.entry_date >= a.since]
            names = ", ".join(sorted(w.symbol))[:400] if (k.startswith("V0") or k.startswith("V3")) else ""
            print(f"{k:<40} N={len(w):3d} mean net {w.net.mean():+5.1f}%  open={int(w.open.astype(bool).sum())}  {names}")


if __name__ == "__main__":
    main()
