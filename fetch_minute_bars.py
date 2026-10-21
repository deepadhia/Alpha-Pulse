# -*- coding: utf-8 -*-
"""
fetch_minute_bars.py -- bulk, resumable download of Upstox V3 1-minute candles for each IPO's first 60 calendar days.

Read-only against MongoDB (instrument_keys) and data.pkl; writes ONLY local files under --out-dir (default data_minute/):
  <SYMBOL>_1m.pkl   DataFrame(ts, open, high, low, close, volume, oi)
  manifest.csv      one row per symbol: stage, status, rows, sessions, http statuses, error
Order: stage 1 = symbols in the policy-P trade file, stage 2 = every other listing since 2022-01-01.
Resumable: a symbol whose pickle exists is skipped.  Back-off: HTTP 429 and 5xx retry with exponential delay; HTTP 401 aborts (token expired).
Never prints or stores the access token.  Nothing is written to MongoDB.

  .venv\\Scripts\\python.exe fetch_minute_bars.py --cache data.pkl --trades reports/2026-10-17/gate_replay_V0.csv
"""
import argparse
import csv
import os
import pickle
import sys
import time

import pandas as pd
import requests

sys.path.insert(0, ".")
import db  # noqa: E402  (loads .env)

BASE = "https://api.upstox.com/v3/historical-candle"
CHUNK_DAYS = 28          # the API allows one month per request for 1-minute candles
WINDOW_DAYS = 60         # the engine's age window
PAUSE = 0.25


class TokenExpired(Exception):
    pass


def get_chunk(sess, key, a, b, headers):
    url = f"{BASE}/{requests.utils.quote(key, safe='')}/minutes/1/{b:%Y-%m-%d}/{a:%Y-%m-%d}"
    delay = 1.0
    last = None
    for attempt in range(7):
        try:
            r = sess.get(url, headers=headers, timeout=60)
        except requests.RequestException as e:
            last = f"net:{type(e).__name__}"
            time.sleep(delay)
            delay = min(delay * 2, 60)
            continue
        if r.status_code == 401:
            raise TokenExpired()
        if r.status_code == 429 or r.status_code >= 500:
            last = f"http{r.status_code}"
            ra = r.headers.get("Retry-After")
            time.sleep(float(ra) if ra and ra.replace('.', '', 1).isdigit() else delay)
            delay = min(delay * 2, 60)
            continue
        try:
            js = r.json()
        except Exception:
            js = {}
        if r.status_code == 200:
            c = js.get("data", {}).get("candles", [])
            return r.status_code, pd.DataFrame(c, columns=["ts", "open", "high", "low", "close", "volume", "oi"]), None
        return r.status_code, pd.DataFrame(), str(js.get("errors") or js)[:200]
    return 0, pd.DataFrame(), f"gave up after retries ({last})"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--trades", required=True)
    ap.add_argument("--out-dir", default="data_minute")
    ap.add_argument("--since", default="2022-01-01")
    ap.add_argument("--limit", type=int, default=0, help="stop after N symbols (testing)")
    a = ap.parse_args()
    os.makedirs(a.out_dir, exist_ok=True)
    token = os.getenv("UPSTOX_ACCESS_TOKEN")
    if not token:
        raise SystemExit("UPSTOX_ACCESS_TOKEN not available after loading .env")
    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
    with open(a.cache, "rb") as fh:
        listing_map, candle_map = pickle.load(fh)
    p_syms = list(dict.fromkeys(pd.read_csv(a.trades).symbol))
    keys = {r["ipo_symbol"]: r["instrument_key"] for r in db.instrument_keys_col.find({}, {"_id": 0, "ipo_symbol": 1, "instrument_key": 1})}
    since = pd.Timestamp(a.since)
    rest = sorted((s for s, v in listing_map.items()
                   if s not in set(p_syms) and pd.Timestamp(v["listing_date"]).tz_localize(None) >= since),
                  key=lambda s: listing_map[s]["listing_date"])
    plan = [(1, s) for s in p_syms] + [(2, s) for s in rest]
    if a.limit:
        plan = plan[:a.limit]
    print(f"plan: stage1 {len(p_syms)} policy-P symbols, stage2 {len(rest)} others, total {len(plan)}", flush=True)
    man_path = os.path.join(a.out_dir, "manifest.csv")
    new_file = not os.path.exists(man_path)
    mf = open(man_path, "a", newline="", encoding="utf-8")
    w = csv.writer(mf)
    if new_file:
        w.writerow(["stage", "symbol", "status", "rows", "sessions", "requests", "http", "error"])
    sess = requests.Session()
    today = pd.Timestamp.today().normalize()
    t0 = time.time()
    done = skipped = failed = 0
    for n, (stage, sym) in enumerate(plan, 1):
        path = os.path.join(a.out_dir, f"{sym}_1m.pkl")
        if os.path.exists(path):
            skipped += 1
            continue
        key = keys.get(sym)
        li = listing_map.get(sym)
        if not key or not li:
            w.writerow([stage, sym, "no_key" if not key else "no_listing", 0, 0, 0, "", ""])
            mf.flush()
            failed += 1
            continue
        ld = pd.Timestamp(li["listing_date"]).tz_localize(None)
        end = min(ld + pd.Timedelta(days=WINDOW_DAYS), today)
        parts, https, err = [], [], None
        s = ld
        try:
            while s <= end:
                e = min(s + pd.Timedelta(days=CHUNK_DAYS - 1), end)
                st, df, er = get_chunk(sess, key, s, e, headers)
                https.append(st)
                if er:
                    err = er
                if len(df):
                    parts.append(df)
                time.sleep(PAUSE)
                s = e + pd.Timedelta(days=1)
        except TokenExpired:
            print("HTTP 401: access token expired; stopping.  Refresh the token and rerun (resumes).", flush=True)
            mf.close()
            sys.exit(2)
        ok = all(h == 200 for h in https)
        if parts:
            m = pd.concat(parts, ignore_index=True)
            m["ts"] = pd.to_datetime(m.ts)
            m = m.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
            sessions = int(m.ts.dt.strftime("%Y-%m-%d").nunique())
        else:
            m, sessions = pd.DataFrame(), 0
        if ok:   # an empty-but-200 result is saved too, so reruns skip it (it is recorded as zero rows)
            m.to_pickle(path)
            done += 1
        else:
            failed += 1
        w.writerow([stage, sym, "ok" if ok else "error", len(m), sessions, len(https), "|".join(map(str, https)), err or ""])
        mf.flush()
        if n % 25 == 0:
            el = time.time() - t0
            print(f"[{n}/{len(plan)}] stage {stage} {sym}: done {done} skipped {skipped} failed {failed}  elapsed {el / 60:.1f} min", flush=True)
    mf.close()
    print(f"FINISHED: done {done}, skipped (already present) {skipped}, failed {failed}, elapsed {(time.time() - t0) / 60:.1f} min", flush=True)


if __name__ == "__main__":
    main()
