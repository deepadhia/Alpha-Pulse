#!/usr/bin/env python3
"""
scripts/backfill_volume_ratio.py

Backfills missing `volume_ratio` on listing-day signals and positions.

Strategy:
  1. If the signal doc has `volume_spike` (a float) but no `volume_ratio`,
     copy volume_spike -> volume_ratio (they are the same metric: current_vol / avg_vol).
  2. If neither exists, try to recompute from historical OHLCV candles:
       volume_ratio = breakout_day_volume / avg(prior 10 trading days volume)
  3. Update both the signal and the matching position doc.

Run:
    python scripts/backfill_volume_ratio.py            # dry-run (shows changes, no writes)
    python scripts/backfill_volume_ratio.py --apply    # apply writes to MongoDB
"""

import sys, os, argparse
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.chdir(os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

from db import db
import pandas as pd
from datetime import datetime, timezone

CUTOFF = datetime(2026, 7, 5, tzinfo=timezone.utc)

signals_col   = db["signals"]
positions_col = db["positions"]


def _fetch_vol_ratio(symbol: str, signal_date) -> float | None:
    """Recompute volume_ratio from OHLCV cache if possible."""
    try:
        # fetch_data lives in streamlined_ipo_scanner, not fetch.py
        from streamlined_ipo_scanner import fetch_data
        sig_date = pd.to_datetime(signal_date).date()

        # Try to get listing date to anchor data fetch
        listing_date = None
        try:
            from db import listing_data_col
            if listing_data_col is not None:
                doc = listing_data_col.find_one({"symbol": symbol}, {"listing_date": 1, "_id": 0})
                if doc:
                    listing_date = pd.to_datetime(doc.get("listing_date")).date()
        except Exception:
            pass

        start = listing_date or (sig_date - pd.Timedelta(days=60))
        df = fetch_data(symbol, start)
        if df is None or df.empty or "VOLUME" not in df.columns:
            # Fallback: try yfinance directly
            try:
                import yfinance as yf
                ticker = f"{symbol}.NS"
                ydf = yf.download(ticker, start=str(start), end=str(sig_date + pd.Timedelta(days=2)),
                                  progress=False, auto_adjust=True)
                if ydf.empty:
                    ticker = f"{symbol}.BO"
                    ydf = yf.download(ticker, start=str(start), end=str(sig_date + pd.Timedelta(days=2)),
                                      progress=False, auto_adjust=True)
                if not ydf.empty:
                    ydf = ydf.reset_index()
                    ydf.columns = [c[0] if isinstance(c, tuple) else c for c in ydf.columns]
                    ydf["DATE"] = pd.to_datetime(ydf["Date"])
                    df = ydf.rename(columns={"Open":"OPEN","High":"HIGH","Low":"LOW",
                                              "Close":"CLOSE","Volume":"VOLUME"})
            except Exception:
                return None

        if df is None or df.empty:
            return None

        df["DATE"] = pd.to_datetime(df["DATE"])
        # Find the breakout-day row
        bo_rows = df[df["DATE"].dt.date == sig_date]
        if bo_rows.empty:
            before = df[df["DATE"].dt.date <= sig_date]
            if before.empty:
                return None
            j = before.index[-1]
        else:
            j = bo_rows.index[0]

        if j < 2:
            return None

        # Use prior 10 bars (skip listing day row=0) as the avg baseline
        base = df.iloc[max(1, j - 10): j]
        if base.empty:
            return None
        avg_vol = base["VOLUME"].mean()
        if avg_vol <= 0:
            return None

        bo_vol = float(df["VOLUME"].iat[j if isinstance(j, int) else df.index.get_loc(j)])
        return round(bo_vol / avg_vol, 2)
    except Exception as e:
        print(f"    [WARN] Could not recompute vol_ratio for {symbol}: {e}")
        return None



def run_backfill(apply: bool):
    print("=" * 70)
    print(f"VOLUME RATIO BACKFILL {'(DRY RUN)' if not apply else '(APPLYING)'}")
    print("=" * 70)

    # ── 1. Signals ──────────────────────────────────────────────────────────
    sigs_missing = list(signals_col.find(
        {
            "entry_date": {"$gte": CUTOFF},
            "$or": [
                {"volume_ratio": {"$exists": False}},
                {"volume_ratio": None},
                {"volume_ratio": 0},
            ]
        },
        {"symbol": 1, "signal_date": 1, "signal_id": 1,
         "volume_spike": 1, "grade": 1, "_id": 1}
    ))
    # Also catch signals that used entry_date as datetime stored differently
    # (some signals use signal_date field not entry_date)
    sigs_missing2 = list(signals_col.find(
        {
            "signal_date": {"$gte": "2026-07-05"},
            "$or": [
                {"volume_ratio": {"$exists": False}},
                {"volume_ratio": None},
                {"volume_ratio": 0},
            ]
        },
        {"symbol": 1, "signal_date": 1, "signal_id": 1,
         "volume_spike": 1, "grade": 1, "_id": 1}
    ))

    # Deduplicate by _id
    seen_ids = set()
    all_sigs = []
    for s in sigs_missing + sigs_missing2:
        sid = str(s["_id"])
        if sid not in seen_ids:
            seen_ids.add(sid)
            all_sigs.append(s)

    print(f"\nSignals missing volume_ratio: {len(all_sigs)}")

    sig_updated = 0
    sig_recomputed = 0
    sig_failed = 0

    for s in all_sigs:
        sym = s.get("symbol", "?")
        sig_date = s.get("signal_date") or s.get("entry_date")
        spike = s.get("volume_spike")

        # Strategy 1: copy from volume_spike if it's a meaningful float
        vol_ratio = None
        source = None
        if spike and float(spike) > 0.1:
            vol_ratio = round(float(spike), 2)
            source = "volume_spike"
        else:
            # Strategy 2: recompute from OHLCV
            vol_ratio = _fetch_vol_ratio(sym, sig_date)
            if vol_ratio:
                source = "recomputed"
                sig_recomputed += 1

        if vol_ratio:
            flag = "→ WILL WRITE" if apply else "→ DRY RUN"
            print(f"  {sym:<14}  vol_ratio={vol_ratio:.2f}x  (from {source})  {flag}")
            if apply:
                signals_col.update_one(
                    {"_id": s["_id"]},
                    {"$set": {"volume_ratio": vol_ratio, "_backfilled": True}}
                )
            sig_updated += 1
        else:
            print(f"  {sym:<14}  [SKIP — no volume data available]")
            sig_failed += 1

    # ── 2. Positions ─────────────────────────────────────────────────────────
    print()
    pos_missing = list(positions_col.find(
        {
            "$or": [
                {"volume_ratio": {"$exists": False}},
                {"volume_ratio": None},
                {"volume_ratio": 0},
            ]
        },
        {"symbol": 1, "entry_date": 1, "signal_id": 1,
         "volume_spike": 1, "grade": 1, "_id": 1}
    ))
    print(f"Positions missing volume_ratio: {len(pos_missing)}")

    pos_updated = 0
    pos_failed = 0

    for p in pos_missing:
        sym = p.get("symbol", "?")
        entry_date = p.get("entry_date")
        spike = p.get("volume_spike")

        # Try to get vol_ratio from the matching signal first (most accurate)
        vol_ratio = None
        source = None
        sig_id = p.get("signal_id")
        if sig_id:
            sig_doc = signals_col.find_one({"signal_id": sig_id}, {"volume_ratio": 1, "volume_spike": 1, "_id": 0})
            if sig_doc:
                vr = sig_doc.get("volume_ratio") or sig_doc.get("volume_spike")
                if vr and float(vr) > 0.1:
                    vol_ratio = round(float(vr), 2)
                    source = "matched_signal"

        if not vol_ratio:
            if spike and float(spike) > 0.1:
                vol_ratio = round(float(spike), 2)
                source = "volume_spike"
            else:
                vol_ratio = _fetch_vol_ratio(sym, entry_date)
                if vol_ratio:
                    source = "recomputed"

        if vol_ratio:
            flag = "→ WILL WRITE" if apply else "→ DRY RUN"
            print(f"  {sym:<14}  vol_ratio={vol_ratio:.2f}x  (from {source})  {flag}")
            if apply:
                positions_col.update_one(
                    {"_id": p["_id"]},
                    {"$set": {"volume_ratio": vol_ratio, "_backfilled": True}}
                )
            pos_updated += 1
        else:
            print(f"  {sym:<14}  [SKIP — no volume data available]")
            pos_failed += 1

    # ── Summary ──────────────────────────────────────────────────────────────
    print()
    print("=" * 70)
    print("BACKFILL SUMMARY")
    print("=" * 70)
    print(f"  Signals  : {sig_updated} updated ({sig_recomputed} recomputed from OHLCV), {sig_failed} skipped")
    print(f"  Positions: {pos_updated} updated, {pos_failed} skipped")
    if not apply:
        print()
        print("  This was a DRY RUN. Run with --apply to commit changes.")
    else:
        print()
        print("  All changes written to MongoDB.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill missing volume_ratio on signals and positions")
    parser.add_argument("--apply", action="store_true",
                        help="Write changes to MongoDB. Without this flag, runs as a dry run.")
    args = parser.parse_args()
    run_backfill(apply=args.apply)
