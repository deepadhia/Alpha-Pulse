# -*- coding: utf-8 -*-
"""
research_intraday_proxy.py -- sensitivity test: does judging breakouts by END-OF-DAY close and filling at the
NEXT open (the audit engine) understate the LISTING_BREAKOUT engine?

Why this exists (2026-10-13 review of the 2026-10-12 audit):
  * The live scanner decides on the CURRENT price when it fires (entry = max(price, listing high), extension and
    post-confirm gates on that price) and buys during the signal day.  The audit engine decides on the day's
    CLOSE and buys at the NEXT open.  Days that close strongly (+4%..+20% over the pivot) are therefore
    rejected as "extended", while the live scanner would have bought them near the pivot.
  * simulate_symbol() drops any trade still open at the end of the data (return None -> break).

What it does (read-only, no parameter is tuned; every threshold is the live value):
  0. CONTROL   - the audit engine as shipped (must print N=36 listing / N=79 all, same as the 2026-10-12 report)
  1. DROPPED   - trades the audit engine silently drops because they are still open at the end of the data
  2. PROXY     - listing-breakout entries filled at the intraday crossing price:
                   trigger = listing high * (1 + min_post_confirm_pct)      (live: +1.5%)
                   skip if the day opens above listing high * (1 + max_extension_pct)   (live: +3.5%)
                   fill  = max(open, trigger) * (1 + slip)
                 EOD volume gate (x1.8), liquidity / circuit / price floors, stop construction, R:R, leader
                 score and the live exit engine are unchanged.  NO condition on the day's close is applied
                 (the EOD body gate is a variant, P4, and is HINDSIGHT: do not read it as achievable).
  3. PROFILE   - payoff profile of the base variants

Limits: the 60-minute hold, the 2.5% pullback ejection and the exact trigger time are not modelled (needs
intraday bars).  EOD volume is >= the volume at trigger time, so the volume gate is somewhat generous.
Fill price is the dominant assumption: see the slip variants.  Open trades are marked to market and reported
separately.

  python research_intraday_proxy.py --cache data.pkl
"""

import argparse
import logging

import numpy as np
import pandas as pd

logging.disable(logging.CRITICAL)

import core.honest_backtest as hb
from core.honest_backtest import (LIVE_FAITHFUL, Params, _Bars, _leader_score, _listing_context,
                                   _profit_target_rr, _run_trade, _stop_from_history, simulate_symbol)
from run_honest_backtest import load_data

COST = 1.0   # round-trip percent, same flat assumption as the audit (CostModel defaults ~1%)


def proxy_trades(df, sym, li, p, cfg, vol_gate=True, body_gate=False, slip=0.0, min_age=0):
    """Listing-breakout trades with an intraday-crossing fill. One position at a time, 30-bar cooldown."""
    n = len(df)
    out = []
    if n < 15:
        return out
    if li.get("is_nse_addition") and not li.get("bse_liquidity_passed", True):
        return out
    ld_high, ld_low, list_date = _listing_context(df, li)
    b = _Bars.of(df)
    rng_pct = (ld_high - ld_low) / ld_high * 100.0 if ld_low else 0.0
    last_exit = -100
    i = 3
    while i < n:
        age = int((pd.Timestamp(b.dates[i]) - list_date).days)
        if age > p.max_age:
            break
        if (i - last_exit) < p.cooldown_bars or age < min_age:
            i += 1
            continue
        o, h, l, c, v = (float(b.opens[i]), float(b.highs[i]), float(b.lows[i]),
                         float(b.closes[i]), float(b.vols[i]))
        trig = ld_high * (1 + p.min_post_confirm_pct / 100.0)
        ceil = ld_high * (1 + p.max_extension_pct / 100.0)
        if h < trig or o > ceil:
            i += 1
            continue
        entry = max(o, trig)
        if entry > ceil or entry < p.min_entry_price or v < p.min_volume:
            i += 1
            continue
        upto = df.iloc[:i]
        turn = float((upto["CLOSE"] * upto["VOLUME"]).tail(20).mean()) / 1e7 if len(upto) else 0.0
        circ = int(((upto["HIGH"] == upto["LOW"]) & (upto["VOLUME"] > 0)).tail(15).sum())
        if (turn > 0 and turn < p.turnover_floor_cr) or circ >= p.circuit_day_threshold:
            i += 1
            continue
        completed = df.iloc[:i]
        if len(completed) > 2:
            avg_vol = float(completed["VOLUME"].iloc[2:].tail(10).mean())
        elif len(completed) == 2:
            avg_vol = float(completed["VOLUME"].iloc[1])
        else:
            avg_vol = v
        avg_vol = avg_vol if avg_vol > 0 else v
        vr = v / avg_vol
        if vol_gate and vr < p.min_vol_mult:
            i += 1
            continue
        if body_gate and h > l and (c - l) / (h - l) < p.min_close_loc:
            i += 1
            continue
        if 0 < rng_pct < 5.0:
            i += 1
            continue
        fill = entry * (1 + slip / 100.0)
        stop = _stop_from_history(df, i, fill, p)
        risk = (fill - stop) / fill
        if risk <= 0:
            i += 1
            continue
        rr = _profit_target_rr(fill, stop, ld_high, ld_low, ld_high)
        if rr < p.min_risk_reward:
            i += 1
            continue
        ext = (entry - ld_high) / ld_high * 100.0
        if _leader_score(ext, vr, rr, entry, ld_high, rng_pct) < p.min_leader_score:
            i += 1
            continue
        cur = pd.Timestamp(b.dates[i])
        sig = dict(type="LISTING_BREAKOUT", pivot=ld_high, post_confirm=ext,
                   max_chase=p.breakout_max_chase_pct, signal_price=fill, vol_ratio=vr, ext_pct=ext, rr=rr)
        t = dict(entry_idx=i, entry_date=cur, entry_price=fill, signal=sig, signal_idx=i, signal_date=cur,
                 trailing=stop, risk_pct=risk, max_runup=0.0, path=[], stop_initial=stop, age=age, missed=False)
        trade, ex = _run_trade(b, t, i, i, cfg, p, sym)   # day0_bar=i: only the protective stop acts on fill day
        if trade is None:                                  # still open at the end of the data: mark to market
            last_c = float(b.closes[-1])
            out.append(dict(symbol=sym, entry_date=cur, pnl_pct=(last_c - fill) / fill * 100.0, open=True,
                            exit_reason="OPEN", year=cur.year, days_held=n - 1 - i, vr=vr, ext=ext))
            break
        trade["open"] = False
        trade["year"] = cur.year
        trade["vr"] = vr
        trade["ext"] = ext
        out.append(trade)
        last_exit = ex
        i = ex + 1
    return out


def stats(label, tr):
    if not tr:
        print(f"{label:58} no trades")
        return None
    t = pd.DataFrame(tr)
    g = t.pnl_pct
    net = g - COST
    w, l = net[net > 0].sum(), -net[net < 0].sum()
    pf = w / l if l > 0 else float("inf")
    ex3 = net.drop(g.nlargest(3).index)
    rng = np.random.default_rng(0)
    bs = [rng.choice(net.values, len(net)).mean() for _ in range(4000)]
    lo, hi = np.percentile(bs, [2.5, 97.5])
    n_open = int(t["open"].sum()) if "open" in t.columns else 0
    print(f"{label:58} N={len(t):3d} win={100 * (net > 0).mean():4.1f}% gross={g.mean():+5.2f}% "
          f"net={net.mean():+5.2f}% med={net.median():+5.2f}% PF={pf:4.2f} exTop3={ex3.mean():+5.2f}% "
          f"CI95=[{lo:+.1f},{hi:+.1f}] open={n_open}")
    return t


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="data.pkl")
    args = ap.parse_args(argv)
    listing_map, candle_map = load_data(args.cache)
    p = Params.from_live()
    cfg = LIVE_FAITHFUL
    print(f"universe: {len(candle_map)} symbols with candles, {len(listing_map)} listing records; "
          f"costs {COST}% round trip\n")

    # ---- 0/1. control + dropped open trades ----
    dropped = []
    orig = hb._run_trade

    def wrapper(b, t, j0, day0_bar, cfg_, p_, symbol):
        r = orig(b, t, j0, day0_bar, cfg_, p_, symbol)
        if r[0] is None:
            last = float(b.closes[-1])
            dropped.append(dict(symbol=symbol, type=t["signal"]["type"],
                                entry_date=str(pd.Timestamp(t["entry_date"]).date()),
                                entry=round(t["entry_price"], 2), last=round(last, 2),
                                mtm_pct=round((last - t["entry_price"]) / t["entry_price"] * 100, 1)))
        return r

    hb._run_trade = wrapper
    ctrl = []
    for sym, df in candle_map.items():
        for tr in simulate_symbol(df, sym, listing_map.get(sym, {}), cfg, p):
            tr["year"] = pd.Timestamp(tr["entry_date"]).year
            ctrl.append(tr)
    hb._run_trade = orig

    print("0. CONTROL: audit engine as shipped (2026-10-12 report: listing N=36 gross +3.35; all N=79 gross +0.79)")
    stats("   listing only", [t for t in ctrl if t["type"] == "LISTING_BREAKOUT"])
    stats("   all types", ctrl)
    print(f"\n1. DROPPED: trades still open at the end of the data that the audit engine silently discards: {len(dropped)}")
    if dropped:
        print(pd.DataFrame(dropped).to_string(index=False))

    # ---- 2. proxy variants ----
    variants = [
        ("P1 cross-fill, vol gate 1.8x, no body gate, slip 0%", dict()),
        ("P2 cross-fill, vol gate, slip +1% (chase)", dict(slip=1.0)),
        ("P3 cross-fill, vol gate, slip +2% (harsh)", dict(slip=2.0)),
        ("P4 cross-fill + EOD body gate  [HINDSIGHT, not achievable]", dict(body_gate=True)),
        ("P5 cross-fill, NO vol gate", dict(vol_gate=False)),
        ("P6 cross-fill, vol gate, age>=5d (live pending rule)", dict(min_age=5)),
        ("P7 age>=5d + slip +1%", dict(min_age=5, slip=1.0)),
    ]
    print("\n2. PROXY: intraday-crossing fills (net = gross - 1.0% costs; 'open' = still-open trades marked to market)")
    results = {}
    for label, kw in variants:
        tr = []
        for sym, df in candle_map.items():
            tr.extend(proxy_trades(df, sym, listing_map.get(sym, {}), p, cfg, **kw))
        results[label] = tr
        print()
        t = stats(label, tr)
        if t is not None and label[:2] in ("P1", "P2", "P6", "P7"):
            by = t.assign(net=t.pnl_pct - COST).groupby("year").net.agg(["count", "mean"]).round(2)
            print("   by year (n, mean net):", {int(k): (int(v["count"]), float(v["mean"])) for k, v in by.iterrows()})
            c = t[~t["open"]]
            cn = c.pnl_pct - COST
            cw, cl = cn[cn > 0].sum(), -cn[cn < 0].sum()
            print(f"   CLOSED ONLY: N={len(c)} net={cn.mean():+.2f}% PF={cw / cl:.2f}   "
                  f"open runners: N={int(t['open'].sum())} MTM sum={t[t['open']].pnl_pct.sum():.0f}pp")

    # ---- 3. profile ----
    print("\n3. PAYOFF PROFILE (closed trades only, net of costs)")
    for label in list(results)[:2]:
        t = pd.DataFrame(results[label])
        t = t[~t["open"]]
        net = t.pnl_pct - COST
        w, l = net[net > 0], net[net < 0]
        print(f"\n{label}\n   N={len(t)} win={100 * len(w) / len(t):.0f}% avg win={w.mean():+.1f}% avg loss={l.mean():+.1f}% "
              f"payoff={w.mean() / -l.mean():.2f} median={net.median():+.1f}%  trades/yr={len(t) / 4.4:.1f}")
        why = t.exit_reason.str.replace(r"\(.*\)", "", regex=True).str.strip()
        print(t.assign(net=net, why=why).groupby("why").net.agg(["count", "mean"]).round(1)
              .sort_values("count", ascending=False).head(6).to_string())
        s = (net.values < 0).astype(int)
        run = best = 0
        for x in s:
            run = run + 1 if x else 0
            best = max(best, run)
        print(f"   longest losing streak: {best}; top-5 winners = {100 * net.nlargest(5).sum() / w.sum():.0f}% of gross profit; "
              f"losers worse than -8%: {100 * (l <= -8).mean():.0f}% of losers")

    t1 = pd.DataFrame(results[variants[0][0]])
    print("\nP1 top 8 trades:")
    print(t1.nlargest(8, "pnl_pct")[["symbol", "entry_date", "pnl_pct", "exit_reason", "open"]].round(1).to_string(index=False))
    print("\nP1 trades entered in 2026:")
    print(t1[t1.year == 2026][["symbol", "entry_date", "pnl_pct", "exit_reason", "vr", "ext", "open"]]
          .round(2).to_string())


if __name__ == "__main__":
    main()
