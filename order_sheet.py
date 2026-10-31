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

  4. S3 PAPER BOOK (PAPER_ONLY, default on, --no-s3 to hide; LEARNINGS.md sections 20, 20.3, 20.4): resting stop-limit BUY orders at the
     highest price since listing +1.5% for IPOs aged 61-400 days whose 20-session mean turnover is <= Rs 25 Cr, plus the stop-alert levels
     of the open S3 paper positions.  X1 exits: close-based stop and trailing ratchet only, no time-based exits.  Everything is stored in
     MongoDB, in its own collections s3_paper_orders and s3_paper_positions (core/s3_store.py), NOT in the policy-P `positions` collection:
     the sheet stores the orders it prints (idempotent per day and symbol; --no-s3-store prints only); you record fills and exits with
     `python s3_paper.py fill SYMBOL PRICE` and `python s3_paper.py exit SYMBOL PRICE`.  Policy P's orders and rules are untouched.

With Mongo connected, --log appends the policy-P resting orders to logs/resting_orders.jsonl (append-only) so fills can be reconciled
against them later; the S3 book needs no log file.
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
from core import s3_store
from s3_paper_alerts import previous_session
from core.risk_guard import GuardConfig, evaluate
from core.setup3 import PAPER_MAX_TURN_CR, PAPER_PARAMS, Setup3Params, paper_exit_params, preview_s3_entry, s3_paper_status

COOLDOWN_CALENDAR_DAYS = 42      # about 30 trading bars, the backtest's per-symbol cooldown
S3_MAX_STALE_DAYS = 6            # newest candle older than this: the cache is stale, place no order from it
S3_PLACEHOLDER_LISTING_DATES = (pd.Timestamp("2026-08-17"), pd.Timestamp("2026-08-18"))


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


# ------------------------------------------------------------------------------------------ S3 paper book (PAPER_ONLY)

def _naive_ts(x) -> pd.Timestamp:
    ts = pd.Timestamp(x)
    return ts.tz_localize(None).normalize() if ts.tzinfo else ts.normalize()


def s3_split_positions(docs: list, today: pd.Timestamp):
    """(open positions {symbol: doc}, {symbol: reason} for exits within the cooldown) from s3_paper_positions documents."""
    open_, cool = {}, {}
    cutoff = today - pd.Timedelta(days=COOLDOWN_CALENDAR_DAYS)
    for d in docs:
        if d.get("status") == "OPEN":
            open_[d["symbol"]] = d
        elif d.get("status") == "CLOSED" and d.get("exit_date") is not None and _naive_ts(d["exit_date"]) >= cutoff:
            cool[d["symbol"]] = "S3 paper exit within the 30-bar cooldown"
    return open_, cool


def s3_candidates(listings: dict, candles: dict, skip: dict, today: pd.Timestamp, s3: Setup3Params = PAPER_PARAMS):
    """
    Pure function (no database): S3 resting-order candidates for `today` from listing records and completed-session candles.
    Data hygiene mirrors core.setup2.clean_universe (the replay universe): real listing date, first candle within 5 days of it,
    listing high within 2% of the first candle high, at least 40 candles, fresh cache.
    """
    cands, notes = [], []
    p = paper_exit_params()
    for sym, li in listings.items():
        if sym in skip:
            notes.append(f"{sym}: {skip[sym]}")
            continue
        df = candles.get(sym)
        if df is None or len(df) < 40:
            continue
        ld = _naive_ts(li["listing_date"])
        if ld in S3_PLACEHOLDER_LISTING_DATES:
            notes.append(f"{sym}: placeholder listing date")
            continue
        if abs((_naive_ts(df["DATE"].iloc[0]) - ld).days) > 5:
            notes.append(f"{sym}: candle history does not start at the listing (pivot unknown)")
            continue
        lh = li.get("listing_day_high")
        if not lh or not (0.98 <= float(lh) / float(df["HIGH"].iloc[0]) <= 1.02):
            notes.append(f"{sym}: listing high inconsistent with the first candle")
            continue
        if (today - _naive_ts(df["DATE"].iloc[-1])).days > S3_MAX_STALE_DAYS:
            notes.append(f"{sym}: candles stale (last {_naive_ts(df['DATE'].iloc[-1]).date()})")
            continue
        pv = preview_s3_entry(df, li, today, p, s3)
        if pv is not None:
            pv["symbol"] = sym
            cands.append(pv)
    return cands, notes


def s3_position_rows(open_holdings: dict, candles: dict) -> list:
    """Status of each S3 paper position from the engine (X1 exits).  Rows carry `error` when the entry date is not in the candles."""
    rows = []
    p = paper_exit_params()
    for sym, h in sorted(open_holdings.items()):
        df = candles.get(sym)
        row = dict(symbol=sym, entry_date=h.get("entry_date"), entry_price=float(h.get("entry_price", 0)), quantity=int(h.get("quantity", 0)))
        if df is None or df.empty:
            row["error"] = "no candles"
        elif _naive_ts(h["entry_date"]) > _naive_ts(df["DATE"].iloc[-1]):
            row["pending"] = True                      # filled today, the candle cache has not caught up yet: not an error
            row["error"] = "entry day not in the candle cache yet (checked again after the evening refresh)"
        else:
            st = s3_paper_status(df, h["entry_date"], row["entry_price"], p, symbol=sym)
            if st is None:
                row["error"] = "entry date or price not usable against the candles"
            else:
                row.update(st)
        rows.append(row)
    return rows


def format_s3_positions(rows: list) -> str:
    lines = ["S3 PAPER POSITIONS  (PAPER_ONLY; X1 exits: decide on the CLOSE, if the close is at or below the alert level mark the exit at the NEXT OPEN)", ""]
    if not rows:
        lines.append("  (none open; record a fill with: python s3_paper.py fill SYMBOL PRICE)")
    for r in rows:
        if r.get("error"):
            lines.append(f"  {r['symbol']:<12} ? {r['error']}")
        elif r["status"] == "EXITED":
            lines.append(f"  {r['symbol']:<12} EXITED {str(r['exit_date'])[:10]} at {r['exit_price']:.2f} ({r['exit_reason']}); pnl {r['pnl_pct']:+.1f}%"
                         f"  <- record it with: python s3_paper.py exit SYMBOL PRICE")
        else:
            flag = "   <== STOP ALERT HIT: paper-sell at the next open" if r["alert"] else ""
            lines.append(f"  {r['symbol']:<12} qty {r['quantity']:>5}  entry {r['entry_price']:>9.2f}  last close {r['last_close']:>9.2f} ({r['pnl_pct']:+.1f}%, peak {r['max_runup']:+.1f}%)"
                         f"  alert level {r['trailing']:>9.2f}  day {r['days_held']}{flag}")
    return "\n".join(lines)


S3_FOOTER = [
    "  PAPER_ONLY: these are NOT live orders.  Place them in a paper/virtual account or record the fill with `python s3_paper.py fill SYMBOL PRICE` (stored in MongoDB); policy P is unaffected.",
    "  What it is: S3 = breakout of the highest price since listing (age 61-400 days), X1 exits (stop + trailing only, no time-based exits), only names with",
    f"  20-session mean turnover <= Rs {PAPER_MAX_TURN_CR:.0f} Cr.  Replay 2023-26: positive but fragile (LEARNINGS 20 / 20.3: needs a few outliers, in-sample turnover",
    "  threshold, one of nine looks).  Unproven; the forward paper sample decides.  Record every fill and exit; expect long flat stretches and 12% stops.",
]


def s3_collect(today: pd.Timestamp, cfg: SheetConfig, guard=None, refresh=None) -> dict:
    """
    Everything the S3 paper section and the S3 alerts need, for orders to be placed on session `today` (completed sessions before it only):
    dict(rows, entries, skipped, warnings, cands, notes, open_n, data_asof, refresh).  Reads MongoDB, writes nothing.  Returns None when Mongo is off.
    `refresh` (optional): callable(candles, listing_symbols, held_symbols) -> (candles, report) that merges the completed sessions the shared
    cache lacks (core.s3_refresh); `data_asof` is then judged on the held positions and the names near their pivot, not on the whole universe.
    """
    from db import daily_candles_col, listing_data_col, positions_col, s3_paper_orders_col, s3_paper_positions_col
    if s3_paper_orders_col is None or s3_paper_positions_col is None:
        return None

    def fetch(symbols):
        out = {}
        if symbols:
            for d in daily_candles_col.find({"symbol": {"$in": list(symbols)}}, {"_id": 0, "symbol": 1, "candles": 1}):
                if not d.get("candles"):
                    continue
                df = pd.DataFrame(d["candles"])
                df["DATE"] = pd.to_datetime(df["DATE"])
                for col in ("OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"):
                    df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
                out[d["symbol"]] = df[df["DATE"] < today].sort_values("DATE").reset_index(drop=True)
        return out

    open_h, cool = s3_split_positions(list(s3_paper_positions_col.find({}, {"_id": 0})), today)
    skip = dict(cool)
    for sym in open_h:
        skip[sym] = "already held in the S3 paper book"
    p_cut = today - pd.Timedelta(days=COOLDOWN_CALENDAR_DAYS)
    for d in positions_col.find({}, {"_id": 0, "symbol": 1, "status": 1, "exit_date": 1}):
        sym, st = d.get("symbol"), d.get("status")
        if st in ("ACTIVE", "PAPER_ONLY"):
            skip.setdefault(sym, "held in the policy-P book")
        elif st in ("CLOSED", "PAPER_CLOSED") and d.get("exit_date") is not None:
            try:
                if _naive_ts(d["exit_date"]) >= p_cut:
                    skip.setdefault(sym, "policy-P exit within the 30-bar cooldown")
            except Exception:
                pass
    lo, hi = today - pd.Timedelta(days=PAPER_PARAMS.max_age_days + 2), today - pd.Timedelta(days=PAPER_PARAMS.min_age_days - 1)
    listings = {}
    for d in listing_data_col.find({}, {"_id": 0, "symbol": 1, "listing_date": 1, "listing_day_high": 1, "listing_day_low": 1,
                                        "listing_day_volume": 1, "is_nse_addition": 1, "bse_liquidity_passed": 1}):
        ld = d.get("listing_date")
        if not d.get("symbol") or ld is None or pd.isna(ld):
            continue
        if lo <= _naive_ts(ld) <= hi:
            listings[d["symbol"]] = d
    candles = fetch(set(listings) | set(open_h))
    rep = None
    if refresh is not None:
        candles, rep = refresh(candles, set(listings), set(open_h))
    rows = s3_position_rows(open_h, candles)
    cands, notes = s3_candidates(listings, candles, skip, today)
    entries, skipped, warnings = build_resting_entries(cands, slots_used=len(open_h), cfg=cfg, guard=guard)
    if rep is not None:
        from core.s3_refresh import describe
        warnings = list(warnings) + describe(rep)
    # the MEDIAN last-candle date of the names that matter (the watched universe, or after a refresh the held names and those near their pivot):
    # one fresh symbol must not hide a stale cache.  A held position whose own candles lag caps the date, its stop alert depends on them.
    relevant = (rep["selected"] & set(listings)) if rep is not None and rep["selected"] & set(listings) else set(listings)
    lasts = sorted(_naive_ts(df["DATE"].iloc[-1]) for sym, df in candles.items() if len(df) and sym in relevant)
    data_asof = lasts[len(lasts) // 2] if lasts else None
    held_lasts = [_naive_ts(candles[s]["DATE"].iloc[-1]) for s in open_h if s in candles and len(candles[s])]
    if held_lasts and (data_asof is None or min(held_lasts) < data_asof):
        data_asof = min(held_lasts)
    return dict(rows=rows, entries=entries, skipped=skipped, warnings=warnings, cands=cands, notes=notes, open_n=len(open_h),
                data_asof=data_asof, refresh=rep)


def s3_order_docs(entries, cands) -> list:
    meta = {c["symbol"]: c for c in cands}
    docs = []
    for e in entries:
        c = meta.get(e.symbol, {})
        docs.append(dict(e.__dict__, max_turn_cr=PAPER_MAX_TURN_CR, pivot=c.get("pivot"), klass=c.get("klass"), sess_old=c.get("sess_old"),
                         turn20=c.get("turn20")))
    return docs


def s3_section(today: pd.Timestamp, cfg: SheetConfig, guard, store: bool = True) -> None:
    """Print the S3 paper-book section.  Reads MongoDB; writes only the orders it prints, to s3_paper_orders (idempotent), unless store=False."""
    from db import s3_paper_orders_col, s3_paper_positions_col
    res = s3_collect(today, cfg, guard)
    if res is None:
        print("\nS3 PAPER BOOK: MongoDB is not connected, section skipped")
        return
    s3_store.ensure_indexes(s3_paper_orders_col, s3_paper_positions_col)
    print()
    print(format_s3_positions(res["rows"]))
    print()
    print(format_resting_entries(res["entries"], res["skipped"], cfg, res["warnings"],
                                 title="S3 PAPER BOOK: PRE-MARKET RESTING ENTRY ORDERS  (PAPER_ONLY stop-limit BUY, DAY validity)", footer=S3_FOOTER))
    stale = res["data_asof"] is None or res["data_asof"] < previous_session(today)
    if stale:
        print(f"\nS3 PAPER BOOK: candle data is as of {res['data_asof'].date() if res['data_asof'] is not None else 'n/a'}, older than the last completed "
              f"session {previous_session(today).date()}; these orders are NOT stored and should not be placed until the cache is refreshed")
    elif store and res["entries"]:
        n = s3_store.record_orders(s3_paper_orders_col, today, s3_order_docs(res["entries"], res["cands"]))
        print(f"\nstored {n} S3 paper order(s) in MongoDB (s3_paper_orders); after a paper fill run: python s3_paper.py fill SYMBOL PRICE")


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
    ap.add_argument("--no-s3", action="store_true", help="hide the S3 PAPER_ONLY section")
    ap.add_argument("--no-s3-store", action="store_true", help="print the S3 section without storing its orders in MongoDB")
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

    if not args.no_s3:
        s3_section(pd.Timestamp(datetime.now().date()), cfg, guard, store=not args.no_s3_store)


if __name__ == "__main__":
    main()
