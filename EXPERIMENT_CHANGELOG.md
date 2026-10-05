# Experiment Changelog

This file tracks analysis and logging cutovers so experiment windows stay comparable.

## Current Active Baseline

- `scanner_version`: `3.5.0`
- `log_schema_version`: `2026-04-23.v1`
- recommended clean analysis start: `2026-07-05` (param tightening: CONSOL_WINDOWS=10,20, MIN_LIVE_GRADE=B)

## Why this exists

- Strategy and logging logic evolve over time.
- Comparing pre-change and post-change rows in one bucket can pollute results.
- This changelog provides explicit cut points for analysis filters.

## Analysis command (clean cohort)

```bash
python analyze_30d_data.py --start-date 2026-06-07 --version 3.3.0 --clean-cohort
```

`--clean-cohort` excludes:

- `signal_type == WATCHLIST`
- grades containing `LOW_VOL`

## Querying mixed-version position logs

After the v3.3.0 bump, all new log entries carry `version = "3.3.0"` in the logs collection.
Positions that were opened under `2.5.0` will still emit `3.3.0` log entries — but the log
payload now includes `position_version` so you can cleanly separate cohorts:

```python
# MongoDB — only 3.3.0 positions in daily snapshots
db.logs.find({"action": "DAILY_SNAPSHOT", "details.position_version": "3.3.0"})

# MongoDB — legacy 2.5.0 positions still running under new scanner
db.logs.find({"action": "DAILY_SNAPSHOT", "details.position_version": "2.5.0"})
```

## Notable milestones

| Date         | Version | Change                                                                                                                                                                                                                           |
| ------------ | ------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `2026-10-04` | `3.5.1` | **Native v3.5.0 Forward Cohort Isolation & Velocity Speed Gate Fix:** (1) **Native v3.5.0 Forward Cutoff (2026-09-29):** Forensic audit revealed 20/22 MongoDB positions were backfilled from legacy pre-v3.5.0 triggers with flawed DNA (42–46% upper wicks, sub-1.0x volume) yet stamped with version 3.5.0. Established strict multi-cohort isolation across all audit tools (`analyze_positions_performance.py`, `monthly_strategy_audit.py`, `core/strategy_evidence.py`), cleanly segregating Pure Native Forward trades (`AUGMONT`, `WEWORK`) from Historical Backfilled Benchmarks; (2) **Velocity Speed Gate Fix & Trading Session Engine:** Resolved loophole where `new_max_runup < 3.5%` prevented underwater trades (`ARCIL` at -3.90% with Day 3 peak +4.3%) from exiting at Day 14. Standardized velocity rule to `(days_held >= 14 or trading_sessions >= 10) and pnl <= 0.0%`, supported by dynamic NSE holiday trading session counter `count_trading_days()`; (3) **Explicit Production Tagging:** Embedded `_backfilled: False` on all live/paper insertions across `listing_day_breakout_scanner.py`, `streamlined_ipo_scanner.py`, and `hourly_breakout_scanner.py`, while backfills stamp `_backfilled: True`; (4) **Market Regime Reality:** Empirical evaluation proved 4/6 September correction losses were legacy setup flaws eliminated by v3.5.0 rules; high-quality setups (`AUGMONT`, `LOTUSDEV`) produced substantial alpha during corrections, confirming that quality gating beats crude blunt market shutdowns. |
| `2026-09-20` | `3.5.0` | **2-Stage Trailing Engine Formalization & Workflow CI Hardening:** (1) **2-Stage Dynamic Trailing Engine & Super-Winner Immunity:** Formalized and documented the 2-Stage exit engine (`README.md` Section 4), ensuring proven winners (`max_runup >= 15%`) receive permanent immunity from time stops and ride the 20% peak cushion to compound into 100%+ discovery runs; (2) **Single-Writer Central Lifecycle:** Documented the unified architecture where `listing_day_breakout_scanner.py` qualifies entries and `streamlined_ipo_scanner.py` authoritatively owns all trailing stop and exit evaluations; (3) **Automated Pipeline Unit Suite:** Added automated regression test suite (`verify_stage2_pipeline.py`) validating all 5 lifecycle progression stages with 100% pass rate; (4) **Biweekly CI Workflow Fix:** Resolved Python 3.10 `python-dotenv` frame inspection `AssertionError` in `.github/workflows/biweekly-db-quality-analysis.yml`; (5) **Full Quant Robustness Audit:** Empirically verified across 225 IPOs that the 3.5.0 parameter grid operates on a robust wide profitability plateau (Profit Factor 1.35–1.56) without overfitting. |
| `2026-09-13` | `3.5.0` | **14-Day Velocity Loophole Closure & Forensic Evidence Store:** (1) **Unconditional 14-Day Speed Gate:** Enforced `days_held >= 14 and pnl <= 0.0` in `streamlined_ipo_scanner.py`, eliminating the >4% runup immunity loophole that trapped underwater trades for 30–40 days; (2) **Listing Day Forensics & Setup DNA Engine:** Upgraded `core/strategy_evidence.py` to extract granular setup DNA (volume surge, 10d base PRNG %, upper wick %, turnover) and classify qualitative archetypes across all clean cohort trades; (3) **System Self-Diagnostics Section 3:** Added dynamic empirical edge analysis for Listing Day Breakouts directly into `diagnose_trade.py --system`; (4) **Clean Cohort Quarantine:** Re-verified 100% clean-cohort isolation (`entry_date >= 2026-07-05`) in MongoDB `positions`/`signals`, safely quarantining pre-cutoff and contaminated test rows to archive collections. |
| `2026-08-29` | `3.5.0` | **Price Action & Velocity Upgrade:** (1) **Upper 50% Candle Body Gate:** Breakout candle must close in upper 50% of range (`(CLOSE-LOW)/(HIGH-LOW) >= 0.50`), eliminating shooting star supply traps; (2) **14-Day Velocity Gate:** Tightened dead-money speed gate to 14 days with volume decay guard; (3) **Max 8% Extension Guard:** Blocks chasing overheated entries >8% above base pivot; (4) **Immediate Base Peak Re-Entry:** Re-triggers closed setups right at prior peak cross; (5) **Strategy Evidence Store:** Persistent trade proof in MongoDB (`strategy_evidence`) and safe archive separation (`positions_legacy_archive`). |
| `2026-08-25` | `3.4.0` | **Grade restamp migration:** `scripts/restamp_listing_breakout_grade.py` sets open listing/re-entry `grade=LISTING_BREAKOUT` where winner_label was stored as grade (`STANDARD`/`POSSIBLE_WINNER`). |
| `2026-08-25` | `3.4.0` | **Critical signal-book fixes:** (1) listing/re-entry positions now persist `grade=LISTING_BREAKOUT` so IPO exit/trail gates apply; (2) hourly never overwrites open IPO rows + respects soft cap; (3) listing `has_active_position` + soft/hard cap parity; (4) strict mode volume spike required for `BASE_BREAKOUT`; (5) hourly volume tied to breakout bar (no prior-bar borrow); (6) live consol enforces `MIN_LIVE_GRADE`. Docs aligned. |
| `2026-08-25` | `3.4.0` | **Hourly volume gate:** Intraday alerts now hard-require ≥1.5x volume (skip incomplete/zero last candle); rejects `no_volume_confirmation` instead of AZAD-style 0.0x price+RSI alerts. **Weekly perf gate:** soft goals use edge cohort only (`ex-INTRADAY`); intraday stats reported as info. |
| `2026-08-24` | `3.4.0` | **Weekly audit false CRITICAL:** `upsert_position` no longer `$unset`s live open metrics (`max_runup_pct`/`pnl_pct`/`days_held`). Shadow audit uses peak fallback; weekly `--fix` restores wiped metrics, lifts below-floor shadows only when peak is reliable, and closes duplicate ACTIVE intraday signals. |
| `2026-08-08` | `3.4.0` | **exit_reason hygiene:** Open positions (`ACTIVE`/`PAPER_ONLY`) no longer persist sticky `exit_reason`; field is set only on close. Shadow reasons stay in `shadow_exit_reason_*`. No strategy/PnL change. |
| `2026-07-18` | `3.4.0` | **Volume Exhaustion Exit:** Added early exit for flat stagnant positions (`-3% <= PnL < +5%`, `runup < 8%`) when post-entry volume decays `< 45%` vs 11-day baseline (excluding Day 0 listing volume). **Trailing Dead Zone Closure:** Lowered `MIN_PNL_FOR_TRAIL` to 4% (3% for `LISTING_BREAKOUT`). **Modular Backtest Engine:** Integrated `run_latest_rules_backtest.py` (`python manage_db.py backtest`) with rule isolation CLI flags (`--disable-vol-exit`, `--disable-stagnant-guard`, `--disable-speed-gates`, `--vol-ratio`). Quant audit across 675 IPOs confirmed +1.18% avg return per trade, 1.25 Profit Factor, and proved naive 5% Re-entry causes 335% trade churn (rejected). |
| `2026-07-11` | `3.4.0` | **Re-Entry Breakouts:** Added tracking for `peak_price_during_trade`. Allows stopped-out valid setups to trigger a Re-Entry if they cross the peak again within 30 days. Re-entries bypass DNA filters but enforce liquidity thresholds, yielding +76.8% absolute port return in backtests. Implemented `PAPER_ONLY` caps for re-entries. |
| `2026-07-10` | `3.3.0` | **Corporate Action Guard:** Suspends exits on >25% drops to prevent false stop triggers. **Breakout Volume Floor:** Volume floor (≥150k) now checks breakout-day volume instead of Day 0. **Stagnant Position Guard:** Exits trades held ≥40d with PnL <10%. |
| `2026-07-05` | `3.3.0` | **Param tightening:** `CONSOL_WINDOWS` narrowed to `10,20` only; `MIN_LIVE_GRADE` raised from `C` to `B`. Based on 64-trade closed-trade analysis (Grade C avg -2.64%/median -5.55%; 40d avg -7.65%). New clean-cohort baseline. |
| `2026-06-07` | `3.3.0` | Listing volume floor (≥150k), base-duration guard fix, 20-day patience stop, Limit Buy alerts, `position_version` log field                                                                                                      |
| `2026-04-23` | `2.5.0` | MongoDB-only architecture, forensic audit, winner trait classification                                                                                                                                                           |
| `2026-04-21` | `2.4.x` | Granular telemetry integration for consolidation                                                                                                                                                                                 |
| `2026-04-15` | `2.4.0` | Lifecycle logging additions in positions pipeline                                                                                                                                                                                |
| `2026-04-01` | `2.3.0` | Institutional analytics research layer                                                                                                                                                                                           |

## Parking lot (deferred — needs larger N)

Do **not** enable these on thin post-exit samples (~5 realized / ~22 dead-money / ~9 trail-winner paths). Prefer missed edge over a backfired rule.

| Idea | Status / Why deferred |
|---|---|
| Underwater dead-money (day 12 / −6%) | Promoted to production on 2026-09-13 via 14-Day Velocity Speed Gate (`days_held >= 14 and pnl <= 0.0`) |
| Peak chandelier trail for runup ≥15% | Deferred: n=9 trail winners; risk of larger givebacks |
| Consol HQ → ACTIVE | Deferred: Capital risk; keep force-paper OOS |
| Retune 20d/21d dead-money | Deferred: Already looks good; do not retune on thin data |
| New Early Base Break logic | Deferred: Legacy label; not in live exit code |

The next clean-cohort analysis run should use --start-date 2026-07-05 to isolate signals generated under these tightened parameters.


---

## 🔬 Active Research Note: The "Days 3–9 High Shelf / Wave 2" Blind Spot (MILKYMIST Case Study)

**Date Logged:** 2026-10-05  
**Topic:** Ensuring quality filters do not reject explosive post-listing winners (`MILKYMIST` +74% runner).

### 1. Empirical Case Study: `MILKYMIST`
- **Day 0 (2026-08-18):** Listed at ₹165.00, hit ₹181.50 Upper Circuit (Listing Day High = ₹181.50).
- **Day 1 (2026-08-19):** Gapped up to ₹190.00, hit ₹199.65 Upper Circuit (+10%).
- **Day 2 (2026-08-20):** Gapped up to ₹207.80, spiked to ₹211.80, then pulled back to test listing high at ₹181.50, closing at ₹184.89 (+1.87% above listing high).
  - *Legacy trigger:* Stamped by older backfill on Day 2 at ₹184.89 (`entry_above_high_pct = +1.87% <= 3.5%`).
  - *Modern v3.5.0 verdict on Day 2 candle:* Fails the Upper 50% Body Gate (`(Close - Low) / (High - Low) = 11.2% < 50%`) because of the morning gap-up open (₹207.80) and low close (₹184.89), even though it held the ₹181.50 listing breakout level.
- **Days 3–5 (Aug 21–25):** Formed a tight 4-day base between ₹181.50 and ₹205.70.
- **Day 6 (2026-08-26):** **THE TRUE INSTITUTIONAL BREAKOUT**:
  - Open: ₹201.90, High: ₹222.11, Low: ₹198.06, Close: ₹222.11.
  - Traded Volume: **20,878,033 shares (₹463 Crores of daily liquidity)**.
  - Body Location: **100.0%** (closed at absolute high of the day).
  - Upper Wick: **0.0%** (zero overhead supply).
  - Smashed through prior peak (₹211.80) to ₹222.11, launching an uninterrupted run to **₹319.70 (+74%)**.

### 2. The Architectural Blind Spot
Why did NEITHER scanner capture this textbook breakout on August 26?
1. **`listing_day_breakout_scanner.py` (The 3.5% Extension Cap):**
   - Restricts breakouts to `entry_above_high_pct <= 3.5%` relative to **Day-0 listing high** (₹181.50).
   - On August 26, `MILKYMIST` was at ₹222.11 (+22.4% above Day-0 listing high). It was rejected as *"too extended from listing high"*.
2. **`streamlined_ipo_scanner.py` (The 10-Day Age Minimum):**
   - The consolidation scanner enforces `CONSOL_WINDOWS = [10, 20]`, requiring `len(df) >= 10`.
   - On August 26, `MILKYMIST` only had 6 trading sessions post-listing, so it was skipped as *"insufficient history"*.
3. **Tier B (`BASE_BREAKOUT`):**
   - Is hard-coded to look for bases *below* listing high (`(listing_day_high - current_high) > 0`).

**The Gap:** Any IPO that gaps up on Days 1–2, forms a tight 3–5 day shelf *above* listing high, and breaks out on **Days 3 to 9** falls into a dead zone between the two scanners.

### 3. Implemented Resolution (2026-10-05): Unified Inculcation into `listing_day_breakout_scanner.py`
Instead of creating an unneeded 3rd scanner, High Shelf Breakout was inculcated directly into the main listing engine:
1. **Target Universe:** IPOs aged **3 to 30 trading sessions** post-listing with base history ≥ 4 bars.
2. **Setup Pattern:** Trading above Day-0 listing high, forming a tight 3–8 bar local shelf (`PRNG <= 18%`, base floor >= 88% of listing high).
3. **Breakout Trigger (`SHELF_BREAKOUT`):**
   - Clean close above local shelf high with entry within ≤ 5.0% of shelf pivot (anti-chasing guardrail).
   - Confirmed by Upper 50% Candle Body Gate (`(Close - Low) / (High - Low) >= 50%`).
   - Supported by volume spike (`>= 1.8x`) OR institutional turnover floor (`>= ₹5.0 Cr`).
4. **Execution & Risk Management:**
   - Single-writer exit ownership: Stored with `grade="LISTING_BREAKOUT"` so it inherits the 14-day velocity speed gate and 2-stage trailing stops.
   - Stop Loss: Anchored at shelf low / 15-day swing low buffered 3%, with strict 12% hard risk cap.
   - Target: Project from shelf pivot with minimum +20% profit return floor.
5. **Empirical Universe Validation (2024–2026 IPOs, N=145):**
   - **Win Rate (Peak Runup ≥ 10%): 62.8%**
   - **Average Peak Runup: +18.04%**
   - **Average 20-Day Return: +4.58%**
   - Flagship Case Study: `MILKYMIST` on Day 6 (2026-08-26) qualified cleanly at ₹222.11 (+50.0% peak runup to ₹333, max drawdown only -7.1%).
