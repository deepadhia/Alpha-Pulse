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
from contextlib import nullcontext

import pandas as pd

from core import resting_exec as X
from core.honest_backtest import Params, _stop_from_history
from core.trade_guard import to_date
from core.session_dates import ist_naive, daily_frame, daily_dates
from core.portfolio_caps import DEFAULT_HARD_ACTIVE_POSITIONS, occupies_capital_slot
from core.exit_rules import ExitParams, evaluate_position


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
    commit_lock: Optional[Callable] = None     # production injects the same lock used by the scanners
    hard_cap: int = DEFAULT_HARD_ACTIVE_POSITIONS
    exit_params: Callable = ExitParams


def _naive(ts) -> pd.Timestamp:
    return ist_naive(ts)


def _bar_on(df: Optional[pd.DataFrame], day) -> Optional[dict]:
    if df is None or df.empty:
        return None
    d = daily_dates(df["DATE"])
    hit = df[d.dt.normalize() == _naive(day).normalize()]
    if hit.empty:
        return None
    r = hit.iloc[-1]
    return dict(OPEN=float(r["OPEN"]), HIGH=float(r["HIGH"]), LOW=float(r["LOW"]), CLOSE=float(r["CLOSE"]))


def _before(df: Optional[pd.DataFrame], day) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["DATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"])
    df = daily_frame(df)
    return df[df["DATE"] < _naive(day).normalize()].reset_index(drop=True)


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


def _open_position(deps: Deps, order: dict, decision: dict, fill_dt: datetime, df: Optional[pd.DataFrame], recovering=False) -> Optional[str]:
    """Write the position for an automatic fill.  Returns None on success, else the reason the order could not become a position."""
    sym = order["symbol"]
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
    if recovering:
        doc["recovery_pending"] = True
        doc["recovery_asof"] = None
    with deps.commit_lock() if deps.commit_lock is not None else nullcontext(True) as held:
        if not held:
            raise RuntimeError(f"{sym}: portfolio commit lock unavailable; fill left pending")
        existing = deps.positions_col.find_one({"symbol": sym})
        if existing and (existing.get("status") == "PAPER_ONLY" or occupies_capital_slot(existing)):
            return "a position in this symbol is already open or awaiting sale"
        if existing and to_date(existing.get("entry_date")) and to_date(existing["entry_date"]) > fill_dt.date():
            raise RuntimeError(f"{sym}: historical fill conflicts with a newer trade; reconciliation required")
        if sum(occupies_capital_slot(p) for p in deps.positions_col.find({})) >= deps.hard_cap:
            return "no free portfolio slot at fill time"
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


def _recover_position(deps: Deps, pos: dict, run_day: date, rep: dict) -> None:
    """Replay missed closes with the shared live exit rules, never with a later price.

    A persisted cursor makes a failed recovery resumable. Missing sessions leave the
    recovery pending and block new orders instead of silently skipping an exit.
    """
    df = deps.daily(pos["symbol"])
    entry_day = to_date(pos["entry_date"])
    cursor = to_date(pos.get("recovery_asof"))
    day = cursor + timedelta(days=1) if cursor else entry_day
    params = deps.exit_params()
    while day <= run_day:
        if not deps.is_trading_day(day):
            day += timedelta(days=1)
            continue
        bar = _bar_on(df, day)
        if bar is None:
            rep["waiting"].append(f"{pos['symbol']}: recovery missing session {day}; no later close substituted")
            return
        price, entry, stop = bar["CLOSE"], float(pos["entry_price"]), float(pos["trailing_stop"])
        pnl = (price / entry - 1.0) * 100.0
        if day == entry_day:
            reason = "Stop Loss (day-0 EOD alert)" if price <= stop else None
            peak, drawdown, trailing = max(0.0, pnl), min(0.0, pnl), stop
        else:
            from core.guards import assess_price_drop, LIKELY_CORPORATE_ACTION
            if assess_price_drop(float(pos.get("current_price") or entry), price).kind == LIKELY_CORPORATE_ACTION:
                rep["waiting"].append(f"{pos['symbol']}: recovery needs a corporate-action check on {day}")
                return
            sessions = sum(deps.is_trading_day(d.date()) for d in pd.date_range(entry_day, day))
            past = daily_frame(df)
            past = past[(past["DATE"] > pd.Timestamp(entry_day)) & (past["DATE"] <= pd.Timestamp(day))]
            result = evaluate_position(entry, stop, price, (day - entry_day).days, sessions,
                                       float(pos.get("max_runup_pct") or 0), float(pos.get("max_drawdown_pct") or 0),
                                       pos.get("grade", "LISTING_BREAKOUT"), pos.get("grade", "LISTING_BREAKOUT"),
                                       volume_provider=lambda: past["VOLUME"].tolist(), params=params)
            reason, peak, drawdown, trailing = result.exit_reason, result.new_max_runup, result.new_max_drawdown, result.new_trailing
        update = dict(symbol=pos["symbol"], signal_id=pos["signal_id"], status="CLOSED" if reason else "ACTIVE",
                      current_price=price, pnl_pct=pnl, trailing_stop=trailing, days_held=(day - entry_day).days,
                      max_runup_pct=peak, max_drawdown_pct=drawdown, recovery_asof=str(day), recovery_pending=not reason and day < run_day)
        if reason:
            update.update(exit_date=str(day), exit_price=price, exit_reason=reason)
        if deps.upsert_position(update) is not True:
            raise RuntimeError(f"{pos['symbol']}: recovery update was not recorded")
        pos = dict(pos, **update)
        if reason:
            rep["notes"].append(f"{pos['symbol']}: recovered {day} exit decision ({reason}); sale priced separately")
            return
        day += timedelta(days=1)
    if pos.get("recovery_pending"):
        if deps.upsert_position(dict(symbol=pos["symbol"], signal_id=pos["signal_id"], recovery_pending=False)) is not True:
            raise RuntimeError(f"{pos['symbol']}: recovery completion was not recorded")


def _settle_due_exits(deps: Deps, run_day: date, rep: dict) -> None:
    """Catch up one sale session at a time, including consecutive circuit deferrals."""
    for initial in pending_exits(deps):
        p = initial
        while p["_target"] <= run_day:
            sym, sale_day = p["symbol"], p["_target"]
            df = deps.daily(sym)
            bar = _bar_on(df, sale_day)
            if bar is None:
                rep["exit_waiting"].append(f"{sym}: no daily bar for {sale_day} yet")
                break
            tier = X.tier_half_spread_bps(df, len(_before(df, sale_day)), bar["OPEN"], float(p.get("quantity") or 1) * bar["OPEN"])
            decision = X.settle_exit_from_daily(bar, _prev_close(df, sale_day), tier)
            if decision["action"] == "SELL":
                out = X.settle_exit(deps.positions_col, deps.ledger_append, p, decision, sale_day)
                rep["sold"].append(f"{sym}: SELL @ {decision['price']:.2f} ({decision['model']}; decision close {out['exit_price_decision']:.2f}; P&L {out['pnl_pct']:+.1f}%)")
                break
            if decision["action"] != "DEFER":
                rep["exit_waiting"].append(f"{sym}: {decision['reason']}")
                break
            _defer_exit(deps, p, decision["reason"])
            rep["deferred"].append(f"{sym}: {sale_day}: {decision['reason']}")
            n = int(p.get("exit_deferred_days") or 0) + 1
            p = dict(p, exit_deferred_days=n, _target=target_sale_day(p["_decision_day"], n, deps.is_trading_day))


# ------------------------------------------------------------------------------------------------ evening
def run_evening(deps: Deps, run_day: date) -> dict:
    rep = dict(day=str(run_day), skipped=None, filled=[], expired=[], waiting=[], day0_stops=[], sold=[], deferred=[], exit_waiting=[], notes=[])
    if not deps.is_trading_day(run_day):
        rep["skipped"] = "not an NSE trading day"
        return rep
    # Resume any previously interrupted catch-up before opening more positions.
    for p in deps.positions_col.find({"entry_source": X.ENTRY_SOURCE}):
        if p.get("status") == "ACTIVE" and p.get("recovery_pending"):
            _recover_position(deps, p, run_day, rep)
    _settle_due_exits(deps, run_day, rep)
    # a) all orders still PLACED: always price each against its ORIGINAL session.
    for o in X.unsettled_orders_through(deps.ledger_col, deps.positions_col, run_day):
        sym = o["symbol"]
        order_day = to_date(o["order_date"])
        df = deps.daily(sym)
        bar = _bar_on(df, order_day)
        if bar is None:
            rep["waiting"].append(f"{sym}: no daily bar for {order_day} yet")
            continue
        try:
            mdf = deps.minute_bars(sym, order_day)
        except Exception as e:                                            # a bars outage must not turn into a guessed fill: fall back to the daily rule, say so
            mdf = None
            rep["notes"].append(f"{sym}: minute bars unavailable ({type(e).__name__}); daily rule used")
        idx = len(_before(df, order_day))
        tier = X.tier_half_spread_bps(df, idx, float(o["trigger"]), float(o.get("quantity") or 1) * float(o["trigger"]))
        d = X.settle_intraday_entry(o, mdf, bar, _prev_close(df, order_day), deps.measured_hs(sym, order_day), tier)
        if d["action"] == "FILL":
            ft = d.get("fill_time")
            fill_dt = ist_naive(ft).to_pydatetime() if ft is not None else datetime.combine(order_day, dtime(9, 15))
            why = _open_position(deps, o, d, fill_dt, df, recovering=order_day < run_day)
            if why:
                if order_day < run_day:
                    raise RuntimeError(f"{sym}: historical fill needs reconciliation: {why}")
                X.record_expiry(deps.ledger_append, o, why)
                rep["expired"].append(f"{sym}: {why}")
            else:
                rep["filled"].append(f"{sym}: BUY {o.get('quantity')} @ {d['price']:.2f} ({d['model']}, spread {d['half_spread_bps']:.0f} bps; trigger {o['trigger']:.2f})"
                                     if d.get("half_spread_bps") is not None else f"{sym}: BUY @ {d['price']:.2f} ({d['model']})")
                if order_day < run_day:
                    position = deps.positions_col.find_one({"symbol": sym})
                    if position is not None:
                        _recover_position(deps, position, run_day, rep)
                        _settle_due_exits(deps, run_day, rep)
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
    # c) settle any sale created by the recovered/day-0 decisions.
    _settle_due_exits(deps, run_day, rep)
    return rep
