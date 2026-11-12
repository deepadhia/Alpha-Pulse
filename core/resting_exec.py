# -*- coding: utf-8 -*-
"""
core/resting_exec.py -- realistic AUTOMATIC fills and exits for policy P's resting orders (forward test, no manual step).

Execution model (ported from Learning-Lab `paper/manager.py`; every record names the model that priced it so the forward ledger can be audited):

  ENTRY  the order is a pre-placed stop-limit BUY (trigger = listing high +1.5%, limit +2.5%).
    * Opens at or above the trigger (gap-up): the first quote after the open decides.  FILL at the best ASK if ask <= limit; skipped when the open or the
      ask is above the limit; no ask in the book with the upper circuit reached = no sellers, not filled.                      model quote_ask_open
    * Trigger reached later in the session: priced from the day's 1-minute bars: the first minute whose high reached the trigger, price
      max(minute open, trigger), plus the stock's half spread (MEASURED from its own open-time quote when one exists, else the liquidity-tier model of
      core/realistic_friction), rounded up to the tick; not filled when that is above the limit or the day was one locked upper-circuit price.
                                                                                                                                 model minute_bar_measured_spread | minute_bar_tier_spread
    * No minute bars: the replay's daily rule (high >= trigger, open <= limit, max(open, trigger) +0.3%).                         model daily_bar_replay_rule
  EXIT   the stop is decided on the CLOSE (live job, 18:30 IST) and the sale happens at the NEXT open: SELL at the best BID of the first quote after
    the open; no bid or the lower circuit reached = deferred to the next session.                                                 model quote_bid_open
    Without an open-time quote the next daily open less the modelled spread; a locked-down day defers the sale.                    model daily_open_tier_spread
  Why it matters: the live stop job records the DECISION-day close as the exit price; a real sale happens at the next open.  `settle_exit` replaces it
  with the realised price and appends an EXIT_SETTLED ledger event, keeping the decision price.

Everything here is pure (collections and callables are injected) so it is tested without a database or network.
"""

import math
from datetime import datetime, time as dtime, timedelta, timezone
from typing import Callable, Dict, List, Optional

import pandas as pd

from core.execution_model import is_locked_down, is_locked_up
from core.realistic_friction import LOCK_BAND_PCT, tick_down, tick_up

IST = timezone(timedelta(hours=5, minutes=30))
RESTING_VERSION = "1.1.0"
ENTRY_SOURCE = "RESTING_AUTOFILL"
ORDER_SOURCE = "resting_order_alerts.py"
OPEN_WINDOW_END = dtime(9, 45)          # a quote taken after this is no longer the open-time book: defer to the evening models
DAILY_SLIP_PCT = 0.3                    # the replay's entry slippage, used only by the daily-bar fallback


def half_spread_bps(quote: Optional[dict]) -> Optional[float]:
    """(ask - bid) / (ask + bid) in basis points (half the quoted spread over the mid); None when either side of the book is empty."""
    if not quote or not quote.get("bid") or not quote.get("ask") or quote["ask"] < quote["bid"]:
        return None
    return (quote["ask"] - quote["bid"]) / (quote["ask"] + quote["bid"]) * 1e4


def _at_upper_circuit(q: dict) -> bool:
    uc = q.get("upper_circuit")
    try:
        return bool(uc) and float(q.get("ltp") or 0.0) >= float(uc) - 0.01
    except (TypeError, ValueError):
        return False


def _at_lower_circuit(q: dict) -> bool:
    lc = q.get("lower_circuit")
    try:
        return bool(lc) and 0 < float(q.get("ltp") or 0.0) <= float(lc) + 0.01
    except (TypeError, ValueError):
        return False


def _in_open_window(now_ist: datetime) -> bool:
    return now_ist.astimezone(IST).time() <= OPEN_WINDOW_END


# ------------------------------------------------------------------------------------------------ entries
def decide_entry_at_open(order: dict, quote: Optional[dict], now_ist: datetime) -> dict:
    """
    One resting order against the first quote after the open.  Returns dict(action, price, reason, model, half_spread_bps) with action
    FILL | EXPIRE | PENDING (decide later in the session, from the minute bars) | WAIT (no usable quote: retry).
    """
    trig, lim = float(order["trigger"]), float(order["limit"])
    if not quote or not (quote.get("open") or 0) > 0:
        return dict(action="WAIT", price=None, reason="no open-time quote yet", model=None, half_spread_bps=None)
    hs = half_spread_bps(quote)
    base = dict(model="quote_ask_open", half_spread_bps=hs)
    if not _in_open_window(now_ist):
        return dict(action="PENDING", price=None, reason="quote taken after the open window: the open-time book is gone", **dict(base, model=None))
    o = float(quote["open"])
    if o < trig:
        return dict(action="PENDING", price=None, reason=f"opened below the trigger ({o:.2f} < {trig:.2f}): priced from the session's minute bars", **base)
    if o > lim:
        return dict(action="EXPIRE", price=None, reason=f"opened above the limit ({o:.2f} > {lim:.2f}): a stop-limit buy cannot fill", **base)
    ask = quote.get("ask")
    if not ask or ask <= 0:
        if _at_upper_circuit(quote):
            return dict(action="EXPIRE", price=None, reason="upper circuit, no sellers in the book", **base)
        return dict(action="WAIT", price=None, reason="no ask in the book", **base)
    if ask > lim:
        return dict(action="EXPIRE", price=None, reason=f"best ask {ask:.2f} above the limit {lim:.2f}", **base)
    return dict(action="FILL", price=round(float(ask), 2), reason=f"gap-up open {o:.2f} >= trigger {trig:.2f}, filled at the best ask", **base)


def settle_intraday_entry(order: dict, minute_df: Optional[pd.DataFrame], daily_bar: Optional[dict], prev_close: Optional[float],
                          measured_hs_bps: Optional[float], tier_hs_bps: float) -> dict:
    """
    An order that did not fill at the open, settled after the close from the session's data.  Returns dict(action FILL | EXPIRE | WAIT, price, reason, model,
    half_spread_bps, fill_time).
    """
    trig, lim = float(order["trigger"]), float(order["limit"])
    if daily_bar is not None and prev_close:
        if is_locked_up(daily_bar["OPEN"], daily_bar["HIGH"], daily_bar["LOW"], daily_bar["CLOSE"], prev_close, LOCK_BAND_PCT):
            return dict(action="EXPIRE", price=None, reason="upper-circuit lock all session: nothing to buy", model=None, half_spread_bps=None, fill_time=None)
    if minute_df is not None and len(minute_df):
        hit = minute_df[minute_df["HIGH"] >= trig]
        if hit.empty:
            return dict(action="EXPIRE", price=None, reason=f"trigger not reached (session high {float(minute_df['HIGH'].max()):.2f} < {trig:.2f})",
                        model=None, half_spread_bps=None, fill_time=None)
        row = hit.iloc[0]
        ref = max(float(row["OPEN"]), trig)
        if ref > lim:
            return dict(action="EXPIRE", price=None, reason=f"price ran through the limit in the trigger minute ({ref:.2f} > {lim:.2f})",
                        model=None, half_spread_bps=None, fill_time=row["TS"])
        hs, model = (measured_hs_bps, "minute_bar_measured_spread") if measured_hs_bps is not None else (tier_hs_bps, "minute_bar_tier_spread")
        price = round(tick_up(ref * (1.0 + hs / 1e4)), 2)
        if price > lim:
            return dict(action="EXPIRE", price=None, reason=f"ask side {price:.2f} above the limit {lim:.2f} at the trigger", model=model,
                        half_spread_bps=hs, fill_time=row["TS"])
        return dict(action="FILL", price=price, reason=f"minute {pd.Timestamp(row['TS']).strftime('%H:%M')} reached the trigger {trig:.2f} (ref {ref:.2f})",
                    model=model, half_spread_bps=hs, fill_time=row["TS"])
    if daily_bar is not None:
        o, h = float(daily_bar["OPEN"]), float(daily_bar["HIGH"])
        if h < trig:
            return dict(action="EXPIRE", price=None, reason=f"trigger not reached (high {h:.2f} < {trig:.2f})", model=None, half_spread_bps=None, fill_time=None)
        if o > lim:
            return dict(action="EXPIRE", price=None, reason=f"opened above the limit ({o:.2f} > {lim:.2f})", model=None, half_spread_bps=None, fill_time=None)
        price = round(min(max(o, trig) * (1.0 + DAILY_SLIP_PCT / 100.0), lim), 2)
        return dict(action="FILL", price=price, reason="no minute bars: replay daily rule", model="daily_bar_replay_rule", half_spread_bps=None, fill_time=None)
    return dict(action="WAIT", price=None, reason="no session data yet", model=None, half_spread_bps=None, fill_time=None)


# -------------------------------------------------------------------------------------------------- exits
def decide_exit_at_open(quote: Optional[dict], now_ist: datetime) -> dict:
    """The sale of a position whose stop fired on the previous close, against the first quote after the open (action SELL | DEFER | PENDING | WAIT)."""
    if not quote or not (quote.get("open") or 0) > 0:
        return dict(action="WAIT", price=None, reason="no open-time quote yet", model=None, half_spread_bps=None)
    hs = half_spread_bps(quote)
    if not _in_open_window(now_ist):
        return dict(action="PENDING", price=None, reason="quote taken after the open window", model=None, half_spread_bps=hs)
    bid = quote.get("bid")
    if not bid or bid <= 0:
        if _at_lower_circuit(quote):
            return dict(action="DEFER", price=None, reason="lower circuit, no bids in the book: sale deferred", model="quote_bid_open", half_spread_bps=hs)
        return dict(action="WAIT", price=None, reason="no bid in the book", model="quote_bid_open", half_spread_bps=hs)
    return dict(action="SELL", price=round(float(bid), 2), reason="sold at the best bid after the open", model="quote_bid_open", half_spread_bps=hs)


def settle_exit_from_daily(next_bar: Optional[dict], prev_close: Optional[float], tier_hs_bps: float) -> dict:
    """No open-time quote was taken: the next session's open less the modelled spread; a locked-down session defers the sale."""
    if next_bar is None:
        return dict(action="WAIT", price=None, reason="no session data yet", model=None, half_spread_bps=None)
    if prev_close and is_locked_down(next_bar["OPEN"], next_bar["HIGH"], next_bar["LOW"], next_bar["CLOSE"], prev_close, LOCK_BAND_PCT):
        return dict(action="DEFER", price=None, reason="lower-circuit lock all session: no sale possible", model="daily_open_tier_spread",
                    half_spread_bps=tier_hs_bps)
    price = round(tick_down(float(next_bar["OPEN"]) * (1.0 - tier_hs_bps / 1e4)), 2)
    return dict(action="SELL", price=price, reason="next open less the modelled spread", model="daily_open_tier_spread", half_spread_bps=tier_hs_bps)


# ------------------------------------------------------------------------------------- position records
def signal_id(symbol: str, day) -> str:
    return f"RESTING_{symbol}_{pd.Timestamp(day).strftime('%Y%m%d')}"


def build_position_doc(order: dict, fill: dict, stop: float, fill_dt: datetime, version: str, capital: float, current_price: Optional[float] = None) -> dict:
    """
    The `positions` document for an automatic fill, shaped like the listing scanner's so the 6:30 PM stop job manages it unchanged
    (grade LISTING_BREAKOUT, trailing_stop = initial stop).  entry_source marks the cohort: never pool it with scanner-alert entries.
    """
    sym = order["symbol"]
    price = float(fill["price"])
    day = pd.Timestamp(fill_dt).normalize()
    return {
        "signal_id": signal_id(sym, day), "symbol": sym,
        "entry_date": pd.Timestamp(fill_dt).to_pydatetime().replace(tzinfo=None),      # naive IST, like the scanner (db.make_utc treats it as IST)
        "entry_price": price, "limit_buy_price": float(order["limit"]), "current_price": float(current_price or price),
        "stop_loss": float(stop), "trailing_stop": float(stop), "target_price": round(price * 1.2, 2),
        "status": "ACTIVE", "lifecycle_state": "POSITION_ACTIVE", "days_held": 0, "max_runup_pct": 0.0, "max_drawdown_pct": 0.0,
        "grade": "LISTING_BREAKOUT", "tier": "A", "winner_label": "STANDARD", "winner_score": 0, "position_size_pct": 10,
        "quantity": int(order.get("quantity") or 0), "capital": float(capital),
        "version": version, "_backfilled": False, "strategy_version": f"{version}-resting-orders", "execution_version": f"{version}-resting-autofill",
        "risk_model_version": f"{version}-archetype-velocity", "is_reentry": False,
        "entry_source": ENTRY_SOURCE, "resting_version": RESTING_VERSION, "order_date": str(day.date()),
        "order_trigger": float(order["trigger"]), "order_limit": float(order["limit"]),
        "fill_model": fill.get("model"), "fill_reason": fill.get("reason"), "fill_half_spread_bps": fill.get("half_spread_bps"),
        "fill_vs_trigger_pct": round((price / float(order["trigger"]) - 1.0) * 100.0, 3),
        # upsert_position merges into the symbol's document: a re-entry must not inherit the previous autofill trade's exit-settlement state
        "exit_settled": False, "exit_deferred_days": 0, "exit_price_decision": None, "exit_fill_model": None, "exit_fill_reason": None,
        "exit_half_spread_bps": None,
    }


def settle_exit(positions_col, ledger_append: Optional[Callable], pos: dict, sale: dict, sale_day, now: Optional[datetime] = None) -> dict:
    """
    Replace the decision-day close the stop job recorded with the REALISED sale (sale = dict(price, model, reason, half_spread_bps)).
    Keeps the decision price (`exit_price_decision`), recomputes pnl_pct from the entry price, marks exit_settled, and appends EXIT_SETTLED to the ledger.
    """
    entry = float(pos["entry_price"])
    price = float(sale["price"])
    pnl = (price / entry - 1.0) * 100.0
    decision = pos.get("exit_price_decision") if pos.get("exit_price_decision") is not None else pos.get("exit_price")
    upd = dict(exit_price=price, exit_date=pd.Timestamp(sale_day).to_pydatetime().replace(tzinfo=timezone.utc), pnl_pct=pnl, exit_settled=True,
               exit_price_decision=decision, exit_fill_model=sale.get("model"), exit_fill_reason=sale.get("reason"),
               exit_half_spread_bps=sale.get("half_spread_bps"), updated_at=datetime.now(timezone.utc))
    positions_col.update_one({"symbol": pos["symbol"], "signal_id": pos.get("signal_id")}, {"$set": upd})
    if ledger_append is not None:
        ledger_append("EXIT_SETTLED", pos["symbol"], dict(signal_id=pos.get("signal_id"), entry_price=entry, decision_price=decision, sale_price=price,
                                                         pnl_pct=round(pnl, 4), model=sale.get("model"), reason=sale.get("reason"),
                                                         half_spread_bps=sale.get("half_spread_bps"), sale_date=str(pd.Timestamp(sale_day).date())))
    return dict(pos, **upd)


# ------------------------------------------------------------------------------------------ order book
def orders_for_day(ledger_col, day) -> List[dict]:
    """The orders this system told the owner to place for `day`: ORDER_GENERATED events (one per symbol, newest event wins)."""
    d = str(pd.Timestamp(day).date())
    best: Dict[str, dict] = {}
    for ev in ledger_col.find({"event_type": "ORDER_GENERATED", "payload.order_date": d}):
        p = dict(ev.get("payload") or {})
        p.setdefault("symbol", ev.get("symbol"))
        p["_seq"] = ev.get("seq", 0)
        if p["symbol"] and (p["symbol"] not in best or p["_seq"] >= best[p["symbol"]]["_seq"]):
            best[p["symbol"]] = p
    return sorted(best.values(), key=lambda x: x["symbol"])


def order_state(ledger_col, positions_col, order: dict) -> str:
    """FILLED (a position with this order's signal id exists) | EXPIRED (a NOTE says it did not fill) | PLACED."""
    sid = signal_id(order["symbol"], order["order_date"])
    if positions_col.find_one({"signal_id": sid}) is not None:
        return "FILLED"
    # The symbol-keyed position can be replaced on re-entry. Its original fill is permanent in the ledger.
    if ledger_col.find_one({"event_type": "ENTRY_ACCEPTED", "symbol": order["symbol"], "payload.signal_id": sid}) is not None:
        return "FILLED"
    if ledger_col.find_one({"event_type": "NOTE", "symbol": order["symbol"], "payload.kind": "RESTING_ORDER_EXPIRED",
                            "payload.order_date": str(order["order_date"])}) is not None:
        return "EXPIRED"
    return "PLACED"


def unsettled_orders_through(ledger_col, positions_col, day) -> List[dict]:
    """Unresolved orders, deduplicated by session and symbol, oldest session first."""
    end = str(pd.Timestamp(day).date())
    days = set()
    for event in ledger_col.find({"event_type": "ORDER_GENERATED"}):
        order_day = (event.get("payload") or {}).get("order_date")
        if order_day and str(order_day) <= end:
            days.add(str(order_day))
    return [o for d in sorted(days) for o in orders_for_day(ledger_col, d)
            if order_state(ledger_col, positions_col, o) == "PLACED"]


def record_expiry(ledger_append: Callable, order: dict, reason: str, extra: Optional[dict] = None) -> None:
    ledger_append("NOTE", order["symbol"], dict(kind="RESTING_ORDER_EXPIRED", order_date=str(order["order_date"]), reason=reason, trigger=order.get("trigger"),
                                               limit=order.get("limit"), **(extra or {})))


def tier_half_spread_bps(df: pd.DataFrame, idx: int, ref_price: float, order_value: float) -> float:
    """Modelled per-leg cost in bps (core.realistic_friction: tier spread + tick floor + impact) from data before bar `idx`."""
    from core import realistic_friction as RF
    return RF.slippage_fraction(RF.adt_before(df, idx), ref_price, order_value, RF.sigma_before(df, idx)) * 1e4
