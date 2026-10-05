# 📋 Changelog — AlphaPulse (IPO-Base-Scanner)

All notable changes, quantitative safeguards, and alerting architecture updates are documented in this file.

---

## [v3.5.2] — 2026-10-05

### 🚀 High Shelf / Wave 2 Breakout Engine (`SHELF_BREAKOUT`)
* **Inculcated Secondary Base Breakouts in Main Listing Engine:**
  * Updated `listing_day_breakout_scanner.py` with `_detect_high_shelf_breakout()` to eliminate the coverage dead zone on Days 3–30 post-listing.
  * Replaces the artificial hard rejection at `entry_above_high_pct > 3.5%` with a strict local consolidation shelf validation.
  * Empirically validated across 2024–2026 IPO universe ($N=145$ setups): **62.8% win rate (peak runup $\ge 10\%$)** and **+18.04% average peak runup** (e.g. `MILKYMIST` +50% peak runup on Day 6).
* **Six Quantitative Anti-Forcing & Anti-Chasing Guardrails:**
  * **Anti-Chasing Pivot Ceiling:** Enforces `entry <= shelf_high * 1.05` (must enter within $\le 5.0\%$ of base pivot).
  * **Base Tightness Guardrail:** Requires prior 3 to 8 bars to form a tight base (`PRNG <= 18.0%`) with base floor $\ge 88\%$ of listing high.
  * **Upper 50% Candle Body Gate:** Breakout candle must close in top half of daily range (`(Close - Low) / (High - Low) >= 50%`), eliminating upper wick traps.
  * **Institutional Liquidity Floor:** Requires volume spike $\ge 1.8	imes$ or daily institutional turnover $\ge ₹5.0	ext{ Cr}$.
  * **60-Minute Observation Hold:** Intraday candidate held in `PENDING` queue; dropped if price slips below pivot or $>2.5\%$ from high.
  * **Structural Stop Loss:** Anchored at shelf support floor with strict $12\%$ maximum drawdown cap.
* **Testing & Regression Suite:**
  * Added `test_high_shelf_breakout_qualification` to `test_regression_fixes.py`.
  * Verified 100% green test suite across 34/34 unit tests.

---

## [v3.5.1] — 2026-10-04

### 🛡️ Production Hardening, Velocity Speed Gate & Cohort Isolation
* **Velocity Speed Gate Overhaul & Trading Session Engine:**
  * Updated `streamlined_ipo_scanner.py` and `run_latest_rules_backtest.py` to enforce `(days_held >= 14 or trading_sessions >= 10) and pnl <= 0.0%`.
  * Eliminated the `new_max_runup < 3.5%` loophole that trapped underwater trades (`ARCIL` at -3.90% with Day 3 peak +4.3%) into holding through dead money towards full stop loss.
  * Added dynamic NSE holiday & weekend trading session counter `count_trading_days(start_date, end_date)` in `utils.py`.
  * Added full unit and regression test coverage in `test_regression_fixes.py` (33/33 tests passing, 100% green).
* **Strict Multi-Cohort Database Isolation:**
  * Identified that 20/22 database positions were historical backfills (`_backfilled: True`) stamped with `version: 3.5.0` despite entering under legacy rules (e.g. 42–46% upper wicks).
  * Upgraded `listing_day_breakout_scanner.py`, `streamlined_ipo_scanner.py`, and `hourly_breakout_scanner.py` to explicitly stamp `_backfilled: False` on all new live/paper positions and signals.
  * Tagged historical backfill scripts (`historical_backfill.py`) with `_backfilled: True`.
  * Upgraded `core/strategy_evidence.py` to classify and tag `is_native_v350` and `_backfilled` cohorts.
* **Portfolio & Strategy Audit Upgrades:**
  * Re-architected `analyze_positions_performance.py` into 4 cleanly isolated reporting sections: (1) Pure Native v3.5.0 Live Production Scorecard, (2) Backfilled Active Trades, (3) Paper-Only Overflow Portfolio, and (4) Realized Exits.
  * Updated `monthly_strategy_audit.py` to report the Native Forward v3.5.0 Scorecard separately from historical benchmarks.

---

## [v3.5.0] — 2026-09-27

### 🛡️ Quantitative Risk & Re-Entry Safeguards
* **Peak-Gated 14-Day Velocity Speed Gate:**
  * Updated `streamlined_ipo_scanner.py` to only exit at Day 14 if `days_held >= 14 and pnl <= 0.0 and new_max_runup < 3.5%`.
  * Protects developing base-builders (`SUDEEPPHRM`, `AEROPLANE`) from premature knife-edge chops while swiftly eliminating true dead money (`JNPR`, `SAATVIKGL`).
* **5-Day Re-Entry Cooldown Quarantine:**
  * Updated `db.py:get_reentry_watchlist()` to enforce `5d <= days_since_exit <= 30d`.
  * Eliminates 100% of next-day whipsaw churn loops where an exited position was immediately re-bought on Day 1.
* **Institutional Volume Surge Floor on Re-Entries:**
  * Updated `listing_day_breakout_scanner.py` to enforce `volume_spike >= 1.50x` on all re-entry attempts.
  * Rejects low-volume retail drift, ensuring re-entries participate strictly in genuine institutional accumulation.
* **Database & Position Hygiene:**
  * Reconciled `AEROPLANE` as a single, continuous, active trade from 11 Sep 2026 (`+2.61%` P&L, 15d held).
  * Purged duplicate Friday re-entry signals and cleaned strategy evidence telemetry.

---

## [v3.5.0] — 2026-09-26

### 📱 Unified Telegram Alert Suite & EOD Consolidation
* **Consolidated Stop Loss Alerts:**
  * Added `format_consolidated_sl_updates()` in `streamlined_ipo_scanner.py` to batch all daily trailing stop movements into a single clean `🛑 STOP LOSS ADJUSTMENTS` card.
* **Unified Daily Portfolio & Risk Report:**
  * Added `format_alphapulse_portfolio_report()` consolidating:
    * Live Positions with Trade Setup Types (`Listing BO`, `Standard`, `Grade B`, `Intraday`).
    * Paper / Forward Research Positions with Winner Traits Scores (`💎 Score: 4/4 🔥`).
    * Shadow Multi-Stop Sandbox Tracking (8%, 10%, 12% stops) with Active Counterfactual Runners (`LOTUSDEV` +45.2%).
    * Top 5 Shadow Performers & Defensive Loss Cuts.
    * Recent Exits chronologically sorted by exit date.
* **Real-Time Intraday Breakout Formatting:**
  * Updated `hourly_breakout_scanner.py:format_intraday_alert()` with exact trigger execution distance and `>3.5%` overextension warnings.
* **Day-0 Chronology Safeguard:**
  * Verified that Day-0 positions (`days_held <= 0`) are exempt from trailing stop moves and time stops to protect structural entry floors.

---

## [v3.4.0] — 2026-08-29

### 🧬 Setup DNA & Strategy Evidence Engine
* Implemented `diagnose_trade.py` and persistent `strategy_evidence` MongoDB collection for real-time post-trade forensics.
* Added Upper 50% Candle Body Gate (`(CLOSE - LOW) / (HIGH - LOW) >= 0.50`) to reject upper wick supply traps.
* Added 8% Anti-Chasing Extension Guard.
