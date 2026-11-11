# -*- coding: utf-8 -*-
"""
core/resting_runtime.py -- builds the LIVE collaborators (MongoDB, Upstox) for core.resting_jobs and formats the Telegram report.
Kept apart from the decision logic so that logic stays import-light and unit-testable.  `db` is imported lazily inside functions (no connection at import).
"""

import html
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from core import resting_exec as X
from core.resting_jobs import Deps

QUOTE_SCANNER = "resting_exec"
QUOTE_ACTION = "QUOTE_AUDIT"
MEASURED_SAMPLES = 5            # median of the stock's last N open-time quotes = its measured half spread


class ReadOnlyCol:
    """Dry-run wrapper: reads pass through, writes are dropped."""

    def __init__(self, col):
        self._c = col

    def find(self, q):
        return self._c.find(q)

    def find_one(self, q):
        return self._c.find_one(q)

    def update_one(self, *a, **k):
        return None


def daily_from_cache(symbol: str) -> Optional[pd.DataFrame]:
    from db import daily_candles_col
    doc = daily_candles_col.find_one({"symbol": symbol}, {"_id": 0, "candles": 1})
    if not doc or not doc.get("candles"):
        return None
    df = pd.DataFrame(doc["candles"])
    df["DATE"] = pd.to_datetime(df["DATE"])
    for c in ("OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    return df.sort_values("DATE").reset_index(drop=True)


def audit_quote(symbol: str, quote: dict, day, now_ist: Optional[datetime] = None) -> None:
    """Every open-time quote is kept (logs collection, 30-day TTL): the measured spread feeds the evening fill model and recalibrates the tiers."""
    from db import insert_log
    hs = X.half_spread_bps(quote)
    insert_log(scanner=QUOTE_SCANNER, symbol=symbol, action=QUOTE_ACTION, candle_timestamp=now_ist or datetime.now(timezone.utc),
               details=dict(day=str(day), open=quote.get("open"), ltp=quote.get("ltp"), bid=quote.get("bid"), ask=quote.get("ask"), bid_qty=quote.get("bid_qty"),
                            ask_qty=quote.get("ask_qty"), half_spread_bps=(round(hs, 2) if hs is not None else None), upper_circuit=quote.get("upper_circuit"),
                            lower_circuit=quote.get("lower_circuit"), quote_ts=quote.get("ts")))


def measured_half_spread(symbol: str, day) -> Optional[float]:
    """Median half spread (bps) of this stock's most recent audited open-time quotes up to `day`; None when it has none."""
    from db import logs_col
    if logs_col is None:
        return None
    rows = logs_col.find({"scanner": QUOTE_SCANNER, "action": QUOTE_ACTION, "symbol": symbol}).sort("timestamp", -1).limit(MEASURED_SAMPLES)
    vals = []
    for r in rows:
        d = r.get("details") or {}
        if d.get("half_spread_bps") is not None and str(d.get("day", "")) <= str(day):
            vals.append(float(d["half_spread_bps"]))
    return float(np.median(vals)) if vals else None


def build_deps(source: str, token: Optional[str], capital: float, dry_run: bool = False, now_ist: Optional[datetime] = None,
               candles: Optional[Dict[str, pd.DataFrame]] = None) -> Deps:
    """Live Deps.  `candles` (symbol -> frame, e.g. refreshed in memory) takes precedence over the cache."""
    from core import upstox_quotes as UQ
    from core.ledger import append as ledger_append, ensure_indexes as ledger_indexes
    from core.trade_guard import default_is_trading_day
    from db import SCANNER_VERSION, get_instrument_key_mapping, positions_col, trade_ledger_col, upsert_position
    if positions_col is None or trade_ledger_col is None:
        raise SystemExit("MongoDB is not connected")
    mapping = get_instrument_key_mapping()
    ledger_indexes(trade_ledger_col)

    def quotes(syms):
        return UQ.fetch_quotes({s: mapping.get(s) for s in syms if mapping.get(s)}, token)

    def minute_bars(sym, day):
        if not mapping.get(sym):
            raise RuntimeError(f"{sym}: no instrument key")
        return UQ.fetch_minute_bars(mapping[sym], day, token)

    def daily(sym):
        if candles is not None and sym in candles:
            return candles[sym]
        return daily_from_cache(sym)

    def append(event_type, symbol, payload):
        if dry_run:
            return {"dry_run": True}
        return ledger_append(trade_ledger_col, event_type, symbol, payload, source, code_version=SCANNER_VERSION)

    def upsert(doc):
        return True if dry_run else upsert_position(doc)

    return Deps(ledger_col=trade_ledger_col, positions_col=ReadOnlyCol(positions_col) if dry_run else positions_col, ledger_append=append, upsert_position=upsert,
                quotes=quotes, minute_bars=minute_bars, daily=daily,
                audit_quote=(lambda s, q, d: None) if dry_run else (lambda s, q, d: audit_quote(s, q, d, now_ist)),
                measured_hs=measured_half_spread, is_trading_day=default_is_trading_day, version=SCANNER_VERSION, capital=capital)


def symbols_needed(deps: Deps, run_day) -> List[str]:
    """Symbols whose daily candles the evening settlement needs: today's open orders, autofill positions, unsettled exits."""
    syms = {o["symbol"] for o in X.orders_for_day(deps.ledger_col, run_day)}
    syms |= {p["symbol"] for p in deps.positions_col.find({"entry_source": X.ENTRY_SOURCE})
             if p.get("status") == "ACTIVE" or not p.get("exit_settled")}
    return sorted(syms)


# ------------------------------------------------------------------------------------------------ report
def _section(lines: List[str], title: str, items: List[str]) -> None:
    if items:
        lines += ["", title]
        lines += [f"  • {html.escape(str(x))}" for x in items]


def format_exec_lines(rep: dict, when: str) -> List[str]:
    """Telegram-HTML lines for a morning / evening execution report (empty list when nothing happened)."""
    lines: List[str] = []
    _section(lines, f"🟢 <b>FORWARD FILLS RECORDED</b> ({when})", rep.get("filled", []))
    _section(lines, "⏳ <b>ORDERS THAT DID NOT FILL</b>", rep.get("expired", []))
    _section(lines, "🔴 <b>EXITS SOLD</b> (realised price replaces the decision close)", rep.get("sold", []))
    _section(lines, "🚧 <b>EXITS DEFERRED</b> (lower circuit / no bids)", rep.get("deferred", []))
    _section(lines, "🛑 <b>DAY-0 STOP</b> (fill-day close at or below the initial stop)", rep.get("day0_stops", []))
    _section(lines, "⏱️ <b>LEFT FOR THE EVENING</b> (not decidable from the open quote)", rep.get("pending", []))
    _section(lines, "⚠️ <b>WAITING FOR DATA</b>", rep.get("waiting", []) + rep.get("exit_waiting", []))
    _section(lines, "ℹ️ <b>NOTES</b>", rep.get("notes", []))
    return lines


def format_morning_message(rep: dict, now_ist: datetime) -> str:
    head = [f"🔔 <b>RESTING FORWARD TEST: open-time execution</b>", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"📅 {rep['day']} • quotes {rep.get('quotes', 0)} • {now_ist.strftime('%H:%M IST')}"]
    body = format_exec_lines(rep, "best ask after the open")
    return "\n".join(head + (body or ["", "💤 <i>Nothing to execute at the open.</i>"]) + ["", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"])
