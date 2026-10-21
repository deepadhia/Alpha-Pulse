# -*- coding: utf-8 -*-
"""
research_live_parity.py -- compare the LIVE book in MongoDB with what the honest engine does (read-only).

For every position in `positions` it reports: listing date, age at the stored entry date, whether the engine
produces any signal for that symbol, and what trade it would have taken.  It also lists how far above the
listing high the genuinely native (non-backfilled) entries actually filled, which is the empirical input for
the fill-slippage assumption, and summarises positions opened in the last N days.

Findings from the 2026-10-13 run are in LEARNINGS.md (section 7) and reports/2026-10-13/.

  python research_live_parity.py --cache data.pkl [--days 30] [--paths MVELECTRO AUGMONT ...]

--paths prints the first 22 candles of a symbol with the live-gate quantities (volume ratio, 20d turnover,
extension over the listing high, close location) to see WHICH gate rejected it.
"""

import argparse
import logging
from collections import Counter
from datetime import datetime, timedelta

import pandas as pd

logging.disable(logging.CRITICAL)

from core.honest_backtest import (LIVE_FAITHFUL, Params, _listing_context, precompute_signals, simulate_symbol)
from run_honest_backtest import load_data


def _ds(v):
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d")
    return str(v)[:10] if v is not None else ""


def print_paths(candle_map, listing_map, symbols):
    for sym in symbols:
        df = candle_map.get(sym)
        if df is None:
            print(f"\n=== {sym}: no candles in cache")
            continue
        li = listing_map.get(sym, {})
        ld_high, ld_low, ld = _listing_context(df, li)
        print(f"\n=== {sym} list={ld.date()} ld_high={ld_high} ld_low={ld_low} "
              f"range%={(ld_high - ld_low) / ld_high * 100:.1f}")
        for i in range(0, min(len(df), 22)):
            r = df.iloc[i]
            age = (pd.Timestamp(r.DATE) - ld).days
            note = ""
            if i >= 3:
                completed = df.iloc[:i]
                avg_vol = float(completed["VOLUME"].iloc[2:].tail(10).mean()) if len(completed) > 2 else r.VOLUME
                vr = r.VOLUME / avg_vol if avg_vol else 0
                up = df.iloc[:i + 1]
                turn = float((up["CLOSE"] * up["VOLUME"]).tail(20).mean()) / 1e7
                ext = (max(r.CLOSE, ld_high) - ld_high) / ld_high * 100 if r.HIGH > ld_high else None
                cl = (r.CLOSE - r.LOW) / (r.HIGH - r.LOW) if r.HIGH > r.LOW else 1
                note = (f" vr={vr:4.1f} turn20={turn:6.1f}Cr brk={'Y' if r.HIGH > ld_high else 'n'} "
                        f"ext={ext if ext is None else round(ext, 1)} closeloc={cl:.2f}")
            print(f"{i:2d} {pd.Timestamp(r.DATE).date()} age={age:3d} O{r.OPEN:8.2f} H{r.HIGH:8.2f} "
                  f"L{r.LOW:8.2f} C{r.CLOSE:8.2f} V{int(r.VOLUME):9d}{note}")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="data.pkl")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--paths", nargs="*", default=[])
    args = ap.parse_args(argv)

    from db import positions_col
    if positions_col is None:
        raise SystemExit("MongoDB is not connected")
    listing_map, candle_map = load_data(args.cache)
    p = Params.from_live()
    cfg = LIVE_FAITHFUL

    docs = list(positions_col.find({}, {"_id": 0}))
    print(f"positions: {len(docs)}   by (status, backfilled): "
          f"{dict(Counter((d.get('status'), bool(d.get('_backfilled'))) for d in docs))}\n")

    print("A. PER-POSITION PARITY (age = calendar days from listing to the STORED entry date; negative = entry "
          "before the stock listed, i.e. the stored date cannot be a real fill)")
    for d in sorted(docs, key=lambda d: _ds(d.get("entry_date"))):
        sym = d["symbol"]
        ed = _ds(d.get("entry_date"))
        df = candle_map.get(sym)
        li = listing_map.get(sym, {})
        head = (f"{sym:12} {str(d.get('status')):12} bf={int(bool(d.get('_backfilled')))} entry={ed} "
                f"px={d.get('entry_price')} pnl={d.get('pnl_pct') if d.get('pnl_pct') is None else round(d['pnl_pct'], 1)}")
        if df is None:
            print(f"{head}  NO CANDLES in cache (fewer than 10 candles or missing)")
            continue
        _, _, list_date = _listing_context(df, li)
        age_at = (pd.Timestamp(ed) - list_date).days if ed else None
        sigs = precompute_signals(df, li, cfg, p)
        sdates = [str(pd.Timestamp(df["DATE"].iat[i]).date()) for i in sigs]
        trades = [(t["entry_date"], round(t["pnl_pct"], 1)) for t in simulate_symbol(df, sym, li, cfg, p)]
        print(f"{head} list={list_date.date()} age@entry={age_at} engine_signals={sdates} engine_trades={trades}")

    print("\nB. NATIVE (non-backfilled) FILLS vs LISTING HIGH (empirical fill extension)")
    exts = []
    for d in docs:
        if d.get("_backfilled"):
            continue
        ldh = listing_map.get(d["symbol"], {}).get("listing_day_high")
        if ldh and d.get("entry_price"):
            e = (d["entry_price"] / ldh - 1) * 100
            exts.append(e)
            print(f"{d['symbol']:12} {str(d.get('status')):12} listing_high={ldh} entry={d['entry_price']} "
                  f"ext={e:+.2f}%  current={d.get('current_price')} updated={_ds(d.get('updated_at'))}")
    sane = [e for e in exts if e < 10]
    if sane:
        print(f"mean ext (excluding entries >10% above the stored listing high, likely shelf/bad record): "
              f"{sum(sane) / len(sane):.2f}%  n={len(sane)}")

    cutoff = datetime.now() - timedelta(days=args.days)
    print(f"\nC. POSITIONS ENTERED IN THE LAST {args.days} DAYS (stored entry date >= {cutoff.date()})")
    rows = [d for d in docs if _ds(d.get("entry_date")) >= str(cutoff.date()) and d.get("pnl_pct") is not None
            and not str(d["symbol"]).startswith("COMMIT_CLEAN_TEST")]
    if rows:
        pn = pd.Series([d["pnl_pct"] for d in rows])
        closed = pd.Series([d["pnl_pct"] for d in rows if str(d.get("status")).endswith("CLOSED")])
        opn = pd.Series([d["pnl_pct"] for d in rows if not str(d.get("status")).endswith("CLOSED")])
        print(f"all: N={len(pn)} mean={pn.mean():+.2f}% median={pn.median():+.2f}% up={int((pn > 0).sum())} down={int((pn < 0).sum())}")
        print(f"closed: N={len(closed)} mean={closed.mean() if len(closed) else float('nan'):+.2f}%   "
              f"open (unrealised): N={len(opn)} mean={opn.mean() if len(opn) else float('nan'):+.2f}%")
        nat = pd.Series([d["pnl_pct"] for d in rows if not d.get("_backfilled")])
        print(f"native only (not backfilled): N={len(nat)} mean={nat.mean() if len(nat) else float('nan'):+.2f}% "
              f"median={nat.median() if len(nat) else float('nan'):+.2f}%")

    if args.paths:
        print_paths(candle_map, listing_map, args.paths)


if __name__ == "__main__":
    main()
