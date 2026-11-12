# -*- coding: utf-8 -*-
"""
core/s3_refresh.py

In-memory candle refresh for the PAPER_ONLY S3 evening alert (2026-10-31 fix).

Why: the shared `daily_candles_cache` is filled by the morning scanners with sessions up to the PREVIOUS trading day (streamlined_ipo_scanner.fetch_data
treats a cache holding D-1 as fresh all through day D, and the 18:30 IST run only fetches symbols held in the policy-P book).  The S3 job at 18:50 IST
therefore saw data one session old every day: all runs 2026-10-23..30 were flagged STALE, orders were computed without day D's high, no order was stored,
and a stop alert would have used the previous close.

What: for the held S3 positions and the watched names near their pivot, fetch the missing COMPLETED daily bars from Upstox and merge them into the
candle frames in memory.  Nothing is written to MongoDB and nothing touches policy P or the shared cache.  A bar is merged only if it is a completed
session (never today's forming bar), is internally valid, and the overlapping bar agrees with the cache (a split/bonus adjustment would not).
Failures leave the symbol at its cached date, so the freshness check keeps reporting STALE honestly.
"""

import time as _time
from datetime import datetime, timedelta, timezone
from datetime import time as dtime
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

import pandas as pd
from core.session_dates import daily_frame, session_date, ist_naive

MARKET_CLOSE_IST = dtime(15, 45)     # same cutoff as streamlined_ipo_scanner.fetch_data: after it today's bar is final
NEAR_PIVOT_FRAC = 0.75               # refresh names whose last close >= 0.75 x pivot: a 20% circuit day can lift 0.75 to 0.90 (the S3 proximity gate)
OVERLAP_TOL = 0.01                   # the overlapping cached close must match the fetched one within 1%
MIN_REQUEST_GAP_S = 0.1
UPSTOX_URL = "https://api.upstox.com/v2/historical-candle/{key}/day/{to}/{frm}"


def last_complete_date(now_ist, run_day=None) -> pd.Timestamp:
    """Date of the newest session whose daily bar is final: today after 15:45 IST, else yesterday; never later than `run_day` when given."""
    now = ist_naive(now_ist)
    d = now.normalize() if now.time() >= MARKET_CLOSE_IST else now.normalize() - pd.Timedelta(days=1)
    if run_day is not None:
        d = min(d, session_date(run_day))
    return d


def parse_upstox_candles(raw) -> pd.DataFrame:
    """Upstox rows [timestamp, open, high, low, close, volume, oi] -> frame DATE (naive midnight), OPEN, HIGH, LOW, CLOSE, VOLUME, ascending."""
    rows = []
    for r in raw or []:
        try:
            ts = pd.Timestamp(r[0])
            ts = ts.tz_localize(None) if ts.tzinfo else ts
            rows.append(dict(DATE=ts.normalize(), OPEN=float(r[1]), HIGH=float(r[2]), LOW=float(r[3]), CLOSE=float(r[4]), VOLUME=float(r[5])))
        except (TypeError, ValueError, IndexError):
            continue
    if not rows:
        return pd.DataFrame(columns=["DATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"])
    return pd.DataFrame(rows).drop_duplicates("DATE", keep="last").sort_values("DATE").reset_index(drop=True)


def _valid_bar(r) -> bool:
    o, h, l, c, v = (float(r[k]) for k in ("OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"))
    return min(o, h, l, c) > 0 and v >= 0 and h >= l and h >= max(o, c) * 0.999 and l <= min(o, c) * 1.001


def merge_fresh(df: pd.DataFrame, fresh: pd.DataFrame, max_date) -> Tuple[pd.DataFrame, str]:
    """
    Append the completed, valid bars of `fresh` that are newer than df's last bar.  Returns (frame, status) with status one of
    "ok" | "nothing_new" | "overlap_mismatch" | "invalid_bar" | "empty".  On any problem the original frame is returned unchanged.
    """
    if fresh is None or fresh.empty:
        return df, "empty"
    df, fresh = daily_frame(df), daily_frame(fresh)
    max_date = session_date(max_date)
    last = pd.Timestamp(df["DATE"].iloc[-1]).normalize()
    overlap = fresh[fresh["DATE"] == last]
    if not overlap.empty:
        old_c, new_c = float(df["CLOSE"].iloc[-1]), float(overlap["CLOSE"].iloc[-1])
        if old_c <= 0 or abs(new_c / old_c - 1.0) > OVERLAP_TOL:
            return df, "overlap_mismatch"               # a split/bonus adjusted history or a bad bar: do not splice
    new = fresh[(fresh["DATE"] > last) & (fresh["DATE"] <= pd.Timestamp(max_date).normalize())]
    if new.empty:
        return df, "nothing_new"
    if not all(_valid_bar(r) for _, r in new.iterrows()):
        return df, "invalid_bar"
    add = new.copy()
    if "LTP" in df.columns:
        add["LTP"] = add["CLOSE"]
    for c in df.columns:
        if c not in add.columns:
            add[c] = float("nan")
    out = pd.concat([df, add[list(df.columns)]], ignore_index=True)
    out["DATE"] = pd.to_datetime(out["DATE"])
    return out, "ok"


def select_symbols(candles: Dict[str, pd.DataFrame], listing_syms: Iterable[str], held: Iterable[str]) -> Set[str]:
    """Held positions always; watched names whose last close is within reach of their post-listing high (the only ones that can become orders)."""
    sel = set(held) & set(candles)
    for s in listing_syms:
        df = candles.get(s)
        if df is None or df.empty:
            continue
        piv = float(df["HIGH"].max())
        if piv > 0 and float(df["CLOSE"].iloc[-1]) >= NEAR_PIVOT_FRAC * piv:
            sel.add(s)
    return sel


def _skipped_session_gap(last: pd.Timestamp, max_date: pd.Timestamp, is_holiday: Optional[Callable] = None) -> bool:
    """
    True when more than one trading day lies between the cached last bar and max_date: a single 30-minute day cannot close that gap.
    `is_holiday` (optional predicate on a Timestamp) removes exchange holidays from the count; without it only weekends are skipped, so the first
    session after a holiday would wrongly look like a two-day gap and never use the 30-minute fallback.
    """
    gap = pd.bdate_range(pd.Timestamp(last) + pd.Timedelta(days=1), pd.Timestamp(max_date))
    if is_holiday is not None:
        gap = [d for d in gap if not _safe_holiday(is_holiday, d)]
    return len(gap) > 1


def _safe_holiday(is_holiday: Callable, d) -> bool:
    try:
        return bool(is_holiday(d))
    except Exception:
        return False


def fetch_upstox_daily(symbol: str, key: str, token: str, start, end, http_get: Callable = None, sleep: Callable = _time.sleep):
    """(raw candles list, None) or (None, reason).  One retry after a 429."""
    import requests
    http_get = http_get or requests.get
    url = UPSTOX_URL.format(key=key, to=pd.Timestamp(end).strftime("%Y-%m-%d"), frm=pd.Timestamp(start).strftime("%Y-%m-%d"))
    headers = {"Accept": "application/json", "Api-Version": "2.0", "Authorization": f"Bearer {token}"}
    try:
        r = http_get(url, headers=headers, timeout=15)
        if r.status_code == 429:
            sleep(1.0)
            r = http_get(url, headers=headers, timeout=15)
        if r.status_code != 200:
            return None, f"http {r.status_code}"
        return (r.json().get("data") or {}).get("candles") or [], None
    except Exception as e:                                     # network, JSON: never let one symbol abort the alert
        return None, f"{type(e).__name__}"


INTRADAY_URL = "https://api.upstox.com/v3/historical-candle/{key}/minutes/30/{day}/{day}"
# The historical endpoint above has NO bars for the current session (checked 2026-11-11 22:04 IST: 0 bars for the day, while this endpoint returned
# the full 13 half-hour bars with the same open, high, low and close as the live quote).  Evening jobs (18:30 to 19:30 IST) always ask for today.
INTRADAY_TODAY_URL = "https://api.upstox.com/v3/historical-candle/intraday/{key}/minutes/30"
EXTRA_BUDGET_S = 480.0            # seconds the optional extra calls may use per refresh (the workflow timeout is 15 minutes; a full S3 refresh of 217 names took 260 s on 2026-11-11)
MIN_INTRADAY_BARS = 12            # a full session has 13 half-hour bars (09:15 .. 15:15)
IST = timezone(timedelta(hours=5, minutes=30))


def ist_today() -> pd.Timestamp:
    return pd.Timestamp(datetime.now(IST).date())


def _get_candles(url: str, token: str, http_get: Callable, sleep: Callable):
    """(raw rows, None) or (None, reason).  One retry after a 429."""
    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
    try:
        r = http_get(url, headers=headers, timeout=15)
        if r.status_code == 429:
            sleep(1.0)
            r = http_get(url, headers=headers, timeout=15)
        if r.status_code != 200:
            return None, f"30m http {r.status_code}"
        return (r.json().get("data") or {}).get("candles") or [], None
    except Exception as e:
        return None, f"30m {type(e).__name__}"


def _session_from_30m_rows(raw, day: pd.Timestamp):
    """(daily frame, None) or (None, reason) from raw 30-minute rows of `day`; refuses an incomplete session (never a guessed bar)."""
    rows = []
    for c in raw or []:
        try:
            ts = pd.Timestamp(c[0])
            ts = ts.tz_localize(None) if ts.tzinfo else ts
            if ts.normalize() == day:
                rows.append((ts, float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])))
        except (TypeError, ValueError, IndexError):
            continue
    rows.sort(key=lambda x: x[0])
    if len(rows) < MIN_INTRADAY_BARS or rows[0][0].strftime("%H:%M") != "09:15" or rows[-1][0].strftime("%H:%M") < "15:15":
        return None, f"30m incomplete session ({len(rows)} bars)"
    frame = pd.DataFrame([dict(DATE=day, OPEN=rows[0][1], HIGH=max(x[2] for x in rows), LOW=min(x[3] for x in rows), CLOSE=rows[-1][4],
                               VOLUME=sum(x[5] for x in rows))])
    return frame, None


def fetch_upstox_day_from_30m(symbol: str, key: str, token: str, day, http_get: Callable = None, sleep: Callable = _time.sleep, today=None):
    """
    One completed session built from Upstox 30-minute candles: (frame with that single daily row, None) or (None, reason).  Used when the daily
    endpoint does not (yet) return the session: it serves the previous day's bar only some hours after the close, while the 30-minute feed is
    available at once (checked 2026-11-06: open, high, low and close identical to the daily candle, volume within 0.001%).
    For the CURRENT session (day == today in IST) the historical 30-minute endpoint is empty, so the intraday endpoint is asked next (2026-11-11 fix);
    callers only ask for today after the close (last_complete_date), and a session not complete up to the 15:15 bar is refused either way.
    """
    import requests
    http_get = http_get or requests.get
    day = pd.Timestamp(day).normalize()
    today = pd.Timestamp(today).normalize() if today is not None else ist_today()
    if day == today:
        urls = [INTRADAY_TODAY_URL.format(key=key)]          # the historical URL is known to be empty for the current session: do not spend a call on it
    else:
        urls = [INTRADAY_URL.format(key=key, day=day.strftime("%Y-%m-%d"))]
    reason = "30m no data"
    for url in urls:
        raw, err = _get_candles(url, token, http_get, sleep)
        if err:
            reason = err
            continue
        frame, why = _session_from_30m_rows(raw, day)
        if frame is not None:
            return frame, None
        reason = why
    return None, reason


def _newest(frame: pd.DataFrame):
    return None if frame is None or frame.empty else pd.Timestamp(frame["DATE"].max()).normalize()


def refresh_candles(candles: Dict[str, pd.DataFrame], listing_syms: Iterable[str], held: Iterable[str], max_date, token: Optional[str],
                    mapping: Dict[str, str], http_get: Callable = None, sleep: Callable = _time.sleep,
                    extra_budget_s: float = EXTRA_BUDGET_S, clock: Callable = _time.monotonic,
                    is_holiday: Optional[Callable] = None) -> Tuple[Dict[str, pd.DataFrame], dict]:
    """
    Merge missing completed sessions (<= max_date) into the frames of the selected symbols.  Returns (new candle dict, report) with
    report = dict(selected=set, fetched=int, updated=int, failed={symbol: reason}, note=str|None).  The input dict is not mutated.
    """
    candles = {s: daily_frame(df) for s, df in candles.items()}
    out = dict(candles)
    selected = select_symbols(candles, listing_syms, held)
    rep = dict(selected=selected, fetched=0, updated=0, failed={}, note=None, from_30m=[], still_old=[])
    if not token:
        rep["note"] = "UPSTOX_ACCESS_TOKEN missing: candles NOT refreshed"
        return out, rep
    max_date = session_date(max_date)
    t0 = clock()
    for s in sorted(selected, key=lambda x: (x not in set(held), x)):      # held positions first: their stop alerts depend on the data
        df = candles[s]
        last = pd.Timestamp(df["DATE"].iloc[-1]).normalize()
        if last >= max_date:
            continue                                           # already current
        key = mapping.get(s)
        if not key:
            rep["failed"][s] = "no instrument key"
            continue
        rep["fetched"] += 1
        raw, err = fetch_upstox_daily(s, key, token, last, max_date, http_get, sleep)
        fresh = parse_upstox_candles(raw) if not err else None
        in_budget = (clock() - t0) <= extra_budget_s or s in set(held)     # the extra calls (range retry, 30-minute bars) stop once the budget is spent
        # The earlier-start retry exists for the quirk where a daily range ends one session short; it cannot help for the CURRENT session, which the
        # daily endpoint does not have yet, so it is skipped then (it doubled the calls per name and exhausted the budget before every name was done).
        if in_budget and not err and max_date != ist_today() and (_newest(fresh) is None or _newest(fresh) < max_date):
            # the daily endpoint can answer one date range with an older newest bar than another range (seen 2026-11-06: from 10-28 ended 11-04,
            # from 11-02 and from 10-01 included 11-05): ask once more with an earlier start
            raw2, err2 = fetch_upstox_daily(s, key, token, last - pd.Timedelta(days=7), max_date, http_get, sleep)
            fresh2 = parse_upstox_candles(raw2) if not err2 else None
            if fresh2 is not None and (_newest(fresh) is None or (_newest(fresh2) is not None and _newest(fresh2) > _newest(fresh))):
                fresh = fresh2
        if in_budget and (fresh is None or _newest(fresh) is None or _newest(fresh) < max_date) and (max_date - last).days <= 4 and not _skipped_session_gap(last, max_date, is_holiday):
            bar, err3 = fetch_upstox_day_from_30m(s, key, token, max_date, http_get, sleep)
            if bar is not None:
                fresh = bar if fresh is None or fresh.empty else pd.concat([fresh, bar], ignore_index=True).drop_duplicates("DATE", keep="last").sort_values("DATE")
                err = None
                rep["from_30m"].append(s)
            elif err is None and fresh is None:
                err = err3
        if err and (fresh is None):
            rep["failed"][s] = err
        elif fresh is None or fresh.empty:
            rep["failed"][s] = "empty"
        else:
            merged, status = merge_fresh(df, fresh.reset_index(drop=True), max_date)
            if status == "ok":
                out[s] = merged
                rep["updated"] += 1
            elif status != "nothing_new":
                rep["failed"][s] = status
        # Whatever the path, a selected name that ends below max_date is reported (2026-11-11: about 23 of 217 S3 names stayed on the earlier bar
        # without a word because the 30-minute session was incomplete and the daily endpoint had returned older bars, i.e. no error was recorded).
        if s not in rep["failed"] and _newest(out[s]) < max_date:
            rep["still_old"].append(s)
        sleep(MIN_REQUEST_GAP_S)
    return out, rep


def describe(rep: Optional[dict]) -> List[str]:
    """Warning lines for the alert: empty when everything refreshed cleanly."""
    if not rep:
        return []
    lines = []
    if rep.get("note"):
        lines.append(rep["note"])
    if rep.get("from_30m"):
        lines.append(f"last session built from 30-minute candles for {len(rep['from_30m'])} name(s) (daily candle not published yet)")
    if rep.get("still_old"):
        so = sorted(rep["still_old"])
        lines.append(f"{len(so)} name(s) still on an older bar (no complete session available): " + ", ".join(so[:8]) + (" ..." if len(so) > 8 else ""))
    if rep.get("failed"):
        f = rep["failed"]
        lines.append(f"candle refresh failed for {len(f)} of {rep['fetched']} fetched: " + ", ".join(f"{k} ({v})" for k, v in sorted(f.items())[:8]))
    return lines
