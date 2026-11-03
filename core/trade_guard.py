# -*- coding: utf-8 -*-
"""
core/trade_guard.py

Pure validation of trade records (no database, no network at import time).  Two uses:

  1. validate_new_entry(doc, ...)    run BEFORE a new position is stored.  `hard` problems mean the record is impossible or unsafe and must
                                     not be stored (non-trading day, future date, stop at or above entry, risk above the cap, chase above 8%);
                                     `warn` problems are kept in the ledger only.
  2. reconcile_position(pos, candles) run AFTER the fact against the daily candles: entry or exit price outside that day's range, entry on a
                                     non-trading day, profit that does not match entry and exit.  Used by reconcile_trades.py (read-only).

Why: the 2026-11-04 audit of the stored book found weekend and holiday entry dates (wall-clock time of a forced offline run), entries above the
day's high (a stale listing high or a backfill), and stored profits that did not match the stored prices.  See LEARNINGS.md section 21.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Callable, List, Optional

IST = timezone(timedelta(hours=5, minutes=30))

HARD_RISK_CAP_PCT = 12.5          # the live stop cap is 12%; 0.5 allows for tick rounding
MAX_EXTENSION_PCT = 8.0           # AGENTS.md section 5 rule 3: anti-chasing guard over the pivot / listing high
WARN_ENTRY_VS_CURRENT_PCT = 3.0
PNL_MISMATCH_PP = 0.6
RANGE_TOLERANCE = 0.002           # prices may sit 0.2% outside the day's range (rounding, tick size)


def to_date(x) -> Optional[date]:
    """date from datetime / pandas Timestamp / 'YYYY-MM-DD' string (aware datetimes are converted to IST first); None when unreadable."""
    if x is None:
        return None
    try:
        if isinstance(x, str):
            return datetime.strptime(x[:10], "%Y-%m-%d").date()
        if isinstance(x, datetime):
            return (x.astimezone(IST) if x.tzinfo else x).date()
        if hasattr(x, "to_pydatetime"):
            return to_date(x.to_pydatetime())
        if isinstance(x, date):
            return x
    except Exception:
        return None
    return None


def default_is_trading_day(d: date) -> bool:
    """Weekday and not an NSE holiday.  If the holiday list is unavailable only the weekend check applies (never blocks on a lookup failure)."""
    if d.weekday() >= 5:
        return False
    try:
        from utils import get_dynamic_nse_holidays
        return d.strftime("%Y-%m-%d") not in get_dynamic_nse_holidays(d.year)
    except Exception:
        return True


def previous_trading_day(d: date, is_trading_day: Callable[[date], bool] = default_is_trading_day) -> date:
    x = d - timedelta(days=1)
    for _ in range(15):
        if is_trading_day(x):
            return x
        x -= timedelta(days=1)
    return x


def validate_new_entry(doc: dict, now: Optional[datetime] = None, is_trading_day: Callable[[date], bool] = default_is_trading_day,
                       ref_high: Optional[float] = None, check_calendar: bool = True) -> dict:
    """
    Returns {"hard": [...], "warn": [...]} for a position about to be OPENED.  `ref_high` is the pivot or listing high the breakout is measured from
    (enables the 8% anti-chasing check).  `check_calendar=False` skips the date checks (tests, offline research).
    """
    hard: List[str] = []
    warn: List[str] = []
    sym = doc.get("symbol")
    if not sym:
        hard.append("missing symbol")
    try:
        ep = float(doc.get("entry_price"))
    except (TypeError, ValueError):
        ep = 0.0
    if not ep > 0:
        hard.append("entry_price missing or not positive")
    sl = doc.get("stop_loss")
    try:
        sl = float(sl) if sl is not None else None
    except (TypeError, ValueError):
        sl = None
    if ep > 0:
        if sl is None or not sl > 0:
            hard.append("stop_loss missing")
        elif sl >= ep:
            hard.append(f"stop_loss {sl} is not below entry_price {ep}")
        else:
            risk = (ep - sl) / ep * 100.0
            if risk > HARD_RISK_CAP_PCT:
                hard.append(f"initial risk {risk:.1f}% above the {HARD_RISK_CAP_PCT}% cap")
        lim = doc.get("limit_buy_price")
        if lim is not None:
            try:
                if float(lim) < ep:
                    warn.append(f"limit_buy_price {lim} below entry_price {ep}")
            except (TypeError, ValueError):
                warn.append("limit_buy_price unreadable")
        cur = doc.get("current_price")
        try:
            if cur is not None and abs(ep / float(cur) - 1.0) * 100.0 > WARN_ENTRY_VS_CURRENT_PCT:
                warn.append(f"entry_price {ep} is more than {WARN_ENTRY_VS_CURRENT_PCT}% from current_price {cur}")
        except (TypeError, ValueError, ZeroDivisionError):
            pass
        if ref_high:
            ext = (ep / float(ref_high) - 1.0) * 100.0
            if ext > MAX_EXTENSION_PCT:
                hard.append(f"entry is {ext:.1f}% above the reference high {ref_high} (limit {MAX_EXTENSION_PCT}%)")
    if check_calendar:
        d = to_date(doc.get("entry_date"))
        today = (now or datetime.now(IST)).astimezone(IST).date() if (now is None or now.tzinfo) else now.date()
        if d is None:
            hard.append("entry_date missing or unreadable")
        else:
            if d > today:
                hard.append(f"entry_date {d} is in the future")
            if not is_trading_day(d):
                hard.append(f"entry_date {d} ({d.strftime('%A')}) is not an NSE trading day")
    return {"hard": hard, "warn": warn}


def reconcile_position(pos: dict, candles, is_trading_day: Callable[[date], bool] = default_is_trading_day) -> List[str]:
    """
    Flags for one stored position against its daily candles (DataFrame with DATE, HIGH, LOW or a dict date_str -> (low, high); None = no candles).
    Empty list = nothing wrong found.
    """
    flags: List[str] = []
    bars = _bar_lookup(candles)
    ed, xd = to_date(pos.get("entry_date")), to_date(pos.get("exit_date"))
    ep, xp = pos.get("entry_price"), pos.get("exit_price")
    if ed is not None and not is_trading_day(ed):
        flags.append(f"ENTRY_NOT_TRADING_DAY({ed})")
    if bars is None:
        flags.append("NO_CANDLES")
    else:
        if ed is not None and ep:
            b = bars.get(ed.strftime("%Y-%m-%d"))
            if b is None:
                if is_trading_day(ed) and ed <= max_date(bars):
                    flags.append("ENTRY_DATE_HAS_NO_BAR")
            else:
                lo, hi = b
                if ep > hi * (1 + RANGE_TOLERANCE):
                    flags.append(f"ENTRY_ABOVE_DAY_HIGH({(ep / hi - 1) * 100:.1f}%)")
                if ep < lo * (1 - RANGE_TOLERANCE):
                    flags.append(f"ENTRY_BELOW_DAY_LOW({(ep / lo - 1) * 100:.1f}%)")
        if xd is not None and xp:
            b = bars.get(xd.strftime("%Y-%m-%d"))
            if b is not None:
                lo, hi = b
                if xp > hi * (1 + RANGE_TOLERANCE) or xp < lo * (1 - RANGE_TOLERANCE):
                    flags.append("EXIT_OUTSIDE_DAY_RANGE")
    if ep and xp and pos.get("pnl_pct") is not None:
        calc = (float(xp) / float(ep) - 1.0) * 100.0
        if abs(calc - float(pos["pnl_pct"])) > PNL_MISMATCH_PP:
            flags.append(f"PNL_MISMATCH(stored {float(pos['pnl_pct']):.1f}, from prices {calc:.1f})")
    return flags


def max_date(bars: dict) -> date:
    return max(datetime.strptime(k, "%Y-%m-%d").date() for k in bars)


def _bar_lookup(candles):
    if candles is None:
        return None
    if isinstance(candles, dict):
        return candles or None
    try:
        if len(candles) == 0:
            return None
        return {str(r["DATE"])[:10]: (float(r["LOW"]), float(r["HIGH"])) for r in candles.to_dict("records")}
    except Exception:
        return None
