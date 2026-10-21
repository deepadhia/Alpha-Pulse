# -*- coding: utf-8 -*-
"""
order_sheet.py -- daily manual-execution sheet (read-only).

  python order_sheet.py --capital 100000
  python order_sheet.py --capital 112000 --holdings holdings.json

holdings.json maps symbol -> the share quantity YOU hold at the broker, e.g. {"AUGMONT": 12, "MANIKA": 270}.
The scanner does not know your quantities, so without it each stop line says "enter your held quantity".

Three sections (LEARNINGS.md section 12 has the evidence for each):
  1. STOPS.  Default --stop-mode close_alert: the decision is taken on the CLOSE (the 6:30 PM stop_loss_update scan
     already works this way) and the sale happens at the next open; an optional wide disaster GTT sits 8% below.
     --stop-mode resting reproduces the old intraday GTT-at-the-stop sheet (it cost about 2pp per trade in the replay).
  2. NEW ENTRIES from today's ACTIVE signals (unchanged).
  3. PRE-MARKET RESTING ENTRY ORDERS (default on, --no-resting-entries to hide): stop-limit BUY orders at listing
     high +1.5% (limit +2.5%) for every eligible IPO aged 5-60 days.  Eligibility runs the SAME gates as the research
     engine (core.honest_backtest.preview_resting_entry) on prior-session data only.

Nothing is written to MongoDB.  --log appends the generated orders to logs/resting_orders.jsonl (append-only) so fills
can be reconciled against them later.
"""

import argparse
import json
import os
from datetime import datetime, timedelta

import pandas as pd

from core.honest_backtest import Params, preview_resting_entry
from core.order_sheet import (SheetConfig, build_resting_entries, build_sheet, format_resting_entries,
                               format_sheet)
from core.portfolio_caps import DEFAULT_HARD_ACTIVE_POSITIONS
from core.risk_guard import GuardConfig, evaluate

COOLDOWN_CALENDAR_DAYS = 42      # about 30 trading bars, the backtest's per-symbol cooldown


def load_state():
    from db import positions_col, signals_col
    if positions_col is None or signals_col is None:
        raise SystemExit("MongoDB is not connected")
    positions = list(positions_col.find({"status": "ACTIVE"}, {"_id": 0}))
    today = datetime.now().strftime("%Y-%m-%d")
    signals = []
    for s in signals_col.find({"status": "ACTIVE"}, {"_id": 0}):
        sd = s.get("signal_date")
        sd = sd.strftime("%Y-%m-%d") if hasattr(sd, "strftime") else str(sd)[:10]
        if sd == today:
            signals.append(s)
    return positions, signals


def load_watchlist(today: pd.Timestamp, p: Params):
    """(listing records, candle DataFrames) for IPOs old enough to be eligible; plus symbols to skip."""
    from db import daily_candles_col, listing_data_col, positions_col
    lo = today - pd.Timedelta(days=p.max_age + 2)
    hi = today - pd.Timedelta(days=4)
    listings = {}
    for d in listing_data_col.find({}, {"_id": 0, "symbol": 1, "listing_date": 1, "listing_day_high": 1,
                                        "listing_day_low": 1, "listing_day_volume": 1, "is_nse_addition": 1,
                                        "bse_liquidity_passed": 1}):
        ld = d.get("listing_date")
        if not d.get("symbol") or ld is None or pd.isna(ld):
            continue
        ts = pd.Timestamp(ld)
        ts = ts.tz_localize(None).normalize() if ts.tzinfo else ts.normalize()
        if lo <= ts <= hi:
            listings[d["symbol"]] = d
    candles = {}
    if listings:
        for d in daily_candles_col.find({"symbol": {"$in": list(listings)}}, {"_id": 0, "symbol": 1, "candles": 1}):
            if not d.get("candles"):
                continue
            df = pd.DataFrame(d["candles"])
            df["DATE"] = pd.to_datetime(df["DATE"])
            for col in ("OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"):
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
            df = df[df["DATE"] < today].sort_values("DATE").reset_index(drop=True)
            candles[d["symbol"]] = df
    skip = {}
    cutoff = today - pd.Timedelta(days=COOLDOWN_CALENDAR_DAYS)
    for d in positions_col.find({}, {"_id": 0, "symbol": 1, "status": 1, "exit_date": 1}):
        sym, st = d.get("symbol"), d.get("status")
        if st in ("ACTIVE", "PAPER_ONLY"):
            skip[sym] = "already held"
        elif st in ("CLOSED", "PAPER_CLOSED") and d.get("exit_date") is not None:
            try:
                if pd.Timestamp(d["exit_date"]).tz_localize(None) >= cutoff:
                    skip.setdefault(sym, "exited within the 30-bar cooldown")
            except Exception:
                pass
    return listings, candles, skip


def shadow_pre_open(df, li, today, mkt, supply):
    """LOGGED-ONLY (core/shadow_score.py): the features known before the open.  Never raises, never affects an order."""
    try:
        from core.shadow_score import PRE_OPEN_FEATS, entry_features, load_model
        f = entry_features(df, li, today, mkt=mkt, supply=supply)
        if f is None:
            return dict(status="no_features")
        meta = load_model()
        return dict(status="awaiting_gap", model=(meta or {}).get("version"), score=None,
                    features={k: f.get(k) for k in PRE_OPEN_FEATS})
    except Exception as e:
        return dict(status=f"error:{type(e).__name__}")


def resting_candidates(today: pd.Timestamp, p: Params):
    listings, candles, skip = load_watchlist(today, p)
    cands, notes = [], []
    mkt, supply = None, None
    try:
        from core.shadow_score import market_state, supply30
        from db import listing_data_col
        mkt = market_state(candles, listings, today)
        recent = [d["listing_date"] for d in listing_data_col.find(
            {"listing_date": {"$gte": (today - pd.Timedelta(days=31)).to_pydatetime()}}, {"_id": 0, "listing_date": 1})]
        supply = supply30(recent, today)
    except Exception:
        pass                      # shadow logging only; the order list below does not depend on it
    for sym, li in listings.items():
        if sym in skip:
            notes.append(f"{sym}: {skip[sym]}")
            continue
        df = candles.get(sym)
        if df is None or len(df) < 15:
            continue
        pv = preview_resting_entry(df, li, today, p)
        if pv is not None:
            pv["symbol"] = sym
            pv["shadow"] = shadow_pre_open(df, li, today, mkt, supply)
            cands.append(pv)
    return cands, notes


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--capital", type=float, required=True, help="current total account value in Rs")
    ap.add_argument("--holdings", default=None, help="JSON file {symbol: quantity}")
    ap.add_argument("--hard-cap", type=int, default=DEFAULT_HARD_ACTIVE_POSITIONS,
                    help="max open positions; weight per position is 1 / this. Default comes from core/portfolio_caps.py "
                         "(10 slots, 10% each; the scanners use the same constant). Pass 7 for the pre-2026-10-14 behaviour")
    ap.add_argument("--buffer", type=float, default=2.0, help="sell-limit buffer below a stop trigger, percent")
    ap.add_argument("--stop-mode", choices=["close_alert", "resting"], default="close_alert")
    ap.add_argument("--disaster-buffer", type=float, default=8.0,
                    help="close_alert mode: optional GTT this far below the alert level, percent")
    ap.add_argument("--no-resting-entries", action="store_true", help="hide the pre-market resting entry orders")
    ap.add_argument("--log", action="store_true", help="append generated resting orders to logs/resting_orders.jsonl")
    ap.add_argument("--peak-capital", type=float, default=None,
                    help="highest account value you have reached (enables the drawdown kill-switch)")
    ap.add_argument("--losing-streak", type=int, default=0, help="current consecutive losing trades")
    ap.add_argument("--day-change-pct", type=float, default=None, help="today's account change in percent")
    args = ap.parse_args(argv)

    holdings = {}
    if args.holdings and os.path.exists(args.holdings):
        with open(args.holdings, "r", encoding="utf-8") as fh:
            holdings = json.load(fh)

    positions, signals = load_state()
    held_symbols = {p["symbol"] for p in positions}
    for p in positions:
        p["quantity"] = int(holdings.get(p["symbol"], 0))
    new_signals = [s for s in signals if s["symbol"] not in held_symbols]

    cfg = SheetConfig(capital=args.capital, hard_cap=args.hard_cap, stop_limit_buffer_pct=args.buffer,
                      stop_mode=args.stop_mode, disaster_buffer_pct=args.disaster_buffer)
    guard = None
    if args.peak_capital:
        guard = evaluate(args.capital, args.peak_capital, [-1.0] * args.losing_streak, args.day_change_pct, GuardConfig())
    print(format_sheet(build_sheet(positions, new_signals, cfg, guard=guard), cfg))

    if not args.no_resting_entries:
        today = pd.Timestamp(datetime.now().date())
        cands, notes = resting_candidates(today, Params())
        entries, skipped, warnings = build_resting_entries(cands, slots_used=len(held_symbols), cfg=cfg, guard=guard)
        print()
        print(format_resting_entries(entries, skipped, cfg, warnings))
        if args.log and entries:
            os.makedirs("logs", exist_ok=True)
            with open(os.path.join("logs", "resting_orders.jsonl"), "a", encoding="utf-8") as fh:
                shadow = {c["symbol"]: c.get("shadow") for c in cands}
                for e in entries:
                    fh.write(json.dumps(dict(date=str(today.date()), generated_at=datetime.now().isoformat(timespec="seconds"),
                                             shadow=shadow.get(e.symbol), **e.__dict__), default=str) + "\n")
            print(f"\nlogged {len(entries)} order(s) to logs/resting_orders.jsonl")


if __name__ == "__main__":
    main()
