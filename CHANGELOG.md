# 📋 Changelog — AlphaPulse (IPO-Base-Scanner)

All notable changes, quantitative safeguards, and alerting architecture updates are documented in this file.

---

## [v3.6.0] — 2026-10-21 (MILESTONE bump: hardened execution release; entry rules unchanged)

* **Why a milestone bump:** the uncommitted hardening of v3.5.5-v3.5.7 changes how capital is allocated and exited live (flat 10 x 10% slots instead of 5/7, close-based stop alerts,
  atomic portfolio commit lock, fail-safe corporate-action guard, shelf and hourly signals PAPER_ONLY by default, resting-order tooling). Rows written after deployment are tagged `3.6.0`
  so the slot-cap regime can be separated from the 5/7-slot `3.5.0` rows. **No entry rule, exit rule, gate or stop was changed by the bump itself.**
* **Synchronized touchpoints (all six, one change):** `master_audit.EXPECTED_VERSION`, `SCANNER_VERSION` in the three scanners and `db.py`, README badge (and footer).
* **Cohort integrity:** new `core/versioning.py` (`NATIVE_FORWARD_VERSIONS = ("3.5.0", "3.6.0")`, cutoff 2026-09-29). `analyze_positions_performance.py`, `monthly_strategy_audit.py` and
  `core/strategy_evidence.py` now use it, so the native forward scorecard does not go empty after the bump. Guarded by `test_versioning.py` (touchpoints match; cohort includes the current version).
* **Docs fixed:** `SYSTEM_KNOWLEDGE.md` listed `DEAD_MONEY_DAYS_IPO` as 20; the code default is 14.
* **Research only, no live change:** second setup S2 (pivot breakout below the listing high) was tested and rejected (`LEARNINGS.md` section 18).

---

## [v3.5.7] — 2026-10-17 (patch, no milestone bump, nothing committed)

### Shadow score (LOGGED ONLY) and minute-bar research (see `LEARNINGS.md` section 17, `reports/2026-10-17/`)
* **Added:** `core/shadow_score.py` (feature formulas, market state, model train/load/score; scikit-learn optional), `train_shadow_model.py`, `shadow_score.py`
  (post-session scoring and forward report), `test_shadow_score.py` (7 tests, including a check that the features reproduce `research_event_model.events()` exactly).
* **Changed (additive, behaviour-neutral):** `order_sheet.py` adds `listing_day_volume` to the listing projection and a `shadow` field to each logged resting order
  (features known before the open, status `awaiting_gap`).  A test asserts the placed orders and skip notes are identical with and without it; any exception in the hook is swallowed.
* **Not changed:** any scanner gate, exit, stop, slot cap, entry rule, or which orders are generated.  The live volume floor, close-location gate, 60-minute hold and +3.5% chase cap are unchanged (replay evidence in LEARNINGS 17.3; decision pending with the owner).
* **Scraped research data (local only, gitignored):** `fetch_ipo_meta.py` collects issue size, issue price and subscription multiples from Trendlyne public monthly pages (52 requests, 2 s apart, robots.txt respected); `research_ipo_meta_signal.py` tests them (LEARNINGS 17.9: no signal).
* **Research tooling:** `fetch_minute_bars.py` (resumable Upstox V3 1-minute download, local files only), `research_*` scripts listed in LEARNINGS 17, `.gitignore` entries `data_minute/` and `models/`.

---

## [v3.5.6] — 2026-10-14 (patch, no milestone bump)

### 🎯 Execution and stop-style research (see `LEARNINGS.md` section 12, `reports/2026-10-14/`)
* **Corrections:** the "live-faithful" engine assumed an intraday stop; live stops are close-based (6:30 PM scan, sell next open).
  Entry gates also used same-day volume, which a pre-placed order cannot know. Both are now selectable and measured (`causal=True`,
  `exit_fill="eod_next_open"`); legacy parity is still EXACT.
* **Findings (paired, symbol-cluster bootstrap):** a pre-placed stop-limit entry beats buying after the alert by +1.3 to +1.9pp per
  trade (every year); the live close-based stop beats an intraday resting stop by +2.0pp (every year); no pre-registered quality
  filter, exit variant, risk-sized position or drawdown throttle is adoptable. Policy P profit factor: 1.67 closed-only, 2.45 with
  open runners at MTM, 1.14 without the top three trades.
* **Tooling (additive, scanner untouched):** `order_sheet.py` defaults to close-based stop alerts with an optional disaster GTT and
  prints pre-market resting entry orders from the backtest's own gates (`preview_resting_entry`); `--log` writes
  `logs/resting_orders.jsonl`. Engine: `simulate_symbol_cross`, `include_open`, `cat_stop_pct`, `breakeven_*`; portfolio sim:
  `risk_budget_pct`, `dd_half_pct`, `dd_halt_pct` (all default off). New: `research_alpha_lab.py`, `research_risk_profile.py`,
  `test_alpha_lab.py`, `research_tail_capture.py`, `research_dead_money.py`, `research_small_account.py`.
* **Small-account study (research only, 2026-10-15):** Rs 50k to Rs 1 lakh, 10 slots x 10%: no account fell to 50% in 41 real 12-month windows or 250 resampled histories per cell (structural bound: one trade costs about 1.2% of the account, worst replayed 1.7%). A losing first year is still likely (real windows 41-49%, resampled 18-32%); Rs 50k pays 1.78% round-trip charges on Rs 5,000 positions vs 1.15% at Rs 10,000. 10 x 10% takes 48 of 52 signals a year; 5 x 20% takes 34 and risks a 30% drawdown in 5-15% of histories.
* **Quality vs quantity, PF target (reporting only):** every pre-registered quality rule lowered out-of-sample PF (kept 1.80-2.14 vs 2.28 for all trades; the rejected trades had PF 2.5-4.0), so no selection rule is added. Policy P PF is 2.45 incl. open runners (90% interval 1.67-3.43), 1.67 closed-only (1.02-2.50), 1.14 without the top 3. `paper_scorecard.py` now prints a profit-factor monitor (`core.scorecard.pf_report`, `--pf-target`); the STOP / CONTINUE / SCALE decision is unchanged.
* **Dead-money study (research only):** 36% of trades end as dead money (-4.6% each, 14% of capital-days) in every pattern tested; no pattern separates it from the outliers that follow. The shelf breakout, modelled under the corrected entry and stop assumptions, is positive but weaker than the listing-high breakout (+3.4% vs +5.3% net, 44% vs 36% dead money) and stays PAPER_ONLY. Rotating capital out of dead-looking positions was worse in every scenario (it sold SIGNATURE +138%, SHUKRAPHAR +51%). Engine additions are default-off: `simulate_symbol_cross(setup="shelf")`, `PortfolioConfig.rotate_after_days`.
* **Tail study (research only):** 10.6% of trades (+25% or more) produce 77% of gross profit and no entry feature predicts them (AUC 0.39-0.59), so no quality filter is added. Re-entry, pyramiding and conviction sizing did not clear the bar. `DEAD_MONEY_RUNUP_IPO=0` (keep positive sleepers past day 14) is a reversible bounded bet (+1.18pp per trade, CI -0.13..+3.11, driven by 2 runners); default unchanged.
* **Doc fix:** the shelf "62.8% win rate" is a peak-run-up measure, not a realized result (honest backtest: -2.36% net, PAPER_ONLY).
* **Slot caps 5/7 -> 10/10 (live behaviour change, requested 2026-10-14):** one flat cap of 10 positions, 10% equal weight, defined once in
  `core/portfolio_caps.py` and read by the three scanners and `order_sheet.py`. Replay: about 5-8pp less max drawdown at equal or higher CAGR in
  three of four fill scenarios, neutral under today's chase-style fills; no change to any single trade's expectancy. Applies to NEW signals only
  (stored PAPER_ONLY rows are not promoted). Rollback: `MAX_ACTIVE_POSITIONS=5` and `HARD_ACTIVE_POSITIONS=7`. Tests: `test_portfolio_caps.py`.

---

## [v3.5.5] — 2026-10-12 (patch, no milestone bump)

### 🛡️ Parity Audit, Fail-Safe Guards & Honest Backtest (see `HARDENING_REPORT.md`)
* **Audit result:** `run_latest_rules_backtest.py` (PF 2.27) did not follow the live rules. With a live-faithful
  engine (`run_honest_backtest.py`, legacy parity EXACT) the same history gives 79 trades, PF 1.24, +0.79% gross
  per trade. The "verified" v3.5.4 figures above describe the legacy backtest, not live behaviour.
* **Shared exit rules:** `core/exit_rules.py` is now the single definition used by `stop_loss_update_scan`, the honest
  backtest and the tests (behaviour identical, proven on 40,000 random states).
* **Fail-safe Corporate Action Guard:** only split/bonus-ratio drops pause a position; other large drops run the normal stop.
* **Atomic slot commit:** `core/locks.py` mutex around count -> cap -> insert; lock failure stores PAPER_ONLY.
* **Capital policy:** `SHELF_BREAKOUT` and hourly `INTRADAY` signals are PAPER_ONLY by default
  (`LISTING_SHELF_LIVE_CAPITAL_ENABLED`, `HOURLY_LIVE_CAPITAL_ENABLED`).
* **New tooling:** `order_sheet.py` (resting-stop levels, whole-share sizing), `core/risk_guard.py` (drawdown kill-switch),
  `paper_scorecard.py` (pre-registered STOP/CONTINUE/SCALE rules), research scripts (`research_*.py`).

### 🔎 Review of the audit (2026-10-13, documentation and research only, no scanner change)
* **Correction:** the audit engine was biased against the strategy: end-of-day gating with next-open fills rejects strong
  breakout days (MVELECTRO missed by 0.15 points on the 3.5% extension cap), trades still open at the end of the data are
  dropped (KSHINTL +183%), and IPOs with fewer than 10 candles are skipped (MANIKA, STEAMHOUSE, WEWORK, LGEINDIA).
* **Corrected estimate (listing breakouts, intraday-crossing fills, 1% costs):** N=177, +2.45% net at a +1.5% fill (PF 1.59),
  +0.57% at +1% worse, -0.93% at +2% worse. Native fills average +3.2% above the listing high, so realistic net is about 0%.
  Weakly positive to break-even, not proven. Payoff profile: 39% win, +13.4% avg win, -7.2% avg loss, ~38 trades a year.
* **New:** `research_intraday_proxy.py`, `research_live_parity.py`, `reports/2026-10-13/`; `LEARNINGS.md` sections 7-11
  (live-capital roadmap and gates); `AGENTS.md` section 7 rules 8-11.

---

## [v3.5.4] — 2026-10-10

### 🎯 IPO Discovery Lifecycle Realignment & Structural Volatility Cushion
* **IPO Discovery Horizon Realignment (<= 60 Calendar Days):**
  * Realigned the listing breakout lifecycle from legacy 730-day (2-year) ceiling to a disciplined **60-calendar-day window** across `listing_day_breakout_scanner.py` (`MAX_DAYS_SINCE_LISTING_FOR_BREAKOUT = 60`, `LISTING_TIER_A_MAX_AGE_DAYS = 60`, `LISTING_TIER_APLUS_MAX_AGE_DAYS = 60`, `LISTING_TIER_B_MAX_AGE_DAYS = 60`).
  * Eliminates 150+ stale, secondary-market zombie IPOs (100–700 days old) that historically degraded system performance (25.9% to 28.0% win rate).
  * Validated via continuous parameter sensitivity sweep showing a stable plateau from 15d to 60d (PF 1.78) and walk-forward out-of-sample stability in 2025–2026.
* **Structural Volatility Cushion Enforcement (>= 5.0% Listing Day Range):**
  * Added `LISTING_MIN_RANGE_CUSHION_PCT = 5.0%` in `_assign_breakout_tier()`.
  * Structurally disqualifies compressed, flat listing days (< 5.0% range) that lack price discovery and institutional participation.
  * Proven to scale average returns monotonically from +2.8% to +15.5% and Profit Factor from 1.73 to 4.77.
* **Synchronized Dynamic Backtest Engine (`run_latest_rules_backtest.py`):**
  * Directly imports and binds to live production constants (`lscan.MAX_DAYS_SINCE_LISTING_FOR_BREAKOUT`, `lscan.LISTING_MIN_RANGE_CUSHION_PCT`).
  * Enforces exact single-writer execution order, local swing low stop loss, 14-day velocity gate, and 2-stage trailing stops.
  * Verified multi-year performance across 848 listings (2022–2026):
    * **196 Trades | 45.41% Win Rate | +4.48% Avg Return | 2.27 Profit Factor**
    * **`SHELF_BREAKOUT`**: N=140 | 47.9% Win Rate | +3.81% Avg | 2.17 PF
    * **`LISTING_BREAKOUT`**: N=56 | 39.3% Win Rate | +6.16% Avg | 2.46 PF
    * Gross Gains +1,571.3% vs Gross Losses -692.9%, maximum single loss strictly capped at -12.00%.

---

## [v3.5.3] — 2026-10-08

### 🛡️ Session-Boundary Lifecycle Hardening & Execution Invariants (Approach A)
* **Intraday Cutoff Guard (14:30 IST):**
  * Enforced `LISTING_PENDING_CUTOFF_TIME = dt_time(14, 30)` IST in `listing_day_breakout_scanner.py`.
  * Blocks any new 60-minute pending observation window when fewer than 60 minutes remain before the 15:30:00 IST close (`pending_cutoff_exceeded`). Eliminates partial, unverifiable intraday breakouts.
* **Strict Market Hours Boundary & After-Hours Protection:**
  * Top-level guard in `scan_listing_day_breakouts()` immediately halts candidate scanning outside 09:15–15:30 IST, runs EOD pending state cleanup/purging, and terminates cleanly in ~1 second.
  * Closes the after-hours vulnerability where post-market dispatcher runs (16:15 IST) could evaluate candidates against empty pending dictionaries.
  * Added defense-in-depth guard in `commit_trade_to_db()` strictly refusing live `ACTIVE` position commits outside market hours.
* **Universal Real-Time Auto-Eviction:**
  * Coupled `_log_listing_rejection()` directly to `_eject_pending()`. Any candidate in observation failing any quality gate (liquidity, volume ratio, upper-wick selling exhaustion, pivot hold) is instantly evicted.
  * Added real-time MongoDB `$unset` to `_eject_pending()` so dropped candidates are removed from `db.pending_states` immediately without waiting for scan loop termination.
* **Atomic State Mutual Exclusivity:**
  * `commit_trade_to_db()` atomically executes an `$unset` on `db.pending_states` upon trade entry, guaranteeing that a candidate is never simultaneously `pending` and `active`.
* **Confirmation Engine Parity:**
  * Added `'BASE_BREAKOUT'` to the pending confirmation tuple alongside `'BREAKOUT'` and `'SHELF_BREAKOUT'`.
* **Telegram Dispatcher Integrity Fix (utils.py):**
  * Fixed `NameError: name 'sys' is not defined` in `send_telegram_msg()` introduced in commit `77c9799`.
  * Added missing `import sys` to `utils.py`.
  * Added regression test `test_utils_telegram_dispatcher_integrity` to `test_regression_fixes.py` to prevent dispatch regressions.
* **Test Suite & Verification:**
  * Added `test_approach_a_pending_engine_and_cutoff_guards` covering all 5 boundary invariants to `test_regression_fixes.py`.
  * Verified 100% green test suite (37/37 passing).

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
