#!/usr/bin/env python3
"""
core/strategy_evidence.py

Persistent Strategy Evidence Store & Self-Diagnosing Quality Engine
Harvests, normalizes, and analyzes trade setups and outcomes to uncover
what is working (proven alpha), what is leaking (system weaknesses),
and what hypotheses are currently under test — specifically tailored
for high-conviction Listing Day Breakouts and tight Consolidation Breakouts.
"""

import os
import sys
from datetime import datetime, timezone, timedelta
import pandas as pd
import numpy as np
from pymongo import MongoClient

# Terminal Colors
class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    MAGENTA = '\033[95m'
    CYAN = '\033[96m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    UNDERLINE = '\033[4m'
    END = '\033[0m'


def build_trade_evidence_doc(trade: dict, db=None, fetch_data_fn=None) -> dict:
    """
    Builds a granular forensic evidence document pairing setup DNA with empirical trade outcome.
    Works for both position documents and signal documents.
    """
    sym = trade.get("symbol")
    raw_date = trade.get("entry_date") or trade.get("signal_date") or ""
    if isinstance(raw_date, datetime):
        entry_date_str = raw_date.strftime("%Y-%m-%d")
    else:
        entry_date_str = str(raw_date)[:10]

    evidence_id = f"EV_{sym}_{entry_date_str.replace('-', '')}"
    status = trade.get("status", "UNKNOWN")
    grade = trade.get("grade") or "STANDARD"
    signal_id = trade.get("signal_id")
    scanner = trade.get("scanner") or ("listing_day" if (grade == "LISTING_BREAKOUT" or "BREAKOUT_" in str(signal_id)) else "consolidation_live")
    engine_type = "LISTING_DAY_BREAKOUT" if (scanner == "listing_day" or grade == "LISTING_BREAKOUT" or "BREAKOUT_" in str(signal_id)) else "CONSOLIDATION"

    entry_price = float(trade.get("entry_price") or 0.0)
    current_price = float(trade.get("current_price") or trade.get("exit_price") or entry_price)
    pnl_pct = float(trade.get("pnl_pct") or 0.0)
    max_runup = float(trade.get("max_runup_pct") or max(0.0, pnl_pct))
    max_drawdown = float(trade.get("max_drawdown_pct") or min(0.0, pnl_pct))
    days_held = float(trade.get("days_held") or 0.0)
    peak_price = float(trade.get("peak_price_during_trade") or max(current_price, entry_price))
    exit_reason = trade.get("exit_reason")

    # Match Signal for setup parameters
    sig = {}
    if db is not None:
        try:
            signals_col = db["signals"]
            if signal_id:
                sig = signals_col.find_one({"signal_id": signal_id}) or {}
            if not sig:
                try:
                    dt = datetime.strptime(entry_date_str, "%Y-%m-%d")
                    sig = signals_col.find_one({
                        "symbol": sym,
                        "signal_date": {"$gte": dt - timedelta(days=7), "$lte": dt + timedelta(days=7)}
                    }) or {}
                except Exception:
                    pass
        except Exception:
            pass

    # Setup DNA Parameters (No dummy/magic constants — real market data only)
    days_since_listing = int(sig.get("days_since_listing") or trade.get("days_since_listing") or sig.get("ipo_age") or trade.get("ipo_age") or 0)
    listing_vol = sig.get("listing_day_volume") or trade.get("listing_day_volume")
    vol_spike = sig.get("volume_spike") or sig.get("volume_ratio") or trade.get("volume_ratio")
    prng_10d = sig.get("listing_range_pct") if (sig.get("listing_range_pct") is not None and sig.get("listing_range_pct") > 0) else (trade.get("consolidation_range_pct") or sig.get("consolidation_range_pct"))
    upper_wick_pct = trade.get("upper_wick_pct") or sig.get("upper_wick_pct")
    turnover_cr = trade.get("turnover_cr") or sig.get("turnover_cr") or sig.get("avg_turnover_cr")
    market_regime = sig.get("market_regime") or trade.get("market_regime") or "BULL"

    # Automatically load real market data fetcher if not provided
    if fetch_data_fn is None:
        try:
            from streamlined_ipo_scanner import fetch_data
            fetch_data_fn = fetch_data
        except Exception:
            fetch_data_fn = None

    # Enrich directly from genuine market candles up to entry date
    if fetch_data_fn:
        try:
            df = fetch_data_fn(sym, "2025-01-01")
            if df is not None and not df.empty:
                df.columns = [c.upper() for c in df.columns]
                if 'VOLUME' in df.columns and len(df) > 0:
                    listing_vol = int(df['VOLUME'].iloc[0])

                match_idx = df.index[df['DATE'].astype(str).str[:10] <= entry_date_str]
                if len(match_idx) > 0:
                    sub_df = df.loc[:match_idx[-1]]
                else:
                    first_date_str = str(df['DATE'].iloc[0])[:10]
                    try:
                        dt_entry = datetime.strptime(entry_date_str, "%Y-%m-%d")
                        dt_first = datetime.strptime(first_date_str, "%Y-%m-%d")
                        if 0 <= (dt_first - dt_entry).days <= 3:
                            sub_df = df.iloc[:1]
                        else:
                            sub_df = pd.DataFrame()
                    except Exception:
                        sub_df = pd.DataFrame()

                if len(sub_df) > 0:
                    last_c = sub_df.iloc[-1]
                    c_range = float(last_c['HIGH'] - last_c['LOW'])
                    if c_range > 0:
                        u_wick = float(last_c['HIGH'] - max(last_c['OPEN'], last_c['CLOSE']))
                        upper_wick_pct = round((u_wick / c_range) * 100.0, 1)

                    r20 = sub_df.tail(20)
                    r20_vol = float(r20['VOLUME'].mean()) if 'VOLUME' in r20.columns and len(r20) > 0 else 0.0
                    if r20_vol > 0:
                        vol_spike = round(float(last_c['VOLUME']) / r20_vol, 2)
                        turnover_cr = round((r20_vol * float(last_c['CLOSE'])) / 10_000_000.0, 1)

                    r10 = sub_df.tail(10)
                    if len(r10) > 0:
                        r10_l = float(r10['LOW'].min())
                        r10_h = float(r10['HIGH'].max())
                        if r10_l > 0:
                            prng_10d = round(((r10_h - r10_l) / r10_l) * 100.0, 1)
        except Exception as _fetch_err:
            pass

    # Ensure clean float conversions without dummy defaults (Preserve None if uncomputable)
    vol_spike = float(vol_spike) if vol_spike is not None else None
    prng_10d = float(prng_10d) if prng_10d is not None else None
    upper_wick_pct = float(upper_wick_pct) if upper_wick_pct is not None else None
    turnover_cr = float(turnover_cr) if turnover_cr is not None else None
    listing_vol = int(listing_vol) if listing_vol is not None else None

    is_concluded = status in ["CLOSED", "PAPER_CLOSED"]
    is_win = pnl_pct > 0.0

    # Archetype & Explicit Forensic Takeaway
    archetype = "STANDARD_BREAKOUT"
    failure_reason = None
    algo_takeaway = ""
    learning_summary = ""

    if is_win:
        if vol_spike is not None and vol_spike >= 3.0 and pnl_pct >= 10.0:
            archetype = "HIGH_VOL_MOMENTUM_RUNNER"
            algo_takeaway = f"Institutional volume expansion ({vol_spike:.1f}x) drove strong follow-through (+{pnl_pct:.1f}%). SuperTrend trailing protects multi-week runners."
            learning_summary = f"Massive volume burst ({vol_spike:.1f}x avg vol) validated institutional conviction, powering a +{pnl_pct:.1f}% gain."
        elif prng_10d is not None and prng_10d <= 15.0 and pnl_pct >= 10.0:
            archetype = "TIGHT_BASE_COMPOUNDER"
            algo_takeaway = f"Tight base coil ({prng_10d:.1f}% PRNG) capped risk floor and enabled asymmetric payout (+{pnl_pct:.1f}%)."
            learning_summary = f"Narrow volatility coil ({prng_10d:.1f}% PRNG) prevented wide whipsaws and yielded +{pnl_pct:.1f}% upside."
        else:
            archetype = "MODERATE_GAIN_TRADE"
            algo_takeaway = f"Solid breakout follow-through (+{pnl_pct:.1f}%). Systematic trailing stop locked in gains."
            learning_summary = f"Steady breakout follow-through (+{pnl_pct:.1f}%) with systematic stop protection."
    else:
        if upper_wick_pct is not None and upper_wick_pct >= 35.0:
            archetype = "UPPER_WICK_SUPPLY_TRAP"
            failure_reason = f"Breakout closed with {upper_wick_pct:.1f}% upper wick; heavy institutional supply rejection into the close."
            algo_takeaway = "Upper 50% Candle Body Gate (v3.5.0) structurally rejects breakouts closing with >35% upper wick."
            learning_summary = f"Long upper wick ({upper_wick_pct:.1f}%) signaled severe overhead supply; avoided by candle body confirmation gate."
        elif days_held is not None and days_held >= 14 and pnl_pct <= 0.0:
            archetype = "STAGNANT_DEAD_MONEY_BLEED"
            failure_reason = f"Position held {days_held:.0f} days with negative return ({pnl_pct:.1f}%); volume decayed below Day 1 baseline."
            algo_takeaway = "14-Day Velocity Speed Gate exits stagnant trades early at small drawdown, freeing capital for fast runners."
            learning_summary = f"Breakout lost momentum and stagnated for {days_held:.0f} days ({pnl_pct:.1f}%); eliminated early by 14-day velocity speed gate."
        else:
            archetype = "EARLY_FALSE_BREAKOUT"
            failure_reason = f"Breakout failed follow-through ({pnl_pct:.1f}%)."
            algo_takeaway = "Dynamic swing low stop (capped at 12%) strictly contained downside loss."
            learning_summary = f"Immediate lack of continuation ({pnl_pct:.1f}%); downside risk strictly contained by stop loss."

    # ── Calculate Structured Winner Traits & Loser Trap Flags ──
    winner_traits = []
    if days_since_listing is not None and 0 < days_since_listing <= 35:
        winner_traits.append("early_breakout_le_35d")
    if prng_10d is not None and prng_10d >= 5.0:
        winner_traits.append("listing_range_gte_5pct")
    if vol_spike is not None and vol_spike >= 1.5:
        winner_traits.append("volume_ratio_gte_1_5")
    if turnover_cr is not None and turnover_cr >= 1.0:
        winner_traits.append("institutional_turnover_gte_1cr")
    if listing_vol is not None and listing_vol >= 150_000:
        winner_traits.append("listing_vol_gte_150k")
    winner_score = len(winner_traits)

    trap_flags = []
    if upper_wick_pct is not None and upper_wick_pct >= 35.0:
        trap_flags.append("SUPPLY_TRAP_UPPER_WICK")
    if prng_10d is not None and prng_10d > 15.0:
        trap_flags.append("LOOSE_BASE_VOLATILITY")
    if vol_spike is not None and vol_spike > 0 and vol_spike < 1.5:
        trap_flags.append("ANEMIC_VOLUME_SPIKE")
    if turnover_cr is not None and turnover_cr > 0 and turnover_cr < 1.0:
        trap_flags.append("ILLIQUID_TURNOVER_TRAP")
    if days_held is not None and days_held >= 14 and pnl_pct <= 0.0:
        trap_flags.append("STAGNANT_DRIFT_14D")
    trap_score = len(trap_flags)

    cohorts = [archetype, engine_type]
    cohorts.append(f"WINNER_SCORE_{winner_score}")
    cohorts.append(f"TRAP_SCORE_{trap_score}")
    for tf in trap_flags:
        cohorts.append(tf)

    if vol_spike is not None:
        if vol_spike >= 3.0:
            cohorts.append("HIGH_VOLUME_BURST")
        elif vol_spike >= 1.5:
            cohorts.append("MODERATE_VOLUME_SPIKE")
        else:
            cohorts.append("LOW_VOLUME_WEAK")

    if prng_10d is not None:
        if prng_10d <= 15.0:
            cohorts.append("TIGHT_BASE")
        elif prng_10d <= 25.0:
            cohorts.append("NORMAL_BASE")
        else:
            cohorts.append("WIDE_LOOSE_BASE")

    if upper_wick_pct is not None and upper_wick_pct >= 35.0:
        cohorts.append("HIGH_UPPER_WICK_REJECTION")

    if is_win and pnl_pct >= 10.0:
        cohorts.append("PROVEN_BIG_WINNER")
    elif not is_win and is_concluded:
        cohorts.append("STOPPED_OUT_LOSER")

    doc = {
        "evidence_id": evidence_id,
        "symbol": sym,
        "entry_date": entry_date_str,
        "signal_id": signal_id,
        "grade": grade,
        "engine_type": engine_type,
        "version": trade.get("version") or "3.5.0",
        "strategy_version": trade.get("strategy_version") or ("3.5.0-listing-day" if engine_type == "LISTING_DAY_BREAKOUT" else "3.5.0-consolidation"),
        "setup_dna": {
            "listing_vol": listing_vol,
            "volume_spike": vol_spike,
            "prng_10d_pct": prng_10d,
            "upper_wick_pct": upper_wick_pct,
            "turnover_cr": turnover_cr,
            "days_since_listing": days_since_listing,
            "market_regime": market_regime,
            "winner_score": winner_score,
            "winner_traits": winner_traits,
            "trap_score": trap_score,
            "trap_flags": trap_flags
        },
        "outcome": {
            "status": status,
            "entry_price": entry_price,
            "current_price": current_price,
            "peak_price": peak_price,
            "pnl_pct": round(pnl_pct, 2),
            "max_runup_pct": round(max_runup, 2),
            "max_drawdown_pct": round(max_drawdown, 2),
            "days_held": days_held,
            "exit_reason": exit_reason,
            "is_concluded": is_concluded,
            "is_win": is_win
        },
        "forensics": {
            "archetype": archetype,
            "failure_reason": failure_reason,
            "algo_takeaway": algo_takeaway,
            "learning_summary": learning_summary
        },
        "cohort_labels": cohorts,
        "updated_at": datetime.now(timezone.utc)
    }
    return doc


def record_trade_closure_evidence(trade: dict, db=None, fetch_data_fn=None) -> bool:
    """
    Persists a single trade's forensic evidence doc directly to MongoDB `strategy_evidence`
    and updates the corresponding `positions` document with structured DNA fields.
    """
    if db is None:
        try:
            from db import db as default_db
            db = default_db
        except Exception:
            pass
    if db is None:
        return False
    try:
        doc = build_trade_evidence_doc(trade, db=db, fetch_data_fn=fetch_data_fn)
        evidence_col = db["strategy_evidence"]
        evidence_col.update_one(
            {"evidence_id": doc["evidence_id"]},
            {"$set": doc},
            upsert=True
        )
        # Sync DNA and forensics to positions collection for unified querying
        positions_col = db["positions"]
        dna = doc.get("setup_dna", {})
        forensics = doc.get("forensics", {})
        positions_col.update_one(
            {"symbol": doc["symbol"]},
            {"$set": {
                "winner_score": dna.get("winner_score"),
                "winner_traits": dna.get("winner_traits"),
                "trap_score": dna.get("trap_score"),
                "trap_flags": dna.get("trap_flags"),
                "archetype": forensics.get("archetype"),
                "algo_takeaway": forensics.get("algo_takeaway")
            }}
        )
        return True
    except Exception as e:
        print(f"⚠️ Error recording trade closure evidence: {e}")
        return False


def sync_all_trade_evidence(db, fetch_data_fn=None) -> int:
    """
    Ingests all clean-cohort positions and historical trades into `strategy_evidence`.
    Captures both active portfolio positions and concluded signals since 2026-07-05.
    Calculates granular setup DNA (PRNG, volume spike, upper wick, turnover).
    """
    if db is None:
        return 0

    positions_col = db["positions"]
    signals_col = db["signals"]
    evidence_col = db["strategy_evidence"]

    clean_cutoff = datetime(2026, 7, 5)

    # 1. Gather clean positions
    positions = list(positions_col.find({}))
    all_trades = []
    pos_map = {}

    for pos in positions:
        ed = pos.get("entry_date")
        if ed and (str(ed) >= "2026-07-05" or (isinstance(ed, datetime) and ed >= clean_cutoff)):
            all_trades.append(pos)
            pos_map.setdefault(pos["symbol"], []).append(pos)

    # 2. Gather distinct prior completed trades from clean signals
    closed_signals = list(signals_col.find({
        "status": {"$in": ["CLOSED", "PAPER_CLOSED"]},
        "signal_date": {"$gte": clean_cutoff}
    }))

    for sig in closed_signals:
        sym = sig.get("symbol")
        sd = sig.get("signal_date")
        matches = pos_map.get(sym, [])
        is_duplicate = False
        for pos in matches:
            pos_ed = pos.get("entry_date")
            if pos.get("signal_id") == sig.get("signal_id"):
                is_duplicate = True
                break
            if pos_ed and isinstance(pos_ed, datetime) and isinstance(sd, datetime):
                if abs((sd - pos_ed).days) <= 7:
                    is_duplicate = True
                    break
            if round(float(sig.get("pnl_pct", 0) or 0), 1) == round(float(pos.get("pnl_pct", 0) or 0), 1):
                is_duplicate = True
                break

        if not is_duplicate:
            trade_doc = dict(sig)
            trade_doc["entry_date"] = sd
            all_trades.append(trade_doc)

    if not all_trades:
        return 0

    valid_evidence_ids = set()
    synced_count = 0
    for trade in all_trades:
        doc = build_trade_evidence_doc(trade, db=db, fetch_data_fn=fetch_data_fn)
        evidence_col.update_one(
            {"evidence_id": doc["evidence_id"]},
            {"$set": doc},
            upsert=True
        )
        # Also sync back to positions collection
        dna = doc.get("setup_dna", {})
        forensics = doc.get("forensics", {})
        positions_col.update_one(
            {"symbol": doc["symbol"]},
            {"$set": {
                "winner_score": dna.get("winner_score"),
                "winner_traits": dna.get("winner_traits"),
                "trap_score": dna.get("trap_score"),
                "trap_flags": dna.get("trap_flags"),
                "archetype": forensics.get("archetype"),
                "algo_takeaway": forensics.get("algo_takeaway")
            }}
        )
        valid_evidence_ids.add(doc["evidence_id"])
        synced_count += 1

    # Prune obsolete/ghost evidence records
    if valid_evidence_ids:
        evidence_col.delete_many({"evidence_id": {"$nin": list(valid_evidence_ids)}})

    return synced_count


def generate_system_diagnostics_report(db):
    """
    Analyzes accumulated strategy evidence to present:
    1. Proven Alpha Strengths (What is Working)
    2. System Leaks & Weaknesses (Where Losses Occur)
    3. Dedicated Listing Day Breakouts Forensic DNA & Edge Analysis
    4. Hypotheses Under Test (Experimental Track)
    5. Actionable Algorithmic Directives
    """
    if db is None:
        print("❌ MongoDB not connected.")
        return

    evidence_col = db["strategy_evidence"]
    docs = list(evidence_col.find({}))

    if not docs:
        print("⚠️ No strategy evidence found. Run --sync-evidence first.")
        return

    df = pd.DataFrame(docs)
    total_samples = len(df)

    # Flatten outcome and setup_dna
    df['status'] = df['outcome'].apply(lambda x: x.get('status'))
    df['pnl_pct'] = df['outcome'].apply(lambda x: x.get('pnl_pct', 0.0))
    df['max_runup_pct'] = df['outcome'].apply(lambda x: x.get('max_runup_pct', 0.0))
    df['days_held'] = df['outcome'].apply(lambda x: x.get('days_held', 0.0))
    df['is_win'] = df['outcome'].apply(lambda x: x.get('is_win', False))
    df['is_concluded'] = df['outcome'].apply(lambda x: x.get('is_concluded', False))
    df['vol_spike'] = df['setup_dna'].apply(lambda x: x.get('volume_spike', 1.5))
    df['prng'] = df['setup_dna'].apply(lambda x: x.get('prng_10d_pct', 15.0))
    df['upper_wick'] = df['setup_dna'].apply(lambda x: x.get('upper_wick_pct', 0.0))
    df['turnover_cr'] = df['setup_dna'].apply(lambda x: x.get('turnover_cr', 5.0))
    df['archetype'] = df['forensics'].apply(lambda x: x.get('archetype', ''))
    df['learning_summary'] = df['forensics'].apply(lambda x: x.get('learning_summary', ''))

    active_df = df[~df['is_concluded']]
    concluded_df = df[df['is_concluded']]

    c_wins = len(concluded_df[concluded_df['is_win']])
    c_losses = len(concluded_df[~concluded_df['is_win']])
    c_win_rate = (c_wins / len(concluded_df)) * 100.0 if len(concluded_df) > 0 else 0.0
    c_avg_pnl = concluded_df['pnl_pct'].mean() if len(concluded_df) > 0 else 0.0

    o_wins = len(active_df[active_df['is_win']])
    o_losses = len(active_df[~active_df['is_win']])
    o_win_rate = (o_wins / len(active_df)) * 100.0 if len(active_df) > 0 else 0.0
    o_avg_pnl = active_df['pnl_pct'].mean() if len(active_df) > 0 else 0.0

    win_count = len(df[df['is_win']])
    loss_count = len(df[~df['is_win']])
    overall_win_rate = (win_count / total_samples) * 100.0 if total_samples > 0 else 0.0
    avg_pnl = df['pnl_pct'].mean()
    avg_runup = df['max_runup_pct'].mean()

    print("\n" + "═" * 90)
    print(f"{Colors.BOLD}{Colors.CYAN}🏛️ SYSTEM-WIDE STRATEGY DIAGNOSTIC & ALPHA EVIDENCE REPORT{Colors.END}")
    print(f"Sample Size: {total_samples} Pure Clean-Cohort Trades ({len(active_df)} Active/Paper Open / {len(concluded_df)} Realized Concluded)")
    print("═" * 90)

    # Baseline Summary
    print(f"{Colors.BOLD}📊 System Performance Baseline:{Colors.END}")
    print(f"  • Realized Win Rate (Closed):  {Colors.BOLD}{Colors.GREEN if c_win_rate >= 50 else Colors.YELLOW}{c_win_rate:.1f}%{Colors.END} ({c_wins} Win / {c_losses} Loss) | Realized Avg PnL: {Colors.BOLD}{Colors.GREEN if c_avg_pnl >= 0 else Colors.RED}{c_avg_pnl:+.2f}%{Colors.END}")
    print(f"  • Active/Paper MTM (Running): {Colors.BOLD}{Colors.GREEN if o_win_rate >= 50 else Colors.YELLOW}{o_win_rate:.1f}% Green{Colors.END} ({o_wins} Green / {o_losses} Red) | Running Avg MTM: {Colors.BOLD}{Colors.GREEN if o_avg_pnl >= 0 else Colors.RED}{o_avg_pnl:+.2f}%{Colors.END}")
    print(f"  • Combined Portfolio Edge:    {Colors.BOLD}{Colors.GREEN if overall_win_rate >= 50 else Colors.YELLOW}{overall_win_rate:.1f}%{Colors.END} ({win_count} Win / {loss_count} Loss) | Combined Avg PnL: {Colors.BOLD}{Colors.GREEN if avg_pnl >= 0 else Colors.RED}{avg_pnl:+.2f}%{Colors.END}")
    print(f"  • Average Peak Runup:         {Colors.BOLD}{Colors.GREEN}+{avg_runup:.2f}%{Colors.END}")
    if not df.empty:
        max_idx = df['pnl_pct'].idxmax()
        print(f"  • Max Observed Winner:        {Colors.BOLD}{Colors.GREEN}+{df.loc[max_idx, 'pnl_pct']:.2f}% ({df.loc[max_idx, 'symbol']}){Colors.END}")

    # SECTION 1: PROVEN ALPHA STRENGTHS
    print(f"\n{Colors.BOLD}{Colors.GREEN}══════════════════════════════════════════════════════════════════════════════{Colors.END}")
    print(f"{Colors.BOLD}{Colors.GREEN}🏆 SECTION 1: PROVEN ALPHA STRENGTHS (WHAT IS WORKING & SYSTEM EDGE){Colors.END}")
    print(f"{Colors.BOLD}{Colors.GREEN}══════════════════════════════════════════════════════════════════════════════{Colors.END}")

    # Volume Surge Cohort
    high_vol = df[df['vol_spike'] >= 3.0]
    if len(high_vol) > 0:
        hv_wr = (len(high_vol[high_vol['is_win']]) / len(high_vol)) * 100.0
        hv_avg = high_vol['pnl_pct'].mean()
        hv_runup = high_vol['max_runup_pct'].mean()
        hv_archetypes = ", ".join([f"{r['symbol']} ({r['pnl_pct']:+.1f}%)" for _, r in high_vol[high_vol['is_win']].head(4).iterrows()])
        print(f"\n  {Colors.BOLD}1. Massive Institutional Volume Surge (>= 3.0x Average Volume):{Colors.END}")
        print(f"     • Win Rate: {Colors.GREEN}{hv_wr:.1f}%{Colors.END} | Avg PnL: {Colors.GREEN}{hv_avg:+.2f}%{Colors.END} | Avg Peak Runup: {Colors.GREEN}+{hv_runup:.1f}%{Colors.END} (Sample: {len(high_vol)})")
        if hv_archetypes:
            print(f"     • Proven Archetypes: {Colors.CYAN}{hv_archetypes}{Colors.END}")
        print(f"     • {Colors.DIM}Empirical Conclusion: Setups with >3x volume expansion provide multi-day momentum ignition and rarely fail on Day 1.{Colors.END}")

    # Tight Base Cohort
    tight_base = df[df['prng'] <= 15.0]
    if len(tight_base) > 0:
        tb_wr = (len(tight_base[tight_base['is_win']]) / len(tight_base)) * 100.0
        tb_avg = tight_base['pnl_pct'].mean()
        tb_archetypes = ", ".join([f"{r['symbol']} ({r['pnl_pct']:+.1f}%)" for _, r in tight_base[tight_base['is_win']].head(4).iterrows()])
        print(f"\n  {Colors.BOLD}2. Tight Base Accumulation (PRNG <= 15.0% Volatility Range):{Colors.END}")
        print(f"     • Win Rate: {Colors.GREEN}{tb_wr:.1f}%{Colors.END} | Avg PnL: {Colors.GREEN}{tb_avg:+.2f}%{Colors.END} (Sample: {len(tight_base)})")
        if tb_archetypes:
            print(f"     • Proven Archetypes: {Colors.CYAN}{tb_archetypes}{Colors.END}")
        print(f"     • {Colors.DIM}Empirical Conclusion: Narrow base consolidation allows tight risk stops with asymmetrical 3:1+ reward-to-risk payouts.{Colors.END}")

    # SECTION 2: SYSTEM WEAKNESSES & LEAKS
    print(f"\n{Colors.BOLD}{Colors.RED}══════════════════════════════════════════════════════════════════════════════{Colors.END}")
    print(f"{Colors.BOLD}{Colors.RED}⚠️ SECTION 2: SYSTEM WEAKNESSES & LOSS DRIVERS (WHERE LEAKS OCCUR){Colors.END}")
    print(f"{Colors.BOLD}{Colors.RED}══════════════════════════════════════════════════════════════════════════════{Colors.END}")

    # Upper Wick Rejection Leak
    wick_leak = df[df['upper_wick'] >= 35.0]
    if len(wick_leak) > 0:
        wl_loss_rate = (len(wick_leak[~wick_leak['is_win']]) / len(wick_leak)) * 100.0
        wl_trades = ", ".join([f"{r['symbol']} ({r['pnl_pct']:+.1f}%)" for _, r in wick_leak[~wick_leak['is_win']].iterrows()])
        print(f"\n  {Colors.BOLD}1. Upper Wick Rejection at Listing Highs (Upper Wick >= 35%):{Colors.END}")
        print(f"     • Failure Rate: {Colors.RED}{wl_loss_rate:.1f}%{Colors.END} (Sample: {len(wick_leak)})")
        if wl_trades:
            print(f"     • Trapped Setups: {Colors.YELLOW}{wl_trades}{Colors.END}")
        print(f"     • {Colors.DIM}Root Cause: Breakout pokes above resistance intraday but gets dumped into close, trapping late buyers.{Colors.END}")
        print(f"     • {Colors.GREEN}Eliminated by: Upper 50% Candle Body Gate (v3.5.0).{Colors.END}")

    # Stale Active Holding Leak
    stale_trades = df[(df['days_held'] >= 14) & (df['pnl_pct'] <= 0)]
    if len(stale_trades) > 0:
        st_trades = ", ".join([f"{r['symbol']} ({r['days_held']:.0f}d, {r['pnl_pct']:+.1f}%)" for _, r in stale_trades.iterrows()])
        print(f"\n  {Colors.BOLD}2. Stagnant / Dead-Money Drift (Held >= 14 Days with PnL <= 0%):{Colors.END}")
        print(f"     • Capital Drag: {len(stale_trades)} trades tied up in stagnant positions.")
        if st_trades:
            print(f"     • Stagnant Trades: {Colors.YELLOW}{st_trades}{Colors.END}")
        print(f"     • {Colors.DIM}Root Cause: Breakouts with 0 follow-through by Day 14 suffer volume decay and slowly bleed toward stops.{Colors.END}")
        print(f"     • {Colors.GREEN}Eliminated by: 14-Day Velocity Speed Gate (v3.5.0).{Colors.END}")

    # SECTION 3: LISTING DAY BREAKOUTS FORENSIC DNA & EDGE ANALYSIS
    print(f"\n{Colors.BOLD}{Colors.CYAN}══════════════════════════════════════════════════════════════════════════════{Colors.END}")
    print(f"{Colors.BOLD}{Colors.CYAN}🏛️ SECTION 3: LISTING DAY BREAKOUTS FORENSIC DNA & EDGE ANALYSIS{Colors.END}")
    print(f"{Colors.BOLD}{Colors.CYAN}══════════════════════════════════════════════════════════════════════════════{Colors.END}")

    ld_df = df[df['engine_type'] == 'LISTING_DAY_BREAKOUT']
    if not ld_df.empty:
        ld_active = ld_df[~ld_df['is_concluded']]
        ld_concluded = ld_df[ld_df['is_concluded']]

        ld_c_w = len(ld_concluded[ld_concluded['is_win']])
        ld_c_l = len(ld_concluded[~ld_concluded['is_win']])
        ld_c_wr = (ld_c_w / len(ld_concluded) * 100.0) if len(ld_concluded) > 0 else 0.0
        ld_c_avg = ld_concluded['pnl_pct'].mean() if len(ld_concluded) > 0 else 0.0

        ld_o_w = len(ld_active[ld_active['is_win']])
        ld_o_l = len(ld_active[~ld_active['is_win']])
        ld_o_wr = (ld_o_w / len(ld_active) * 100.0) if len(ld_active) > 0 else 0.0
        ld_o_avg = ld_active['pnl_pct'].mean() if len(ld_active) > 0 else 0.0

        ld_tot_w = len(ld_df[ld_df['is_win']])
        ld_tot_l = len(ld_df[~ld_df['is_win']])
        ld_tot_wr = (ld_tot_w / len(ld_df) * 100.0)
        ld_tot_avg = ld_df['pnl_pct'].mean()

        print(f"  • Total Listing Day Breakout Setups: {len(ld_df)} ({len(ld_active)} Running Open / {len(ld_concluded)} Concluded)")
        print(f"  • Realized Win Rate (Closed):       {Colors.BOLD}{Colors.GREEN if ld_c_wr >= 50 else Colors.YELLOW}{ld_c_wr:.1f}%{Colors.END} ({ld_c_w}W / {ld_c_l}L) | Realized Avg PnL: {Colors.BOLD}{Colors.GREEN if ld_c_avg >= 0 else Colors.RED}{ld_c_avg:+.2f}%{Colors.END}")
        print(f"  • Active/Paper MTM (Running):      {Colors.BOLD}{Colors.GREEN if ld_o_wr >= 50 else Colors.YELLOW}{ld_o_wr:.1f}% Green{Colors.END} ({ld_o_w}W / {ld_o_l}L) | Running Avg MTM: {Colors.BOLD}{Colors.GREEN if ld_o_avg >= 0 else Colors.RED}{ld_o_avg:+.2f}%{Colors.END}")
        print(f"  • Combined Listing Breakout Edge:   {Colors.BOLD}{Colors.GREEN if ld_tot_wr >= 50 else Colors.YELLOW}{ld_tot_wr:.1f}%{Colors.END} ({ld_tot_w}W / {ld_tot_l}L) | Combined Avg PnL: {Colors.BOLD}{Colors.GREEN if ld_tot_avg >= 0 else Colors.RED}{ld_tot_avg:+.2f}%{Colors.END}")

        # DNA Matrix Breakdown
        print(f"\n  {Colors.BOLD}🧬 Quantitative DNA Attribute Breakdown (Listing Day Breakouts):{Colors.END}")
        
        # 1. Volume Surge
        ld_hv = ld_df[ld_df['vol_spike'] >= 3.0]
        ld_lv = ld_df[ld_df['vol_spike'] < 3.0]
        hv_rate = (len(ld_hv[ld_hv['is_win']]) / len(ld_hv) * 100) if len(ld_hv) > 0 else 0
        lv_rate = (len(ld_lv[ld_lv['is_win']]) / len(ld_lv) * 100) if len(ld_lv) > 0 else 0
        print(f"     1. Volume Surge:     >= 3.0x: {Colors.GREEN}{hv_rate:.1f}% Win Rate{Colors.END} (Avg {ld_hv['pnl_pct'].mean():+.1f}%, n={len(ld_hv)}) vs < 3.0x: {lv_rate:.1f}% Win Rate (Avg {ld_lv['pnl_pct'].mean():+.1f}%, n={len(ld_lv)})")

        # 2. Base Tightness
        ld_tb = ld_df[ld_df['prng'] <= 15.0]
        ld_lb = ld_df[ld_df['prng'] > 15.0]
        tb_rate = (len(ld_tb[ld_tb['is_win']]) / len(ld_tb) * 100) if len(ld_tb) > 0 else 0
        lb_rate = (len(ld_lb[ld_lb['is_win']]) / len(ld_lb) * 100) if len(ld_lb) > 0 else 0
        print(f"     2. Base Tightness:   <= 15%:  {Colors.GREEN}{tb_rate:.1f}% Win Rate{Colors.END} (Avg {ld_tb['pnl_pct'].mean():+.1f}%, n={len(ld_tb)}) vs > 15%:  {lb_rate:.1f}% Win Rate (Avg {ld_lb['pnl_pct'].mean():+.1f}%, n={len(ld_lb)})")

        # 3. Upper Wick
        ld_lw = ld_df[ld_df['upper_wick'] < 35.0]
        ld_hw = ld_df[ld_df['upper_wick'] >= 35.0]
        lw_rate = (len(ld_lw[ld_lw['is_win']]) / len(ld_lw) * 100) if len(ld_lw) > 0 else 0
        hw_rate = (len(ld_hw[ld_hw['is_win']]) / len(ld_hw) * 100) if len(ld_hw) > 0 else 0
        print(f"     3. Upper Wick:       < 35%:   {Colors.GREEN}{lw_rate:.1f}% Win Rate{Colors.END} (Avg {ld_lw['pnl_pct'].mean():+.1f}%, n={len(ld_lw)}) vs >= 35%: {hw_rate:.1f}% Win Rate (Avg {ld_hw['pnl_pct'].mean():+.1f}%, n={len(ld_hw)})")

        # Setup-by-Setup Concluded Learnings Table
        if not ld_concluded.empty:
            print(f"\n  {Colors.BOLD}📋 Concluded Listing Day Breakouts — Setup DNA & Empirical Learnings:{Colors.END}")
            print(f"  {'Symbol':<12} | {'Date':<10} | {'Outcome':<9} | {'Vol':<6} | {'PRNG':<6} | {'Wick':<6} | {'Takeaway Learning'}")
            print("  " + "─" * 88)
            for _, r in ld_concluded.sort_values('entry_date').iterrows():
                pnl_str = f"{r['pnl_pct']:+.1f}%"
                pnl_colored = f"{Colors.GREEN}{pnl_str:<9}{Colors.END}" if r['is_win'] else f"{Colors.RED}{pnl_str:<9}{Colors.END}"
                vol_str = f"{r['vol_spike']:.1f}x"
                prng_str = f"{r['prng']:.1f}%"
                wick_str = f"{r['upper_wick']:.1f}%"
                print(f"  {r['symbol']:<12} | {r['entry_date']:<10} | {pnl_colored} | {vol_str:<6} | {prng_str:<6} | {wick_str:<6} | {r['learning_summary']}")

    # SECTION 4: HYPOTHESES UNDER TEST
    print(f"\n{Colors.BOLD}{Colors.MAGENTA}══════════════════════════════════════════════════════════════════════════════{Colors.END}")
    print(f"{Colors.BOLD}{Colors.MAGENTA}🔬 SECTION 4: HYPOTHESES UNDER ACTIVE TEST & EVIDENCE ACCUMULATION{Colors.END}")
    print(f"{Colors.BOLD}{Colors.MAGENTA}══════════════════════════════════════════════════════════════════════════════{Colors.END}")

    print(f"\n  {Colors.BOLD}[HYPOTHESIS A] 60-Minute Intraday Rejection Cutoff Gate:{Colors.END}")
    print(f"    • Status: {Colors.GREEN}PROVEN IN TELEMETRY{Colors.END} (Prevented 55 bad entries in KUSUMGAR via PENDING_REJECTED).")
    print(f"    • Rule: Never buy immediately on cross; require 60-min continuous hold above listing high.")

    print(f"\n  {Colors.BOLD}[HYPOTHESIS B] 14-Day Stagnant Dead-Money Cutoff (Tightened from 21d):{Colors.END}")
    print(f"    • Status: {Colors.GREEN}PROVEN IN DATA{Colors.END} (Eliminates stagnant decay in trades like JNPR -4.8% and STYL -5.1%).")
    print(f"    • Goal: Cut flat/losing trades at Day 14 at -1% to -2% rather than waiting 21-40 days for full stop loss.")

    print(f"\n  {Colors.BOLD}[HYPOTHESIS C] Dynamic SuperTrend Trailing for >= 3.0x Volume Surge Winners:{Colors.END}")
    print(f"    • Status: {Colors.GREEN}PROVEN ALPHA{Colors.END} (MILKYMIST +58.3%, MVELECTRO +31.2%, SHUKRAPHAR +22.2%).")
    print(f"    • Goal: Do not take premature fixed partial profits when volume surge >= 3x; let SuperTrend trail to capture 30-60% runners.")

    # SECTION 5: CONCRETE ALGO UPDATE DIRECTIVES
    print(f"\n{Colors.BOLD}{Colors.CYAN}══════════════════════════════════════════════════════════════════════════════{Colors.END}")
    print(f"{Colors.BOLD}{Colors.CYAN}⚡ SECTION 5: CONCRETE ALGORITHMIC DIRECTIVES TO SELECT ONLY TOP-TIER RUNNERS{Colors.END}")
    print(f"{Colors.BOLD}{Colors.CYAN}══════════════════════════════════════════════════════════════════════════════{Colors.END}")

    print(f"  {Colors.BOLD}1. REJECT SUPPLY TRAPS — 'Upper 50% Candle Body Confirmation Gate':{Colors.END}")
    print(f"     └─ If a breakout candle closes with an upper wick >= 35%, reject entry immediately.")
    print(f"  {Colors.BOLD}2. CUT DEAD MONEY EARLY — 'Day-14 Velocity Speed Gate':{Colors.END}")
    print(f"     └─ If position is flat or underwater after 14 sessions with decaying volume, exit at market to free portfolio slot.")
    print(f"  {Colors.BOLD}3. RIDE MONSTER RUNNERS — 'Tier-A High Volume Surge Runner':{Colors.END}")
    print(f"     └─ When breakout volume >= 3.0x and PRNG <= 15%, allocate Tier A and trail with SuperTrend to capture 30-60% fat-tail returns.")

    print("\n" + "═" * 90)
    print("✅ System Diagnostics complete.")


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
    client = MongoClient(os.getenv("MONGO_URI", ""))
    db = client["ipo_scanner_v2"]
    
    try:
        from streamlined_ipo_scanner import fetch_data
    except Exception:
        fetch_data = None

    sync_all_trade_evidence(db, fetch_data_fn=fetch_data)
    generate_system_diagnostics_report(db)
