# -*- coding: utf-8 -*-
"""
research_capture_gaps.py -- why does policy P miss explosive episodes?  (diagnostic, not a strategy change)

For every liquid IPO-day (age 5-60) whose next 10 sessions reach a high >= +20% above its close (one episode per symbol per 20 sessions),
classify the episodes policy P (causal resting entry, no same-day gates) did NOT hold, by the situation on the episode day:
  distance of the close from the listing-day high, age, price floor, recent exit (cooldown), episode before the first 5 days.
Then measure how much extra capture each candidate widening would add, using the engine as is (no new rules invented here):
  S  shelf breakout (setup="shelf", currently PAPER_ONLY)
  A  age 3-4 days allowed (min_age 3)
Capture is judged against the episode list; profit quality of each add-on is shown from its own trades (net, PF, N).

  .venv\\Scripts\\python.exe research_capture_gaps.py --cache data.pkl
"""
import argparse
import logging
import pickle
from dataclasses import replace

import numpy as np
import pandas as pd

logging.disable(logging.CRITICAL)
from core.honest_backtest import LIVE_FAITHFUL, Params, simulate_symbol_cross
from research_alpha_lab import line, pf, COST
from research_gate_replay import captured, episodes, run_variant

CFG = replace(LIVE_FAITHFUL, exit_fill="eod_next_open")


def episode_rows(candle_map, listing_map, thr):
    rows = []
    for s, c in candle_map.items():
        r = listing_map.get(s)
        if r is None or len(c) < 15:
            continue
        ld = pd.Timestamp(r["listing_date"]).tz_localize(None)
        ldh = float(r["listing_day_high"])
        c = c.copy()
        c["DATE"] = pd.to_datetime(c.DATE).dt.tz_localize(None)
        c = c.sort_values("DATE").reset_index(drop=True)
        hi, cl, v, op = c.HIGH.values, c.CLOSE.values, c.VOLUME.values, c.OPEN.values
        last = -99
        for i in range(len(c) - 10):
            age = (pd.Timestamp(c.DATE[i]) - ld).days
            if not (5 <= age <= 60) or v[i] < 150000 or i - last < 20:
                continue
            if hi[i + 1:i + 11].max() / cl[i] - 1 >= thr:
                rows.append(dict(symbol=s, start=pd.Timestamp(c.DATE[i + 1]), end=pd.Timestamp(c.DATE[i + 10]), age=age,
                                 dist_lh=(cl[i] / ldh - 1) * 100, price=cl[i], year=pd.Timestamp(c.DATE[i]).year,
                                 gain=(hi[i + 1:i + 11].max() / cl[i] - 1) * 100))
                last = i
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    a = ap.parse_args()
    with open(a.cache, "rb") as fh:
        listing_map, candle_map = pickle.load(fh)
    p = Params()
    v0 = run_variant(candle_map, listing_map, p, dict(causal=True, vol_gate=False, body_gate=False))
    ep = episode_rows(candle_map, listing_map, 0.20)
    far = pd.Timestamp("2100-01-01")
    by = {s: g for s, g in v0.groupby("symbol")}

    def held(r):
        g = by.get(r.symbol)
        if g is None:
            return False
        end = g.exit_date.where(~g.open.astype(bool), far).fillna(far)
        return bool(((g.entry_date <= r.end) & (end >= r.start)).any())

    def recent_exit(r):
        g = by.get(r.symbol)
        if g is None:
            return False
        gap = (r.start - g.exit_date).dt.days
        return bool(((gap >= 0) & (gap <= 42)).any())

    ep["held"] = [held(r) for r in ep.itertuples()]
    ep["cooldown"] = [recent_exit(r) for r in ep.itertuples()]
    print(f"episodes (+20% within 10 sessions): {len(ep)}; held by policy P: {int(ep.held.sum())} ({100 * ep.held.mean():.1f}%)")
    miss = ep[~ep.held].copy()
    bins = [-999, -20, -10, -5, 0, 2.5, 8, 999]
    labels = ["<-20% below LH", "-20..-10", "-10..-5", "-5..0 (just under LH)", "0..+2.5 (at trigger zone)", "+2.5..+8 (above fill cap)", ">+8 (extended)"]
    ep["bucket"] = pd.cut(ep.dist_lh, bins, labels=labels)
    t = ep.groupby("bucket", observed=True).agg(episodes=("held", "size"), held=("held", "sum"), median_gain=("gain", "median"))
    t["held_pct"] = (100 * t.held / t.episodes).round(1)
    print("\nBy close vs listing-high on the episode day (held = policy P was in a position):")
    print(t.to_string())
    print("\nMissed episodes: price < Rs 25:", int((miss.price < 25).sum()), "| inside 42-day cooldown after a policy-P exit:", int(miss.cooldown.sum()),
          "| age 5-9:", int(((miss.age >= 5) & (miss.age <= 9)).sum()), "| age 10-30:", int(((miss.age >= 10) & (miss.age <= 30)).sum()),
          "| age 31-60:", int((miss.age >= 31).sum()))
    print("Missed episodes by year:", {int(y): (int((miss.year == y).sum()), int((ep.year == y).sum())) for y in sorted(ep.year.unique())})

    # add-ons judged on their own trades
    print("\n" + "=" * 100 + "\nCandidate widenings (engine unchanged, causal resting model, slip 0.3%, cap 2.5%)\n" + "=" * 100)
    print(line("V0 policy P", v0.net.values, v0.symbol.values, f"open={int(v0.open.sum())}"))
    shelf = []
    for sym, df in candle_map.items():
        shelf.extend(simulate_symbol_cross(df, sym, listing_map.get(sym, {}), p, slip_pct=0.3, fill_cap_pct=2.5, min_age=3,
                                           include_open=True, cfg=CFG, causal=True, vol_gate=False, setup="shelf"))
    sh = pd.DataFrame(shelf)
    sh["net"] = sh.pnl_pct - COST
    sh["entry_date"] = pd.to_datetime(sh.entry_date)
    sh["exit_date"] = pd.to_datetime(sh.exit_date)
    print(line("S shelf breakout (paper-only today)", sh.net.values, sh.symbol.values, f"open={int(sh.open.sum())}"))
    both = pd.concat([v0, sh], ignore_index=True)
    eps_list = list(zip(ep.symbol, ep.start, ep.end))
    print(f"capture of +20% episodes: V0 {captured(eps_list, v0)}  shelf alone {captured(eps_list, sh)}  V0+shelf {captured(eps_list, both)}  of {len(eps_list)}")
    # shelf trades that are not already a V0 trade on the same symbol within 10 days
    print(f"  shelf trades by year (n, mean, PF):", {int(y): (int(len(g)), round(float(g.net.mean()), 1), round(pf(g.net.values), 2))
                                                    for y, g in sh.groupby(sh.entry_date.dt.year)})
    for k, d in {"V0": v0, "shelf": sh}.items():
        s = np.sort(d.net.values)[::-1]
        cl = d[~d.open.astype(bool)]
        print(f"  {k}: PF incl open {pf(d.net.values):.2f} | closed {pf(cl.net.values):.2f} | ex-top3 {pf(s[3:]):.2f}")


if __name__ == "__main__":
    main()
