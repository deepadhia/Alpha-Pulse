# -*- coding: utf-8 -*-
"""
research_minute_probe.py -- coverage probe for Upstox V3 1-minute history (MINUTE_BAR_TEST_PLAN.md, step 1).

Read-only: reads instrument_keys and the data.pkl snapshot, calls the Upstox historical-candle V3 endpoint, writes raw candles to a local folder.
Never prints or stores the access token.  Checks per symbol: HTTP status, candle count, first/last timestamp, minutes per session, agreement of the
summed minute volume with the daily candle volume, and the one-month request limit.

  .venv\\Scripts\\python.exe research_minute_probe.py --cache data.pkl --out-dir <folder>
"""
import argparse
import os
import pickle
import sys
import time

import pandas as pd
import requests

sys.path.insert(0, ".")
import db  # noqa: E402  (loads .env)

SYMBOLS = ["HTEL", "DEEPA", "AUGMONT", "STEAMHOUSE", "MVELECTRO", "OMNI", "ARFIN", "SIGNATURE", "AZAD", "NUVAMA"]
BASE = "https://api.upstox.com/v3/historical-candle"


def fetch(session, key, a, b, headers):
    url = f"{BASE}/{requests.utils.quote(key, safe='')}/minutes/1/{b:%Y-%m-%d}/{a:%Y-%m-%d}"
    t0 = time.time()
    r = session.get(url, headers=headers, timeout=60)
    info = dict(status=r.status_code, secs=round(time.time() - t0, 2), limit_hdr={k: v for k, v in r.headers.items() if "limit" in k.lower() or "remaining" in k.lower()})
    try:
        js = r.json()
    except Exception:
        js = {}
    if r.status_code == 200:
        c = js.get("data", {}).get("candles", [])
        df = pd.DataFrame(c, columns=["ts", "open", "high", "low", "close", "volume", "oi"]) if c else pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume", "oi"])
        return df, info
    info["error"] = str(js.get("errors") or js)[:300]
    return pd.DataFrame(), info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args()
    os.makedirs(a.out_dir, exist_ok=True)
    token = os.getenv("UPSTOX_ACCESS_TOKEN")
    if not token:
        raise SystemExit("UPSTOX_ACCESS_TOKEN not available after loading .env")
    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
    with open(a.cache, "rb") as fh:
        listing_map, candle_map = pickle.load(fh)
    sess = requests.Session()
    summary = []
    for sym in SYMBOLS:
        row = db.instrument_keys_col.find_one({"ipo_symbol": sym}, {"_id": 0})
        li = listing_map.get(sym)
        if not row or not li:
            print(f"{sym}: key={'yes' if row else 'NO'} listing={'yes' if li else 'NO'} -> skipped")
            summary.append(dict(symbol=sym, key=bool(row), listing=bool(li)))
            continue
        key = row["instrument_key"]
        ld = pd.Timestamp(li["listing_date"]).tz_localize(None)
        end = min(ld + pd.Timedelta(days=60), pd.Timestamp.today().normalize())
        parts, infos = [], []
        s = ld
        while s <= end:
            e = min(s + pd.Timedelta(days=27), end)
            df, info = fetch(sess, key, s, e, headers)
            infos.append((f"{s:%Y-%m-%d}..{e:%Y-%m-%d}", info))
            if len(df):
                parts.append(df)
            time.sleep(0.2)
            s = e + pd.Timedelta(days=1)
        m = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
        rec = dict(symbol=sym, key=True, listing=True, listing_date=str(ld.date()), requests=len(infos),
                   statuses=[i["status"] for _, i in infos], errors=[i.get("error") for _, i in infos if i.get("error")][:2])
        if len(m):
            m["ts"] = pd.to_datetime(m.ts)
            m = m.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
            m["day"] = m.ts.dt.tz_localize(None).dt.normalize() if m.ts.dt.tz is not None else m.ts.dt.normalize()
            per = m.groupby("day").agg(minutes=("ts", "size"), vol=("volume", "sum"), hi=("high", "max"), lo=("low", "min"), cl=("close", "last"))
            d = candle_map.get(sym)
            if d is not None and len(d):
                dd = d.copy()
                dd["day"] = pd.to_datetime(dd.DATE).dt.tz_localize(None).dt.normalize()
                dd = dd.set_index("day")
                j = per.join(dd[["VOLUME", "HIGH", "LOW", "CLOSE"]], how="inner")
                j["vol_ratio"] = j.vol / j.VOLUME
                rec.update(days_minute=len(per), days_daily_overlap=len(j),
                           median_minutes_per_day=int(per.minutes.median()), min_minutes=int(per.minutes.min()),
                           vol_ratio_median=round(float(j.vol_ratio.median()), 3), vol_ratio_p10=round(float(j.vol_ratio.quantile(0.1)), 3),
                           vol_ratio_p90=round(float(j.vol_ratio.quantile(0.9)), 3),
                           close_match_pct=round(float(((j.cl - j.CLOSE).abs() / j.CLOSE < 0.002).mean() * 100), 1),
                           daily_days_without_minutes=int(len(set(dd.index[(dd.index >= ld) & (dd.index <= end)]) - set(per.index))))
            rec.update(first=str(m.ts.min()), last=str(m.ts.max()), rows=len(m))
            m.drop(columns=["day"]).to_parquet(os.path.join(a.out_dir, f"{sym}_1m.parquet")) if False else m.drop(columns=["day"]).to_pickle(os.path.join(a.out_dir, f"{sym}_1m.pkl"))
        summary.append(rec)
        print(rec)

    # one-month limit test and a 2022 test
    print("\nOne-month limit test (HTEL, 45-day request):")
    row = db.instrument_keys_col.find_one({"ipo_symbol": "HTEL"}, {"_id": 0})
    df, info = fetch(sess, row["instrument_key"], pd.Timestamp("2026-09-01"), pd.Timestamp("2026-10-15"), headers)
    print("  status", info["status"], "rows", len(df), info.get("error", ""))
    # earliest listing with a key in the cache
    early = sorted(((pd.Timestamp(v["listing_date"]).tz_localize(None), k) for k, v in listing_map.items()), key=lambda x: x[0])[:6]
    print("\nEarliest listings in the cache, minute coverage near listing:")
    for ld, sym in early:
        row = db.instrument_keys_col.find_one({"ipo_symbol": sym}, {"_id": 0})
        if not row:
            print(f"  {sym} {ld.date()}: no instrument key")
            continue
        df, info = fetch(sess, row["instrument_key"], ld, ld + pd.Timedelta(days=20), headers)
        print(f"  {sym} {ld.date()}: status {info['status']} rows {len(df)} {info.get('error', '')[:120]}")
        time.sleep(0.2)
    print("\nrate-limit headers seen:", [i for _, i in infos][0].get("limit_hdr") if infos else None)
    pd.DataFrame(summary).to_csv(os.path.join(a.out_dir, "probe_summary.csv"), index=False)


if __name__ == "__main__":
    main()
