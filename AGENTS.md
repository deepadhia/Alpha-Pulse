# 🤖 AGENTS.md — Development Guidelines & System Architecture (AlphaPulse)

> **New session? Start with `SESSION_HANDOFF.md`** (goals, evidence, repo state, open decisions), then `LEARNINGS.md` sections 12-17.
> **Before you answer ANY question about performance, edge, or "is it good?", read section 10 (Honesty Protocol).** The owner wants the accurate answer, not the encouraging one.

This file defines the core engineering standards, domain knowledge, and operational protocols for any AI agent or engineer working in the **AlphaPulse** repository.

---

## 🏛️ 1. Repository Purpose & Architecture

**AlphaPulse** is a behavior-driven quantitative trading system that identifies, validates, and manages high-conviction momentum breakouts in Indian equities (Mainboard NSE/BSE).

### Core Engines & Scanners
1. **Listing Day Breakout Engine (`listing_day_breakout_scanner.py`):**
   - Targets newly listed IPOs up to 60 calendar days post-listing (<= 60d), eliminating stale secondary-market lag.
   - Enforces a mandatory listing-day volatility cushion (Listing Day Range >= 5.0%) to ensure institutional price discovery.
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
5. **IPO High Shelf Breakout Engine (`SHELF_BREAKOUT` - Inculcated in Main Listing Logic):**
   - Eliminates the historical coverage dead zone on Days 3–30 (<= 30d post-listing) between the standard listing breakout (<= 3.5% extension cap) and consolidation scanner (>= 10-day history requirement).
   - Automatically detects 3–8 bar consolidation shelves formed above/near listing high (`PRNG ≤ 18%`).
   - Breakouts within ≤5% of shelf pivot qualify as `SHELF_BREAKOUT` with stops anchored at the shelf floor (12% hard cap) and Upper 50% Body Gate protection.
   - Reuses single-writer exit ownership, 2-stage trailing stop, and 14-day velocity speed gate. The 2026-10-05 figure "62.8% win rate, +18.04% avg peak runup (N=145)" counts a trade as a win when its PEAK run-up reached 10%; it is not a realized result. Under live exits in the honest backtest the shelf engine is -2.36% net per trade (N=43, PF 0.56, 28% win rate), which is why it stays PAPER_ONLY.

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
> 1. **Pure Native Forward Scorecard:** ONLY includes trades where `entry_date >= '2026-09-29'`, `version in core.versioning.NATIVE_FORWARD_VERSIONS` (`3.5.0`, `3.6.0`), and `_backfilled != True` (or `is_native_v350: True`; the field name is kept for stored documents). v3.6.0 (2026-10-21) is the hardened execution release (10 x 10% slots, close-based stop alerts, atomic commit lock); its entry rules equal v3.5.0, so the two tags are one cohort, reported separately when comparing slot-cap regimes.
> 2. **Historical Backfilled Benchmark:** Trades marked `_backfilled: True` or entered pre-September-29 must be reported in their own isolated cohort for backtest validation and research post-mortems. They MUST NEVER be conflated with the live production algorithm scorecard.

### Rules for All Analytics & Audit Scripts
* **Never include pre-July-5, 2026 data in active strategy statistics.**
* Any new analytical or statistical script **MUST** filter `entry_date >= '2026-07-05'` and `signal_date >= '2026-07-05'`.
* Pre-July-5 historical records are safely preserved in:
  - `positions_legacy_archive` (MongoDB)
  - `signals_legacy_archive` (MongoDB)
* Active collections `positions` and `signals` must remain 100% clean-cohort data only.

### Synchronized System Versioning & Analytics Cleanliness Invariant
Whenever the system version is updated, it **MUST be updated everywhere simultaneously in one synchronized atomic commit** to prevent telemetry drift and broken analytics queries.

* **The 6 Mandatory Touchpoints That Must Always Match Exactly:**
  1. `master_audit.py`: `EXPECTED_VERSION = "X.Y.Z"`
  2. `streamlined_ipo_scanner.py`: `SCANNER_VERSION = "X.Y.Z"`
  3. `listing_day_breakout_scanner.py`: `SCANNER_VERSION = "X.Y.Z"`
  4. `hourly_breakout_scanner.py`: `SCANNER_VERSION = "X.Y.Z"`
  5. `db.py`: `SCANNER_VERSION = "X.Y.Z"`
  6. `README.md`: Version badge (`badge/version-X.Y.Z-orange`)

* **Why Isolated Version Bumps Are Strictly Forbidden:**
  - `master_audit.py` Section 3 enforces automated static checks on version drift. If `README.md` or any scanner differs from `EXPECTED_VERSION`, the audit immediately emits `[FAIL]`.
  - MongoDB queries aggregate telemetry and position performance by cohort (e.g. `db.logs.find({"version": EXPECTED_VERSION})` and `db.positions.find({"position_version": EXPECTED_VERSION})`). Bumping a single file fragments cohort data and pollutes forward performance statistics.

* **Two-Tier Versioning Protocol:**
  - **Milestone System Version (`SCANNER_VERSION`):** The single source of truth across code, DB records, and `master_audit.py`. Only bumped when a synchronized strategy release occurs.
  - **Patch Notes (`CHANGELOG.md`):** Documents chronological hotfixes, refactors, and guardrails (`[v3.5.1]`, `[v3.5.2]`, `[v3.5.3]`) without fracturing MongoDB cohort tags.

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
10. **Lifecycle Age Horizon & Volatility Cushion (v3.5.4 Standards):**
    - Listing Breakouts and Tier B Base Breakouts: Capped at <= 60 calendar days post-listing (eliminates stale zombie IPO drag).
    - High Shelf Breakouts (Wave 2): Days 3 to 30 post-listing (<= 30d, aligning with institutional anchor lockup).
    - Listing Day Volatility Cushion: Listing day high-to-low range must be >= 5.0% to ensure institutional liquidity and market-maker participation.

---

## 📋 6. Agent Workflow & Safety Checklist

Before delivering code or modifying the repository:
1. **Minimal Blast Radius:** Prefer targeted, additive, modular changes over broad refactors.
2. **Verify Imports & Types:** Ensure all imports and references exist.
3. **Preserve Immature Hypotheses:** Do not enable experimental gates on small sample sizes (see `EXPERIMENT_CHANGELOG.md`).
4. **Validate Against Clean Cohort:** Test queries against `positions` and `strategy_evidence` using `2026-07-05` cutoff.
5. **Never Destructively Overwrite DB Collections:** Always use upsert or archival tables.
6. **Synchronized Versioning Rule:** If bumping system versions, update all 6 touchpoints atomically (master_audit.py, all 3 scanners, db.py, README.md badge) and verify with python master_audit.py --section 3 to guarantee zero analytics fragmentation.
7. **Readable Formatting Standard (No LaTeX Math Syntax):**
   - Never output LaTeX math syntax (e.g. $\le$, $\ge$, $\pm$, $\times$) in user responses, console logs, git commits, or documentation.
   - Always print clean, standard ASCII symbols (<=, >=, +/-, x) or plain English text ("less than or equal to", "greater than or equal to") for immediate readability across all terminals, markdown previews, and chat interfaces.


---

## 🧱 7. Single Source of Truth & Backtest Fidelity (added after the Oct-2026 parity audit)

The legacy backtest (`run_latest_rules_backtest.py`) was found to follow different rules from the live scanners
(bars vs calendar days, intraday highs vs closes, entry-risk trail vs 10% trail, OR vs AND shelf turnover gate,
a shelf-detector off-by-one, missing live gates, stop fills at the stop price). Its profit factor (2.27) fell to
about 1.24 once it followed the live rules. See `HARDENING_REPORT.md`. To keep this from recurring:

1. **Exit and trailing rules live in ONE place: `core/exit_rules.py`.** `stop_loss_update_scan`, the honest
   backtest and the tests all call `evaluate_position()`. Never re-implement exit logic inline in a script or a
   test. `test_exit_rules.py` proves equivalence with the original inline code on 40,000 random states.
2. **Evaluate strategy changes with `run_honest_backtest.py`** (live-faithful entry gates, next-open fills,
   gap-aware stops, circuit-lock heuristic, whole-share sizing, Indian delivery costs) and `core/portfolio_sim.py`
   (slots, cash, mark-to-market drawdown). `--verify-legacy` must still report EXACT MATCH.
3. **Research discipline:** pre-register the variant list, compare PAIRED on the same signals, cluster by symbol,
   require year-by-year consistency, and validate walk-forward (`research_*.py`). A result that does not survive
   walk-forward is noise. Do not tune on the 79-trade live-faithful book.
4. **Portfolio commits are atomic:** `commit_trade_to_db` takes `core.locks.portfolio_commit_lock`; if the lock
   is unavailable the trade is stored PAPER_ONLY, never uncapped ACTIVE.
5. **Capital policy defaults (env-reversible):** `SHELF_BREAKOUT` and hourly `INTRADAY` signals are PAPER_ONLY
   (`LISTING_SHELF_LIVE_CAPITAL_ENABLED=0`, `HOURLY_LIVE_CAPITAL_ENABLED=0`) until they have live-faithful
   out-of-sample evidence.
6. **The Corporate Action Guard is fail-safe** (`core/guards.py`): only split/bonus-ratio drops pause a position;
   any other large drop runs the normal stop logic.
7. **Manual execution uses resting stops:** `python order_sheet.py --capital <Rs>` prints GTT stop-limit levels,
   whole-share entry sizes and the drawdown kill-switch state (`core/risk_guard.py`).
8. **Never discard right-censored trades.** A trade still open at the end of the data is marked to market, flagged
   `open`, and reported separately. `simulate_symbol` currently drops them (`core/honest_backtest.py:667`; it dropped
   KSHINTL, +183%): do not quote its totals as complete until that is fixed.
9. **Evaluate every gate at the time of the live decision, not at the day's close.** The live scanner decides on the
   current price when it fires; an end-of-day gate with a next-open fill rejects strong breakout days and keeps the ones
   that faded. State the fill model explicitly and report results for a range of fill slippage. Reference: native fills
   averaged +3.2% above the listing high (n=8, range +1.7% to +5.6%); a fill at +1.5% vs +3% changes net expectancy by
   about 2 points per trade (`research_intraday_proxy.py`).
10. **Backfilled rows are not live evidence.** Positions with `_backfilled: true`, entry dates before listing, or entries
    older than the 60-day window must not be quoted as live proof (`research_live_parity.py` lists them). Stored
    `entry_date` values are unreliable until the append-only trade ledger exists.
11. **The 1-2 month forward test validates plumbing, not edge.** At about 38 trades a year it yields 6-10 trades. Judge the
    forward test on fill extension, parity replay, resting stops and data integrity; judge the edge on the broad event
    set and `paper_scorecard.py` rules only. See `LEARNINGS.md` section 10 for the gates.


---

## 🧭 8. Execution, stop style and evidence rules (added 2026-10-14, see `LEARNINGS.md` section 12)

12. **Live stops are close-based.** `stop_loss_update` runs once a day at 6:30 PM IST: it compares the CLOSE with the stop and
    alerts; you sell at the NEXT OPEN. A live-faithful backtest must use `exit_fill="eod_next_open"`. `exit_fill="gap_aware"`
    models an intraday resting stop, which the live system does not use. In the 2022-26 replay the close-based style earned about
    2pp more per trade than an intraday resting stop (paired CI +0.8..+3.5, 5 of 5 years). Do not make a resting GTT at the
    trailing level the default; an optional disaster GTT 8% below the alert level is insurance only and does not fill through gaps.
13. **Entries: a pre-placed stop-limit buy beats buying after the alert.** `order_sheet.py` prints pre-market orders (trigger
    listing high +1.5%, limit +2.5%) for IPOs aged 5-60 days. Eligibility comes from `core.honest_backtest.preview_resting_entry`,
    the same gates as the backtest, on prior-session data only. Never add a same-day volume condition to a pre-placed order.
    Record every fill (`--log`); the pass mark for the forward test is fills within +0.5% of the trigger.
14. **Do not add quality filters on this evidence.** Supply, breadth, gap and liquidity filters all removed profitable trades;
    gap-up breakouts are the strongest trades. A filter is adopted only if the trades it removes have a negative net mean out of
    sample (pre-registration bar in the `research_alpha_lab.py` docstring). Exploratory screens (section G of that script) are
    never adoptable.
15. **Report profit factor three ways:** closed-only, including still-open trades at mark to market, and closed excluding the
    three best trades. Policy P is 1.67 / 2.45 / 1.14. A PF near 2 depends on the right tail.
16. **Never judge a result by one account path.** Drawdown throttles and risk-based sizing changed sign between stop styles; only
    paired trade-level evidence with a cluster-bootstrap interval and year-by-year consistency counts.
17. **Test discipline:** `python -m pytest test_alpha_lab.py test_portfolio_caps.py test_commit_policy.py test_locks_and_orders.py test_exit_rules.py test_honest_backtest.py`
    must pass and `python run_honest_backtest.py --verify-legacy` must still report EXACT MATCH. Use the system `python` for
    pytest; `.venv` has pandas but not pytest.
18. **Slot caps are ONE flat cap of 10 with a 10% equal weight, defined in `core/portfolio_caps.py`** and read by the three scanners (through `streamlined_ipo_scanner.MAX_ACTIVE_POSITIONS` / `HARD_ACTIVE_POSITIONS`) and by `order_sheet.py`. Never hard-code a cap or a 1/N weight elsewhere; `test_portfolio_caps.py` pins the boundaries. Rollback: set both env vars to 5 and 7.
19. **Dead money is structural, not a defect to filter or rotate away.** 36% of policy-P trades end as time stops (-4.6% each, 14% of capital-days); no setup pattern predicts them and rotating capital out of dead-looking positions sold SIGNATURE (+138%) and SHUKRAPHAR (+51%) early. Do not tighten the dead-money gate or add rotation without new out-of-sample evidence. The shelf breakout is positive but weaker than the listing-high breakout under the corrected model and stays PAPER_ONLY.
20. **Quality over quantity does not hold for this strategy.** Profit is 77% outliers and no entry feature predicts them; every quality rule lowered out-of-sample PF. Do not add selection filters to raise PF. Judge the PF target (2.0) with `paper_scorecard.py` (closed-only, incl. open runners, ex-top-3, with the interval) once there are 30+ closed trades and 5+ closed outliers; closed-only PF above 2 is roughly a one-in-four outcome in the replay.

## 9. Added 2026-10-17 (see `LEARNINGS.md` section 17, `SESSION_HANDOFF.md` sections 11-12)

21. **Do not add gates or confirmation steps that read partial-day values** (volume so far, the forming candle, a 60-minute hold, a chase cap that depends on when a scan runs). Replayed from 1-minute bars they rejected 46% of the winners and cost about 5.5pp per signal. Resting orders are the validated entry path.
22. **Compression, dry-up and "quiet day" patterns have no edge here** (three independent tests), nor do subscription multiples, issue size or issue price. Do not retest without new information; subjects not yet tested are listed in `SESSION_HANDOFF.md` section 12.3.
23. **The shadow score (`core/shadow_score.py`, `shadow_score.py`) is LOGGED ONLY.** It must never gate, size or cancel an order until the forward bar in `SESSION_HANDOFF.md` section 12.2 is met. The entry cap (listing high +2.5% limit) stays: widening it cut whole-book PF from 2.43 to about 2.
24. **Environment:** build engine data and events with the project `.venv`, fit or score models with the system python (scikit-learn, different pandas version); a pickle cannot cross between them. Write scripts with the Write tool; heredocs with apostrophes break the Bash tool.

---

## 10. Honesty Protocol for AI Agents (added 2026-10-21): clear and accurate beats pleasant

The owner has said, repeatedly and explicitly, that they want accurate answers, not happy ones. Capital is real (Rs 50,000 to Rs 1,00,000) and the history of this repo
includes backtests that were wrong in BOTH directions (PF 2.27 too optimistic, then PF 1.24 too pessimistic). An agent that flatters the strategy, or the owner's ideas, costs money.

### 10.1 How to answer
1. **Verdict first, in one sentence, even when it is unwelcome.** Then the evidence. No warm-up praise ("great idea", "excellent question"), no cheerleading words ("goated", "bulletproof", "guaranteed").
2. **Current status of the strategy, say it as it is:** the edge is *plausible, not proven*. Only live forward trading with resting orders can prove it. Do not upgrade this to "profitable" or "proven" because a backtest looks good.
3. **Never promise or imply a profit factor, return, win rate or drawdown.** You may report what a replay measured, with its sample size and interval. "I cannot promise a PF of 2" is a correct answer.
4. **Disagree with the owner when the evidence disagrees.** If they propose an idea that is already in `SESSION_HANDOFF.md` section 12.4 (do not retest list), say so, cite the number, and do not run it again unless there is NEW information. If they hold a belief the data contradicts, state the number that contradicts it.
5. **Bad news goes in the first paragraph, not in a footnote.** Attach the caveat to the number it qualifies (for example: "PF 2.43 including open runners; 1.71 closed only; it rests on 12 trades in +100% IPOs").
6. **Recommend, do not survey.** Give one recommended next step and why; list alternatives only if they are realistic.
7. **Say "I do not know" or "this sample cannot tell" when true.** At about 51 trades a year with a per-trade SD near 17%, effects under about 3pp are not detectable. Do not narrate noise as a finding.
8. **Verify before asserting.** Check the file, line, field name or number in the repo before stating it (two factual errors in the Oct-2026 sessions came from asserting without checking). If you did not run it, say you did not run it.
9. **Do not soften a failed result.** A failed pre-registered test is reported as failed. Never reframe it as "promising", "directionally positive" or "needs more data" unless the interval actually supports that wording.

### 10.2 Mandatory way to report any performance number
* State: population (which engine, which dates, which cohort per section 2), N, fill model, stop model, cost assumption, and whether open trades are included.
* **Profit factor three ways, always:** closed only, including open trades at mark to market, and closed excluding the top 3 trades. With the 90% interval.
* Paired comparisons on the same signals, symbol-cluster bootstrap, year-by-year table. Drop the single largest contributor before believing any tail gain.
* Label the evidence tier of every claim: **LIVE** (real fills) > **PAPER forward** > **REPLAY of the honest engine** > **research/exploratory**. Never mix tiers in one headline number. Backfilled rows are never live evidence.
* "Win rate" means REALISED outcome. Never count a trade as a win because its peak reached some run-up (the 62.8% figure in section 1 is exactly this trap).

### 10.3 Known failure modes in THIS project (check each before trusting or presenting a result)
1. **Look-ahead through same-day values:** end-of-day volume, close location, the forming candle, or a 60-minute hold used for a decision a pre-placed order must make earlier.
2. **Dropped right-censored trades:** trades still open at the data end must be marked to market and shown, not silently removed (KSHINTL, +183%).
3. **Wrong exit model:** live stops are close-based and sold at the next open; an intraday resting-stop model gives different results.
4. **Biased in either direction:** a pessimistic backtest is as wrong as an optimistic one. When a result "kills" the strategy, audit the auditor.
5. **Tuning on small books:** never tune on the 79-trade or 22-position books. Thresholds come from earlier years, validated on later ones.
6. **Outlier dependence:** profit is about 77% outliers. A result that vanishes without 3 trades is not a result.
7. **Single account path:** drawdown throttles, risk sizing and slot counts flip sign between assumptions; trust paired trade-level evidence only.
8. **Backfilled or placeholder data:** `_backfilled: true`, 114 placeholder listing dates, 18 stale instrument keys, truncated 2022-23 minute sessions.
9. **Fill assumptions:** report across a range of slippage; native fills averaged +3.2% above the listing high versus a +1.5% trigger. Resting-order fills are idealised until the forward test measures them.
10. **Short history:** 4.4 years, no 2008 or 2020 style crash, strong cohort waves (W50 rate from 2% to 40% by month). Say so when discussing risk.

### 10.4 When asked "is it good / profitable / ready / goated?" answer with this structure
1. Verdict (one sentence, status: plausible, not proven).
2. The best evidence for it (with N, interval, tier).
3. The strongest evidence against it, and what would falsify it.
4. What is unverified or assumed.
5. The single next action that most reduces uncertainty (currently: live resting-order forward test, then `paper_scorecard.py` after 30+ closed trades and 5+ closed outliers).

### 10.5 Self-check before sending an answer about the strategy
* Did I open with praise or reassurance instead of the verdict?
* Did any sentence promise or imply a result I did not measure?
* Is every number labelled with population, N, tier, and (for PF) all three versions?
* Did I mention the strongest counter-evidence, or omit it because it was inconvenient?
* Did I verify the facts I stated against the repo or the data, or am I recalling?
* Would I give the same answer if the owner were hoping for the opposite result?

If any answer is "no" to the last four or "yes" to the first two, rewrite before sending.

### 10.6 Optional red-team prompt (the owner can paste this to any AI reviewing results)
"Act as a skeptical quant reviewer. Assume the result is wrong until shown otherwise. Look for look-ahead, dropped open trades, fill and stop-model mismatch, survivorship, outlier dependence, tuning on small samples and cohort contamination. Report the verdict first, bad news first, and give a profit factor closed-only, including open trades and excluding the top 3. Do not soften conclusions or praise the work."
