# -*- coding: utf-8 -*-
"""
core/resting_jobs.py -- orchestration of the automatic forward test of policy P's resting orders (decisions live in core/resting_exec.py).

  run_morning(deps, now_ist)   ~09:16 IST: first quote after the open.  Gap-up entries fill at the best ask, exits whose stop fired on the previous
                               close sell at the best bid (lower circuit / no bid defers), every quote is audited (spread measurement).
  run_evening(deps, run_day)   after the close: orders that did not fill at the open are priced from the day's 1-minute bars, the fill day's own close is
                               checked against the initial stop (the 6:30 PM job skips day-0 positions), and any exit still unsettled is priced from the
                               next daily open less the modelled spread.
Entries are written through `db.upsert_position` (entry guard + ENTRY_ACCEPTED ledger event) into the existing `positions` collection, tagged
entry_source=RESTING_AUTOFILL so they form their own cohort; the stop job then manages them with the live exit rules.  Orders and their outcomes live in the
append-only trade ledger (ORDER_GENERATED, then a position or a RESTING_ORDER_EXPIRED note); no new collection.

All collaborators are injected (`Deps`) so the whole flow is tested with fakes.
"""

from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta
from typing import Callable, Dict, List, Optional

import pandas as pd

from core import resting_exec as X
from core.honest_backtest import Params, _stop_from_history
from core.trade_guard import to_date


@dataclass
class Deps:
    ledger_col: object
    positions_col: object
    ledger_append: Callable            # (event_type, symbol, payload) -> event | None
    upsert_position: Callable          # (doc) -> True | False (guard refused) | None
    quotes: Callable                   # (symbols) -> {symbol: quote}; raises on failure
    minute_bars: Callable              # (symbol, day) -> DataFrame TS/OPEN/HIGH/LOW/CLOSE/VOLUME
    daily: Callable                    # (symbol) -> DataFrame DATE/OPEN/HIGH/LOW/CLOSE/VOLUME (completed sessions)
    audit_quote: Callable              # (symbol, quote, day) -> None
    measured_hs: Callable              # (symbol, day) -> Optional[float] half spread bps measured from a real quote
    is_trading_day: Callable           # (date) -> bool
    version: str = "3.8.0"
    capital: float = 100000.0


def _naive(ts) -> pd.Timestamp:
    ts = pd.Timestamp(ts)
    return ts.tz_localize(None) if ts.tzinfo else ts


def _bar_on(df: Optional[pd.DataFrame], day) -> Optional[dict]:
    if df is None or df.empty:
        return None
    d = pd.to_datetime(df["DATE"])
    hit = df[d.dt.normalize() == _naive(day).normalize()]
    if hit.empty:
        return None
    r = hit.iloc[-1]
    return dict(OPEN=float(r["OPEN"]), HIGH=float(r["HIGH"]), LOW=float(r["LOW"]), CLOSE=float(r["CLOSE"]))


def _before(df: Optional[pd.DataFrame], day) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["DATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"])
    d = pd.to_datetime(df["DATE"])
    return df[d.dt.normalize() < _naive(day).normalize()].reset_index(drop=True)


def _prev_close(df, day) -> Optional[float]:
    b = _before(df, day)
    return float(b["CLOSE"].iloc[-1]) if len(b) else None


def target_sale_day(decision_day: date, deferred_days: int, is_trading_day: Callable) -> date:
    """The (deferred_days + 1)-th trading day after the day the stop fired: the first open after the decision, or later when a lower circuit deferred it."""
    d, n = decision_day, deferred_days + 1
    for _ in range(60):
        d += timedelta(days=1)
        if is_trading_day(d):
            n -= 1
            if n == 0:
                return d
    return d


def pending_exits(deps: Deps) -> List[dict]:
    """Autofill positions the stop job already closed at the decision-day close whose realised sale has not been recorded."""
    out = []
    for p in deps.positions_col.find({"entry_source": X.ENTRY_SOURCE}):
        if p.get("status") in ("CLOSED", "PAPER_CLOSED") and not p.get("exit_settled"):
            dd = to_date(p.get("exit_date"))
            if dd is not None:
                p = dict(p, _decision_day=dd, _target=target_sale_day(dd, int(p.get("exit_deferred_days") or 0), deps.is_trading_day))
                out.append(p)
    return out


def _open_position(deps: Deps, order: dict, decision: dict, fill_dt: datetime, df: Optional[pd.DataFrame]) -> Optional[str]:
    """Write the position for an automatic fill.  Returns None on success, else the reason the order could not become a position."""
    sym = order["symbol"]
    existing = deps.positions_col.find_one({"symbol": sym})
    if existing and existing.get("status") in ("ACTIVE", "PAPER_ONLY"):
        return "a position in this symbol is already open"
    price = float(decision["price"])
    hist = _before(df, fill_dt)
    if len(hist) >= 3:
        row = hist.iloc[-1].copy()
        row["DATE"], row["OPEN"], row["HIGH"], row["LOW"], row["CLOSE"] = _naive(fill_dt).normalize(), price, price, price, price
        ext = pd.concat([hist, pd.DataFrame([row])], ignore_index=True)
        stop = float(_stop_from_history(ext, len(ext) - 1, price, Params()))
    else:
        stop = float(order.get("stop_alert") or price * 0.92)
        if stop >= price:
            stop = price * 0.92
    doc = X.build_position_doc(order, decision, stop, fill_dt, deps.version, deps.capital)
    ok = deps.upsert_position(doc)
    if ok is False:
        return "the entry guard refused the position"
    if ok is None:
        raise RuntimeError(f"{sym}: positions store unavailable, fill not recorded")
    return None


# ------------------------------------------------------------------------------------------------ morning
def run_morning(deps: Deps, now_ist: datetime) -> dict:
    day = now_ist.date()
    rep = dict(day=str(day), skipped=None, filled=[], expired=[], pending=[], waiting=[], sold=[], deferred=[], exit_waiting=[], quotes=0)
    if not deps.is_trading_day(day):
        rep["skipped"] = "not an NSE trading day"
        return rep
    placed = [o for o in X.orders_for_day(deps.ledger_col, day) if X.order_state(deps.ledger_col, deps.positions_col, o) == "PLACED"]
    exits = [p for p in pending_exits(deps) if p["_target"] == day]
    syms = sorted({o["symbol"] for o in placed} | {p["symbol"] for p in exits})
    quotes = deps.quotes(syms) if syms else {}
    rep["quotes"] = len(quotes)
    for s, qt in quotes.items():
        deps.audit_quote(s, qt, day)
    for o in placed:
        sym = o["symbol"]
        d = X.decide_entry_at_open(o, quotes.get(sym), now_ist)
        if d["action"] == "FILL":
            why = _open_position(deps, o, d, now_ist.replace(tzinfo=None), deps.daily(sym))
            if why:
                X.record_expiry(deps.ledger_append, o, why)
                rep["expired"].append(f"{sym}: {why}")
            else:
                rep["filled"].append(f"{sym}: BUY {o.get('quantity')} @ {d['price']:.2f} (ask, spread {d['half_spread_bps']:.0f} bps; trigger {o['trigger']:.2f})"
                                     if d.get("half_spread_bps") is not None else f"{sym}: BUY @ {d['price']:.2f}")
        elif d["action"] == "EXPIRE":
            X.record_expiry(deps.ledger_append, o, d["reason"], dict(model=d.get("model"), half_spread_bps=d.get("half_spread_bps")))
            rep["expired"].append(f"{sym}: {d['reason']}")
        elif d["action"] == "PENDING":
            rep["pending"].append(f"{sym}: {d['reason']}")
        else:
            rep["waiting"].append(f"{sym}: {d['reason']}")
    for p in exits:
        sym = p["symbol"]
        d = X.decide_exit_at_open(quotes.get(sym), now_ist)
        if d["action"] == "SELL":
            out = X.settle_exit(deps.positions_col, deps.ledger_append, p, d, day)
            rep["sold"].append(f"{sym}: SELL @ {d['price']:.2f} (best bid; decision close {out['exit_price_decision']:.2f}; P&L {out['pnl_pct']:+.1f}%)")
        elif d["action"] == "DEFER":
            _defer_exit(deps, p, d["reason"])
            rep["deferred"].append(f"{sym}: {d['reason']}")
        else:
            rep["exit_waiting"].append(f"{sym}: {d['reason']}")
    return rep


def _defer_exit(deps: Deps, p: dict, reason: str) -> None:
    n = int(p.get("exit_deferred_days") or 0) + 1
    deps.positions_col.update_one({"symbol": p["symbol"], "signal_id": p.get("signal_id")}, {"$set": {"exit_deferred_days": n}})
    deps.ledger_append("NOTE", p["symbol"], dict(kind="RESTING_EXIT_DEFERRED", signal_id=p.get("signal_id"), deferred_days=n, reason=reason))


# ------------------------------------------------------------------------------------------------ evening
def run_evening(deps: Deps, run_day: date) -> dict:
    rep = dict(day=str(run_day), skipped=None, filled=[], expired=[], waiting=[], day0_stops=[], sold=[], deferred=[], exit_waiting=[], notes=[])
    if not deps.is_trading_day(run_day):
        rep["skipped"] = "not an NSE trading day"
        return rep
    # a) orders still PLACED: priced from the session's data
    for o in [o for o in X.orders_for_day(deps.ledger_col, run_day) if X.order_state(deps.ledger_col, deps.positions_col, o) == "PLACED"]:
        sym = o["symbol"]
        df = deps.daily(sym)
        bar = _bar_on(df, run_day)
        if bar is None:
            rep["waiting"].append(f"{sym}: no daily bar for {run_day} yet")
            continue
        try:
            mdf = deps.minute_bars(sym, run_day)
        except Exception as e:                                            # a bars outage must not turn into a guessed fill: fall back to the daily rule, say so
            mdf = None
            rep["notes"].append(f"{sym}: minute bars unavailable ({type(e).__name__}); daily rule used")
        idx = len(_before(df, run_day))
        tier = X.tier_half_spread_bps(df, idx, float(o["trigger"]), float(o.get("quantity") or 1) * float(o["trigger"]))
        d = X.settle_intraday_entry(o, mdf, bar, _prev_close(df, run_day), deps.measured_hs(sym, run_day), tier)
        if d["action"] == "FILL":
            ft = d.get("fill_time")
            fill_dt = pd.Timestamp(ft).to_pydatetime() if ft is not None else datetime.combine(run_day, dtime(9, 15))
            why = _open_position(deps, o, d, fill_dt, df)
            if why:
                X.record_expiry(deps.ledger_append, o, why)
                rep["expired"].append(f"{sym}: {why}")
            else:
                rep["filled"].append(f"{sym}: BUY {o.get('quantity')} @ {d['price']:.2f} ({d['model']}, spread {d['half_spread_bps']:.0f} bps; trigger {o['trigger']:.2f})"
                                     if d.get("half_spread_bps") is not None else f"{sym}: BUY @ {d['price']:.2f} ({d['model']})")
        elif d["action"] == "EXPIRE":
            X.record_expiry(deps.ledger_append, o, d["reason"], dict(model=d.get("model")))
            rep["expired"].append(f"{sym}: {d['reason']}")
        else:
            rep["waiting"].append(f"{sym}: {d['reason']}")
    # b) the fill day's own close against the initial stop (the 6:30 PM job skips positions with 0 days held)
    for p in deps.positions_col.find({"entry_source": X.ENTRY_SOURCE}):
        if p.get("status") != "ACTIVE" or to_date(p.get("entry_date")) != run_day:
            continue
        bar = _bar_on(deps.daily(p["symbol"]), run_day)
        if bar is not None and bar["CLOSE"] <= float(p["trailing_stop"]):
            pnl = (bar["CLOSE"] / float(p["entry_price"]) - 1.0) * 100.0
            deps.upsert_position(dict(symbol=p["symbol"], signal_id=p.get("signal_id"), status="CLOSED", exit_date=str(run_day), exit_price=bar["CLOSE"],
                                      pnl_pct=pnl, days_held=0, current_price=bar["CLOSE"], exit_reason="Stop Loss (day-0 EOD alert)"))
            rep["day0_stops"].append(f"{p['symbol']}: close {bar['CLOSE']:.2f} <= stop {float(p['trailing_stop']):.2f}; sale due at the next open")
    # c) exits still unsettled whose sale day has passed (no open-time quote was taken): next daily open less the modelled spread
    for p in [p for p in pending_exits(deps) if p["_target"] <= run_day]:
        sym = p["symbol"]
        df = deps.daily(sym)
        bar = _bar_on(df, p["_target"])
        if bar is None:
            rep["exit_waiting"].append(f"{sym}: no daily bar for {p['_target']} yet")
            continue
        idx = len(_before(df, p["_target"]))
        tier = X.tier_half_spread_bps(df, idx, bar["OPEN"], float(p.get("quantity") or 1) * bar["OPEN"])
        d = X.settle_exit_from_daily(bar, _prev_close(df, p["_target"]), tier)
        if d["action"] == "SELL":
            out = X.settle_exit(deps.positions_col, deps.ledger_append, p, d, p["_target"])
            rep["sold"].append(f"{sym}: SELL @ {d['price']:.2f} ({d['model']}; decision close {out['exit_price_decision']:.2f}; P&L {out['pnl_pct']:+.1f}%)")
        elif d["action"] == "DEFER":
            _defer_exit(deps, p, d["reason"])
            rep["deferred"].append(f"{sym}: {d['reason']}")
        else:
            rep["exit_waiting"].append(f"{sym}: {d['reason']}")
    return rep
