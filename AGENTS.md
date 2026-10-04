# 🤖 AGENTS.md — Development Guidelines & System Architecture (AlphaPulse)

This file defines the core engineering standards, domain knowledge, and operational protocols for any AI agent or engineer working in the **AlphaPulse** repository.

---

## 🏛️ 1. Repository Purpose & Architecture

**AlphaPulse** is a behavior-driven quantitative trading system that identifies, validates, and manages high-conviction momentum breakouts in Indian equities (Mainboard NSE/BSE).

### Core Engines & Scanners
1. **Listing Day Breakout Engine (`listing_day_breakout_scanner.py`):**
   - Targets IPOs from listing day up to 730 calendar days (2 years).
   - Validates initial breaks of Day-1 listing high or tight local bases.
   - Enforces 60-minute holding observation (`PENDING` state) to reject intraday wick traps.
   - Hard 12% risk stop-loss cap dynamically buffered by 3% below the 15-day swing low.
2. **Consolidation Breakout Engine (`streamlined_ipo_scanner.py`):**
   - Targets IPOs 10–200 days post-listing breaking out of tight multi-day bases.
   - Scan windows: 10 & 20 days (narrowed from 40/80/120 on 2026-07-05).
   - Currently operating in forced **Out-of-Sample Forward Testing Mode (`PAPER_ONLY`)**.
3. **Watchlist Hourly Scanner (`hourly_breakout_scanner.py`):**
   - Intraday alerting engine for active watchlist symbols.
   - Requires ≥1.5x breakout-bar volume; never overwrites open daily positions.
4. **Trade Diagnostics & Strategy Evidence Engine (`diagnose_trade.py` & `core/strategy_evidence.py`):**
   - Self-diagnosing intelligence layer that evaluates trade DNA (volume surges, base PRNG %, upper wick exhaustion) against real trade outcomes to continuously uncover proven strengths and eliminate strategy leaks.

---

## 🛡️ 2. Clean-Cohort Baseline & Statistical Integrity

> [!IMPORTANT]
> **CLEAN COHORT CUTOFF: `2026-07-05`**
> On 2026-07-05 (v3.3.0 parameter tightening), flawed legacy rules were eliminated:
> 1. Grade C setups were permanently disqualified.
> 2. Wide 40/80/120-day consolidation windows were removed.
> 3. Strict volume floors (≥150,000 shares) and turnover thresholds (≥ Rs 1 Cr) were made mandatory.

> [!IMPORTANT]
> **NATIVE v3.5.0 FORWARD PRODUCTION CUTOFF: `2026-09-29`**
> In October 2026, an exhaustive forensic audit discovered that out of 22 positions in the active `positions` collection, **20 were backfilled** (`_backfilled: True`) from pre-v3.5.0 historical triggers.
> Because they were backfilled during research, their documents carried `"version": "3.5.0"`, even though they entered under legacy flawed conditions (e.g. fatal 42–46% upper-wick supply traps or sub-1.0x volume spikes) that current v3.5.0 rules actively reject!
> 
> **Cohort Isolation Rule for Analytics, Performance & Audits:**
> 1. **Pure Native Forward Scorecard:** ONLY includes trades where `entry_date >= '2026-09-29'`, `version == '3.5.0'`, and `_backfilled != True` (or `is_native_v350: True`).
> 2. **Historical Backfilled Benchmark:** Trades marked `_backfilled: True` or entered pre-September-29 must be reported in their own isolated cohort for backtest validation and research post-mortems. They MUST NEVER be conflated with the live production algorithm scorecard.

### Rules for All Analytics & Audit Scripts
* **Never include pre-July-5, 2026 data in active strategy statistics.**
* Any new analytical or statistical script **MUST** filter `entry_date >= '2026-07-05'` and `signal_date >= '2026-07-05'`.
* Pre-July-5 historical records are safely preserved in:
  - `positions_legacy_archive` (MongoDB)
  - `signals_legacy_archive` (MongoDB)
* Active collections `positions` and `signals` must remain 100% clean-cohort data only.

---

## 💾 3. Database Schema & Conventions (MongoDB)

* **Database Name:** `ipo_scanner_v2`
* **Key Collections:**
  - `positions`: Active, paper, and clean closed trades.
  - `signals`: High-conviction breakout signals.
  - `strategy_evidence`: Granular setup DNA paired with empirical trade outcomes.
  - `logs`: Daily scanner execution and telemetry logs.
  - `daily_candles_cache`: Fast local cache for Upstox daily OHLCV candles.
  - `instrument_keys`: Upstox instrument key mappings.
  - `positions_legacy_archive` & `signals_legacy_archive`: Isolated historical archives.

### Status Field Conventions
- `ACTIVE`: Live capital allocated trade.
- `PAPER_ONLY`: Forward-testing / portfolio-cap overflow paper trade.
- `CLOSED`: Realized live trade.
- `PAPER_CLOSED`: Realized paper trade.

---

## 🔍 4. Forensic Diagnostics & System Evaluation Tools

When diagnosing setups, investigating failures, or auditing strategy health, use the built-in CLI tools:

```powershell
# 1. System-Level Self-Diagnosis (Strengths, Weaknesses, Hypotheses & Edge Directives)
python diagnose_trade.py --system

# 2. Sync all trade forensics into MongoDB collection 'strategy_evidence'
python diagnose_trade.py --sync-evidence

# 3. Diagnose specific symbols with Strengths, Weaknesses, and Algo Takeaways
python diagnose_trade.py KUSUMGAR CMRGREEN --vs-winners

# 4. Diagnose all currently active portfolio positions
python diagnose_trade.py --active-only

# 5. Unified MongoDB management entrypoint
python manage_db.py diagnose --system
python manage_db.py diagnose --symbols KUSUMGAR CMRGREEN --vs-winners
```

---

## 🚦 5. Quantitative & Risk Management Rules (v3.5.0 Standards)

1. **Upper 50% Candle Body Confirmation Rule:**
   - Any breakout attempt must close in the upper 50% of its total daily range: `(CLOSE - LOW) / (HIGH - LOW) >= 0.50`.
   - Rejects long shooting stars and upper supply traps (e.g. `KUSUMGAR`) structurally without curve-fitting narrow wick thresholds.
2. **14-Day / 10-Trading-Session Velocity Speed Gate (Industry-Standard Momentum Rule):**
   - Any position held `≥ 14 calendar days` OR `≥ 10 NSE trading sessions` (calculated dynamically using the NSE holiday calendar via `utils.count_trading_days`) that remains flat or underwater (`PnL <= 0.0%`) is exited immediately (`Time Stop - Dead Money`).
   - **Loophole Closed:** Eliminated the legacy `new_max_runup < 3.5%` condition that trapped underwater trades like `ARCIL` (+4.3% Day 3 peak, then drifting to -3.9% on Day 13) into holding all the way down to full stop loss.
   - **Empirical Proof:** Across 104 historical trades, 0 out of 46 trades underwater after 10 sessions / 14 days ever recovered to become multi-baggers; 100% drifted into full stop losses. Cutting at Session 10 / Day 14 at -1% to -3% preserves portfolio capital.
   - Confirmed momentum runners (`max_runup >= 15%`) are protected by the Super-Winner Archetype lock.
3. **Anti-Chasing 8% Max Extension Guard:**
   - Breakout entries $> 8.0\%$ extended above the base breakout pivot or listing high are rejected to prevent buying overheated tops.
4. **Base Peak Re-Entry Engine with 5-Day Cooldown & Institutional Volume Floor:**
   - **5-Day Quarantine:** Requires `5d <= days_since_exit <= 30d` before a closed stock is eligible for re-entry, completely eliminating next-day whipsaw churn.
   - **Institutional Volume Floor:** Requires `volume_spike >= 1.50x` on re-entries, ensuring positions are only taken on genuine institutional accumulation rather than low-volume noise.
5. **Volume Surge Multiplier:**
   - High-conviction Tier A setups require `≥ 3.0x` volume surge over the 20-day average.
   - Setups with volume spike `< 1.5x` or volume `< 150,000` shares are automatically rejected.
6. **Base Tightness (PRNG):**
   - 10-day price range (PRNG) must be `≤ 15%` to ensure coiled volatility and tight risk stops.
7. **Day-0 Chronology Safeguard:**
   - Day-0 entries (`days_held == 0`) are completely exempt from trailing stop adjustments and time stops, preserving initial structural risk floors for entry-day noise.
8. **Consolidated Institutional Telegram Suite:**
   - Replaces noisy individual per-trade alerts with a single batched `🛑 STOP LOSS ADJUSTMENTS` card and a unified EOD `📊 DAILY PORTFOLIO & RISK REPORT` tracking live book, paper book, setup types (`Listing BO`, `Grade B`), Winner Scores (`💎 4/4 🔥`), Shadow Multi-Stop Tracking (Top 5 Performers & Defensive Cuts), and recent exits.
9. **Backtest & Quant Integrity:**
   - Never alter trading rules or backtesting logic silently.
   - Backtest integrity and statistical rigor take precedence over code aesthetics.

---

## 📋 6. Agent Workflow & Safety Checklist

Before delivering code or modifying the repository:
1. **Minimal Blast Radius:** Prefer targeted, additive, modular changes over broad refactors.
2. **Verify Imports & Types:** Ensure all imports and references exist.
3. **Preserve Immature Hypotheses:** Do not enable experimental gates on small sample sizes (see `EXPERIMENT_CHANGELOG.md`).
4. **Validate Against Clean Cohort:** Test queries against `positions` and `strategy_evidence` using `2026-07-05` cutoff.
5. **Never Destructively Overwrite DB Collections:** Always use upsert or archival tables.
