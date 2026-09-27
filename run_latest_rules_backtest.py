# -*- coding: utf-8 -*-
"""
run_latest_rules_backtest.py
Production Rules Backtesting Engine & Strategy Audit (v3.5.0)

Default configuration automatically aligns with live production v3.5.0 rules:
  - Clean-Cohort baseline (entry_date >= 2026-07-05) per AGENTS.md
  - Minimum volume floor: 150,000 shares
  - Volume spike multiplier: >= 1.5x (20d avg)
  - Upper 50% Candle Body Confirmation Rule: (Close - Low)/(High - Low) >= 0.50
  - Anti-Chasing 8% Max Extension Guard: Close <= listing_high * 1.08
  - Peak-Gated 14-Day Velocity Speed Gate: max_runup < 3.5% & PnL <= 0%
  - 40-Day Stagnant Position Guard: PnL <= 0% & max_runup < 8.0%
  - Dynamic trailing stop loss (10% risk cap, 3% IPO / 4% Consolidation activation)

Supports modular rule testing via CLI parameters:
  --all-history            : Run across full historical database (bypassing 2026-07-05 cutoff)
  --disable-vol-exit       : Disable Volume Exhaustion Early Exit
  --disable-stagnant-guard : Disable 40-day Stagnant Position Guard
  --disable-speed-gates    : Disable Speed Gates (14d/20d/21d)
  --trail-pnl-ipo FLOAT    : Set custom trailing activation PnL% for IPO (default: 3.0)
  --trail-pnl-consol FLOAT : Set custom trailing activation PnL% for Consolidation (default: 4.0)
  --vol-ratio FLOAT        : Set custom Volume Exhaustion ratio threshold (default: 0.45)
"""

import os
import sys
import argparse
import pandas as pd
import numpy as np
from datetime import datetime
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

def load_all_ipo_candles():
    from db import db, daily_candles_col
    if db is None or daily_candles_col is None:
        logger.error("MongoDB not connected!")
        return {}

    logger.info("Loading cached daily candles from MongoDB...")
    docs = list(daily_candles_col.find({}, {"_id": 0, "symbol": 1, "candles": 1}))
    
    symbol_data = {}
    for doc in docs:
        sym = doc.get("symbol")
        candles = doc.get("candles")
        if not sym or not candles or len(candles) < 20:
            continue
        
        df = pd.DataFrame(candles)
        df["DATE"] = pd.to_datetime(df["DATE"])
        for col in ["OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
        
        df = df.sort_values("DATE").reset_index(drop=True)
        symbol_data[sym] = df

    logger.info(f"Loaded valid OHLCV candle data for {len(symbol_data)} symbols.")
    return symbol_data


def simulate_latest_v35_system(df, symbol, config):
    trades = []
    if len(df) < 25:
        return trades

    listing_high = df["HIGH"].iloc[:3].max()
    
    in_trade = False
    entry_price = 0.0
    entry_idx = 0
    entry_date = None
    trailing_stop = 0.0
    max_runup = 0.0
    grade = "LISTING_BREAKOUT"  # All signals in this backtest are listing-high breakouts
    stop_pct = 0.10             # 10% risk cap
    trail_activation = config["trail_pnl_ipo"]

    # Clean-cohort date filter: default >= 2026-07-05 (v3.3.0+ clean cohort cutoff per AGENTS.md)
    start_date_filter = config.get("start_date")
    if start_date_filter:
        start_date_filter = pd.to_datetime(start_date_filter)

    # Absolute volume floor: default 150,000 shares (v3.3.0+ live rule)
    min_volume_filter = config.get("min_volume", 150000)
    # Volume spike multiplier: default 1.5x (AGENTS.md Section 5 Rule 5)
    min_vol_mult = config.get("min_vol_mult", 1.5)
    # Upper 50% candle body rule: default 0.50 (AGENTS.md Section 5 Rule 1)
    min_close_loc = config.get("min_close_loc", 0.50)
    # Anti-chasing max extension: default 8.0% (AGENTS.md Section 5 Rule 3)
    max_extension_pct = config.get("max_extension_pct", 8.0)

    for i in range(5, len(df)):
        current_date = df["DATE"].iat[i]
        close = df["CLOSE"].iat[i]
        high = df["HIGH"].iat[i]
        low = df["LOW"].iat[i]
        vol = df["VOLUME"].iat[i]

        if not in_trade:
            # Entry on Close > Listing High
            if close > listing_high:
                # 1. Clean-cohort date gate
                if start_date_filter and current_date < start_date_filter:
                    continue
                # 2. Volume floor gate (150,000 shares)
                if vol < min_volume_filter:
                    continue
                # 3. Volume spike gate (>= 1.5x 20-day average volume)
                lookback_vols = df["VOLUME"].iloc[max(0, i-20):i]
                avg_vol = lookback_vols.mean() if len(lookback_vols) > 0 else 0
                vol_spike = (vol / avg_vol) if avg_vol > 0 else 1.0
                if vol_spike < min_vol_mult:
                    continue
                # 4. Upper 50% Candle Body Confirmation Gate (v3.5.0 Standard)
                candle_range = high - low
                if candle_range > 0:
                    close_location = (close - low) / candle_range
                    if close_location < min_close_loc:
                        continue
                # 5. Anti-Chasing Max Extension Guard (v3.5.0 Standard: <= 8.0% above pivot)
                ext_pct = ((close - listing_high) / listing_high) * 100.0
                if ext_pct > max_extension_pct:
                    continue

                in_trade = True
                entry_price = close
                entry_idx = i
                entry_date = current_date
                trailing_stop = round(entry_price * (1 - stop_pct), 2)
                max_runup = 0.0
                # trail_activation is dynamically initialized per-trade at entry
                trail_activation = config["trail_pnl_ipo"]
                continue

        if in_trade:
            days_held = (i - entry_idx)
            pnl_pct = (close - entry_price) / entry_price * 100.0
            current_runup = (high - entry_price) / entry_price * 100.0
            max_runup = max(max_runup, current_runup)
            
            # Trail Stop Adjustment
            if pnl_pct >= trail_activation:
                candidate_stop = round(close * (1 - stop_pct), 2)
                if candidate_stop > trailing_stop:
                    trailing_stop = candidate_stop

            exit_reason = None
            exit_price = close

            # 1. Trailing Stop Loss Check
            if low <= trailing_stop:
                exit_reason = "Trailing Stop Loss"
                exit_price = trailing_stop

            # 2. Peak-Gated 14-Day Velocity Speed Gate (v3.5.0 Standard)
            # Cut dead-money trades held >= 14 days with non-positive PnL and peak runup < 3.5%
            elif config["enable_speed_gates"] and (days_held >= 14 and pnl_pct <= 0.0 and max_runup < 3.5):
                exit_reason = "Time Stop - Dead Money (14-Day Velocity Gate)"
                exit_price = close

            # 3. 20d IPO Patience Speed Gate
            elif config["enable_speed_gates"] and (grade == "LISTING_BREAKOUT" and days_held >= 20 and max_runup < 4.0):
                exit_reason = "Time Stop - IPO Dead Money (20d)"
                exit_price = close
            elif config["enable_speed_gates"] and (grade != "LISTING_BREAKOUT" and days_held >= 21 and max_runup < 5.0):
                exit_reason = "Time Stop - Consolidation Dead Money (21d)"
                exit_price = close

            # 4. Volume Exhaustion Exit (Day 15+, PnL -3% to 5%, max_runup < 8%)
            elif config["enable_vol_exit"] and (days_held >= 15) and (-3.0 <= pnl_pct < 5.0) and (max_runup < 8.0):
                post_entry_vols = df["VOLUME"].iloc[entry_idx+1:i+1]
                if len(post_entry_vols) >= 16:
                    baseline_vol = post_entry_vols.iloc[:11].mean()
                    recent_vol = post_entry_vols.iloc[-5:].mean()
                    if baseline_vol >= 50000:
                        vol_ratio = recent_vol / baseline_vol
                        if vol_ratio < config["vol_ratio"]:
                            exit_reason = f"Volume Exhaustion (ratio: {vol_ratio:.2f})"
                            exit_price = close

            # 5. Legacy Fallback Stops (30d -5%, 60d -8%)
            elif (days_held > 30 and close < entry_price * 0.95):
                exit_reason = "Time Stop -5% (30d)"
                exit_price = close
            elif (days_held > 60 and close < entry_price * 0.92):
                exit_reason = "Time Stop -8% (60d)"
                exit_price = close

            # 6. Secondary Stagnant Position Guard (v3.5.0 Standard: 40d, PnL <= 0% and peak < 8%)
            elif config["enable_stagnant_guard"] and (days_held >= 40 and pnl_pct <= 0.0 and max_runup < 8.0):
                exit_reason = "Time Stop - Stagnant Guard (40d)"
                exit_price = close

            if exit_reason:
                final_pnl = (exit_price - entry_price) / entry_price * 100.0
                trades.append({
                    "symbol": symbol,
                    "entry_date": entry_date.strftime("%Y-%m-%d"),
                    "exit_date": current_date.strftime("%Y-%m-%d"),
                    "days_held": days_held,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "pnl_pct": final_pnl,
                    "max_runup_pct": max_runup,
                    "exit_reason": exit_reason,
                    "win": final_pnl > 0
                })
                in_trade = False

    return trades


def main(argv=None):
    parser = argparse.ArgumentParser(description="Production Rules Backtesting Engine & Strategy Audit (v3.5.0)")
    parser.add_argument("--disable-vol-exit", action="store_true", help="Disable Volume Exhaustion Early Exit")
    parser.add_argument("--disable-stagnant-guard", action="store_true", help="Disable 40-day Stagnant Guard")
    parser.add_argument("--disable-speed-gates", action="store_true", help="Disable Speed Gates (14d/20d/21d)")
    parser.add_argument("--trail-pnl-ipo", type=float, default=3.0, help="Trailing start PnL%% for IPO (default: 3.0)")
    parser.add_argument("--trail-pnl-consol", type=float, default=4.0, help="Trailing start PnL%% for Consolidation (default: 4.0)")
    parser.add_argument("--vol-ratio", type=float, default=0.45, help="Volume Exhaustion ratio threshold (default: 0.45)")
    parser.add_argument(
        "--start-date", type=str, default="2026-07-05",
        help="Only count entries on or after this date (YYYY-MM-DD). "
             "Default: '2026-07-05' (Clean Cohort Cutoff per AGENTS.md). Pass 'all' or --all-history to disable."
    )
    parser.add_argument(
        "--all-history", action="store_true",
        help="Bypass the clean-cohort start date filter and run across all historical data."
    )
    parser.add_argument(
        "--min-volume", type=int, default=150000,
        help="Minimum breakout-day volume (shares). Default: 150000 (Mandatory live trading rule)."
    )
    parser.add_argument(
        "--min-vol-mult", type=float, default=1.5,
        help="Minimum breakout volume spike over 20-day avg volume. Default: 1.5x (AGENTS.md rule)."
    )
    parser.add_argument(
        "--min-close-loc", type=float, default=0.50,
        help="Minimum candle close location in daily range: (Close - Low)/(High - Low). Default: 0.50 (Upper 50%% Body Rule)."
    )
    parser.add_argument(
        "--max-extension-pct", type=float, default=8.0,
        help="Maximum entry extension above listing high. Default: 8.0%% (Anti-Chasing Guard)."
    )

    if argv is None:
        argv = sys.argv[1:]
    args = parser.parse_args(argv)

    start_date = None if args.all_history or (args.start_date and args.start_date.lower() in ("all", "none")) else args.start_date

    config = {
        "enable_vol_exit": not args.disable_vol_exit,
        "enable_stagnant_guard": not args.disable_stagnant_guard,
        "enable_speed_gates": not args.disable_speed_gates,
        "trail_pnl_ipo": args.trail_pnl_ipo,
        "trail_pnl_consol": args.trail_pnl_consol,
        "vol_ratio": args.vol_ratio,
        "start_date": start_date,
        "min_volume": args.min_volume,
        "min_vol_mult": args.min_vol_mult,
        "min_close_loc": args.min_close_loc,
        "max_extension_pct": args.max_extension_pct,
    }

    symbol_data = load_all_ipo_candles()
    if not symbol_data:
        print("No candle data available.")
        return

    all_trades = []
    for sym, df in symbol_data.items():
        t_list = simulate_latest_v35_system(df, sym, config)
        all_trades.extend(t_list)

    if not all_trades:
        print("No trades generated.")
        return

    tdf = pd.DataFrame(all_trades)
    
    total_trades = len(tdf)
    wins = tdf["win"].sum()
    losses = total_trades - wins
    win_rate = (wins / total_trades) * 100.0
    
    avg_pnl = tdf["pnl_pct"].mean()
    total_pnl = tdf["pnl_pct"].sum()
    avg_days = tdf["days_held"].mean()
    
    gross_wins = tdf[tdf["pnl_pct"] > 0]["pnl_pct"].sum()
    gross_losses = abs(tdf[tdf["pnl_pct"] < 0]["pnl_pct"].sum())
    profit_factor = (gross_wins / gross_losses) if gross_losses > 0 else 0.0
    
    max_win = tdf["pnl_pct"].max()
    max_loss = tdf["pnl_pct"].min()
    
    # Exit Reason Breakdown
    reasons = tdf["exit_reason"].apply(lambda r: r.split(" (ratio")[0]).value_counts()

    print("\n" + "=" * 80)
    print("        SYSTEM PERFORMANCE AUDIT: STRATEGY BACKTEST ENGINE (v3.5.0)")
    print("=" * 80)
    print("Active Configuration (Live Production Rules Aligned):")
    print(f"   * Clean-Cohort Start Date : {config['start_date'] or 'ALL (unfiltered)'}")
    print(f"   * Volume Floor (breakout) : {config.get('min_volume', 0):,} shares")
    print(f"   * Volume Spike Multiplier : {config.get('min_vol_mult', 1.5):.2f}x (20d avg)")
    print(f"   * Upper 50% Body Rule     : >= {config.get('min_close_loc', 0.50)*100:.0f}% of daily range")
    print(f"   * Anti-Chasing Max Ext.   : <= {config.get('max_extension_pct', 8.0):.1f}% above pivot")
    print(f"   * Speed Gates Enabled     : {config['enable_speed_gates']} (14d Velocity / 20d IPO / 21d Consol)")
    print(f"   * Stagnant Guard Enabled  : {config['enable_stagnant_guard']} (40d: PnL <= 0% & Peak < 8%)")
    print(f"   * Volume Exit Enabled     : {config['enable_vol_exit']} (threshold: {config['vol_ratio']})")
    print(f"   * Trailing Activation     : {config['trail_pnl_ipo']}% (IPO) / {config['trail_pnl_consol']}% (Consol)")
    print("-" * 80)
    print(f"Total Trades Executed    : {total_trades}")
    print(f"Winning Trades          : {wins} ({win_rate:.2f}%)")
    print(f"Losing Trades           : {losses} ({100 - win_rate:.2f}%)")
    print(f"Profit Factor           : {profit_factor:.2f}")
    print(f"Average Return per Trade : {avg_pnl:+.2f}%")
    print(f"Max Single Winner       : +{max_win:.2f}%")
    print(f"Max Single Loser        : {max_loss:.2f}%")
    print(f"Average Holding Period   : {avg_days:.1f} days")
    print("-" * 80)
    print("Exit Reason Breakdown:")
    for reason, count in reasons.items():
        pct = (count / total_trades) * 100.0
        print(f"   * {reason:<38}: {count:3d} ({pct:.1f}%)")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    main()
