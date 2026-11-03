# -*- coding: utf-8 -*-
"""
core/ledger.py

Append-only trade ledger.  One event per fact: an order generated, an entry accepted or rejected, a manual fill, a stop moved, a position closed.
Events are only ever inserted (no update, no delete in this module).  Each carries a sequence number and a SHA-256 hash that covers the previous
event's hash, so an edited or removed row is detectable with verify_chain().  The `positions` collection is keyed by symbol and is overwritten on
every re-entry, so it cannot answer "what exactly happened"; the ledger can.

  collection  trade_ledger   (unique index on seq)
  mirror      logs/ledger.jsonl   (best-effort local copy; also the only store when MongoDB is not configured)

Every function takes the collection object (or None), so tests run on an in-memory fake and nothing here connects at import time.
append() never raises: a ledger failure must not stop trading, it is logged and returned as None.
"""

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Iterable, List, Optional

logger = logging.getLogger(__name__)

COLLECTION = "trade_ledger"
LOCAL_PATH = os.path.join("logs", "ledger.jsonl")
GENESIS = "0" * 64
MAX_RETRIES = 6

EVENT_TYPES = {
    "ORDER_GENERATED",     # a resting order printed by order_sheet.py (what the owner was told to place)
    "ENTRY_ACCEPTED",      # a position document was opened in `positions` (any status)
    "ENTRY_REJECTED",      # the guard refused a new position (reasons in the payload)
    "MANUAL_FILL",         # the owner's real broker fill, recorded with trade_ledger.py fill
    "STOP_MOVED",          # the trailing stop was raised
    "POSITION_CLOSED",     # exit recorded by the scanner (exit price, reason)
    "MANUAL_SELL",         # the owner's real sell, recorded with trade_ledger.py sell
    "NOTE",
}


def _canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def compute_hash(event: dict) -> str:
    body = {k: event[k] for k in ("seq", "ts", "event_type", "symbol", "source", "code_version", "payload", "prev_hash")}
    return hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()


def build_event(seq: int, prev_hash: str, event_type: str, symbol: Optional[str], payload: dict, source: str,
                code_version: Optional[str] = None, now: Optional[datetime] = None) -> dict:
    if event_type not in EVENT_TYPES:
        raise ValueError(f"unknown ledger event type {event_type!r}")
    ev = dict(seq=int(seq), ts=(now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(timespec="milliseconds"),
              event_type=event_type, symbol=(symbol or "").upper() or None, source=source,
              code_version=code_version, payload=json.loads(_canonical(payload or {})), prev_hash=prev_hash)
    ev["hash"] = compute_hash(ev)
    return ev


def _last_event(col, local_path: str) -> Optional[dict]:
    if col is not None:
        cur = col.find({}, {"_id": 0})
        if isinstance(cur, list):                    # in-memory fake collections return plain lists
            docs = sorted(cur, key=lambda d: d["seq"])
            return docs[-1] if docs else None
        docs = list(cur.sort("seq", -1).limit(1))
        return docs[0] if docs else None
    events = _local_events(local_path)
    return events[-1] if events else None


def _local_events(path: str) -> List[dict]:
    if not os.path.exists(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    return out


def append(col, event_type: str, symbol: Optional[str], payload: dict, source: str, code_version: Optional[str] = None,
           local_path: Optional[str] = LOCAL_PATH, now: Optional[datetime] = None) -> Optional[dict]:
    """Insert one event; returns it, or None when it could not be written (logged, never raised)."""
    try:
        for _ in range(MAX_RETRIES):
            last = _last_event(col, local_path or LOCAL_PATH)
            ev = build_event((last["seq"] + 1) if last else 1, last["hash"] if last else GENESIS, event_type, symbol, payload, source,
                             code_version, now)
            if col is not None:
                try:
                    col.insert_one(dict(ev))
                except Exception as e:
                    if type(e).__name__ == "DuplicateKeyError":      # another writer took this seq: re-read and retry
                        continue
                    raise
            if local_path:
                try:
                    os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
                    with open(local_path, "a", encoding="utf-8") as fh:
                        fh.write(_canonical(ev) + "\n")
                except Exception as e:
                    logger.warning(f"ledger local mirror write failed: {e}")
            return ev
        logger.error(f"ledger: could not append {event_type} {symbol}: sequence contention")
    except Exception as e:
        logger.error(f"ledger: append failed for {event_type} {symbol}: {e}")
    return None


def ensure_indexes(col) -> None:
    col.create_index("seq", unique=True)
    col.create_index([("symbol", 1), ("seq", 1)])


def verify_chain(events: Iterable[dict]) -> List[str]:
    """Problems found in a list of ledger events (empty = intact): gaps, a broken hash link, or a row whose content no longer matches its hash."""
    problems: List[str] = []
    prev_hash, prev_seq = GENESIS, 0
    for ev in sorted(events, key=lambda e: e["seq"]):
        if ev["seq"] != prev_seq + 1:
            problems.append(f"seq {ev['seq']}: gap after {prev_seq} (a row is missing)")
        if ev.get("prev_hash") != prev_hash:
            problems.append(f"seq {ev['seq']}: prev_hash does not match the previous event")
        if compute_hash(ev) != ev.get("hash"):
            problems.append(f"seq {ev['seq']}: content does not match its hash (row was edited)")
        prev_hash, prev_seq = ev.get("hash"), ev["seq"]
    return problems


def events_for(col, symbol: Optional[str] = None, local_path: str = LOCAL_PATH) -> List[dict]:
    if col is not None:
        q = {"symbol": symbol.upper()} if symbol else {}
        return sorted((dict(d) for d in col.find(q)), key=lambda d: d["seq"])
    ev = _local_events(local_path)
    return [e for e in ev if not symbol or e.get("symbol") == symbol.upper()]


def all_events(col, local_path: str = LOCAL_PATH) -> List[dict]:
    return events_for(col, None, local_path)
