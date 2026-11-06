# -*- coding: utf-8 -*-
"""
refresh_stale_caches.py -- find IPOs whose shared candle cache has stopped growing and rebuild it (core/cache_refresh.py explains the why).

  python refresh_stale_caches.py                      # DRY RUN: list stale caches (IPOs up to 400 days old), write nothing
  python refresh_stale_caches.py --apply              # refetch the full history from Upstox for each and replace its cache document
  python refresh_stale_caches.py --symbols AZADIND --apply
  python refresh_stale_caches.py --min-lag-days 7     # only caches at least 7 calendar days behind the last completed session

A document is replaced only if the refetched history passes core.cache_refresh.validate_frame (valid bars, ends at the last session, starts at the
listing date).  Run it after `repair_instrument_keys.py --apply`.  Needs MONGO_URI and UPSTOX_ACCESS_TOKEN.
"""

import argparse
import os
from datetime import datetime, timedelta, timezone

from core import cache_refresh as cr

IST = timezone(timedelta(hours=5, minutes=30))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write the rebuilt caches (default: dry run)")
    ap.add_argument("--symbols", nargs="*", default=None)
    ap.add_argument("--min-lag-days", type=int, default=4)
    ap.add_argument("--max-age-days", type=int, default=400)
    a = ap.parse_args(argv)

    import streamlined_ipo_scanner as sc          # imported first: it loads .env (UPSTOX_ACCESS_TOKEN, MONGO_URI)
    if not os.getenv("UPSTOX_ACCESS_TOKEN"):
        raise SystemExit("UPSTOX_ACCESS_TOKEN is missing")
    from db import daily_candles_col, listing_data_col, upsert_cached_candles
    if daily_candles_col is None or listing_data_col is None:
        raise SystemExit("MongoDB is not connected")

    now_ist = datetime.now(timezone.utc).astimezone(IST).replace(tzinfo=None)
    expected = cr.expected_last_session(now_ist, lambda d: d.strftime("%Y-%m-%d") in sc.NSE_HOLIDAYS)
    listings = list(listing_data_col.find({}, {"_id": 0, "symbol": 1, "listing_date": 1}))
    if a.symbols:
        want = {s.upper() for s in a.symbols}
        listings = [l for l in listings if l.get("symbol") in want]
    cache_last = {d["symbol"]: d.get("last_completed_date") for d in daily_candles_col.find({}, {"_id": 0, "symbol": 1, "last_completed_date": 1})}
    stale = cr.find_stale(listings, cache_last, expected, a.min_lag_days, a.max_age_days, today=now_ist.date())
    print(f"expected last completed session: {expected}; {len(stale)} stale cache(s) among IPOs up to {a.max_age_days} days old "
          f"({'APPLY' if a.apply else 'dry run'})")
    ok = 0
    for r in stale:
        res = cr.rebuild_symbol(r["symbol"], r["listing_date"], expected, now_ist, sc.fetch_from_upstox, upsert_cached_candles, apply=a.apply)
        ok += res["ok"]
        print(f"  {r['symbol']:<12} age {r['age']:>3}d  cache last {r['last']}  ->  {res['reason']}"
              + (f" ({res['rows']} bars, last {res['last']})" if res["ok"] else ""))
    print(f"\n{ok} of {len(stale)} {'rebuilt' if a.apply else 'rebuildable'}" + ("" if a.apply or not stale else "; rerun with --apply"))


if __name__ == "__main__":
    main()
