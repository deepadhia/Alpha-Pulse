# -*- coding: utf-8 -*-
"""
trade_ledger.py -- record real broker fills and sells in the append-only trade ledger, and check the ledger.

  python trade_ledger.py fill SYMBOL PRICE [--qty N] [--time "2026-11-05 10:12"]    your real BUY fill (checks it against the logged resting order)
  python trade_ledger.py sell SYMBOL PRICE --reason "stop alert, next open" [--qty N] [--time ...]
  python trade_ledger.py list [--symbol SYMBOL]
  python trade_ledger.py verify                                                      hash chain intact? (gaps or edited rows are reported)

`fill` reads logs/resting_orders.jsonl (written by order_sheet.py --log) and records the fill extension over the trigger.  A price above the limit
of the order is refused: a stop-limit BUY cannot fill there, so that would be a typing mistake or a different order.
Nothing here edits or deletes an event; a wrong entry is corrected by appending a NOTE.
"""

import argparse
import json
import os
import sys
from datetime import datetime

from core import ledger
from core.trade_guard import IST, default_is_trading_day, to_date

ORDERS_LOG = os.path.join("logs", "resting_orders.jsonl")


def _col():
    try:
        from db import trade_ledger_col
        if trade_ledger_col is not None:
            ledger.ensure_indexes(trade_ledger_col)        # idempotent; the unique seq index is what makes concurrent writers safe
        return trade_ledger_col
    except Exception:
        return None


def _version():
    try:
        from db import SCANNER_VERSION
        return SCANNER_VERSION
    except Exception:
        return None


def latest_order(symbol: str, path: str = ORDERS_LOG):
    """Most recent logged resting order for the symbol (None if there is none)."""
    if not os.path.exists(path):
        return None
    best = None
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("symbol") == symbol.upper():
                best = d
    return best


def check_fill(order, price: float, when: datetime):
    """(problems, extension_over_trigger_pct).  Problems block the record; an order-less fill is allowed but noted."""
    problems = []
    if not price > 0:
        problems.append("price must be positive")
    d = when.date()
    if not default_is_trading_day(d):
        problems.append(f"{d} ({d.strftime('%A')}) is not an NSE trading day")
    if when > datetime.now(IST).replace(tzinfo=None):          # `when` is a naive IST wall-clock time
        problems.append("fill time is in the future")
    ext = None
    if order:
        trig, lim = float(order.get("trigger") or 0), float(order.get("limit") or 0)
        if lim and price > lim * 1.0005:
            problems.append(f"price {price} is above the order's limit {lim}: a stop-limit BUY cannot fill there")
        if trig:
            ext = (price / trig - 1.0) * 100.0
            if price < trig * 0.998:
                problems.append(f"price {price} is below the order's trigger {trig}: a stop BUY cannot fill below its trigger")
    return problems, ext


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("fill", "sell"):
        sp = sub.add_parser(name)
        sp.add_argument("symbol")
        sp.add_argument("price", type=float)
        sp.add_argument("--qty", type=int, default=None)
        sp.add_argument("--time", default=None, help='fill time in IST, "YYYY-MM-DD HH:MM" (default: now)')
        if name == "sell":
            sp.add_argument("--reason", required=True)
        sp.add_argument("--force", action="store_true", help="record even if the checks fail (the problems are stored with the event)")
    sp = sub.add_parser("list")
    sp.add_argument("--symbol", default=None)
    sub.add_parser("verify")
    args = ap.parse_args(argv)

    col = _col()
    if args.cmd in ("fill", "sell"):
        when = datetime.strptime(args.time, "%Y-%m-%d %H:%M") if args.time else datetime.now(IST).replace(tzinfo=None, microsecond=0)
        order = latest_order(args.symbol) if args.cmd == "fill" else None
        problems, ext = check_fill(order, args.price, when) if args.cmd == "fill" else ([], None)
        if args.cmd == "sell":
            if not args.price > 0:
                problems.append("price must be positive")
            if not default_is_trading_day(when.date()):
                problems.append(f"{when.date()} is not an NSE trading day")
        if problems and not args.force:
            print("NOT recorded:")
            for p in problems:
                print("  -", p)
            print("Fix the input, or pass --force to record it with the problems attached.")
            return 1
        payload = dict(price=args.price, quantity=args.qty, time_ist=when.isoformat(timespec="minutes"), problems=problems)
        if args.cmd == "fill":
            payload.update(order_trigger=(order or {}).get("trigger"), order_limit=(order or {}).get("limit"), order_date=(order or {}).get("date"),
                           extension_over_trigger_pct=None if ext is None else round(ext, 3), had_logged_order=order is not None)
        else:
            payload["reason"] = args.reason
        ev = ledger.append(col, "MANUAL_FILL" if args.cmd == "fill" else "MANUAL_SELL", args.symbol, payload, "trade_ledger.py", code_version=_version())
        if ev is None:
            print("ledger write FAILED (see the log)")
            return 2
        print(f"recorded #{ev['seq']} {ev['event_type']} {ev['symbol']} @ {args.price}" +
              (f"  (fill {ext:+.2f}% vs trigger; pass mark is within +0.5%)" if ext is not None else "  (no logged resting order found)"))
        return 0

    if args.cmd == "list":
        for e in ledger.events_for(col, args.symbol):
            print(f"#{e['seq']:<5} {e['ts'][:19]}  {e['event_type']:<16} {e.get('symbol') or '-':<12} {json.dumps(e['payload'], default=str)[:150]}")
        return 0

    if args.cmd == "verify":
        events = ledger.all_events(col)
        problems = ledger.verify_chain(events)
        print(f"{len(events)} event(s); " + ("chain intact" if not problems else f"{len(problems)} PROBLEM(S)"))
        for p in problems:
            print("  -", p)
        return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
