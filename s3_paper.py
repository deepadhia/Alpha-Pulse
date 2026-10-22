# -*- coding: utf-8 -*-
"""
s3_paper.py -- record and list the PAPER_ONLY S3 book in MongoDB (collections s3_paper_orders, s3_paper_positions; see core/s3_store.py).

  python s3_paper.py fill MARKOLINES 195.4                  # you bought (paper): qty comes from today's logged order
  python s3_paper.py fill MARKOLINES 195.4 --qty 50 --date 2026-10-23
  python s3_paper.py exit MARKOLINES 171.2 --date 2026-11-02 --reason "stop alert"
  python s3_paper.py list                                   # open and closed positions
  python s3_paper.py orders --days 10                       # generated orders and whether they were filled

order_sheet.py reads the open positions from this collection and prints their stop-alert levels; it never writes positions itself.
Policy P's `positions` collection is not touched.
"""

import argparse
from datetime import datetime, timedelta, timezone

import pandas as pd

from core import s3_store


def _cols():
    from db import db
    if db is None:
        raise SystemExit("MongoDB is not connected")
    orders, positions = db["s3_paper_orders"], db["s3_paper_positions"]
    s3_store.ensure_indexes(orders, positions)
    return orders, positions


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fill", help="record a paper entry")
    f.add_argument("symbol")
    f.add_argument("price", type=float)
    f.add_argument("--qty", type=int, default=None)
    f.add_argument("--date", default=None, help="entry date YYYY-MM-DD (default today)")
    x = sub.add_parser("exit", help="record a paper exit")
    x.add_argument("symbol")
    x.add_argument("price", type=float)
    x.add_argument("--date", default=None)
    x.add_argument("--reason", default="manual")
    sub.add_parser("list", help="list positions")
    o = sub.add_parser("orders", help="list recent generated orders")
    o.add_argument("--days", type=int, default=10)
    a = ap.parse_args(argv)
    orders, positions = _cols()
    today = pd.Timestamp(datetime.now().date())
    if a.cmd == "fill":
        d = s3_store.open_position(positions, orders, a.symbol, a.date or today, a.price, a.qty)
        print(f"recorded: {d['symbol']} {d['quantity']} @ {d['entry_price']:.2f} on {d['entry_date'].date()} (order {'matched' if d['order_date'] else 'not found'})")
    elif a.cmd == "exit":
        d = s3_store.close_position(positions, a.symbol, a.date or today, a.price, a.reason)
        print(f"closed: {d['symbol']} @ {d['exit_price']:.2f} on {d['exit_date'].date()}  pnl {d['pnl_pct']:+.1f}%  ({d['exit_reason']})")
    elif a.cmd == "list":
        rows = list(positions.find({}, {"_id": 0}).sort("entry_date", 1))
        if not rows:
            print("(no S3 paper positions)")
        for r in rows:
            tail = f"exit {r['exit_date'].date()} @ {r['exit_price']:.2f} pnl {r['pnl_pct']:+.1f}% ({r['exit_reason']})" if r["status"] == "CLOSED" else "open"
            print(f"  {r['symbol']:<12} {r['status']:<6} {r['quantity']:>5} @ {r['entry_price']:>9.2f} on {r['entry_date'].date()}  {tail}")
    else:
        cutoff = datetime.now(timezone.utc) - timedelta(days=a.days)
        rows = list(orders.find({"order_date": {"$gte": cutoff}}, {"_id": 0}).sort("order_date", -1))
        if not rows:
            print("(no S3 paper orders in that window)")
        for r in rows:
            print(f"  {r['order_date'].date()} {r['symbol']:<12} {r['status']:<7} qty {(r.get('quantity') or 0):>5} trigger {r.get('trigger', 0):>9.2f} limit {r.get('limit', 0):>9.2f}"
                  + (f"  filled @ {r['fill_price']:.2f}" if r.get("fill_price") else ""))


if __name__ == "__main__":
    main()
