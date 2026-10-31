# -*- coding: utf-8 -*-
"""
core/instrument_keys.py

Validation and repair of the Upstox instrument keys stored in MongoDB `instrument_keys` (2026-10-31).

Why: update_upstox_mapping.py builds the key as "NSE_EQ|<ISIN from NSE EQUITY_L.csv>" and never checks that Upstox accepts it, and it never looks at an
existing key again.  When NSE issues a new ISIN (face-value split, consolidation, re-listing) the stored key dies: Upstox answers http 400
UDAPI100011 "Invalid Instrument key", the candle fetch silently falls back or fails, and the symbol's cache and alerts go stale.  AZADIND
(INE566M01017 -> INE566M01025) and BUILDPRO (INE24OJ01011 -> INE24OJ01029) were exactly this.

What: probe a key with a tiny daily-candle request; for an invalid key compare the stored ISIN with the CURRENT NSE ISIN and accept the new key only
when Upstox answers 200 for it.  Everything here takes the HTTP getter as an argument, so tests need no network.
"""

import time
from datetime import date, timedelta
from typing import Callable, Dict, List, Optional

UPSTOX_URL = "https://api.upstox.com/v2/historical-candle/{key}/day/{to}/{frm}"
NSE_EQUITY_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
INVALID_CODE = "UDAPI100011"


def probe_key(key: str, token: str, http_get: Callable = None, today: Optional[date] = None, sleep: Callable = time.sleep) -> str:
    """'ok' (Upstox accepts the key), 'invalid' (UDAPI100011), or 'error:<reason>' (rate limit, auth, network: say nothing about the key)."""
    import requests
    http_get = http_get or requests.get
    today = today or date.today()
    url = UPSTOX_URL.format(key=key, to=today.isoformat(), frm=(today - timedelta(days=7)).isoformat())
    headers = {"Accept": "application/json", "Api-Version": "2.0", "Authorization": f"Bearer {token}"}
    try:
        r = http_get(url, headers=headers, timeout=15)
        if r.status_code == 429:
            sleep(1.0)
            r = http_get(url, headers=headers, timeout=15)
        if r.status_code == 200:
            return "ok"
        if r.status_code == 400 and INVALID_CODE in (getattr(r, "text", "") or ""):
            return "invalid"
        return f"error:http {r.status_code}"
    except Exception as e:
        return f"error:{type(e).__name__}"


def load_nse_isins(http_get: Callable = None) -> Dict[str, dict]:
    """{symbol: {isin, name}} from NSE EQUITY_L.csv (the same source update_upstox_mapping.py already uses)."""
    import requests
    from io import StringIO

    import pandas as pd
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
    get = http_get or s.get
    if http_get is None:
        s.get("https://www.nseindia.com", timeout=15)
    r = get(NSE_EQUITY_URL, timeout=45)
    r.raise_for_status()
    df = pd.read_csv(StringIO(r.text))
    df.columns = [c.strip() for c in df.columns]
    return {str(row["SYMBOL"]).strip(): dict(isin=str(row["ISIN NUMBER"]).strip(), name=str(row.get("NAME OF COMPANY", "")).strip())
            for _, row in df.iterrows()}


def plan_repair(symbol: str, stored: dict, token: str, nse: Dict[str, dict], http_get: Callable = None, sleep: Callable = time.sleep) -> dict:
    """
    One symbol -> dict(symbol, status, old_key, new_key, old_isin, new_isin).  status:
      ok                      the stored key works, nothing to do
      repair                  stored key invalid, NSE has a different ISIN and Upstox accepts NSE_EQ|<new ISIN>
      unresolved_not_in_nse   stored key invalid and the symbol is not in NSE EQUITY_L (delisted or renamed)
      unresolved_same_isin    stored key invalid but NSE still lists the same ISIN (Upstox does not carry it)
      unresolved_new_invalid  NSE has a different ISIN but Upstox rejects that key too
      error                   the probe could not decide (rate limit, auth, network)
    """
    old_key = stored.get("instrument_key") or ""
    old_isin = stored.get("isin") or (old_key.split("|")[-1] if "|" in old_key else None)
    out = dict(symbol=symbol, status="", old_key=old_key, new_key=None, old_isin=old_isin, new_isin=None)
    p = probe_key(old_key, token, http_get, sleep=sleep)
    if p == "ok":
        out["status"] = "ok"
        return out
    if p != "invalid":
        out["status"] = "error"
        out["detail"] = p
        return out
    row = nse.get(symbol)
    if not row:
        out["status"] = "unresolved_not_in_nse"
        return out
    out["new_isin"] = row["isin"]
    if row["isin"] == old_isin:
        out["status"] = "unresolved_same_isin"
        return out
    new_key = f"NSE_EQ|{row['isin']}"
    p2 = probe_key(new_key, token, http_get, sleep=sleep)
    out["new_key"] = new_key
    out["status"] = "repair" if p2 == "ok" else ("unresolved_new_invalid" if p2 == "invalid" else "error")
    if out["status"] == "error":
        out["detail"] = p2
    return out


def plan_all(stored_docs: List[dict], token: str, nse: Dict[str, dict], http_get: Callable = None, sleep: Callable = time.sleep,
             gap_s: float = 0.12) -> List[dict]:
    res = []
    for d in stored_docs:
        sym = d.get("ipo_symbol")
        if not sym:
            continue
        res.append(plan_repair(sym, d, token, nse, http_get, sleep))
        sleep(gap_s)
    return res
