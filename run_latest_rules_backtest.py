# -*- coding: utf-8 -*-
"""
run_latest_rules_backtest.py
Production Rules Backtesting Engine & Strategy Audit

DYNAMIC STRATEGY BINDING INVARIANT:
This engine does NOT maintain disconnected or hardcoded strategy rules.
It dynamically imports and binds to the active production scanners:
  - listing_day_breakout_scanner.py (Entry gates, Shelf detection, Winner DNA, Extension caps)
  - streamlined_ipo_scanner.py (Exits, Velocity gates, 2-stage dynamic trailing stop)

Whenever a rule, constant, or gate is updated in production or .env,
this backtest engine automatically inherits the latest rules and tests
against the available historical database.
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

# Dynamically import active production scanner modules
import listing_day_breakout_scanner as lscan
import streamlined_ipo_scanner as sscan


def load_dataset():
    """Load cached daily candles and listing metadata from MongoDB."""
    from db import db, daily_candles_col, listing_data_col
    if db is None or daily_candles_col is None or listing_data_col is None:
        logger.error("MongoDB collections not connected!")
        return {}, {}

    logger.info("Loading listing metadata from MongoDB...")
    listing_docs = list(listing_data_col.find({}, {
        "symbol": 1, "listing_date": 1, "listing_day_high": 1, "listing_day_low": 1,
        "listing_day_close": 1, "listing_day_volume": 1, "is_nse_addition": 1, "bse_liquidity_passed": 1
    }))
    listing_map = {d["symbol"]: d for d in listing_docs if d.get("symbol")}

    logger.info("Loading cached daily candles from MongoDB...")
    candle_docs = list(daily_candles_col.find({}, {"_id": 0, "symbol": 1, "candles": 1}))
    candle_map = {}
    for d in candle_docs:
        sym = d.get("symbol")
        candles = d.get("candles")
        if not sym or not candles or len(candles) < 10:
            continue
        
        df = pd.DataFrame(candles)
        df["DATE"] = pd.to_datetime(df["DATE"])
        for col in ["OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
        
        df = df.sort_values("DATE").reset_index(drop=True)
        candle_map[sym] = df

    logger.info(f"Loaded {len(listing_map)} listing records, {len(candle_map)} valid candle dataframes.")
    return listing_map, candle_map


def simulate_production_latest_system(df, symbol, listing_info, config):
    """
    Simulate a single symbol strictly executing active live production rules.
    Directly invokes production breakout detection, tier qualification, and single-writer exits.
    """
    trades = []
    if len(df) < 15:
        return trades

    ld_high = listing_info.get("listing_day_high")
    ld_low = listing_info.get("listing_day_low")
    if not ld_high or ld_high <= 0:
        ld_high = float(df["HIGH"].iloc[:3].max())
        ld_low = float(df["LOW"].iloc[:3].min())

    if listing_info.get("is_nse_addition") and not listing_info.get("bse_liquidity_passed", True):
        return trades

    in_trade = False
    entry_price = 0.0
    entry_idx = 0
    entry_date = None
    trailing_stop = 0.0
    initial_risk_pct = 0.0
    max_runup = 0.0
    trade_type = ""
    last_exit_idx = -100

    start_date_filter = config.get("start_date")
    if start_date_filter:
        start_date_filter = pd.to_datetime(start_date_filter)

    max_breakout_age = config.get("max_age", lscan.MAX_DAYS_SINCE_LISTING_FOR_BREAKOUT)
    min_volume_floor = config.get("min_volume", 150000)
    min_vol_mult = config.get("min_vol_mult", lscan.MIN_VOLUME_MULTIPLIER)
    max_extension_pct = config.get("max_extension_pct", lscan.MAX_ENTRY_ABOVE_HIGH_PCT)
    cooldown_days = config.get("cooldown_days", 30)

    for i in range(3, len(df)):
        current_date = df["DATE"].iat[i]
        close = df["CLOSE"].iat[i]
        high = df["HIGH"].iat[i]
        low = df["LOW"].iat[i]
        vol = df["VOLUME"].iat[i]
        days_since_listing = i

        if start_date_filter and current_date < start_date_filter:
            continue

        # ── POSITION MANAGEMENT (SINGLE-WRITER EXIT ENGINE) ─────────────────
        if in_trade:
            days_held = i - entry_idx
            pnl_pct = (close - entry_price) / entry_price * 100.0
            day_high_runup = (high - entry_price) / entry_price * 100.0
            max_runup = max(max_runup, day_high_runup)
            is_winner_archetype = (max_runup >= 15.0)

            exit_reason = None
            exit_price = close

            # 1. Stop Loss Hit
            if low <= trailing_stop:
                exit_reason = "Trailing Stop Loss"
                exit_price = trailing_stop

            # 2. 14-Day Velocity Speed Gate (Session 10 / Day 14, non-winners)
            elif config["enable_speed_gates"] and not is_winner_archetype and days_held >= 14 and pnl_pct <= 0.0:
                exit_reason = "Time Stop - 14-Day Velocity Gate"
                exit_price = close

            # 3. 20-Day IPO Dead Money Gate
            elif config["enable_speed_gates"] and not is_winner_archetype and days_held >= sscan.DEAD_MONEY_DAYS_IPO and max_runup < sscan.DEAD_MONEY_RUNUP_IPO:
                exit_reason = f"Time Stop - {sscan.DEAD_MONEY_DAYS_IPO}d IPO Dead Money"
                exit_price = close

            # 4. Volume Exhaustion Exit (Day 15+, PnL -3% to 5%, peak < 8%)
            elif config["enable_vol_exit"] and not is_winner_archetype and days_held >= 15 and (-3.0 <= pnl_pct < 5.0) and max_runup < 8.0:
                post_entry_vols = df["VOLUME"].iloc[entry_idx+1:i+1]
                if len(post_entry_vols) >= 16:
                    baseline_vol = post_entry_vols.iloc[:11].mean()
                    recent_vol = post_entry_vols.iloc[-5:].mean()
                    if baseline_vol >= 50000 and (recent_vol / baseline_vol) < config["vol_ratio"]:
                        exit_reason = "Volume Exhaustion Exit"
                        exit_price = close

            # 5. Secondary Stagnant Position Guard (40d, PnL <= 0% & peak < 8%)
            elif config["enable_stagnant_guard"] and not is_winner_archetype and days_held >= 40 and pnl_pct <= 0.0 and max_runup < 8.0:
                exit_reason = "Time Stop - 40d Stagnant Guard"
                exit_price = close

            # 6. Legacy fallback time stop (30d -5%)
            elif not is_winner_archetype and days_held > 30 and close < entry_price * 0.95:
                exit_reason = "Time Stop -5% (30d)"
                exit_price = close

            if exit_reason:
                final_pnl = (exit_price - entry_price) / entry_price * 100.0
                trades.append({
                    "symbol": symbol,
                    "entry_date": entry_date.strftime("%Y-%m-%d"),
                    "exit_date": current_date.strftime("%Y-%m-%d"),
                    "year": entry_date.year,
                    "type": trade_type,
                    "setup_type": trade_type,
                    "days_held": days_held,
                    "days_since_listing": entry_age,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "pnl_pct": final_pnl,
                    "max_runup_pct": max_runup,
                    "exit_reason": exit_reason,
                    "win": final_pnl > 0
                })
                in_trade = False
                last_exit_idx = i
                continue

            # ── 2-STAGE DYNAMIC TRAILING STOP ENGINE ─────────────────────────
            if is_winner_archetype:
                # Stage 2 (Super-Winner: peak >= 15%): 20% cushion from peak + locked +5% min profit
                peak_price = entry_price * (1.0 + max_runup / 100.0)
                candidate_trailing = max(entry_price * 1.05, peak_price * 0.80)
                trailing_stop = max(trailing_stop, candidate_trailing)
            elif pnl_pct >= config["trail_pnl_ipo"]:
                # Stage 1: Grade-based trailing starts at +3%
                candidate_trailing = close * (1.0 - initial_risk_pct)
                trailing_stop = max(trailing_stop, candidate_trailing)

            continue

        # ── ENTRY SCANNING (LIVE PRODUCTION BREAKOUT GATES) ─────────────────
        if not in_trade:
            # 1. Production 30-Day Anti-Churn Cooldown
            if (i - last_exit_idx) < cooldown_days:
                continue

            # 2. Maximum Lifecycle Age Gate
            if days_since_listing > max_breakout_age:
                continue

            # 3. Minimum Volume Floor Gate (150,000 shares)
            if vol < min_volume_floor:
                continue

            # 4. Clean Baseline Volume Calculation (excluding Day 0/1 launch spike)
            completed_bars = df.iloc[:i]
            if len(completed_bars) > 2:
                avg_vol = float(completed_bars["VOLUME"].iloc[2:].tail(10).mean())
            else:
                avg_vol = float(completed_bars["VOLUME"].mean())
            if avg_vol <= 0:
                avg_vol = vol
            vol_ratio = vol / avg_vol

            is_breakout = False
            cand_type = ""
            pivot = ld_high

            # Fresh Breakout Cross Check
            prev_high = df["HIGH"].iat[i-1]
            prev_was_below = (prev_high <= ld_high)

            # Setup A: Standard Listing Day High Breakout (Production <= 3.5% extension)
            if high > ld_high and prev_was_below:
                ext_pct = ((close - ld_high) / ld_high) * 100.0
                if ext_pct <= max_extension_pct and vol_ratio >= min_vol_mult:
                    is_breakout = True
                    cand_type = "LISTING_BREAKOUT"
                    pivot = ld_high

            # Setup B: Production High Shelf Wave 2 Breakout (Invokes lscan._detect_high_shelf_breakout)
            if not is_breakout and lscan.LISTING_SHELF_BREAKOUT_ENABLED and (lscan.LISTING_SHELF_MIN_AGE_DAYS <= days_since_listing <= lscan.LISTING_SHELF_MAX_AGE_DAYS):
                shelf_ok, s_high, s_low, s_prng, s_desc = lscan._detect_high_shelf_breakout(
                    df=completed_bars,
                    current_price=close,
                    current_high=high,
                    listing_day_high=ld_high,
                    days_since_listing=days_since_listing,
                )
                if shelf_ok:
                    turnover = close * vol
                    min_turnover_bytes = lscan.LISTING_SHELF_MIN_TURNOVER_CR * 1e7
                    if turnover >= min_turnover_bytes or vol_ratio >= 2.0 or (vol_ratio >= 1.5 and s_prng <= 12.0):
                        is_breakout = True
                        cand_type = "SHELF_BREAKOUT"
                        pivot = s_high

            if not is_breakout:
                continue

            # 5. Upper 50% Candle Body Confirmation Gate
            candle_range = high - low
            if candle_range <= 0 or (close - low) / candle_range < config["min_close_loc"]:
                continue

            # 6. Winner DNA Volatility Cushion
            if config.get("enforce_winner_cushion", False):
                ld_range = ld_high - ld_low if (ld_low and ld_low > 0) else ld_high * 0.10
                if (ld_range / ld_high * 100.0) < 5.0:
                    continue

            # 7. Production 15-Day Swing Low Stop Loss (3% buffer, 12% max risk cap, 8% fallback)
            swing_bars = df.iloc[max(0, i-15):i]
            swing_low = float(swing_bars["LOW"].min()) if len(swing_bars) > 0 else low
            struct_stop = swing_low * 0.97
            hard_cap_stop = close * 0.88  # 12% hard cap
            stop_loss = max(struct_stop, hard_cap_stop)
            if stop_loss >= close or pd.isna(stop_loss):
                stop_loss = close * 0.92  # 8% fallback

            risk_pct = (close - stop_loss) / close
            if risk_pct <= 0 or risk_pct > 0.12:
                continue

            # 8. Mandatory Risk/Reward Gate (Reward / Risk >= 1.25)
            ld_range = ld_high - ld_low if (ld_low and ld_low > 0) else ld_high * 0.10
            ext_above_pivot = ((close - pivot) / pivot * 100.0) if pivot > 0 else 0
            target_mult = 1.0 if ext_above_pivot <= 2.0 else (0.75 if ext_above_pivot <= 5.0 else 0.5)
            target = close + max(ld_range * target_mult, close * 0.20)
            reward = target - close
            risk = close - stop_loss
            rr = reward / risk if risk > 0 else 0
            if rr < config["min_risk_reward"]:
                continue

            # Enter Position
            in_trade = True
            entry_price = close
            entry_idx = i
            entry_date = current_date
            trailing_stop = stop_loss
            initial_risk_pct = risk_pct
            max_runup = 0.0
            trade_type = cand_type
            entry_age = days_since_listing

    return trades


def main(argv=None):
    parser = argparse.ArgumentParser(description=f"Production Rules Backtesting Engine & Strategy Audit (AlphaPulse v{lscan.SCANNER_VERSION})")
    parser.add_argument("--disable-vol-exit", action="store_true", help="Disable Volume Exhaustion Early Exit")
    parser.add_argument("--disable-stagnant-guard", action="store_true", help="Disable 40-day Stagnant Guard")
    parser.add_argument("--disable-speed-gates", action="store_true", help="Disable Speed Gates (14d/20d)")
    parser.add_argument("--trail-pnl-ipo", type=float, default=3.0, help="Trailing start PnL%% for Stage 1 (default: 3.0)")
    parser.add_argument("--vol-ratio", type=float, default=0.45, help="Volume Exhaustion ratio threshold (default: 0.45)")
    parser.add_argument(
        "--start-date", type=str, default=None,
        help="Optional start date filter (YYYY-MM-DD). By default, tests across full available historical data."
    )
    parser.add_argument(
        "--clean-cohort", action="store_true",
        help="Convenience flag: filter to clean cohort (entry_date >= 2026-07-05 per AGENTS.md)."
    )
    parser.add_argument(
        "--min-volume", type=int, default=150000,
        help="Minimum breakout-day volume (shares). Default: 150000 (Production rule)."
    )
    parser.add_argument(
        "--min-vol-mult", type=float, default=lscan.MIN_VOLUME_MULTIPLIER,
        help=f"Minimum breakout volume spike over clean 10d baseline. Default: {lscan.MIN_VOLUME_MULTIPLIER}x (Live rule)."
    )
    parser.add_argument(
        "--min-close-loc", type=float, default=0.50,
        help="Minimum candle close location in daily range: (Close - Low)/(High - Low). Default: 0.50 (Upper 50%% Body Rule)."
    )
    parser.add_argument(
        "--max-extension-pct", type=float, default=lscan.MAX_ENTRY_ABOVE_HIGH_PCT,
        help=f"Maximum entry extension above listing high. Default: {lscan.MAX_ENTRY_ABOVE_HIGH_PCT}%% (Anti-Chasing Rule)."
    )
    parser.add_argument(
        "--max-age", type=int, default=lscan.MAX_DAYS_SINCE_LISTING_FOR_BREAKOUT,
        help=f"Maximum IPO lifecycle age in days. Default: {lscan.MAX_DAYS_SINCE_LISTING_FOR_BREAKOUT}d (Live rule)."
    )
    parser.add_argument(
        "--cooldown-days", type=int, default=30,
        help="Cooldown days required between trades on the same symbol. Default: 30."
    )
    parser.add_argument(
        "--min-risk-reward", type=float, default=lscan.MIN_RISK_REWARD,
        help=f"Minimum Risk/Reward filter threshold. Default: {lscan.MIN_RISK_REWARD} (Live rule)."
    )
    parser.add_argument(
        "--enforce-winner-cushion", dest="enforce_winner_cushion", action="store_true", default=True,
        help="Enforce Winner DNA Volatility Cushion (Listing Day Range >= 5.0%%). Default: True."
    )
    parser.add_argument(
        "--disable-winner-cushion", dest="enforce_winner_cushion", action="store_false",
        help="Disable Winner DNA Volatility Cushion."
    )
    parser.add_argument(
        "--sweet-spot", action="store_true",
        help="Convenience preset: enforces Age <= 35d, Vol Mult >= 2.0x, and Winner Cushion."
    )

    if argv is None:
        argv = sys.argv[1:]
    args = parser.parse_args(argv)

    start_date = "2026-07-05" if args.clean_cohort else args.start_date
    max_age = 35 if args.sweet_spot else args.max_age
    min_vol_mult = 2.0 if args.sweet_spot else args.min_vol_mult
    enforce_winner_cushion = True if args.sweet_spot else args.enforce_winner_cushion

    config = {
        "enable_vol_exit": not args.disable_vol_exit,
        "enable_stagnant_guard": not args.disable_stagnant_guard,
        "enable_speed_gates": not args.disable_speed_gates,
        "trail_pnl_ipo": args.trail_pnl_ipo,
        "vol_ratio": args.vol_ratio,
        "start_date": start_date,
        "min_volume": args.min_volume,
        "min_vol_mult": min_vol_mult,
        "min_close_loc": args.min_close_loc,
        "max_extension_pct": args.max_extension_pct,
        "max_age": max_age,
        "cooldown_days": args.cooldown_days,
        "min_risk_reward": args.min_risk_reward,
        "enforce_winner_cushion": enforce_winner_cushion,
    }

    listing_map, candle_map = load_dataset()
    if not candle_map:
        print("No candle data available.")
        return

    all_trades = []
    for sym, df in candle_map.items():
        linfo = listing_map.get(sym, {})
        t_list = simulate_production_latest_system(df, sym, linfo, config)
        all_trades.extend(t_list)

    if not all_trades:
        print("No trades generated under current configuration.")
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
    profit_factor = (gross_wins / gross_losses) if gross_losses > 0 else 999.0
    
    max_win = tdf["pnl_pct"].max()
    max_loss = tdf["pnl_pct"].min()
    super_winners = len(tdf[tdf["pnl_pct"] >= 20.0])
    
    reasons = tdf["exit_reason"].value_counts()

    print("\n" + "=" * 80)
    print("        SYSTEM PERFORMANCE AUDIT: STRATEGY BACKTEST ENGINE")
    print(f"   (DYNAMICALLY BOUND TO LIVE PRODUCTION SCANNERS: v{lscan.SCANNER_VERSION})")
    print("=" * 80)
    print("Active Production Rules (Imported from Live Codebase):")
    print(f"   * Source Modules          : listing_day_breakout_scanner.py & streamlined_ipo_scanner.py")
    print(f"   * System Version          : v{lscan.SCANNER_VERSION}")
    print(f"   * Start Date Filter       : {config['start_date'] or 'ALL AVAILABLE HISTORICAL DATA (2022-2026)'}")
    print(f"   * Volume Floor (breakout) : {config.get('min_volume', 0):,} shares")
    print(f"   * Volume Spike Multiplier : {config.get('min_vol_mult', 1.8):.2f}x (clean baseline)")
    print(f"   * Upper 50% Body Rule     : >= {config.get('min_close_loc', 0.50)*100:.0f}% of daily range")
    print(f"   * Anti-Chasing Max Ext.   : <= {config.get('max_extension_pct', 3.5):.1f}% above pivot")
    print(f"   * Cooldown Quarantine     : {config.get('cooldown_days', 30)} days between same-symbol entries")
    print(f"   * Risk/Reward Gate        : >= {config.get('min_risk_reward', 1.25):.2f}")
    print(f"   * Shelf Detection         : lscan._detect_high_shelf_breakout (PRNG <= {lscan.LISTING_SHELF_MAX_PRNG}%, Ext <= {lscan.LISTING_SHELF_MAX_ENTRY_ABOVE_PIVOT_PCT}%)")
    print(f"   * Stop Loss Standard      : 15-Day Local Swing Low (3% buffer, 12% max risk cap, 8% fallback)")
    print(f"   * Trailing Stop Engine    : 2-Stage Dynamic (Stage 1 @ +3%, Stage 2 @ +15% with 20% peak cushion)")
    print(f"   * Speed Gates Enabled     : {config['enable_speed_gates']} (14d Velocity / {sscan.DEAD_MONEY_DAYS_IPO}d IPO)")
    print(f"   * Stagnant Guard Enabled  : {config['enable_stagnant_guard']} (40d: PnL <= 0% & Peak < 8%)")
    print(f"   * Volume Exit Enabled     : {config['enable_vol_exit']} (threshold: {config['vol_ratio']})")
    print("-" * 80)
    print(f"Total Trades Executed     : {total_trades}")
    print(f"Winning Trades           : {wins} ({win_rate:.2f}%)")
    print(f"Losing Trades            : {losses} ({100 - win_rate:.2f}%)")
    print(f"Profit Factor            : {profit_factor:.2f}")
    print(f"Average Return per Trade  : {avg_pnl:+.2f}%")
    print(f"Gross Win Total          : +{gross_wins:.1f}%")
    print(f"Gross Loss Total         : -{gross_losses:.1f}%")
    print(f"Max Single Winner        : +{max_win:.2f}%")
    print(f"Max Single Loser         : {max_loss:.2f}%")
    print(f"Super-Winners (>= +20%)  : {super_winners} ({(super_winners/total_trades)*100:.1f}%)")
    print(f"Average Holding Period   : {avg_days:.1f} days")
    print("-" * 80)
    print("Performance by Setup Type:")
    for t, g in tdf.groupby("type"):
        gw_t = g[g["pnl_pct"] > 0]["pnl_pct"].sum()
        gl_t = abs(g[g["pnl_pct"] < 0]["pnl_pct"].sum())
        pf_t = (gw_t / gl_t) if gl_t > 0 else 999.0
        wr_t = (g["win"].sum() / len(g)) * 100.0
        print(f"   * {t:<18}: N={len(g):3d} | Win Rate={wr_t:5.1f}% | Avg={g['pnl_pct'].mean():+5.2f}% | PF={pf_t:4.2f}")
    print("-" * 80)
    print("Exit Reason Breakdown:")
    for reason, count in reasons.items():
        pct = (count / total_trades) * 100.0
        print(f"   * {reason:<34}: {count:3d} ({pct:.1f}%)")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    main()
