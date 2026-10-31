# -*- coding: utf-8 -*-
"""
repair_instrument_keys.py -- find and repair dead Upstox instrument keys (see core/instrument_keys.py for the why).

  python repair_instrument_keys.py                       # DRY RUN: probe every stored key, print what would change, write nothing
  python repair_instrument_keys.py --since 2026-01-01    # only symbols listed on/after this date (faster)
  python repair_instrument_keys.py --apply               # write the verified repairs (old key kept in `previous_instrument_key`)
  python repair_instrument_keys.py --symbols AZADIND BUILDPRO --apply

Only a "repair" row is ever written: the stored key was rejected by Upstox AND NSE lists a different ISIN AND Upstox accepts the new key.
Unresolved symbols are listed and left untouched.  After a repair the next scanner run refetches that symbol's full candle history (the cache is
treated as stale because it stopped at the date the old key died), so no cache edit is needed.  Needs MONGO_URI and UPSTOX_ACCESS_TOKEN.
"""

import argparse
import collections
import os
from datetime import datetime, timezone

import pandas as pd


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write verified repairs (default: dry run)")
    ap.add_argument("--since", default=None, help="only symbols with listing_date >= YYYY-MM-DD")
    ap.add_argument("--symbols", nargs="*", default=None, help="only these symbols")
    a = ap.parse_args(argv)

    from core import instrument_keys as ik
    from db import instrument_keys_col, listing_data_col
    token = os.getenv("UPSTOX_ACCESS_TOKEN")
    if not token:
        raise SystemExit("UPSTOX_ACCESS_TOKEN is missing")
    if instrument_keys_col is None:
        raise SystemExit("MongoDB is not connected")

    docs = list(instrument_keys_col.find({}, {"_id": 0}))
    if a.symbols:
        want = {s.upper() for s in a.symbols}
        docs = [d for d in docs if d.get("ipo_symbol") in want]
    if a.since:
        cut = pd.Timestamp(a.since).to_pydatetime()
        recent = {d["symbol"] for d in listing_data_col.find({"listing_date": {"$gte": cut}}, {"_id": 0, "symbol": 1})}
        docs = [d for d in docs if d.get("ipo_symbol") in recent]
    print(f"probing {len(docs)} stored keys ({'APPLY' if a.apply else 'dry run'})")
    nse = ik.load_nse_isins()
    res = ik.plan_all(docs, token, nse)
    print("status counts:", dict(collections.Counter(r["status"] for r in res)))
    for r in res:
        if r["status"] == "ok":
            continue
        print(f"  {r['symbol']:<12} {r['status']:<24} {r['old_key']} -> {r['new_key'] or '-'}" + (f"  ({r['detail']})" if r.get("detail") else ""))
    fixes = [r for r in res if r["status"] == "repair"]
    if not a.apply:
        print(f"\ndry run: {len(fixes)} repair(s) would be written; rerun with --apply")
        return
    now = datetime.now(timezone.utc)
    for r in fixes:
        instrument_keys_col.update_one({"ipo_symbol": r["symbol"]},
                                       {"$set": dict(instrument_key=r["new_key"], isin=r["new_isin"], previous_instrument_key=r["old_key"], repaired_at=now, updated_at=now)})
    print(f"\nwrote {len(fixes)} repair(s)")


if __name__ == "__main__":
    main()
