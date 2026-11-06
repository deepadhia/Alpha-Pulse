# -*- coding: utf-8 -*-
"""
core/s3_autofill.py

Automatic paper fills for the PAPER_ONLY S3 book (2026-11-06).

Why: S3 orders are stored every evening (s3_paper_orders) but a paper POSITION only existed after someone ran `python s3_paper.py fill`.  Nobody did:
s3_paper_positions stayed empty, so the forward sample the whole S3 evaluation waits for (core/s3_scorecard.py: no verdict below 40 closed trades) could
not start, and a name like SWARAJ simply vanished from the order list after its trigger day.

What: for every PLACED order whose session has a completed candle, decide from that bar whether the resting stop-limit BUY would have filled, using the
SAME rule as the replay (core/setup3.setup3_signal): filled when high >= trigger and open <= limit, at max(open, trigger).  The recorded paper price adds
AUTO_FILL_SLIP_PCT (the replay's 0.3% slippage) and is capped at the limit.  Otherwise the order is marked EXPIRED with the reason.  An order is valid
only if it was stored BEFORE that session opened (an order generated or re-run later the same day is not a pre-placed order: no look-ahead).
Nothing here changes a paper rule (core.setup3.paper_rules_fingerprint is untouched); the fill price convention is stored on every auto fill.
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import pandas as pd

from core import s3_store

AUTO_FILL_SLIP_PCT = 0.3                      # same slippage as the replay (setup3_signal slip_pct default)
SESSION_OPEN_UTC = timedelta(hours=3, minutes=45)   # 09:15 IST
MAX_ORDER_AGE_DAYS = 10                       # an order whose candle never shows up within this window is closed out, not left PLACED forever
FILL_MODEL = f"auto: max(open, trigger) + {AUTO_FILL_SLIP_PCT}% slip, capped at the limit; filled if high >= trigger and open <= limit"


def _aware(dt) -> Optional[datetime]:
    if dt is None:
        return None
    if isinstance(dt, pd.Timestamp):
        dt = dt.to_pydatetime()
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def decide(order: dict, bar: Optional[dict], slip_pct: float = AUTO_FILL_SLIP_PCT) -> dict:
    """
    One order against its session bar (dict with OPEN, HIGH).  Returns dict(action, price, reason) with action FILL | EXPIRE | WAIT.
    WAIT = no bar yet (data lag); the caller decides when to give up.
    """
    od = _aware(order.get("order_date"))
    created = _aware(order.get("created_at"))
    if created is not None and od is not None and created >= od + SESSION_OPEN_UTC:
        return dict(action="EXPIRE", price=None, reason="stored after the session opened: not a pre-placed order")
    if bar is None:
        return dict(action="WAIT", price=None, reason="no candle for the order date yet")
    trig, lim = float(order["trigger"]), float(order["limit"])
    o, h = float(bar["OPEN"]), float(bar["HIGH"])
    if h < trig:
        return dict(action="EXPIRE", price=None, reason=f"trigger not reached (high {h:.2f} < trigger {trig:.2f})")
    if o > lim:
        return dict(action="EXPIRE", price=None, reason=f"opened above the limit ({o:.2f} > {lim:.2f})")
    entry = max(o, trig)
    if entry > lim:
        return dict(action="EXPIRE", price=None, reason=f"entry {entry:.2f} above the limit {lim:.2f}")
    price = round(min(entry * (1.0 + slip_pct / 100.0), lim), 2)
    return dict(action="FILL", price=price, reason=f"high {h:.2f} >= trigger {trig:.2f}, open {o:.2f}")


def _bar_on(df: Optional[pd.DataFrame], day) -> Optional[dict]:
    if df is None or df.empty:
        return None
    d = pd.to_datetime(df["DATE"])
    d = d.dt.tz_localize(None) if d.dt.tz is not None else d
    day = pd.Timestamp(day)
    day = day.tz_localize(None) if day.tzinfo else day
    hit = df[d.dt.normalize() == day.normalize()]
    if hit.empty:
        return None
    r = hit.iloc[-1]
    return dict(OPEN=float(r["OPEN"]), HIGH=float(r["HIGH"]), LOW=float(r["LOW"]), CLOSE=float(r["CLOSE"]))


def _has_newer_bar(df: Optional[pd.DataFrame], day) -> bool:
    """True when the frame holds a bar dated after `day`."""
    if df is None or df.empty:
        return False
    d = pd.to_datetime(df["DATE"])
    d = d.dt.tz_localize(None) if d.dt.tz is not None else d
    day = pd.Timestamp(day)
    day = day.tz_localize(None) if day.tzinfo else day
    return bool((d.dt.normalize() > day.normalize()).any())


def autofill(orders_col, positions_col, candles: Dict[str, pd.DataFrame], today, slip_pct: float = AUTO_FILL_SLIP_PCT, apply: bool = True) -> dict:
    """
    Settle every PLACED order against the candles.  `today` = the run date (orders older than MAX_ORDER_AGE_DAYS without a candle are closed out).
    Returns dict(filled=[...], expired=[...], waiting=[...], skipped=[...]) of human-readable strings; with apply=False nothing is written.
    """
    out = dict(filled=[], expired=[], waiting=[], skipped=[])
    placed = sorted(orders_col.find({"status": "PLACED"}), key=lambda d: (_aware(d["order_date"]), d["symbol"]))
    for o in placed:
        sym, od = o["symbol"], s3_store.to_utc_midnight(o["order_date"])
        key = {"order_date": o["order_date"], "symbol": sym}
        dec = decide(o, _bar_on(candles.get(sym), od), slip_pct)
        age = (pd.Timestamp(today).normalize() - pd.Timestamp(od).tz_localize(None).normalize()).days
        if dec["action"] == "WAIT" and _has_newer_bar(candles.get(sym), od):
            # a later session is in the data but this date is not: the exchange was closed (holiday) or the day is missing for good.  A DAY order
            # cannot fill without a session, so close it now instead of leaving it "waiting" for ten days.
            dec = dict(action="EXPIRE", price=None, reason=f"no session candle on {od.date()} (exchange holiday?); newer candles exist")
        if dec["action"] == "WAIT":
            if age > MAX_ORDER_AGE_DAYS:
                dec = dict(action="EXPIRE", price=None, reason=f"no candle for {od.date()} after {age} days")
            else:
                out["waiting"].append(f"{sym} {od.date()}")
                continue
        label = f"{sym} {od.date()}"
        if dec["action"] == "EXPIRE":
            out["expired"].append(f"{label}: {dec['reason']}")
            if apply:
                orders_col.update_one(key, {"$set": {"status": "EXPIRED", "expired_reason": dec["reason"], "updated_at": datetime.now(timezone.utc)}})
            continue
        qty = int(o.get("quantity") or 0)
        if apply:
            try:
                s3_store.open_position(positions_col, orders_col, sym, od, dec["price"], qty or None,
                                       extra=dict(auto_filled=True, fill_model=FILL_MODEL, fill_slip_pct=slip_pct))
            except ValueError as e:
                out["skipped"].append(f"{label}: {e}")
                orders_col.update_one(key, {"$set": {"status": "SKIPPED", "expired_reason": str(e), "updated_at": datetime.now(timezone.utc)}})
                continue
        out["filled"].append(f"{label}: BUY {qty} @ {dec['price']:.2f} ({dec['reason']})")
    return out
