# -*- coding: utf-8 -*-
"""
core/s3_store.py

MongoDB storage of the PAPER_ONLY S3 book (LEARNINGS 20.4).  Two collections, deliberately NOT the policy-P `positions` collection (that one has a
unique index on `symbol` and is managed by the live scanners with policy P's exits, including time stops):

  s3_paper_orders     one document per generated resting order, key (order_date, symbol); status PLACED -> FILLED when a fill is recorded.
  s3_paper_positions  one document per paper position; status OPEN | CLOSED; at most one OPEN per symbol.
  s3_paper_runs       one heartbeat document per day written by s3_paper_alerts.py (data date, counts, whether the alert was sent).

Every function takes the collection object, so tests run on an in-memory fake and nothing here imports `db` (no connection at import time).
All dates are stored as timezone-aware UTC midnight datetimes, like the rest of the database.
"""

from datetime import date, datetime, timezone
from typing import Iterable, List, Optional

import pandas as pd

SETUP = "S3_X1"
ORDER_LOOKBACK_DAYS = 7         # a fill may only take its default quantity from an order placed in the 7 days before it


def _version_fields() -> dict:
    from core.setup3 import S3_PAPER_VERSION, paper_rules_fingerprint
    return dict(s3_version=S3_PAPER_VERSION, rules_fp=paper_rules_fingerprint())


def to_utc_midnight(x) -> datetime:
    ts = pd.Timestamp(x)
    ts = ts.tz_localize(None) if ts.tzinfo else ts
    return datetime(ts.year, ts.month, ts.day, tzinfo=timezone.utc)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_indexes(orders_col, positions_col, runs_col=None) -> None:
    orders_col.create_index([("order_date", 1), ("symbol", 1)], unique=True)
    positions_col.create_index([("symbol", 1), ("status", 1)])
    positions_col.create_index("entry_date")
    if runs_col is not None:
        runs_col.create_index("run_date", unique=True)


def record_run(runs_col, run_date, **fields) -> None:
    """One heartbeat document per day (upsert): proves the daily S3 job ran, with what data and what it reported."""
    runs_col.update_one({"run_date": to_utc_midnight(run_date)}, {"$set": dict(fields, updated_at=_now(), **_version_fields()), "$setOnInsert": {"created_at": _now()}},
                        upsert=True)


def record_orders(orders_col, order_date, entries: Iterable[dict]) -> int:
    """Idempotent: re-running the sheet the same day updates the order, it never duplicates it or resets a FILLED status."""
    od = to_utc_midnight(order_date)
    n = 0
    for e in entries:
        doc = dict(e)
        sym = doc.pop("symbol")
        doc.pop("status", None)
        doc.update(setup=SETUP, paper_only=True, updated_at=_now(), **_version_fields())
        orders_col.update_one({"order_date": od, "symbol": sym},
                              {"$set": doc, "$setOnInsert": {"status": "PLACED", "created_at": _now()}}, upsert=True)
        n += 1
    return n


def open_positions(positions_col) -> List[dict]:
    return list(positions_col.find({"status": "OPEN"}, {"_id": 0}))


def closed_since(positions_col, cutoff) -> List[dict]:
    c = to_utc_midnight(cutoff)
    return [d for d in positions_col.find({"status": "CLOSED"}, {"_id": 0}) if d.get("exit_date") is not None and to_utc_midnight(d["exit_date"]) >= c]


def open_position(positions_col, orders_col, symbol: str, entry_date, entry_price: float, quantity: Optional[int] = None) -> dict:
    """
    Record a paper fill.  `quantity` defaults to the quantity of the most recent order for the symbol placed on or before the entry date and
    at most ORDER_LOOKBACK_DAYS before it (an old order must not size a new fill).
    Raises ValueError when a position is already open, the price is not positive, or no quantity can be determined.
    """
    symbol = symbol.strip().upper()
    if not entry_price or float(entry_price) <= 0:
        raise ValueError("entry price must be positive")
    if positions_col.find_one({"symbol": symbol, "status": "OPEN"}) is not None:
        raise ValueError(f"{symbol}: an S3 paper position is already open")
    ed = to_utc_midnight(entry_date)
    order = None
    cands = [d for d in orders_col.find({"symbol": symbol}, {"_id": 0}) if 0 <= (ed - to_utc_midnight(d["order_date"])).days <= ORDER_LOOKBACK_DAYS]
    if cands:
        order = max(cands, key=lambda d: d["order_date"])
    qty = int(quantity) if quantity else (int(order["quantity"]) if order and order.get("quantity") else 0)
    if qty < 1:
        raise ValueError(f"{symbol}: no quantity given and no earlier order to take it from")
    doc = dict(symbol=symbol, status="OPEN", setup=SETUP, paper_only=True, entry_date=ed, entry_price=float(entry_price), quantity=qty,
               order_date=order["order_date"] if order else None, pivot=(order or {}).get("pivot"), klass=(order or {}).get("klass"),
               turn20=(order or {}).get("turn20"), exit_date=None, exit_price=None, exit_reason=None, pnl_pct=None,
               created_at=_now(), updated_at=_now(), **_version_fields())
    positions_col.insert_one(dict(doc))
    if order:
        orders_col.update_one({"order_date": order["order_date"], "symbol": symbol},
                              {"$set": {"status": "FILLED", "fill_price": float(entry_price), "fill_date": ed, "updated_at": _now()}})
    return doc


def close_position(positions_col, symbol: str, exit_date, exit_price: float, reason: str = "manual") -> dict:
    symbol = symbol.strip().upper()
    pos = positions_col.find_one({"symbol": symbol, "status": "OPEN"}, {"_id": 0})
    if pos is None:
        raise ValueError(f"{symbol}: no open S3 paper position")
    if not exit_price or float(exit_price) <= 0:
        raise ValueError("exit price must be positive")
    pnl = (float(exit_price) / float(pos["entry_price"]) - 1.0) * 100.0
    upd = dict(status="CLOSED", exit_date=to_utc_midnight(exit_date), exit_price=float(exit_price), exit_reason=reason, pnl_pct=pnl, updated_at=_now())
    positions_col.update_one({"symbol": symbol, "status": "OPEN"}, {"$set": upd})
    return dict(pos, **upd)
