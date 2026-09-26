# 📋 Changelog — AlphaPulse (IPO-Base-Scanner)

All notable changes, quantitative safeguards, and alerting architecture updates are documented in this file.

---

## [v3.5.1] — 2026-09-27

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
