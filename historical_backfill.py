import os
import sys
import pandas as pd
from datetime import datetime, date, timezone
import json

# Add project root to path
sys.path.append(os.getcwd())

# Import necessary components from the main scanner
import streamlined_ipo_scanner as scanner
from streamlined_ipo_scanner import (
    logger, fetch_data, get_symbols_and_listing, 
    CONSOL_WINDOWS, VOL_MULT, ABS_VOL_MIN, MAX_PRNG,
    RESEARCH_COHORTS, GRADE_ORDER,
    compute_grade_hybrid, assign_grade, is_live_grade_allowed,
    smart_b_filters, smart_c_filters, reject_quick_losers,
    calculate_grade_based_stop_loss, MIN_LIVE_GRADE,
    MIN_DAYS_BETWEEN_SIGNALS,
    get_market_regime, classify_pattern_type
)

# ── Research Metadata Helpers ───────────────────────────────────────────────
# Pattern archetypes are observational labels ONLY — not separate strategies.

def build_decision_snapshot(pattern_type: str, market_regime: str, valid_cohorts: list,
                            grade: str, metrics: dict, entry: float, stop: float) -> dict:
    """Freeze the exact decision context at signal time to prevent hindsight drift."""
    return {
        "pattern_type": pattern_type,
        "market_regime": market_regime,
        "valid_cohorts": valid_cohorts,
        "grade": grade,
        "metrics_snapshot": metrics,
        "entry_at_signal": entry,
        "stop_at_signal": stop,
        "snapshot_ts": datetime.now(timezone.utc).isoformat()
    }

# Mock Telegram to avoid spam
def mock_send_telegram(msg):
    # print(f"[MOCK TELEGRAM] {msg[:100]}...")
    pass

scanner.send_telegram = mock_send_telegram

from db import signals_col, logs_col, has_active_position, signal_exists

def discover_listing_date(symbol):
    """Fallback: Try to find listing date from yfinance if missing in DB."""
    try:
        import yfinance as yf
        ticker = yf.Ticker(f"{symbol}.NS")
        hist = ticker.history(period="max")
        if not hist.empty:
            return hist.index[0].date()
    except Exception as e:
        print(f"  [Discovery] Failed for {symbol}: {e}")
    return None

def run_historical_backfill(lookback_days=180):
    print(f"Starting Historical Backfill (Lookback: {lookback_days} days)...")
    symbols, listing_map = get_symbols_and_listing()
    
    signals_found = 0
    symbols_processed = 0
    
    # We want to find signals from the past, so we scan the whole history
    for sym in symbols:
        symbols_processed += 1
        if symbols_processed % 20 == 0:
            print(f"  Processed {symbols_processed}/{len(symbols)} symbols...")
            
        ld = listing_map.get(sym)
        if not ld:
            # Fallback: Try to discover listing date dynamically (like the live scanner)
            ld = discover_listing_date(sym)
            if not ld: continue
        
        df = fetch_data(sym, ld)
        if df is None or df.empty: continue
        
        lhigh = df["HIGH"].iloc[0]
        
        # ── 1. Listing Day Breakout Detection (Momentum Anchor) ──
        # Check first 20 days for a breakout above Listing High
        for j in range(1, min(20, len(df))):
            close_j = df["CLOSE"].iat[j]
            open_j = df["OPEN"].iat[j]
            high_j = df["HIGH"].iat[j]
            vol_j = df["VOLUME"].iat[j]
            
            # Trigger: Close is significantly above Listing High
            # PRIORITY: We want the EARLIEST cross (The Pivot), not the LATEST spike.
            is_breakout = close_j > lhigh
            is_momentum = (close_j > open_j) or (vol_j > df["VOLUME"].iloc[0:j].mean() * 1.2)
            
            if is_breakout and is_momentum:
                # Calculate stop loss: Either listing low or 8% below entry, whichever is tighter
                entry_l = float(close_j)
                listing_low = float(df["LOW"].iloc[0:j+1].min())
                stop_l = max(listing_low, entry_l * 0.92)  # Max 8% risk for listing breakouts
                target_l = round(entry_l * 1.25, 2)
                signal_date_l = df['DATE'].iat[j].date()

                # ── Phase 2: Research Metadata ───────────────────────────────
                _vol_ratio_l = round(vol_j / df["VOLUME"].iloc[0:j].mean(), 2) if j > 0 else 1.0
                _metrics_l = {"prng": 0, "vol_ratio": _vol_ratio_l, "w": j}
                _cohorts_l = ["PERMISSIVE", "STRICT", "ULTRA_STRICT"]
                _pattern_l = classify_pattern_type("LISTING_BREAKOUT", j, _vol_ratio_l, 0)
                _regime_l  = get_market_regime(signal_date_l)
                _data_src  = getattr(df, 'attrs', {}).get('data_source', 'unknown')
                _snapshot_l = build_decision_snapshot(
                    _pattern_l, _regime_l, _cohorts_l, "LISTING_BREAKOUT", _metrics_l, entry_l, stop_l
                )
                # ─────────────────────────────────────────────────────────────

                sid_l = f"LISTING_{sym}_{signal_date_l.strftime('%Y%m%d')}"
                if signal_exists(sid_l):
                    # UPGRADE PATH: Repair legacy signals with Phase 2 research metadata
                    update_fields = {
                        "pattern_type": _pattern_l,
                        "market_regime": _regime_l,
                        "source_type": _data_src,
                        "data_quality": "CONFIRMED" if _data_src == "Upstox API" else "FALLBACK",
                        "decision_snapshot": _snapshot_l,
                        "valid_cohorts": _cohorts_l
                    }
                    signals_col.update_one({"signal_id": sid_l}, {"$set": update_fields})
                    continue

                days_old = (datetime.today().date() - signal_date_l).days
                status_l = "ACTIVE" if days_old <= 15 else "CLOSED"

                sig_doc_l = {
                    "signal_id": sid_l,
                    "symbol": sym,
                    "grade": "LISTING_BREAKOUT",
                    "entry_price": entry_l,
                    "breakout_close": entry_l,
                    "stop_loss": round(stop_l, 2),
                    "target_price": target_l,
                    "status": status_l,
                    "lifecycle_state": "POSITION_ACTIVE" if status_l == "ACTIVE" else "CLOSED",
                    "created_at": datetime.combine(signal_date_l, datetime.min.time()),
                    "metrics": _metrics_l,
                    "valid_cohorts": _cohorts_l,
                    # ── Phase 2 Research Metadata ──
                    "pattern_type": _pattern_l,
                    "market_regime": _regime_l,
                    "source_type": _data_src,
                    "data_quality": "CONFIRMED" if _data_src == "Upstox API" else "FALLBACK",
                    "decision_snapshot": _snapshot_l,
                    "source": "backfill_v2",
                    "_backfilled": True
                }
                if status_l == "CLOSED":
                    lp = float(df["CLOSE"].iloc[-1])
                    sig_doc_l["pnl_pct"] = round((lp / entry_l - 1) * 100, 2)
                    sig_doc_l["exit_price"] = lp
                    sig_doc_l["lifecycle_state"] = "CLOSED"

                signals_col.insert_one(sig_doc_l)
                signals_found += 1
                print(f"  [FOUND] Listing Day Breakout: {sym} on {signal_date_l} | Pattern: {_pattern_l} | Regime: {_regime_l}")
                break  # Found listing breakout, move to consolidation check
        
        for w in CONSOL_WINDOWS[::-1]:
            if len(df) < w: continue
            
            # FOR BACKFILL: Scan the entire history, not just the last 10 days
            # We only look at data within the lookback period
            start_idx = max(w, len(df) - lookback_days - 30) # Buffer for base formation
            
            for j in range(start_idx, len(df)):
                # 1. Define immediate base O(N)
                base_window = df.iloc[j-w:j]
                if len(base_window) < w: continue
                
                low = base_window["LOW"].min()
                high2 = base_window["HIGH"].max()
                
                # 2. Context Rule
                perf = (low - lhigh) / lhigh
                if not (0.08 <= -perf <= 0.35): continue
                
                # 3. Base Tightness
                prng = round((high2 - low) / low * 100, 2)
                if prng > MAX_PRNG: continue
                
                # 4. Volume Checks
                avgv = base_window["VOLUME"].mean()
                if avgv <= 0: continue
                vol_ratio = round(df["VOLUME"].iat[j] / avgv, 2)
                
                vol_ok = ((df["VOLUME"].iat[j] >= 2.5*avgv and df["VOLUME"].iloc[j-2:j+1].sum() >= 4*avgv) or
                         vol_ratio >= VOL_MULT or
                         (df["VOLUME"].iloc[j-2:j+1].sum() * df["CLOSE"].iat[j]) >= ABS_VOL_MIN)
                
                if not vol_ok: continue
                
                # 5. Breakout Confirmation
                if not (df["CLOSE"].iat[j] > high2 and df["CLOSE"].iat[j] > df["OPEN"].iat[j]):
                    continue
                
                score, metrics = compute_grade_hybrid(df, j, w, avgv)
                grade = assign_grade(score)

                # --- Cohort Validation (Multi-Bucket Research) ---
                valid_cohorts = []
                for name, config in RESEARCH_COHORTS.items():
                    # Window check
                    if w < config["min_window"]: continue
                    # Tightness check
                    if prng > config["max_prng"]: continue
                    # Grade check
                    if GRADE_ORDER.index(grade) < GRADE_ORDER.index(config["min_grade"]): continue
                    
                    # Follow-through (if available)
                    if j + 1 < len(df):
                        breakout_volume = df["VOLUME"].iat[j]
                        next_day_volume = df["VOLUME"].iat[j + 1]
                        if next_day_volume < config["vol_follow"] * breakout_volume:
                            continue
                    
                    valid_cohorts.append(name)

                if not valid_cohorts:
                    continue
                
                # Found a valid historical signal!
                signal_date = df['DATE'].iat[j]
                if hasattr(signal_date, 'date'): signal_date = signal_date.date()
                
                sid = f"CONSOL_{sym}_{signal_date.strftime('%Y%m%d')}_{w}"

                # Prepare signal doc
                entry = float(df["CLOSE"].iat[j])
                stop, _ = calculate_grade_based_stop_loss(entry, low, grade)
                target = entry + (entry - stop) * 2.0  # Default 2R

                # ── Phase 2: Research Metadata ───────────────────────────────
                _consol_metrics = {**metrics, "prng": prng, "vol_ratio": vol_ratio, "w": w}
                _pattern = classify_pattern_type(grade, (signal_date - df['DATE'].iloc[0].date()).days, vol_ratio, prng)
                _regime  = get_market_regime(signal_date)
                _data_src = getattr(df, 'attrs', {}).get('data_source', 'unknown')
                _snapshot = build_decision_snapshot(
                    _pattern, _regime, valid_cohorts, grade, _consol_metrics, entry, stop
                )
                # ─────────────────────────────────────────────────────────────

                if signal_exists(sid):
                    # UPGRADE PATH: Repair legacy signals with Phase 2 research metadata
                    update_fields = {
                        "pattern_type": _pattern,
                        "market_regime": _regime,
                        "source_type": _data_src,
                        "data_quality": "CONFIRMED" if _data_src == "Upstox API" else "FALLBACK",
                        "decision_snapshot": _snapshot,
                        "valid_cohorts": valid_cohorts
                    }
                    signals_col.update_one({"signal_id": sid}, {"$set": update_fields})
                    continue

                # Mark status as CLOSED for historical ones, or ACTIVE if very recent
                days_ago = (datetime.today().date() - signal_date).days
                status = "ACTIVE" if days_ago <= 15 else "CLOSED"

                # ── Phase 2: Research Metadata ───────────────────────────────
                _consol_metrics = {**metrics, "prng": prng, "vol_ratio": vol_ratio, "w": w}
                _pattern = classify_pattern_type(grade, (signal_date - df['DATE'].iloc[0].date()).days, vol_ratio, prng)
                _regime  = get_market_regime(signal_date)
                _data_src = getattr(df, 'attrs', {}).get('data_source', 'unknown')
                _snapshot = build_decision_snapshot(
                    _pattern, _regime, valid_cohorts, grade, _consol_metrics, entry, stop
                )
                # ─────────────────────────────────────────────────────────────

                sig_doc = {
                    "signal_id": sid,
                    "symbol": sym,
                    "grade": grade,
                    "entry_price": entry,
                    "breakout_close": entry,
                    "stop_loss": stop,
                    "target_price": target,
                    "status": status,
                    "lifecycle_state": "POSITION_ACTIVE" if status == "ACTIVE" else "CLOSED",
                    "created_at": datetime.combine(signal_date, datetime.min.time()),
                    "metrics": _consol_metrics,
                    "valid_cohorts": valid_cohorts,
                    # ── Phase 2 Research Metadata ──
                    "pattern_type": _pattern,
                    "market_regime": _regime,
                    "source_type": _data_src,
                    "data_quality": "CONFIRMED" if _data_src == "Upstox API" else "FALLBACK",
                    "decision_snapshot": _snapshot,
                    "source": "backfill_v2",
                    "_backfilled": True
                }

                # Calculate PnL for closed trades
                if status == "CLOSED":
                    last_price = float(df["CLOSE"].iloc[-1])
                    sig_doc["pnl_pct"] = round((last_price / entry - 1) * 100, 2)
                    sig_doc["exit_price"] = last_price
                    sig_doc["lifecycle_state"] = "CLOSED"

                signals_col.insert_one(sig_doc)
                signals_found += 1
                print(f"  [FOUND] Consol Breakout: {sym} on {signal_date} | Pattern: {_pattern} | Regime: {_regime}")

                break  # Found one signal for this symbol in this window, move to next symbol

    print(f"\nBackfill complete! Found and inserted {signals_found} valid historical signals.")


if __name__ == "__main__":
    run_historical_backfill(lookback_days=120)
