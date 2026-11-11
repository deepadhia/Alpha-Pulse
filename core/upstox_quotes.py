# -*- coding: utf-8 -*-
"""
core/upstox_quotes.py -- Upstox full market quotes (open, last price, best bid / ask with quantities, circuit limits) and 1-minute candles for the
automatic forward test (core/resting_exec.py).  Ported in shape from Learning-Lab `nse_quant_lab/upstox_feed.py` (the quote parsing there was
verified live on 2026-11-08 against the real API).  Pure parsing functions are separate from HTTP so they are unit-tested without a network.
"""

import time
from typing import Callable, Dict, Iterable, List, Optional

import pandas as pd

API_BASE = "https://api.upstox.com"
QUOTE_URL = API_BASE + "/v2/market-quote/quotes"
MINUTE_URL = API_BASE + "/v3/historical-candle/{key}/minutes/1/{to_date}/{from_date}"
MINUTE_TODAY_URL = API_BASE + "/v3/historical-candle/intraday/{key}/minutes/1"     # the current session only; the historical URL above is empty for it
MAX_QUOTE_KEYS = 100
USER_AGENT = "alphapulse/1.0 (personal research client for the authorised Upstox API)"


def _headers(token: str) -> dict:
    return {"Accept": "application/json", "Authorization": f"Bearer {token}", "Api-Version": "2.0", "User-Agent": USER_AGENT}


def normalise_quote(q: dict) -> dict:
    """One Upstox quote -> flat dict.  bid / ask are the BEST levels with a positive price (None when that side of the book is empty)."""
    depth = q.get("depth") or {}
    buys = [x for x in depth.get("buy", []) if (x.get("price") or 0) > 0]
    sells = [x for x in depth.get("sell", []) if (x.get("price") or 0) > 0]
    ohlc = q.get("ohlc") or {}
    return {
        "open": float(ohlc.get("open") or 0.0), "high": float(ohlc.get("high") or 0.0), "low": float(ohlc.get("low") or 0.0),
        "ltp": float(q.get("last_price") or 0.0),
        "bid": float(buys[0]["price"]) if buys else None, "ask": float(sells[0]["price"]) if sells else None,
        "bid_qty": float(buys[0].get("quantity") or 0.0) if buys else None, "ask_qty": float(sells[0].get("quantity") or 0.0) if sells else None,
        "volume": float(q.get("volume") or 0.0),
        "upper_circuit": q.get("upper_circuit_limit"), "lower_circuit": q.get("lower_circuit_limit"),
        "ts": q.get("timestamp"),
    }


def parse_quotes(payload: dict, key_to_symbol: Dict[str, str]) -> Dict[str, dict]:
    """Match each returned quote to its symbol by instrument_token (falls back to the 'NSE_EQ:SYMBOL' response key)."""
    out: Dict[str, dict] = {}
    syms = set(key_to_symbol.values())
    for res_key, q in (payload.get("data") or {}).items():
        sym = key_to_symbol.get(q.get("instrument_token"))
        if sym is None and ":" in str(res_key) and res_key.split(":", 1)[1] in syms:
            sym = res_key.split(":", 1)[1]
        if sym:
            out[sym] = normalise_quote(q)
    return out


def fetch_quotes(symbol_to_key: Dict[str, str], token: Optional[str], http_get: Callable = None, sleep: Callable = time.sleep) -> Dict[str, dict]:
    """{symbol: quote} for the symbols that have an instrument key.  Raises on HTTP errors so the caller fails loud (no guessed quote)."""
    if not token:
        raise RuntimeError("UPSTOX_ACCESS_TOKEN missing: no quotes")
    import requests
    get = http_get or requests.get
    key_to_symbol = {k: s for s, k in symbol_to_key.items() if k}
    keys = list(key_to_symbol)
    out: Dict[str, dict] = {}
    for i in range(0, len(keys), MAX_QUOTE_KEYS):
        batch = keys[i:i + MAX_QUOTE_KEYS]
        r = get(QUOTE_URL, params={"instrument_key": ",".join(batch)}, headers=_headers(token), timeout=20)
        if r.status_code != 200:
            raise RuntimeError(f"Upstox quote HTTP {r.status_code}: {str(r.text)[:200]}")
        out.update(parse_quotes(r.json(), key_to_symbol))
        sleep(0.2)
    return out


def parse_minute_candles(raw: Iterable) -> pd.DataFrame:
    """Upstox rows [timestamp, open, high, low, close, volume, oi] -> frame TS (naive IST), OPEN, HIGH, LOW, CLOSE, VOLUME, ascending."""
    rows = []
    for r in raw or []:
        try:
            ts = pd.Timestamp(r[0])
            ts = ts.tz_convert("Asia/Kolkata").tz_localize(None) if ts.tzinfo else ts
            rows.append(dict(TS=ts, OPEN=float(r[1]), HIGH=float(r[2]), LOW=float(r[3]), CLOSE=float(r[4]), VOLUME=float(r[5])))
        except (TypeError, ValueError, IndexError):
            continue
    df = pd.DataFrame(rows, columns=["TS", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"])
    return df.sort_values("TS").reset_index(drop=True)


def _get_minute_rows(url: str, token: str, get: Callable, retries: int, sleep: Callable, label: str):
    """Raw candle rows of one URL; [] on 400/404; raises after repeated failures."""
    last = None
    for attempt in range(retries):
        r = get(url, headers=_headers(token), timeout=30)
        if r.status_code == 200:
            return (r.json().get("data") or {}).get("candles") or []
        if r.status_code in (400, 404):
            return []
        last = f"HTTP {r.status_code}"
        sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"Upstox minute candles failed for {label}: {last}")


def fetch_minute_bars(key: str, day, token: Optional[str], http_get: Callable = None, retries: int = 3, sleep: Callable = time.sleep,
                      today=None) -> pd.DataFrame:
    """
    1-minute candles of one completed session.  Empty frame when Upstox has none; raises after repeated failures (no guessed bars).
    The historical endpoint has no bars for the CURRENT session (checked 2026-11-11 22:04 IST), so for day == today (IST) the intraday endpoint is
    asked when the historical one is empty; its bars count only when the session is complete (last bar 15:25 or later), otherwise the frame is empty.
    """
    if not token:
        raise RuntimeError("UPSTOX_ACCESS_TOKEN missing: no minute bars")
    import requests
    get = http_get or requests.get
    d = pd.Timestamp(day).strftime("%Y-%m-%d")
    qkey = requests.utils.quote(key, safe="")
    rows = _get_minute_rows(MINUTE_URL.format(key=qkey, to_date=d, from_date=d), token, get, retries, sleep, f"{key} {d}")
    if not rows:
        from datetime import datetime, timedelta, timezone
        t = pd.Timestamp(today).normalize() if today is not None else pd.Timestamp(datetime.now(timezone(timedelta(hours=5, minutes=30))).date())
        if pd.Timestamp(day).normalize() == t:
            df = parse_minute_candles(_get_minute_rows(MINUTE_TODAY_URL.format(key=qkey), token, get, retries, sleep, f"{key} {d} (intraday)"))
            df = df[df["TS"].dt.normalize() == t] if len(df) else df
            if len(df) and df["TS"].iloc[-1].strftime("%H:%M") >= "15:25":
                return df.reset_index(drop=True)
            return parse_minute_candles([])
    return parse_minute_candles(rows)
