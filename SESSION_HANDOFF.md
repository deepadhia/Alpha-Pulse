# SESSION HANDOFF: Alpha-Pulse (written 2026-10-15)

Give this file to a new session and say: **"Read SESSION_HANDOFF.md, then LEARNINGS.md sections 12 to 16, and resume from section 9 (open decisions)."**
It is self-contained: goals, what exists, what was proven, what was rejected, what changed in the repo, how to reproduce everything,
and what is still open.  Nothing here depends on the chat that produced it.

> **Updated 2026-10-22: read section 14 (end of file) first** (S3 paper book, the proof tests, what is stored in MongoDB), then section 13, sections 11-12 and LEARNINGS.md sections 17-20.5.
>
> **Earlier update 2026-10-21: read section 13 (end of file) first, then sections 11-12 and LEARNINGS.md sections 17-19.**  Section 13 holds the current repo state, the v3.6.0 bump, the failed second setup and the open decisions.
>
> **Earlier update 2026-10-17: read section 11 (end of file) and LEARNINGS.md section 17 first.**  They add minute-bar results, the live-gate replay, the
> selection and early-cut tests, and the logged-only shadow score; where they differ from section 9 they win.

---

## 1. The owner, the goal, the constraints

* Owner: dadhia@nuvista.ai.  System: **Alpha-Pulse**, an Indian IPO breakout system (NSE/BSE mainboard) with MANUAL execution
  (Telegram alerts, the owner places orders).  Repo: `D:\Personal Projects\Alpha-Pulse`.
* Stated goal: a robust, scalable alpha generator with stop protection, losses small, winners being **outliers**; win rate is secondary.
  Wants a profit factor (PF) near or above 2.  Accepts runs of consecutive 8-10% stop-outs if PF holds; does **not** want stops tightened to avoid them.
* Capital: starting with only **Rs 50,000 to Rs 1,00,000**.  Asked whether it can run without an account blowup (answer in section 8).
* Asked repeatedly to "use all quant knowledge" but also that changes must not degrade performance.  The honest outcome: very little survived testing; say so plainly.
* Working preferences: never commit or push without explicit permission (the owner pushes themselves); **do not read `.env`** (access is denied; it holds secrets);
  no LaTeX math in outputs or docs (use `<=`, `>=`, `x`, plain words, per AGENTS.md); do not change anything beyond what was asked.

## 2. What the system is (one paragraph)

Engines: listing-day breakout (`listing_day_breakout_scanner.py`, IPOs up to 60 days old, breakout of the Day-1 listing high), shelf breakout (same file, days 3-30,
PAPER_ONLY), consolidation breakout (`streamlined_ipo_scanner.py`, forced PAPER_ONLY) and an hourly watchlist scanner (PAPER_ONLY).  Exits and trailing stops are owned by
`streamlined_ipo_scanner.stop_loss_update_scan`, which runs ONCE A DAY at 6:30 PM IST (`cloudflare-dispatcher/src/index.js`): it compares the CLOSE with the stop and alerts;
the owner sells at the NEXT OPEN.  The exit rules live in `core/exit_rules.py` (single source of truth).  Storage: MongoDB Atlas (`ipo_scanner_v2`).  AGENTS.md is the standing rulebook.

## 3. The decisive findings, in order of importance

All figures are net of a 1.0% round-trip cost unless stated; "open" trades are marked to market; CI = 90% symbol-cluster bootstrap; "paired" = same signals, different rule.

**3.1 History of the numbers (why you cannot trust the old ones)**
* Legacy backtest (`run_latest_rules_backtest.py`): PF 2.27, 196 trades.  It did not follow live rules.  A first audit (2026-10-12) converted it to live rules: 79 trades, PF 1.24, "no edge".
* A review (2026-10-13) showed that audit was biased AGAINST the strategy: end-of-day gating with next-open fills, trades still open at the data end dropped (KSHINTL +183%), IPOs with <10 candles skipped.
* Today's session found two more modelling errors: (a) the "live-faithful" engine assumed an INTRADAY stop, but live is close-based; (b) entry gates used same-day (end-of-day) volume, which a
  pre-placed order cannot know.  Both are now selectable in the engine (`exit_fill="eod_next_open"`, `causal=True`).  Re-measured, the strategy is positive but not proven.

**3.2 Policy P (the recommended policy and its honest numbers).**  Listing breakout, IPO age 5-60 days, a pre-placed stop-limit BUY at listing high +1.5% (limit +2.5%, modelled fill +0.3%),
no same-day volume gate, live exits (close-based stops, 12% cap, trailing, dead-money gates), live liquidity gates.  227 trades 2022-05 to 2026-10 (about 51 a year, 22 still open).

| Measure | Value |
|---|---|
| Net per trade incl. open | +5.29% (CI +2.6 to +8.2); closed only +2.66% (CI +0.1 to +5.5) |
| PF incl. open / closed only / closed without top 3 | 2.45 / 1.67 / 1.14 |
| PF 90% interval and P(PF >= 2) | incl. open 1.67-3.43, 81%; closed only 1.02-2.50, 24% |
| PF by entry year (incl. open) | 2022 0.35, 2023 4.88, 2024 2.24, 2025 1.12, 2026 4.33 |
| Win rate, average win, average loss | 38%, +17.5%, -6.4% (payoff 2.72); PF 2.0 needs payoff 3.26 at this win rate |
| Rs 1 lakh account, 10 slots x 10%, costs on | +126% total, CAGR 20.6%, max DD -25%, Calmar 0.81 (closed only: CAGR 10.3%, DD -26%) |
| Same with TODAY'S measured fills (chase +1.7%) | CAGR about 10.8%, max DD -30% (7 slots: CAGR 13.4%, DD -35%) |

**3.3 Execution is the edge.**  Native live fills average +3.2% above the listing high (n=8) versus a +1.5% trigger.  A pre-placed stop-limit entry beats buying after the alert by
+1.33pp per trade under live stops (CI +1.13..+1.54), +1.86pp under intraday stops (CI +1.32..+2.53), positive in every year.  It shrinks to about +0.8pp if price jumps through the limit
intraday (daily bars cannot see that).  Gap-up breakouts are the STRONGEST trades (opens above the walk-forward gap threshold of 1-2% over the prior close: +13.1% mean, n=31), so never filter them out.

**3.4 Stop style.**  The close-based stop (live) beats an intraday resting stop by +2.03pp per trade (CI +0.79..+3.54; excluding the top 3 trades +0.86, CI +0.29..+1.50; 5 of 5 years;
+2.23pp under a +1.0% fill) because intraday stops get hit by wicks that recover.  Cost: a fatter left tail (worst replayed loss -16.9% vs -13.9%; 2.4% of closed trades below -15%).
A resting disaster stop 8% below the alert level keeps most of the gain (+1.92pp) but never reduced the worst case in the sample; it does not fill through gaps or lower-circuit locks.
The 12% initial stop cap is right: a 10% cap gives -0.04pp under live stops and -1.0pp (CI -1.8..-0.3) under intraday stops.  **Do not make GTT-at-the-stop the default.**

**3.5 Quality filters do not work; quality over quantity is false here.**  Pre-registered, walk-forward (thresholds from earlier years): low IPO supply, cohort breadth, no gap-up, liquidity floor.
Every kept subset had LOWER out-of-sample PF than taking everything (1.93, 1.80, 1.85, 2.14 vs 2.28) and every rejected bucket was profitable (means +6.1%, +6.1%, +13.1%, +8.8%; PF 2.5-4.0).
Profit is 77% outliers (24 of 227 trades at +25% or more; 7 trades at +75% or more = 42%) and none of 18 entry features predicts them (rank AUC 0.39-0.59; tight 10-day base about 0.61 inverted, too weak).
So thinning trades cuts outliers proportionally.  Quantity is how the outliers get drawn.

**3.6 Exits and sizing tweaks all failed the pre-registered bar** (paired, CI excludes 0, positive without the single largest mover, <= 1 worse year): trail 8%/13%, winner cushion 0.85/0.75,
dead-money 10/21 days, trail start +5%, sell a third at +20%, breakeven stop (+0.04pp, immaterial), pyramid (+0.64pp per trade, CI -0.35..+1.73, 0.83x return per capital-day),
tight-base conviction sizing (-0.37pp), re-entry cooldown 20/10/5 bars (not monotone, CIs include 0), risk-based sizing and a drawdown throttle (flipped sign between stop styles).
A combined "let winners run" change looked like +4.75pp but three trades (KAYNES +611pp, CPPLUS +267pp, OMNI +145pp) produced 94% of it; without them +0.27pp (CI -0.39..+1.00).

**3.7 Dead money is structural.**  81 of 227 trades (36%) end as time stops: median 14 days, mean peak run-up 2.2%, mean -4.6%, total -373pp against +1,200pp for all trades, 14% of capital-days
(0.9 positions open at a time).  28-43% every year.  No setup pattern predicts it (first cross vs re-cross 34% vs 37%; every bucket 29-47%).  **Rotating capital out of dead-looking positions
failed in every scenario** (Rs 1 lakh: +126.0% to +92.6% at 7 days, +112.7% at 10 days): it sold SIGNATURE (+138%), SHUKRAPHAR (+51%), EPACKPEB (+30%) at about -2.5% on average, versus +12.8% had they been held.
Outliers incubate inside positions that look dead.

**3.8 Shelf breakout vs listing-high breakout** (same causal, resting, close-stop model): shelf +3.36% (CI +0.8..+6.3), PF 1.78/1.60, 44% dead money (18% of capital-days), 9.0% outliers, negative in 2022 and 2025;
listing +5.29%, PF 2.45/1.67, 36% dead, 10.6% outliers.  Account: shelf alone CAGR 12.3%, Calmar 0.46; listing + shelf 18.8% / 0.72 vs listing alone 20.6% / 0.81 (shelf displaces listing trades).
The earlier audit's -2.36% for the shelf was an artifact of biased modelling.  **Decision: shelf stays PAPER_ONLY.**  The tightest shelves (range <= 8%) were worst (51% dead); the loosest (12-18%) best but on 32 trades only.

**3.9 Capital and slots.**  The book averages 6.0 positions and is at the 10-slot cap on 22.8% of days.  Cash binds more than slots (winners grow past 10% of NAV).  With 10 slots 52 of 227 signals were skipped
(mean +3.7%, 6 outliers: ARSSBL, KISSHT, JSFB, HEXAGON, MVELECTRO, MOLBIO); with 7 slots 84 were skipped (mean +5.7%, 12 outliers).  10 slots x 10% had 5-8pp less max drawdown at equal or higher CAGR in 3 of 4 fill
scenarios (neutral under today's chase fills).  It is a risk and diversification change, not an alpha change.

**3.10 Risk of ruin and loss streaks.**  Per trade: one stopped trade costs about 1.2% of the account at 10% weight (worst replayed 1.7%); all ten positions at the worst case at once about 17%.  Longest closed
losing streak 11 (resting) to 15 (moderate); worst 10 consecutive closed trades -83pp = -8.3% of NAV at 10% weights.  Next 40 trades: median longest streak 6, P(streak >= 8) 26%, P(PF < 1) 25%.

**3.11 Tail anatomy.**  Closed outliers (+25% or more): 14 trades, +1,035pp; other 191 closed trades -489pp (wins +328, losses -817).  Closed PF 2.0 needs about 1,306pp of outlier profit (26% more than the replay had);
with half of it PF is 1.03.  Winners that peaked at +30% or more kept 57% of the peak and were held a median 63 days (the best 100-400 days).  PF is a right-tail statistic here.

**3.12 Capacity.**  Median 20-day turnover at entry Rs 101 Cr, lower quartile Rs 18.6 Cr, 10th percentile Rs 5.1 Cr.  At 14% weights a Rs 50 lakh account is above 2% of daily turnover on 6.6% of trades, Rs 1 crore on 14%.
Above about Rs 50 lakh a liquidity-scaled size cap is needed; the 0.3% slippage assumption is not valid above 2% participation.

## 4. What was changed in the repo (nothing is committed)

`git status` shows both the earlier hardening session's work and this one as uncommitted.  Last commit: `1d7aff3`.

**Live-behaviour changes (the owner asked for them or they came from the 2026-10-12 hardening, see HARDENING_REPORT.md):**
* **Slot caps 5/7 -> 10/10** (this session, on request).  One flat cap of 10 positions, 10% equal weight, defined once in `core/portfolio_caps.py`; read by `streamlined_ipo_scanner.py`
  (`MAX_ACTIVE_POSITIONS`, `HARD_ACTIVE_POSITIONS`, env-overridable), the fallbacks in `listing_day_breakout_scanner.py` and `hourly_breakout_scanner.py`, and `order_sheet.py`.  Applies to NEW signals only;
  stored PAPER_ONLY rows are not promoted.  **Rollback:** set BOTH `MAX_ACTIVE_POSITIONS=5` and `HARD_ACTIVE_POSITIONS=7` in the scanners' environment and pass `--hard-cap 7` to `order_sheet.py`.
  Docs updated (README, SYSTEM_KNOWLEDGE, CHANGELOG).  Pinned by `test_portfolio_caps.py`.
* From the earlier session (still uncommitted): shelf and hourly signals PAPER_ONLY by default, atomic portfolio commit lock (`core/locks.py`), fail-safe corporate-action guard (`core/guards.py`),
  shared exit rules (`core/exit_rules.py`, behaviour identical to before), `test_regression_fixes.py` fixed so it no longer writes to the production DB.

**Additive tooling (default off or read-only):**
* `core/honest_backtest.py`: `simulate_symbol_cross` (intraday-crossing entry, causal mode, `setup="shelf"`), `cross_entry`, `shelf_cross_entry`, `preview_resting_entry`, `include_open`, `cat_stop_pct`, `breakeven_*`.
  `python run_honest_backtest.py --verify-legacy` must still say EXACT MATCH (198 trades, 0 mismatches as of 2026-10-15).
* `core/portfolio_sim.py`: `risk_budget_pct`, `dd_half_pct`, `dd_halt_pct`, `rotate_after_days` (all default off; defaults reproduce the old results exactly).
* `core/order_sheet.py` and `order_sheet.py`: close-based stop alert mode (CLI default; `--stop-mode resting` restores the old sheet), optional disaster GTT, pre-market **resting entry orders**
  (same gates as the backtest), `--log` appends orders to `logs/resting_orders.jsonl`.  Slot count and weight come from `core/portfolio_caps.py`.
* `core/scorecard.py` and `paper_scorecard.py`: profit-factor monitor (`pf_report`, `--pf-target`, reporting only; STOP/CONTINUE/SCALE decision untouched).
* Research scripts (all take `--cache data.pkl`): `research_alpha_lab.py`, `research_risk_profile.py`, `research_tail_capture.py`, `research_dead_money.py`, `research_small_account.py`, plus the 2026-10-12/13 set.
* Docs: `LEARNINGS.md` sections 1-16 (7-11 override 1-6; 12-16 are from this work), `AGENTS.md` section 8 (rules 12-20), `HARDENING_REPORT.md` section 12, `CHANGELOG.md` v3.5.6, `EXPERIMENT_CHANGELOG.md`, `reports/2026-10-14/README.md`.
* Tests: `test_alpha_lab.py`, `test_portfolio_caps.py` (new) plus the earlier ones.  **135 tests pass** (command in section 7).  `test_regression_fixes.py` was NOT run this session (it imports the scanners and may touch the DB).

**Not changed:** any exit rule, stop rule, entry gate in the scanners, shelf/hourly PAPER_ONLY status, the 12% stop cap, the dead-money gate (`DEAD_MONEY_RUNUP_IPO` is still 4.0).

## 5. Policy P: the operating protocol (what to actually do, daily)

1. Pre-market: `python order_sheet.py --capital <account value> --holdings holdings.json --peak-capital <highest account value> --log`.  Section 3 prints stop-limit BUY orders (trigger listing high +1.5%, limit +2.5%)
   for eligible IPOs aged 5-60 days, capped at the free slots.  Place them as day orders; cancel unfilled ones before the close.  Record every fill price and time (pass mark: within +0.5% of the trigger).
2. After the close: the 6:30 PM scan messages stop alerts.  **If the close is at or below the alert level, sell at the next open.**  The optional disaster GTT 8% below is insurance only.
3. Size: at most 10% of the account per position, 10 slots.  No leverage, no averaging down, no F&O.
4. Risk guard: halt new entries at a 20% drawdown from the peak (`--peak-capital`); a halt cannot recover by itself (it locked the replay out for all of 2026), so keep logging paper orders and resume when they recover.
   The resume rule is NOT yet pre-registered.
5. Judge the system only with `python paper_scorecard.py` once there are 30+ closed trades and 5+ closed outliers (PF three ways with an interval).  The 1-2 month forward test validates plumbing (fills, orders, parity), not edge.
6. Expect long flat or losing stretches (PF was 0.35 in 2022 and 1.12 in 2025), 7-15 losing trades in a row, and a median trade of about -1.4%.

## 6. Small account (Rs 50k to Rs 1 lakh): the answer

* Blowup (NAV <= 50% of the start): **0 of 41 real 12-month windows and 0 of 250 resampled histories per cell**, structurally bounded by position size x stop (section 3.10).
* A losing first year is still likely: real 12-month windows P(loss) 41-44% at Rs 1 lakh and 46-49% at Rs 50k; resampled 18-32%.  Median first year +6% to +8% (real windows) or +8% to +18% (resampled).
  Median max drawdown -11% to -13%; P(DD >= 20%) 10-22%; P(DD >= 30%) 0% for 10 x 10%.  24 months at Rs 1 lakh (resampled): median +29%, P(loss) 12%, 5th percentile -13%.
* Rs 50k pays 1.78% round-trip charges on Rs 5,000 positions vs 1.15% at Rs 10,000 and 0.65% at Rs 50,000: that is the whole gap.  Whole shares are no problem (median entry Rs 326, none above Rs 5,000).
  Start at Rs 1 lakh or top Rs 50k up to about Rs 75k if possible.
* 10 slots x 10% takes 48 of about 52 signals a year; 5 x 20% takes 34 and risks a 30% drawdown in 5-15% of histories; 10 x 6% earns too little after charges.
* The only realistic route to a large loss is a person not selling a stop, or an untested regime (history is 4.4 years; no 2008 or 2020 style crash).  Details: LEARNINGS.md section 16.

## 7. How to reproduce everything

* Python: use `.venv\Scripts\python.exe` for the research scripts (pandas installed, **no pytest**); use the system `python` for pytest.  Repo scripts must NOT be run with `-I` (they import `core`).
* Data: `data.pkl` (18.8 MB, gitignored) is in the repo root: candles for 850 symbols 2022-05-27 to 2026-10-13 and 871 listing records, loaded read-only from Atlas.  Rebuild with
  `.venv\Scripts\python.exe run_honest_backtest.py --verify-legacy --cache data.pkl` (reads Atlas read-only; about a minute).  If it is stale, delete it and rebuild.
* Tests: `python -m pytest test_alpha_lab.py test_portfolio_caps.py test_commit_policy.py test_locks_and_orders.py test_exit_rules.py test_honest_backtest.py test_strategy_evidence_unit.py test_volume_exhaustion.py -p no:cacheprovider`
  (expect 135 passed; pymongo shutdown thread noise at exit is harmless).
* Reports (all in `reports/2026-10-14/`, indexed in its README.md):
  * `.venv\Scripts\python.exe research_alpha_lab.py --cache data.pkl --out-dir reports/2026-10-14 --causal --no-vol-gate --base-slip 0.3 --base-cap 2.5 --tag _causal_resting_eod --exit-mode eod_alert`  (primary population; section F filters, E exits, X2 execution, G exploratory, S accounts)
  * `research_risk_profile.py --cache data.pkl` (stop style, PF three ways, streaks, accounts, capacity)
  * `research_tail_capture.py --cache data.pkl --trades reports/2026-10-14/data_base_trades_causal_resting_eod.csv` (tail anatomy, re-entry, pyramid, conviction sizing, E11)
  * `research_dead_money.py --cache data.pkl` (dead money, shelf vs listing, capital constraint, rotation)
  * `research_small_account.py --cache data.pkl --sims 250` (Rs 50k / Rs 1 lakh)
* Pre-registration discipline: each research script states its hypotheses and adoption bar in its docstring BEFORE results.  Bar: paired/clustered 90% CI of the gain excludes 0, positive after dropping the single
  largest contributor, at most one worse year, trade count not reduced.  Exploratory results (section G of the lab) are never adoptable.

## 8. Lessons about process (worth repeating to any new session)

1. A backtest that "binds to production constants" is not one that runs production logic; check the exit and fill MODEL against how the live system really behaves (it was a daily close-based alert, not an intraday stop).
2. Audit the auditor: a pessimistic backtest is as wrong as an optimistic one.  Never drop right-censored trades; report closed-only next to marked-to-market.
3. Decide on information available at decision time; do not use same-day volume for a pre-placed order.
4. Judge changes on paired trade-level evidence with a symbol-cluster bootstrap and year-by-year consistency; single account paths flip sign between assumptions.  Drop the top contributor before believing a tail gain.
5. With about 51 trades a year and an SD of 17% per trade, nothing small is detectable; edges under about 3pp need hundreds of trades.  Live trading validates plumbing first.
6. Heredocs containing apostrophes or `%` in format strings broke tool calls repeatedly: write a script file instead.  Do not read `.env`.

## 9. OPEN DECISIONS AND TO-DO (resume here)

**Decisions waiting for the owner**
1. **E11: apply `DEAD_MONEY_RUNUP_IPO=0`?**  Drops the "peak run-up < 4% at day 14" dead-money clause (the velocity gate day 14/10 sessions with PnL <= 0, volume exhaustion and the 40-day stagnant guard stay).
   Replay: 9 of 227 trades change; 2 improve (KAYNES +217.6pp, BHARTIHEXA +77.8pp), 7 worsen (average -3.8pp); +1.18pp per trade, CI -0.13..+3.11; +0.23 (CI -0.17..+0.90) without the largest mover.
   It breaks even if 2.5% of affected trades become runners (2 of 9 did).  It fails the pre-registered bar (rests on two trades) but its downside is bounded and it matches the outlier goal.  It is an environment variable
   (read by the live exit engine and by shadow tracking in `streamlined_ipo_scanner.py` lines about 867/879/4806), so it can be tried with no code change; making it the default means changing the default in
   `streamlined_ipo_scanner.py`, checking tests that pin it (`test_exit_rules.py` keeps its own constants), and documenting rollback.  Alternative: wait for the resting-order forward test.  **Not applied.**
2. **Git:** nothing is committed.  Ask before committing; the owner pushes themselves.  A sensible split: (a) hardening + engine + tests, (b) slot caps, (c) order sheet + docs + reports.
3. Check `.env` and GitHub Actions secrets/variables for `MAX_ACTIVE_POSITIONS` / `HARD_ACTIVE_POSITIONS` overrides (the workflow files set none, but `.env` could not be read).

**Housekeeping carried over from the 2026-10-12/13 review**
* Delete the leftover `COMMIT_CLEAN_TEST` documents (1 position, 2 signals) from Atlas if they still exist (needs the owner's go-ahead).  Reconcile STEAMHOUSE's listing record (fill extension showed +26.6%).
* Verify real round-trip costs against a broker contract note (the 1.0% flat and the CostModel are assumptions).
* Run `test_regression_fixes.py` deliberately (it imports the scanners; it was fixed to leave the DB unchanged but was not re-run after the cap change).  `SYSTEM_KNOWLEDGE.md` still lists `DEAD_MONEY_DAYS_IPO` as 20 (the code uses 14).

**Next research, ordered**
1. Forward test: place the pre-market orders for about a month with `--log`; compare fills with the replay (within +0.5% of the trigger); keep the alert path running in parallel.
2. Pull Upstox minute bars (check history coverage for 2022+ IPOs) to model the 60-minute hold and measure how often price jumps through the limit; this turns the +0.8 to +1.9pp execution estimate into a measured number.
3. Pre-register a resume rule for the drawdown halt (paper-book recovery) before relying on it.
4. More outlier draws, the only honest way to raise PF: a wider universe (small/mid-cap breakouts) and information not yet used (IPO subscription multiples, issue price/listing gain, anchor lock-in dates, sector, index regime).
5. Revisit the loose-shelf pattern (12-18% range, 25% dead money, 34% runners, n=32) when there are more trades; keep PAPER_ONLY until then.
6. Liquidity-scaled size cap if the account ever exceeds about Rs 50 lakh.
7. Re-run the lab on refreshed candles each quarter (rebuild `data.pkl`).

## 10. Where to find things

* Rulebook: `AGENTS.md` (sections 1-7 original, section 8 rules 12-20, section 9 rules 21-24 from 2026-10-17).  Evidence: `LEARNINGS.md` (index below).  Audit history: `HARDENING_REPORT.md`.  Changes: `CHANGELOG.md` (v3.5.5, v3.5.6, v3.5.7), `EXPERIMENT_CHANGELOG.md`.  Minute-bar plan: `MINUTE_BAR_TEST_PLAN.md`.
* LEARNINGS.md map: 1-6 first audit (superseded where 7-11 differ); 7-11 review and corrected engine; **12** execution, stop style, policy P, changes; **13** tail/outliers and E11; **14** dead money, shelf, capital, rotation; **15** quality vs quantity and PF; **16** small account; **17** explosive moves, live-gate replay, minute bars, selection and early-cut tests, shadow model, big movers, subscription, quiet day (17.1-17.11).  Outputs of every 2026-10-17 test: `reports/2026-10-17/`.
* Engine: `core/honest_backtest.py`, `core/portfolio_sim.py`, `core/exit_rules.py`, `core/execution_model.py`.  Tooling: `order_sheet.py`, `core/order_sheet.py`, `core/risk_guard.py`, `core/scorecard.py`, `paper_scorecard.py`, `core/portfolio_caps.py`.
* Standing memory for the assistant (outside the repo): project memory folder `C:\Users\DeepJAdhia\.claude\projects\D--Personal-Projects\memory\` (goals, evidence state, git-permission rule).


---

## 11. STATE AS OF 2026-10-17 (this section and section 12 override section 9 where they differ; evidence detail in LEARNINGS.md section 17)

Resume prompt for a new session: **"Read SESSION_HANDOFF.md (sections 11 and 12 first), then LEARNINGS.md sections 12 to 17, and continue from section 12."**

### 11.1 The owner and what they want (updated)
* Goal restated several times in this session: catch the explosive post-listing IPO moves (+20% to +100% in days to months), be selective ("quality over quantity", "cut losers early"), keep the
  profit factor (PF) very good, and have an algo that can be trusted.  Missing some moves is fine to them.  Capital Rs 50,000 to Rs 1,00,000, manual execution.
* Hard preferences: never commit or push without explicit permission (the owner pushes); do not read `.env` (Python scripts that import `db` load it themselves); no LaTeX math anywhere (use <=, >=, x);
  change nothing live beyond what is asked; scraping for research data was explicitly allowed (done, polite and local only); report PF three ways (closed only, incl. open at mark to market, ex-top-3).
* Working-style lessons from this session: the owner pushes ideas (dry-up, consolidation, subscription, size); every idea was turned into a pre-registered test with a stated bar, and most failed.
  Say so plainly; do not promise a PF.  I made two factual mistakes that were later corrected (a wrong field name hid the instrument keys; HTEL was wrongly blamed on the volume floor): verify before asserting.

### 11.2 Where the algo stands (honest)
* **Policy P** (listing-high breakout, IPO age 5-60 days, pre-placed stop-limit BUY at listing high +1.5% with limit +2.5%, close-based stops sold at the next open, 12% stop cap, trailing and dead-money exits,
  10 slots x 10%): on refreshed data, 228 trades 2022-05 to 2026-10, net +5.22% per trade, PF **2.43 incl. open runners, 1.71 closed only, 1.91 ex-top-3**; by year PF 0.35 (2022), 4.88 (2023), 2.24 (2024),
  1.09 (2025), 4.30 (2026); about 51 trades a year; 77% of profit comes from outliers; 12 trades in +100% IPOs produced 41% of all gross wins.  **The edge is plausible, not proven.**  Only live forward trading settles it.
* Nothing found this session raises PF without a look-ahead.  A "very good PF" cannot be promised; it can only be measured going forward.
* The strategy is structurally a right-tail harvester: many stop-outs of 8-12% (BAD trades average -8.2%, 38% of trades), few big winners.  Expect streaks of 7-15 losers (LEARNINGS 15, 16).

### 11.3 What the 2026-10-17 research established (details and numbers in LEARNINGS 17.1-17.11)
| Question | Result |
|---|---|
| Is there a common pattern in IPOs that rose 30% / 50% / 100% after listing? | No.  Nothing at listing (range, close location, turnover, price, supply) and nothing at sessions 5/10/20 separates winners from the rest.  28% of IPOs rise 30%+ within 60 days, 19% rise 50%+ within 120 days, 6% rise 100%+, in waves by cohort (2% to 40% by month). |
| Subscription, issue size, issue price? | No signal (AUC 0.43-0.59, signs flip by year); the +100% names range from 1x to 161x subscription and Rs 71 Cr to Rs 2,150 Cr.  Data scraped from Trendlyne, 298 of 462 IPOs matched. |
| Volume dry-up / consolidation / a single quiet day near the listing high? | Three independent null results (multi-day dry-up inverted; DRYCOIL at checkpoints no edge; single quiet day lift 0.97x, tradable setup -0.86% vs +0.32% control).  Dry-up days are 44% of all days. |
| Entry quality filters (18 daily features, first-60-minute features, gate rules)? | None keeps >= 80% of winners while rejecting bad trades better than random (best lift 6pp). |
| Early loser cuts (sell next open if a day 1-5 close is below -4/-6/-8%)? | Separable (1.9% of winners vs 57% of losers close below -6%) but worthless: best +0.12pp (CI -0.16..+0.40); -4% variants lose -0.8pp. |
| Live scanner gates (replayed from 1-minute bars)? | They cost money: volume floor at the trigger minute rejects 40% of trades incl. 46% of winners (rejected mean +7.7%); 60-minute hold test rejects 54%; confirm-then-buy with hold test + chase cap costs -5.5pp per signal and misses 72% of winners.  No gate meets the bar. |
| Resting orders (policy P) vs chasing? | Supported: +1.3 to +1.9pp per trade; measured fills average +0.06% over the trigger (idealised floor on slippage). |
| Widen the +2.5% entry cap to catch gap-up winners? | No: 10 of 14 missed +100% names gapped above the cap, but caps of 5% to none add trades averaging -2.8% to +1.6% and cut whole-book PF from 2.43 to about 1.9-2.3.  The cap stays. |
| Any selection signal at all? | One: a gradient-boosting model on 263 breakout events (AUC 0.70 out of sample, permutation p = 0.000, stable across seeds and depths; top-30% hit rate 21.7% vs 13.6% base, same direction in 3 of 3 test years).  Drivers: opening gap (+), listing-day range (+), prior 5-day return (-), turnover.  But the link to realised net is unproven (63 matched trades, +9.9% vs +3.3%, CI -5.5..+19.5).  **LOGGED ONLY.** |
| Capture of explosive moves | Policy P holds 35% of +20% and +40% episodes; 43% of episodes start more than 10% below the listing high (unreachable by a listing-high breakout); the shelf setup adds 8pp of capture (PF 1.77, negative in 2025, stays PAPER_ONLY). |

### 11.4 Facts about the live system found this session (decisions pending, nothing changed live)
* The live listing scanner gates on partial-day values at alert time: volume floor 150k (`listing_day_breakout_scanner.py` about line 1384), close location >= 50% (about 1746), 60-minute PENDING confirmation,
  +3.5% chase cap.  Replayed from minute bars they reject winners (11.3).  HTEL (+40% in 3 days) was lost to scan latency plus the chase cap (crossed at 12:24, +3.5% by 12:31, +6% at the next scan), not the volume floor;
  DEEPA failed the close-location gate mid-day (47.2% at 11:52).  STEAMHOUSE was entered live at Rs 131.61, 26% above its listing high (a chase), and was flat.
* The hardening work (slot caps 10/10, close-based stop sheet, resting orders, locks, guards) is **uncommitted**, so any scheduler that runs the repo remote runs the last commit (1d7aff3).  Where the scanners execute was not verified.
* Data defects: 114 symbols carry placeholder listing dates (2026-08-17/18; only 5 of 228 policy-P trades and 6 of 263 events are affected, PF unchanged without them); 18 Upstox instrument keys are stale (BANSALWIRE, BLSE, NARMADA,
  POCL, KIRLPNU, JLHL, DBEIL, ABINFRA, KMEW, PASHUPATI, AHCL, RNBDENIMS, MWL, LEMERITE, BUILDPRO, TCC, TAALTECH, CORDELIA); the Atlas daily candle cache lacks or truncates a few recent IPOs (STEAMHOUSE, MANIKA, AUGMONT stale);
  2022-23 minute sessions are often partial (full-session share 67% and 75%).  Leftover `COMMIT_CLEAN_TEST` documents may still be in Atlas.

### 11.5 Repo changes (NOTHING is committed; git HEAD is 1d7aff3; `git status` lists all of this as modified or untracked)
* **Modified:** `.gitignore` (adds `data_minute/`, `data_ipo_meta/`, `models/`), `AGENTS.md`, `CHANGELOG.md` (v3.5.6, v3.5.7), `EXPERIMENT_CHANGELOG.md`, `README.md`, `SYSTEM_KNOWLEDGE.md`, the three scanners
  (slot caps 10/10 and the earlier hardening), `test_regression_fixes.py`.
* **New engine and tooling:** `core/` (honest_backtest, portfolio_sim, exit_rules, execution_model, order_sheet, portfolio_caps, risk_guard, guards, locks, scorecard, data_coverage, **shadow_score**), `order_sheet.py`, `paper_scorecard.py`,
  `run_honest_backtest.py`, `check_data_coverage.py`, `fetch_minute_bars.py`, `fetch_ipo_meta.py`, `shadow_score.py`, `train_shadow_model.py`.
* **New research scripts (all pre-registered in their docstrings):** `research_gate_replay.py`, `research_capture_gaps.py`, `research_recall_precision.py`, `research_coil.py`, `research_early_cut.py`, `research_event_model.py` (+ `_robust`),
  `research_minute_probe.py`, `research_minute_coverage.py`, `research_minute_q1_q4.py`, `research_winner_anatomy.py`, `research_ipo_meta_signal.py`, `research_big_movers.py`, `research_gap_cap.py`, `research_quiet_day.py`, plus the 2026-10-12..15 set.
* **Docs:** `LEARNINGS.md` sections 1-17, `HARDENING_REPORT.md`, `MINUTE_BAR_TEST_PLAN.md`, this file, outputs in `reports/2026-10-12` .. `reports/2026-10-17` (the last has every output of this session).
* **Tests:** 149 pass: `python -m pytest test_shadow_score.py test_data_coverage.py test_alpha_lab.py test_portfolio_caps.py test_commit_policy.py test_locks_and_orders.py test_exit_rules.py test_honest_backtest.py test_strategy_evidence_unit.py test_volume_exhaustion.py -p no:cacheprovider -W ignore`
  (system python; `.venv` has no pytest and no scikit-learn).  `test_regression_fixes.py` was not re-run (it imports the scanners).  `run_honest_backtest.py --verify-legacy` must still print EXACT MATCH.
* **Behaviour-neutral shadow hook:** `order_sheet.py` now adds a `shadow` field (features known before the open) to each logged resting order; a test asserts the placed orders and skip notes are identical with and without it, and
  any exception in the hook is swallowed.  No scanner gate, exit, stop, slot cap or entry rule was changed by this session.

### 11.6 Data assets and how to rebuild (all local, gitignored where large)
* `data.pkl`: tuple (listing records, candles) for about 858 symbols to 2026-10-15, built by `.venv\Scripts\python.exe run_honest_backtest.py --verify-legacy --cache data.pkl` (reads Atlas read-only).  Check with `check_data_coverage.py --cache data.pkl`.
* `data_minute/`: Upstox V3 1-minute candles for each IPO first 60 days, 833 of 851 listings since 2022; `fetch_minute_bars.py` is resumable (28-day chunks, back-off on 429, stops on HTTP 401; the token comes from `.env` through `db`).
* `data_ipo_meta/`: raw Trendlyne monthly HTML (52 pages) and `ipo_meta_raw.csv`; `fetch_ipo_meta.py` never refetches a cached month.  Chittorgarh and Investorgain were NOT usable (free tier truncated or empty) and nothing was bypassed.
* `models/shadow_gbm_v1.pkl`: shadow model trained on 263 events (38 positives) through 2026-09-28; regenerate with `train_shadow_model.py`.
* **Python split:** the `.venv` and the system python have different pandas versions, so a pickle made in one cannot be read in the other.  Build events and run engine scripts with the venv; fit and score models (scikit-learn) with the system python.
  Heredocs with apostrophes break the Bash tool: write script files with the Write tool.

### 11.7 Daily and weekly protocol
1. **Pre-market:** `python order_sheet.py --capital <account value> --holdings holdings.json --peak-capital <highest value> --log` -> place stop-limit BUY day orders (trigger listing high +1.5%, limit +2.5%), up to the free slots; cancel unfilled orders before the close.
   Record every fill price and time (pass mark: within +0.5% of the trigger).  The log row also carries the pre-open shadow features.
2. **After the close:** the 6:30 PM stop scan messages; if the close is at or below the alert level, sell at the next open.  Then `python shadow_score.py` (system python, read-only Atlas) to append final scores to `logs/shadow_scores.jsonl`.
3. **Weekly:** `python shadow_score.py --report` (hit rate by score tercile, rows after the model train_end 2026-09-28) and `python paper_scorecard.py` once there are 30+ closed trades and 5+ closed outliers (PF three ways).
4. **Never** add quality filters, tighter stops, rotation or confirmation steps on current evidence (section 12.4).

### 11.8 Decisions waiting for the owner
1. Turn off the live intraday volume floor, the close-location gate and the 60-minute confirmation (behind env flags with a documented rollback), or keep using resting orders only (current choice: resting orders only, no live change).
2. Commit: nothing is committed; a sensible split is (a) hardening, engine and tests, (b) slot caps, (c) order sheet, shadow score and docs, (d) research scripts and reports.  Ask before committing; the owner pushes.
3. E11 (`DEAD_MONEY_RUNUP_IPO=0`, see section 9): still not applied, still fails the pre-registered bar.
4. Check `.env` and any scheduler variables for `MAX_ACTIVE_POSITIONS` / `HARD_ACTIVE_POSITIONS` overrides and confirm which code the schedulers run.

---

## 12. FUTURE PLAN (ordered; each step has a gate)

### 12.1 Phase 0, this week: make live equal tested
* Decide 11.8 item 1; commit per 11.8 item 2 when the owner says so; confirm which code the schedulers run.
* Start the forward test: place the resting orders for real with `--log`; keep the alert path running in parallel; run `shadow_score.py` after each close.
* Housekeeping: re-resolve the 18 stale Upstox keys (`update_upstox_mapping.py`), repair the 114 placeholder listing dates, backfill the missing candle documents, delete leftover `COMMIT_CLEAN_TEST` rows (needs the owner's go-ahead), verify real round-trip costs against a broker contract note.

### 12.2 Phase 1, weeks 1-8: forward evidence (the only thing that can prove the edge)
* Success criteria: fills within +0.5% of the trigger on most orders; parity between replay and live signals; after 30+ closed trades and 5+ closed outliers, closed-only PF with its interval via `paper_scorecard.py`.  Expect long flat stretches and 7-15 losers in a row.
* Kill criteria (agree before starting): halt new entries at a 20% drawdown from the peak (`--peak-capital`); the resume rule is still NOT pre-registered (do it in this phase: resume when the paper book recovers).
* Shadow score decision: after 40-60 crossed, complete rows after 2026-09-28, adopt only if the top third beats the rest on mean net with a symbol-cluster CI excluding 0 and ex-top-3 positive.  If adopted, the action is sizing or skipping at the open (the opening gap is known by then), never a tighter stop.

### 12.3 Phase 2, research queue (each pre-registered before running; bar: paired CI excludes 0, positive without the single largest contributor, <= 1 worse year)
1. **The 43% of explosive episodes that start more than 10% below the listing high:** design a different setup (recovery or base breakout) and test it on the same engine and data; do not bolt it onto policy P.
2. **Loose-shelf pattern** (range 12-18%, n = 32 in the earlier study): revisit when there are more trades; keep PAPER_ONLY until then.
3. **Information not yet tested:** grey market premium history, anchor lock-in dates, sector and theme, fundamentals, index regime.  Chittorgarh and Investorgain history is not freely available; options are a paid IPO dataset (for example the owner's broker or a vendor), manual capture, or a small curated sheet.  Test only with a pre-registered bar (the subscription test is the template: `research_ipo_meta_signal.py`).
4. **Re-run Q1-Q4 on 2024+ trades only** (cleaner minute data) and re-run the shadow model when trades since the train_end accumulate; retrain quarterly.
5. **Regime awareness:** W50 rates swing from 5% to 36% by cohort; a regime gate failed as a filter in earlier tests (breadth, supply), so only test it as a sizing or pause rule with pre-registration.
6. **Capacity:** a liquidity-scaled size cap if the account passes about Rs 50 lakh (earlier plan, section 9).
7. **Quarterly:** rebuild `data.pkl` and `data_minute/`, re-run `research_alpha_lab.py` and the gate replay; check `check_data_coverage.py` first.

### 12.4 Do not retest without new information (all failed or were null, with evidence in LEARNINGS)
Quality filters from daily or first-hour features; volume dry-up, tight consolidation, single quiet day, DRYCOIL; early loser cuts and tighter stops; rotation out of dead-looking positions; breakeven stops and trail tweaks; pyramiding and conviction sizing; widening the entry cap; confirm-then-buy and the 60-minute hold test; subscription multiples, issue size and issue price; EOD volume and body gates as filters (the body gate raises PF only through look-ahead).

### 12.5 Rules of engagement for any new session
Pre-register hypothesis and bar before running; judge paired on the same signals with a symbol-cluster bootstrap; drop the top contributor before believing a tail gain; report PF three ways; never use same-day information for a pre-placed order; never drop right-censored trades; keep changes additive and behaviour-neutral unless the owner approves; never commit or push; never read `.env`; write research outputs into `reports/<date>/` and update LEARNINGS, the changelog and this file.


---

## 13. STATE AS OF 2026-10-21 (this section overrides sections 9, 11.5 and 11.8 where they differ; evidence in LEARNINGS 18 and 19, outputs in `reports/2026-10-21/`)

Resume prompt for a new session: **"Read SESSION_HANDOFF.md (sections 11, 12 and 13), then LEARNINGS.md sections 12 to 19, and continue from 13.4."**

### 13.1 Where the algo stands (unchanged, honest)
* Policy P (listing-high breakout, resting stop-limit buy at listing high +1.5%, close-based stops sold at the next open): replay on `data.pkl`, 228 trades 2022-05 to 2026-10, net +5.22% per trade, PF 2.43 incl. open runners / 1.71 closed only / 1.91 ex-top-3; by year 0.35 (2022), 4.88, 2.24, 1.09 (2025), 4.30 (2026); profit is about 77% outliers.  **The edge is plausible, not proven**; only live forward trading with resting orders can settle it.
* Nothing found in this session raises PF.  A PF of 2 cannot be promised; closed-only PF above 2 is roughly a one-in-four outcome.

### 13.2 What this session did
| Item | Result |
|---|---|
| Second setup S2 (10-session pivot breakout while more than 10% below the listing high, age 15-200; LEARNINGS 18) | **FAILED** the pre-registered bar: 146 trades, +0.41% net per trade (CI -2.6..+3.9), PF 1.07 / 1.08 / 0.58, negative in 2025 and 2026, dead money 49%, entries reach +20% within 20 sessions 22% vs 35% for P, random-day control beat it (-3.0pp, CI -6.3..+0.2), adding it to P cut the Rs 1 lakh replay from +124.6% to +90.2% (Calmar 0.82 to 0.48; displaced 41 P trades averaging +8.8%).  Not adopted, not PAPER_ONLY.  Reaches 17% of the far-below-listing-high episodes but with about zero profit (capture is not profit). |
| Version bump to v3.6.0 (owner request) | All six touchpoints in one change (master_audit, three scanners, db.py, README badge and footer); `master_audit.py --section 3` confirms.  It labels the hardened execution release (10 x 10% slots, close-based stop alerts, commit lock, shelf/hourly PAPER_ONLY); NO entry or exit rule changed.  New `core/versioning.py` (forward cohort = tags 3.5.0 and 3.6.0, cutoff 2026-09-29) is used by `analyze_positions_performance.py`, `monthly_strategy_audit.py`, `core/strategy_evidence.py` so the scorecard does not empty; `test_versioning.py` guards the sync. |
| Anchor lock-in expiries (nominal listing + 28 and + 88 days; LEARNINGS 19) | Both pre-registered tests PASSED (D = -1.74pp, N = 475; D = -1.26pp, N = 446 per 10-session window; U90 negative in only 3 of 5 years) and a post-hoc age-profile audit supports date-specific dips (2-4 days before the nominal dates).  Too small for a 1.0% round trip, not shortable, no entry signal; P entries 0-10 days before an unlock were not worse (N = 27, +1.10%, skip rule FAILS).  No rule, no live change. |
| Owner ideas declined as already tested | Quiet-day entry (LEARNINGS 17.4b, 17.8, 17.11: lift 0.97x, tradable version -0.86%), capping winners at 20-30% (sell-a-third failed; PF lives in the tail), quality-only filtering (every filter lowered PF; AGENTS rules 14 and 20). |
| Data sources checked | capitalmarket.com IPO table: not an API, robots.txt disallows bots and query strings, columns already tested (subscription, size, price, listing gain), no GMP or lock-in column; not used.  Reddit: no threads found.  No free bulk GMP or lock-in dataset exists in search results (paid: Apify "India IPO Tracker"; unclear terms: ipomarket.in; per-page only: ipoji.com).  Chittorgarh scraper (bse-ipo) rejected for the reason in 17.9. |

### 13.3 Repo state (as written 2026-10-21: nothing committed, HEAD 1d7aff3; SUPERSEDED: the owner has since committed this work as 7047739, see 14.1)
* **New files (this session):** `core/setup2.py`, `core/versioning.py`, `research_setup2.py`, `research_anchor_unlock.py`, `research_anchor_unlock_audit.py`, `test_setup2.py` (18), `test_versioning.py` (3), `test_anchor_unlock.py` (5), `reports/2026-10-21/` (README, outputs).
* **Modified for the bump and cohort filters:** `master_audit.py`, `streamlined_ipo_scanner.py`, `listing_day_breakout_scanner.py`, `hourly_breakout_scanner.py`, `db.py`, `README.md` (touchpoints); `analyze_positions_performance.py`, `monthly_strategy_audit.py`, `core/strategy_evidence.py` (cohort filters).  Docs appended or fixed: `AGENTS.md` (cohort rule, section 10 honesty protocol), `CHANGELOG.md` (v3.6.0), `EXPERIMENT_CHANGELOG.md`, `LEARNINGS.md` (18, 19), `SYSTEM_KNOWLEDGE.md` (`DEAD_MONEY_DAYS_IPO` is 14, was documented as 20).
* **`.gitignore`:** also ignores regenerable research CSVs: `reports/2026-10-21/*.csv`, `reports/2026-10-12/data_*.csv`, `reports/2026-10-14/data_base_trades.csv` and `data_base_trades_causal_resting.csv`.  CSVs that other scripts read as input stay tracked (`gate_replay_V*.csv`, `event_model_events.csv`, `ipo_meta_matched.csv`, `data_base_trades_causal_resting_eod.csv`).  `.env`, `data.pkl`, `data_minute/`, `models/`, `data_ipo_meta/` were already ignored.
* **Verification run 2026-10-21:** 175 unit tests pass (command: `python -m pytest test_anchor_unlock.py test_versioning.py test_setup2.py test_shadow_score.py test_data_coverage.py test_alpha_lab.py test_portfolio_caps.py test_commit_policy.py test_locks_and_orders.py test_exit_rules.py test_honest_backtest.py test_strategy_evidence_unit.py test_volume_exhaustion.py -p no:cacheprovider -W ignore`, system python); `run_honest_backtest.py --verify-legacy` EXACT MATCH (203 trades, 0 mismatches); `master_audit.py --section 3` version strings match 3.6.0.  **NOT verified:** `test_regression_fixes.py` HUNG when run (it imports the scanners and seems to wait on the network or DB), so it was stopped; run it in a connected shell before deploying.
* A ready commit message (single commit) was given in chat: subject "v3.6.0: hardened execution release, honest-backtest engine, research and docs".  Four-commit split: (1) hardening, engine, exit rules and tests; (2) flat 10 x 10% slot caps; (3) order sheet, shadow score, scorecard, version sync; (4) research scripts, reports and docs.  The owner pushes; keep the tag at 1d7aff3 as the rollback point.

### 13.4 Decisions waiting for the owner, and next steps (ordered)
1. **Commit (single or four-way) and push, but first:** run `test_regression_fixes.py` in a connected shell; check `.env` and GitHub Actions secrets/variables for `MAX_ACTIVE_POSITIONS` / `HARD_ACTIVE_POSITIONS` overrides (slots 10/10 go live on push); confirm which code the schedulers run.  New rows will carry version 3.6.0.
2. **Live partial-day gates (11.8 item 1, still open):** turn off the 150k intraday volume floor, the close-location gate and the 60-minute hold behind env flags with a documented rollback (the only route with replay evidence of more signals at equal or better profitability: the floor rejects 40% of trades incl. 46% of winners; confirm-then-buy costs -5.5pp per signal), or keep resting orders only.  Not done; offered.
3. **Start the forward test:** `python order_sheet.py --capital <Rs> --holdings holdings.json --peak-capital <Rs> --log`, record every fill (pass mark: within +0.5% of the trigger), run `python shadow_score.py` after each close, and judge the edge with `python paper_scorecard.py` after 30+ closed trades and 5+ closed outliers.
4. **E11** (`DEAD_MONEY_RUNUP_IPO=0`): still fails the bar, still not applied (section 9 item 1).
5. **Housekeeping (from section 12.1, unchanged):** 18 stale Upstox keys, 94 placeholder listing dates (they need a real source: candles start at the placeholder), missing candle documents, leftover `COMMIT_CLEAN_TEST` rows (needs the owner), real round-trip costs from a contract note.

### 13.5 Additions to the do-not-retest list (section 12.4)
10-session pivot breakouts (base and recovery) below the listing high at age 15-200 (LEARNINGS 18); exit timing or entry skipping around the anchor unlocks (effect smaller than costs, LEARNINGS 19); quiet-day / dry-up entries and winner caps (already listed); subscription, size, price and listing gain from capitalmarket.com or Trendlyne (already null).  Untested, not rejected: a 20-session pivot and a full 40-session reclaim (too few signals), exits designed for base setups, the 6-month pre-IPO / promoter unlock (about listing + 178 days), allotment-date-exact unlock timing.  New information that WOULD justify a pre-registered test: grey-market premium history, per-IPO anchor allotment dates, sector or theme tags, index regime.

### 13.6 How to reproduce this session
`.venv\Scripts\python.exe research_setup2.py --cache data.pkl --out-dir reports/2026-10-21` (about 40 s), then `research_anchor_unlock.py` and `research_anchor_unlock_audit.py` with the same arguments (about 2 min each; the audit reads the CSV that the first writes).  Universe hygiene is `core.setup2.clean_universe` (676 of 858 symbols usable: 94 placeholder listing dates, 63 under 40 candles, 15 inconsistent listing high, 7 listed before 2022-05, 3 first candle far from listing).  Rules for any new session: pre-register the hypothesis and bar in the script docstring before running, report PF three ways, label the evidence tier, never commit or push, never read `.env`, write scripts with the Write tool, answer per AGENTS.md section 10.

---

## 14. STATE AS OF 2026-10-22 (overrides section 13 where they differ; evidence in LEARNINGS 20 to 20.5, outputs in `reports/2026-10-22/`)

Resume prompt for a new session: **"Read SESSION_HANDOFF.md (sections 11 to 14), then LEARNINGS.md sections 17 to 20.5, and continue from 14.4."**

### 14.1 What changed (the owner committed v3.6.0 as 7047739 on branch `hardening/parity-and-fixes`, in sync with origin; EVERYTHING BELOW IS UNCOMMITTED)
* **Policy P is untouched** (228 trades, +5.22%, PF 2.43 / 1.71 / 1.91; `run_honest_backtest.py --verify-legacy` EXACT MATCH). `SCANNER_VERSION` stays 3.6.0; the work is logged as the patch note `[v3.6.1]` in CHANGELOG.md.
* **S3 paper book `S3_PAPER_VERSION = "1.0.0"` (PAPER_ONLY, unproven):** resting stop-limit buy at the highest high since listing +1.5% (limit +2.5%), IPOs 61-400 days old, 20-session mean turnover <= Rs 25 Cr, X1 exits (stop and trailing only, no time-based exits), 10 x 10% paper slots, own cooldown. Code: `core/setup3.py`; MongoDB storage `core/s3_store.py` (collections `s3_paper_orders`, `s3_paper_positions`, `s3_paper_runs`); CLI `s3_paper.py fill|exit|list|orders`; `order_sheet.py` section "S3 PAPER BOOK"; alerts `s3_paper_alerts.py` and `.github/workflows/s3-paper-alerts.yml` (same Telegram bot and chat, prefix "S3 PAPER"); forward verdict `s3_scorecard.py` / `core/s3_scorecard.py`.
* **Research results this session (LEARNINGS 20 to 20.3):** the slow IPO leaders (RUBICON, CORONA, SUDEEPPHRM) move 4-9 months after listing; nothing at listing identifies the +50% movers (market cap, issue size / price, subscription, listing gain, minute-bar listing-day shape all null); S3 with X1 exits passes the pre-registered bar, with P's exits it fails; S2 with X1 exits still fails; S4 (buy session 11 after early strength) fails the year rule; volatility hurts as a filter; low turnover is the carried-forward marker (V2-V4 in 20.5).
* **Verification:** full suite passes (test_setup3 9, test_s3_paper 29, test_s3_alerts_scorecard 13 added); live read-only runs of `order_sheet.py`, `s3_paper_alerts.py --dry-run` and `s3_scorecard.py` succeeded; one order document (MARKOLINES, 2026-10-22) exists in `s3_paper_orders` from verification. The live candle cache lagged one session at test time (the alert flags it).

### 14.2 What is proven and what is not
Replay-validated, not proven good: parity sheet vs replay 0 mismatches over 3,319 bars; out-of-time turnover rule +5.8pp over all trades (pooled kept +9.1%, N = 67); permutation p = 0.0012; replication on random S3-state days +3.85pp; paper rule at 1.0% slippage +6.7%. Caveats: 2023-26 only, survivors only, a few outliers carry the profit, the Rs 25 Cr cutoff was chosen on this sample, nine looks were taken. **The forward paper sample decides** (14.4).

### 14.3 Files added or changed
New: `core/setup3.py`, `core/s3_store.py`, `core/s3_scorecard.py`, `s3_paper.py`, `s3_paper_alerts.py`, `s3_scorecard.py`, `.github/workflows/s3-paper-alerts.yml`, `research_setup3.py`, `research_setup2_x1.py`, `research_w50_deep.py`, `research_early_strength.py`, `research_few_quality.py`, `research_s3_validation.py`, `test_setup3.py`, `test_s3_paper.py`, `test_s3_alerts_scorecard.py`, `reports/2026-10-22/`. Changed (additive): `order_sheet.py` (S3 section, `s3_collect`, `--no-s3`, `--no-s3-store`), `core/order_sheet.py` (optional `title` / `footer`, R:R omitted when 0), `db.py` (three collection handles and indexes only; `SCANNER_VERSION` untouched), docs (CHANGELOG, EXPERIMENT_CHANGELOG, AGENTS 25-29, README, SYSTEM_KNOWLEDGE 19, LEARNINGS 20-20.5, this section).

### 14.4 Decisions and next steps (ordered)
1. **Commit and push the S3 work when ready** (the owner commits; nothing from section 14 is in 7047739): the `s3-paper-alerts` workflow then starts running on its schedule; it needs the existing secrets `MONGO_URI`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`. The 13:20 UTC job assumes the candle cache is refreshed by about 18:30 IST; check the first alerts for the STALE flag.
2. **Run the forward paper test:** every evening read the S3 alert, place the orders in a paper account or just note them, record fills and exits with `python s3_paper.py fill|exit`, and run `python s3_scorecard.py` weekly. Re-judge at 40, 60 and 80 closed trades; nothing is promoted before 40.
3. Items 2, 4 and 5 of section 13.4 are unchanged (live partial-day gates, E11, housekeeping).
4. **If an S3 rule must change** (cap, exits, age window): follow AGENTS rule 25 (version bump, CHANGELOG, fingerprint) so the forward sample is never blended.

### 14.5 Do-not-retest additions
See AGENTS rule 28.

### 14.6 How to reproduce
`.venv\Scripts\python.exe research_setup3.py --cache data.pkl --out-dir reports/2026-10-22` (about 3 min), then `research_setup2_x1.py`, `research_w50_deep.py` (with `PYTHONPATH=.`), `research_early_strength.py`, `research_few_quality.py`, `research_s3_validation.py` (with `PYTHONPATH=.`), same arguments. Tests: `python -m pytest` (whole suite).
