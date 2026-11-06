# -*- coding: utf-8 -*-
"""
core/cache_refresh.py

Rebuild the shared `daily_candles_cache` document of IPOs whose cache has stopped growing (2026-11-06 fix).

Why: after an instrument key is repaired (core/instrument_keys.py, repair_instrument_keys.py) the cached candles of that symbol stop at the date the old
key died.  The scanners refetch a stale cache only for symbols they scan (IPO age <= 60 days), so older IPOs (S3 window 61-400 days) stayed stale for
weeks: 11 of 19 repaired symbols were still stale on 2026-11-05, and AZADIND failed `overlap_mismatch` in the S3 refresh every evening.

What: pure helpers (stale detection, frame validation, record building) plus `rebuild_symbol`, which refetches the FULL history from the listing date with
the current key and replaces the cache document.  Same record format as streamlined_ipo_scanner.fetch_data.  Only completed sessions are stored, and
nothing is written unless the new frame passes `validate_frame`.
"""

from datetime import date, datetime, timedelta
from typing import Callable, Iterable, List, Optional, Tuple

import pandas as pd

MARKET_CLOSE_HHMM = (15, 45)        # same cutoff as fetch_data: after it today's bar is final


def _naive_dates(series: pd.Series) -> pd.Series:
    """Datetime series without timezone (Upstox stamps sessions +05:30 midnight; the local wall date is what the cache stores)."""
    d = pd.to_datetime(series)
    return d.dt.tz_localize(None) if d.dt.tz is not None else d


def expected_last_session(now_ist: datetime, is_holiday: Callable[[date], bool] = lambda d: False) -> date:
    """Newest session whose daily bar is final: today after 15:45 IST on a trading day, else the previous trading day."""
    d = now_ist.date()
    after_close = (now_ist.hour, now_ist.minute) >= MARKET_CLOSE_HHMM
    if not (after_close and d.weekday() < 5 and not is_holiday(d)):
        d -= timedelta(days=1)
    while d.weekday() >= 5 or is_holiday(d):
        d -= timedelta(days=1)
    return d


def find_stale(listings: Iterable[dict], cache_last: dict, expected: date, min_lag_days: int = 4, max_age_days: int = 400,
               today: Optional[date] = None) -> List[dict]:
    """
    Symbols whose cache last_completed_date is more than `min_lag_days` calendar days behind `expected`, among IPOs listed at most `max_age_days` ago.
    `listings` rows need symbol and listing_date; `cache_last` maps symbol -> 'YYYY-MM-DD' (missing = no cache document at all).
    A symbol with no cache document is reported with last=None.
    """
    today = today or expected
    out = []
    for li in listings:
        sym = li.get("symbol")
        ld = li.get("listing_date")
        if not sym or ld is None or pd.isna(ld):
            continue
        ld = pd.Timestamp(ld)
        ld = ld.tz_localize(None) if ld.tzinfo else ld
        age = (pd.Timestamp(today) - ld.normalize()).days
        if age < 1 or age > max_age_days:
            continue
        last = cache_last.get(sym)
        lag = None if last is None else (pd.Timestamp(expected) - pd.Timestamp(last)).days
        if last is None or lag > min_lag_days:
            out.append(dict(symbol=sym, listing_date=ld.normalize(), age=age, last=last, lag_days=lag))
    return sorted(out, key=lambda r: (r["lag_days"] is None, -(r["lag_days"] or 0), r["symbol"]))


def validate_frame(df: Optional[pd.DataFrame], listing_date, expected: date, min_rows: int = 5, max_end_gap_days: int = 4) -> Tuple[bool, str]:
    """A rebuilt frame is stored only if it is complete enough to trust.  Returns (ok, reason)."""
    if df is None or len(df) < min_rows:
        return False, "no or too few candles from Upstox"
    for c in ("DATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"):
        if c not in df.columns:
            return False, f"missing column {c}"
    o, h, l, c, v = (df[k].astype(float) for k in ("OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"))
    if ((pd.concat([o, h, l, c], axis=1) <= 0).any().any()) or (v < 0).any() or (h < l).any() or (h < pd.concat([o, c], axis=1).max(axis=1) * 0.999).any():
        return False, "invalid bar (non-positive price, high < low or high below open/close)"
    dates = _naive_dates(df["DATE"])
    last = pd.Timestamp(dates.iloc[-1]).normalize()
    if (pd.Timestamp(expected) - last).days > max_end_gap_days:
        return False, f"history ends {last.date()}, expected about {expected}"
    first = pd.Timestamp(dates.iloc[0]).normalize()
    ld = pd.Timestamp(listing_date)
    ld = ld.tz_localize(None) if ld.tzinfo else ld
    if abs((first - ld.normalize()).days) > 5:
        return False, f"history starts {first.date()}, listing date {ld.date()}"
    if dates.duplicated().any():
        return False, "duplicate dates"
    return True, "ok"


def completed_records(df: pd.DataFrame, now_ist: datetime) -> Tuple[Optional[str], list]:
    """(last_completed_date string, records) for the cache document: today's bar only after the close, exactly as fetch_data stores it."""
    today = pd.Timestamp(now_ist.date())
    d = df.copy()
    d["DATE"] = _naive_dates(d["DATE"])
    done = d[d["DATE"].dt.normalize() < today]
    if (now_ist.hour, now_ist.minute) >= MARKET_CLOSE_HHMM and (d["DATE"].dt.normalize() == today).any():
        done = d
    if done.empty:
        return None, []
    rec = done.copy()
    if "LTP" not in rec.columns:
        rec["LTP"] = rec["CLOSE"]
    rec["DATE"] = rec["DATE"].dt.strftime("%Y-%m-%d")
    return str(rec["DATE"].iloc[-1]), rec[["DATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME", "LTP"]].to_dict(orient="records")


def rebuild_symbol(symbol: str, listing_date, expected: date, now_ist: datetime, fetch: Callable, store: Callable,
                   apply: bool = False) -> dict:
    """
    Refetch the full history of `symbol` (fetch(symbol, start, end) -> DataFrame or None, e.g. streamlined_ipo_scanner.fetch_from_upstox) and, when
    `apply`, hand the validated records to store(symbol, last_completed_date, records).  Returns dict(symbol, ok, reason, rows, last).
    """
    start = pd.Timestamp(listing_date).date()
    try:
        df = fetch(symbol, start, now_ist.date())
    except Exception as e:                      # one symbol must never abort the run
        return dict(symbol=symbol, ok=False, reason=f"fetch error {type(e).__name__}", rows=0, last=None)
    ok, reason = validate_frame(df, listing_date, expected)
    if not ok:
        return dict(symbol=symbol, ok=False, reason=reason, rows=0 if df is None else len(df), last=None)
    last, records = completed_records(df, now_ist)
    if not records:
        return dict(symbol=symbol, ok=False, reason="no completed bars", rows=len(df), last=None)
    if apply:
        store(symbol, last, records)
    return dict(symbol=symbol, ok=True, reason="rebuilt" if apply else "would rebuild", rows=len(records), last=last)
