# -*- coding: utf-8 -*-
"""
core/data_coverage.py -- candle coverage check for the research cache (data.pkl).

Pure functions, no database access, no effect on any trading rule, gate or exit.  Input is the
same (listing_map, candle_map) pair that run_honest_backtest.load_data() returns.

For every listing aged <= max_age_days (the live listing-breakout window) it assigns one status:

  OK         has candles, the last candle is current and the history starts at listing
  MISSING    no candle dataframe in the cache (never fetched, or below the loader floor of 10 candles)
  TRUNCATED  history starts well after the listing date (cache doc was seeded from a position
             entry date, not from listing; the honest replay cannot evaluate these)
  STALE      last candle is more than `stale_sessions` business days older than the as-of date
  TOO_YOUNG  no candles, but fewer than `min_candles` sessions have passed since listing, so the
             loader floor excludes it by design.  Reported, but NOT counted against coverage.

coverage = OK / (listings in scope - TOO_YOUNG).
"""

import numpy as np
import pandas as pd

OK, MISSING, TRUNCATED, STALE, TOO_YOUNG = "OK", "MISSING", "TRUNCATED", "STALE", "TOO_YOUNG"

DEFAULT_MAX_AGE_DAYS = 60
DEFAULT_STALE_SESSIONS = 2
DEFAULT_TRUNCATED_SESSIONS = 3
DEFAULT_MIN_CANDLES = 10      # matches the len(candles) < 10 floor in run_latest_rules_backtest.load_dataset
DEFAULT_CLUSTER_MIN = 10      # listings sharing one listing_date before it is flagged as a placeholder date


def _to_day(value):
    """Timestamp or None, tz dropped, normalized to midnight."""
    if value is None:
        return None
    ts = pd.to_datetime(value, errors="coerce")
    if pd.isna(ts):
        return None
    if ts.tzinfo is not None:
        ts = ts.tz_localize(None)
    return ts.normalize()


def _sessions_between(a, b):
    """Business days from a (inclusive) to b (exclusive); negative if b < a.  No holiday calendar."""
    return int(np.busday_count(a.date(), b.date()))


def cache_as_of(candle_map):
    """Latest candle date anywhere in the cache (the cache's own 'today')."""
    last = None
    for df in candle_map.values():
        if df is not None and len(df):
            d = _to_day(df["DATE"].max())
            if d is not None and (last is None or d > last):
                last = d
    return last


def coverage_report(listing_map, candle_map, as_of=None, max_age_days=DEFAULT_MAX_AGE_DAYS,
                    stale_sessions=DEFAULT_STALE_SESSIONS, truncated_sessions=DEFAULT_TRUNCATED_SESSIONS,
                    min_candles=DEFAULT_MIN_CANDLES, cluster_min=DEFAULT_CLUSTER_MIN):
    """Return dict(as_of, rows, counts, in_scope, evaluable, coverage, suspect_dates).

    rows: list of dicts (symbol, listing_date, age_days, n_candles, first_candle, last_candle, status),
    sorted by status then symbol.  coverage is 1.0 when nothing is evaluable.
    suspect_dates: {listing_date: count} for dates shared by >= cluster_min in-scope listings.  A real
    day has a handful of IPOs; a spike means the listing_date is an ingest-day placeholder and the
    window population is inflated.  Informational only, it does not change coverage.
    """
    as_of = _to_day(as_of) if as_of is not None else cache_as_of(candle_map)
    if as_of is None:
        raise ValueError("cannot infer as_of: candle_map is empty and no as_of given")

    rows = []
    for sym, info in listing_map.items():
        ld = _to_day((info or {}).get("listing_date"))
        if ld is None:
            continue
        age = (as_of - ld).days
        if age < 0 or age > max_age_days:
            continue
        df = candle_map.get(sym)
        n = 0 if df is None else len(df)
        first = last = None
        if n:
            first, last = _to_day(df["DATE"].min()), _to_day(df["DATE"].max())
        if n == 0:
            status = TOO_YOUNG if _sessions_between(ld, as_of) + 1 < min_candles else MISSING
        elif _sessions_between(ld, first) > truncated_sessions:
            status = TRUNCATED
        elif _sessions_between(last, as_of) > stale_sessions:
            status = STALE
        else:
            status = OK
        rows.append(dict(symbol=sym, listing_date=ld.date(), age_days=age, n_candles=n,
                         first_candle=first.date() if first is not None else None,
                         last_candle=last.date() if last is not None else None, status=status))

    order = {MISSING: 0, TRUNCATED: 1, STALE: 2, TOO_YOUNG: 3, OK: 4}
    rows.sort(key=lambda r: (order[r["status"]], r["symbol"]))
    counts = {s: sum(1 for r in rows if r["status"] == s) for s in (OK, MISSING, TRUNCATED, STALE, TOO_YOUNG)}
    evaluable = len(rows) - counts[TOO_YOUNG]
    coverage = counts[OK] / evaluable if evaluable else 1.0
    by_date = {}
    for r in rows:
        by_date[r["listing_date"]] = by_date.get(r["listing_date"], 0) + 1
    clusters = {d: k for d, k in sorted(by_date.items()) if k >= cluster_min}
    return dict(as_of=as_of.date(), rows=rows, counts=counts, in_scope=len(rows),
                evaluable=evaluable, coverage=coverage, suspect_dates=clusters)


def format_report(rep, threshold=None, show_ok=False):
    c = rep["counts"]
    lines = [f"as-of {rep['as_of']}: {rep['in_scope']} listings aged <= window; "
             f"OK={c[OK]} MISSING={c[MISSING]} TRUNCATED={c[TRUNCATED]} STALE={c[STALE]} TOO_YOUNG={c[TOO_YOUNG]} (exempt)",
             f"coverage = {c[OK]}/{rep['evaluable']} = {100.0 * rep['coverage']:.1f}%"
             + (f"  (threshold {100.0 * threshold:.1f}%)" if threshold is not None else "")]
    for d, k in rep.get("suspect_dates", {}).items():
        lines.append(f"WARNING: {k} in-scope listings share listing_date {d}; likely an ingest-day placeholder "
                     f"(window population and 'listing day' fields are unreliable for them)")
    for r in rep["rows"]:
        if r["status"] == OK and not show_ok:
            continue
        lines.append(f"  {r['status']:<9} {r['symbol']:<12} listed {r['listing_date']} age {r['age_days']:>2}d "
                     f"candles={r['n_candles']:>3} first={r['first_candle']} last={r['last_candle']}")
    return "\n".join(lines)
