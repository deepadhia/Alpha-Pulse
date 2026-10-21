# PRODUCTION READINESS PLAN (written 2026-10-21)

Goal: a system the owner can TRUST with real capital. "Production grade" here means: what runs is what was tested, every order and fill is recorded,
failures are loud, and the edge is judged on live evidence. It does NOT mean a guaranteed profit factor.  Evidence status of the strategy is unchanged:
**plausible, not proven** (Policy P replay, 228 trades, PF 2.43 incl. open / 1.71 closed only / 1.91 ex-top-3; see AGENTS.md section 10 and LEARNINGS.md 12-18).

## 0. Verified facts that drive this plan (checked 2026-10-21)

* The Cloudflare dispatcher fires workflows on `ref: "main"` (`cloudflare-dispatcher/src/index.js:25`). `main` and `hardening/parity-and-fixes` are the same commit (`1d7aff3`, 2026-10-11).
  74 files (hardening, engine, order sheet, shadow score, research, docs) are uncommitted. **So production still runs the pre-hardening code, and committing on the branch alone changes nothing; the work must also reach `main`.**
* Test suite: 167 tests pass (system python, the command in SESSION_HANDOFF 11.5 plus `test_setup2.py`). `run_honest_backtest.py --verify-legacy` and `test_regression_fixes.py` were NOT re-run on 2026-10-21.
* `logs/resting_orders.jsonl` does not exist: no resting order has been logged, so the forward test has not started.
* `requirements.txt` has no scikit-learn; only `core/shadow_score.py` and research scripts need it, and no scanner imports it. Keep it that way (research deps must never be required by the CI scanners).
* Live scanner gates that the replay says reject winners are partly hard-coded (volume floor 150,000 at `listing_day_breakout_scanner.py:1384` and `:2886`, close location 0.50 at `:1745` and `:2911`) and partly env-configurable (`LISTING_MIN_VOLUME_MULT`, `LISTING_MAX_ENTRY_ABOVE_HIGH_PCT`, `LISTING_PENDING_CUTOFF_*`). The validated entry path, `order_sheet.py`, does not use them.

## 1. Ranked gates (each gate has an exit test; do not skip order)

### Gate A: live equals tested (blocking; needs the owner's explicit go-ahead for every git step)
1. Commit the 74 files in the split from SESSION_HANDOFF 11.8 (hardening/engine/tests; slot caps; order sheet + shadow + docs; research + reports). Owner pushes.
2. Merge to `main` (the dispatcher's ref). Before merging: run the full suite, `--verify-legacy` (expect EXACT MATCH), and `test_regression_fixes.py` deliberately; confirm no `MAX_ACTIVE_POSITIONS` / `HARD_ACTIVE_POSITIONS` override in GitHub environment `production` or `.env`.
3. After merging: trigger one dispatch manually and read the run logs to confirm the deployed `SCANNER_VERSION`, slot cap 10, and PAPER_ONLY defaults (shelf, hourly).
* Exit test: a production run's log shows the new code and caps.

### Gate B: record everything (append-only ledger)  [design only, not built]
Stored `entry_date` values are unreliable (backfills). Build an append-only ledger (`logs/ledger.jsonl` or a Mongo collection with insert-only writes): resting order placed, fill (price, time, size), stop alert, sell fill, with the signal id and code version.
* Exit test: every closed trade in `paper_scorecard.py` reconciles to ledger rows; fill extension versus trigger is computed from real fills.

### Gate C: forward test of Policy P (the only evidence that counts)
* Daily: `python order_sheet.py --capital <Rs> --holdings holdings.json --peak-capital <Rs> --log`; after close act on the 6:30 PM stop alert, sell at next open; `python shadow_score.py`.
* Pass for plumbing (first 1-2 months, 6-10 trades): fills within +0.5% of the trigger on most orders, signals match the replay, no missed alerts. This does NOT test the edge.
* Edge judged only after 30+ closed trades and 5+ closed outliers with `paper_scorecard.py` (PF three ways with interval).

### Gate D: pre-register the controls BEFORE the first trade
1. Kill rule: halt new entries at a 20% drawdown from the peak (exists in `core/risk_guard.py`). **Resume rule is not yet pre-registered**: propose "resume when the paper book of the same period has recovered to its prior peak less 10%", fix it in writing here before trading.
2. Robustness targets to report (not to tune on): PF excluding the top 5 trades, largest single loss as % of NAV (limit 1.5%), max drawdown (limit 25%), positive years. Policy P does NOT pass the ex-top-3 version today; this is the honest starting point.
3. Continue / stop / scale decision after 30 closed trades: write thresholds now (current `paper_scorecard.py` STOP/CONTINUE/SCALE logic is unchanged).

### Gate E: data integrity (daily, automatic, loud)
* Fix 18 stale Upstox instrument keys (`update_upstox_mapping.py`, DB write: needs go-ahead), 114 placeholder listing dates (2026-08-17/18), missing or truncated candles (STEAMHOUSE, MANIKA, AUGMONT).
* Run `check_data_coverage.py` in the existing daily master audit and make a coverage failure a Telegram alert, not a log line.
* Delete leftover `COMMIT_CLEAN_TEST` rows (needs go-ahead).

### Gate F: monitoring and failure handling
* Dispatcher heartbeat: alert if no scan ran in the expected window (the dispatcher is the single scheduler; GitHub cron is deliberately not a fallback).
* Alert on: scanner exception, stale candle data, DB write failure, order-sheet log gaps, risk-guard halt.
* Keep research dependencies out of `requirements.txt` (scikit-learn, pytest); use a separate `requirements-research.txt` if needed.

### Gate G: costs and execution realism
* Real Dhan costs: confirm DP charge, STT (levied on both sides for delivery by law), stamp duty, exchange fees from a contract note, then set a Dhan `CostModel` and re-run the account sims (additive).
* Measure real slippage versus the 0.3% per side assumption in the forward test.

### Gate H: ongoing research (does not block A to F)
* Shadow score stays LOGGED ONLY until its forward bar (SESSION_HANDOFF 12.2).
* Queue and the do-not-retest list: SESSION_HANDOFF 12.3 / 12.4 (S2 pivot breakouts added 2026-10-21, LEARNINGS 18).
* Untested: ATR-scaled initial stops (the repo's "ATR Trail" is only a label string at `streamlined_ipo_scanner.py:4131`; the real stop is a percentage trail with a 12% cap). Low priority; pre-register before any run.
* Quarterly: rebuild `data.pkl` and `data_minute/`, rerun the lab and the gate replay.

## 2. What would change this plan
* If the forward fills are worse than +0.5% of the trigger on most orders, the replay edge is overstated: stop and re-estimate before adding capital.
* If a gate fails its exit test, nothing after it is relied on.
