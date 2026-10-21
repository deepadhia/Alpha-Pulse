# -*- coding: utf-8 -*-
"""
shadow_score.py -- LOGGED-ONLY scoring of resting breakout orders after the session (read-only on MongoDB).

  python shadow_score.py                 score every logged resting order from earlier dates that has no score yet
  python shadow_score.py --date 2026-10-20
  python shadow_score.py --report        forward evidence: EXPLODE rate by score tercile for scored rows after the model train_end

order_sheet.py --log writes the features known before the open into logs/resting_orders.jsonl ("shadow" field).  The opening gap, the strongest
feature, is only known once the session has traded, so the final score is computed here from that day's completed daily candle and appended
to logs/shadow_scores.jsonl (one row per date and symbol, idempotent).  Nothing here changes an order, a slot or a stop.

Run with a python that has scikit-learn and pandas and can import db (read-only Mongo access).  Train first: train_shadow_model.py.
Outcome fields: crossed = the day high reached the trigger (listing high +1.5%); fill_proxy = max(open, trigger) x 1.003 when crossed and the open
is within the limit (+2.5%); gain10 = highest high of that day and the next 10 sessions over fill_proxy (needs those sessions in the cache).
"""
import argparse
import json
import os
from datetime import datetime

import numpy as np
import pandas as pd

from core.shadow_score import (SCORES_LOG, _dates, _naive, already_scored, append_jsonl, entry_features, load_model, market_state, score,
                               supply30)

ORDERS_LOG = os.path.join("logs", "resting_orders.jsonl")


def read_orders(path=ORDERS_LOG):
    rows = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    return rows


def load_frames(symbols, asof):
    from db import daily_candles_col, listing_data_col
    li = {}
    for d in listing_data_col.find({}, {"_id": 0, "symbol": 1, "listing_date": 1, "listing_day_high": 1, "listing_day_low": 1, "listing_day_volume": 1}):
        if d.get("symbol") and d.get("listing_date") is not None:
            li[d["symbol"]] = d
    lo = asof - pd.Timedelta(days=62)
    active = [s for s, d in li.items() if lo <= _naive(d["listing_date"]) <= asof - pd.Timedelta(days=3)]
    candles = {}
    for d in daily_candles_col.find({"symbol": {"$in": list(set(active) | set(symbols))}}, {"_id": 0, "symbol": 1, "candles": 1}):
        if not d.get("candles"):
            continue
        df = pd.DataFrame(d["candles"])
        df["DATE"] = pd.to_datetime(df["DATE"])
        for c in ("OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"):
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
        candles[d["symbol"]] = df.sort_values("DATE").reset_index(drop=True)
    return li, candles


def score_day(date_str, orders, meta, out_rows):
    asof = pd.Timestamp(date_str)
    syms = [o["symbol"] for o in orders]
    li, candles = load_frames(syms, asof)
    mkt = market_state({s: candles[s] for s in candles if s in li}, li, asof)
    sup = supply30([d["listing_date"] for d in li.values()], asof)
    for o in orders:
        s = o["symbol"]
        df = candles.get(s)
        base = dict(date=date_str, symbol=s, scored_at=datetime.now().isoformat(timespec="seconds"), model=(meta or {}).get("version"),
                    trigger=o.get("trigger"), limit=o.get("limit"))
        if df is None or s not in li:
            out_rows.append(dict(base, status="no_candles"))
            continue
        dts = _dates(df)
        before, day = df[dts < asof], df[dts == asof]
        if day.empty:
            out_rows.append(dict(base, status="session_not_in_cache_yet"))
            continue
        feats = entry_features(before, li[s], asof, mkt=mkt, supply=sup, open_price=float(day["OPEN"].iloc[0]))
        sc, status = score(feats, meta)
        ldh = float(li[s].get("listing_day_high") or before["HIGH"].iloc[0])
        trig = ldh * 1.015
        crossed = bool(day["HIGH"].iloc[0] >= trig and day["OPEN"].iloc[0] <= ldh * 1.025)
        fill = max(float(day["OPEN"].iloc[0]), trig) * 1.003 if crossed else None
        after = df[dts >= asof].head(11)
        gain10 = float((after["HIGH"].max() / fill - 1) * 100) if (crossed and len(after) >= 11) else None
        out_rows.append(dict(base, status=status, score=sc, crossed=crossed, fill_proxy=fill, gain10=gain10, features=feats))


def report(meta):
    if not os.path.exists(SCORES_LOG):
        print("no shadow scores logged yet")
        return
    rows = [json.loads(x) for x in open(SCORES_LOG, encoding="utf-8") if x.strip()]
    df = pd.DataFrame(rows)
    # re-read outcomes for rows scored before their 10 sessions were available is out of scope; report what is complete
    df = df[(df.status == "ok") & df.crossed.fillna(False) & df.gain10.notna()]
    train_end = pd.Timestamp((meta or {}).get("train_end", "1970-01-01"))
    df = df[pd.to_datetime(df.date) > train_end]
    print(f"scored, crossed and complete rows after the model train_end ({train_end.date()}): {len(df)}")
    if len(df) < 15:
        print("fewer than 15 rows: too few to say anything; keep logging (bar for a decision: 40 to 60 crossed rows)")
        return
    df["bucket"] = pd.qcut(df.score.rank(method="first"), 3, labels=["low", "mid", "high"])
    g = df.groupby("bucket", observed=True).agg(n=("gain10", "size"), explode_rate=("gain10", lambda x: float((x >= 20).mean() * 100)), mean_gain10=("gain10", "mean"))
    print(g.round(1).to_string())
    print(f"base EXPLODE rate {100 * (df.gain10 >= 20).mean():.1f}% (research base 13.6%, top-30% 21.7%)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None)
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    meta = load_model()
    if a.report:
        report(meta)
        return
    if meta is None:
        print("no model at models/shadow_gbm_v1.pkl: run train_shadow_model.py first (rows will be logged as no_model otherwise)")
    orders = read_orders()
    done = already_scored()
    today = datetime.now().strftime("%Y-%m-%d")
    by_date = {}
    for o in orders:
        d = o.get("date")
        if not d or (a.date and d != a.date) or (not a.date and d >= today) or (d, o.get("symbol")) in done:
            continue
        by_date.setdefault(d, []).append(o)
    if not by_date:
        print("nothing to score")
        return
    out = []
    for d, os_ in sorted(by_date.items()):
        score_day(d, os_, meta, out)
    n = append_jsonl(SCORES_LOG, out)
    print(f"appended {n} row(s) to {SCORES_LOG}")
    for r in out:
        print(f"  {r['date']} {r['symbol']:<12} {r.get('status'):<24} score={r.get('score')} crossed={r.get('crossed')}")


if __name__ == "__main__":
    main()
