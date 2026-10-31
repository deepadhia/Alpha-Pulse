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
from datetime import time as dtime
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

import pandas as pd

MARKET_CLOSE_IST = dtime(15, 45)     # same cutoff as streamlined_ipo_scanner.fetch_data: after it today's bar is final
NEAR_PIVOT_FRAC = 0.75               # refresh names whose last close >= 0.75 x pivot: a 20% circuit day can lift 0.75 to 0.90 (the S3 proximity gate)
OVERLAP_TOL = 0.01                   # the overlapping cached close must match the fetched one within 1%
MIN_REQUEST_GAP_S = 0.1
UPSTOX_URL = "https://api.upstox.com/v2/historical-candle/{key}/day/{to}/{frm}"


def last_complete_date(now_ist, run_day=None) -> pd.Timestamp:
    """Date of the newest session whose daily bar is final: today after 15:45 IST, else yesterday; never later than `run_day` when given."""
    now = pd.Timestamp(now_ist)
    d = now.normalize() if now.time() >= MARKET_CLOSE_IST else now.normalize() - pd.Timedelta(days=1)
    if run_day is not None:
        d = min(d, pd.Timestamp(run_day).normalize())
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


def refresh_candles(candles: Dict[str, pd.DataFrame], listing_syms: Iterable[str], held: Iterable[str], max_date, token: Optional[str],
                    mapping: Dict[str, str], http_get: Callable = None, sleep: Callable = _time.sleep) -> Tuple[Dict[str, pd.DataFrame], dict]:
    """
    Merge missing completed sessions (<= max_date) into the frames of the selected symbols.  Returns (new candle dict, report) with
    report = dict(selected=set, fetched=int, updated=int, failed={symbol: reason}, note=str|None).  The input dict is not mutated.
    """
    out = dict(candles)
    selected = select_symbols(candles, listing_syms, held)
    rep = dict(selected=selected, fetched=0, updated=0, failed={}, note=None)
    if not token:
        rep["note"] = "UPSTOX_ACCESS_TOKEN missing: candles NOT refreshed"
        return out, rep
    max_date = pd.Timestamp(max_date).normalize()
    for s in sorted(selected):
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
        if err:
            rep["failed"][s] = err
        else:
            merged, status = merge_fresh(df, parse_upstox_candles(raw), max_date)
            if status == "ok":
                out[s] = merged
                rep["updated"] += 1
            elif status != "nothing_new":
                rep["failed"][s] = status
        sleep(MIN_REQUEST_GAP_S)
    return out, rep


def describe(rep: Optional[dict]) -> List[str]:
    """Warning lines for the alert: empty when everything refreshed cleanly."""
    if not rep:
        return []
    lines = []
    if rep.get("note"):
        lines.append(rep["note"])
    if rep.get("failed"):
        f = rep["failed"]
        lines.append(f"candle refresh failed for {len(f)} of {rep['fetched']} fetched: " + ", ".join(f"{k} ({v})" for k, v in sorted(f.items())[:8]))
    return lines
