# AlphaPulse Hardening Report (2026-10-12)

Audit of the system before live capital: what was wrong, what was fixed, what the data actually says,
and where real alpha could still come from.  Every number below is reproducible with the commands in
section 8.  Branch: `hardening/parity-and-fixes` (not committed).

> **CORRECTION 2026-10-13 - see section 11.**  The audit engine behind sections 1-3 and 6 was found to lean AGAINST
> the strategy (end-of-day gating with next-open fills, trades still open at the end of the data dropped, candle gaps
> for the newest IPOs).  The legacy-vs-live attribution in section 2 and the code fixes in section 5 stand; the "no edge"
> headline does not.  Corrected, the LISTING_BREAKOUT engine is weakly positive to break-even per trade (about -1% to
> +2.5% depending on the fill price), not proven.

---

## 1. Bottom line

* **The 5-year result in the dossier (PF 2.24, +1,154%) is not what the live system would have done.**
  The legacy backtest followed different rules from the live scanners and was optimistic on every
  axis.  Run the way the live scanners run, the same 849-IPO history gives **79 trades, PF 1.24, +0.79%
  gross per trade (-0.21% net of 1% costs)**; at Rs 1 lakh with realistic Indian costs the account result
  is about **-3%** over the whole period.
* **No exit, stop or entry-filter change survives out-of-sample testing.**  35 pre-registered exit/stop
  variants on 5,323 paired signals: nothing robust.  8 entry filters and a regularised model, walk-forward:
  nothing.  The existing parameters sit on a flat plateau, so further tuning has no value.
* **There is a small, real signal in the trigger.**  Breakout days beat an arbitrary liquid IPO-day by
  about +1.2 percentage points per trade (CI -0.17 to +2.66, 4 of 5 years).  The structural baseline of
  the system's exits is negative (-0.84% per trade for an arbitrary IPO-day), so the trigger roughly
  cancels the drag.  After costs the edge is about zero.
* **The strategy cannot be validated by live trading on a useful timescale.**  ~19 trades a year with an
  SD of 12.5% per trade: confirming a true +2% edge needs ~177 trades (~9.5 years).  Evidence has to come
  from the broad signal set (~177 signals a year, ~2 years) and from new information, not from the book.
* **What was fixed** (code, tests, defaults) is in section 5.  **What would create alpha** is in section 7.

## 2. Why the backtest and the live system disagreed

`run_latest_rules_backtest.py` claimed to bind to the live scanners but re-implemented the rules.  With
`run_honest_backtest.py --verify-legacy` the new engine reproduces it exactly (196 trades, 0 mismatches) and
each divergence is then switched on one at a time:

| Step (cumulative)                                              | N   | PF   | Avg/trade | Top-3 share of profit |
|----------------------------------------------------------------|-----|------|-----------|-----------------------|
| 0. Legacy backtest                                             | 196 | 2.27 | +4.48%    | 51%  |
| 1. Shelf detector off-by-one fixed                             | 206 | 2.13 | +4.46%    | 49%  |
| 2. IPO age in calendar days (live) not bar index               | 157 | 2.02 | +4.19%    | 51%  |
| 3. Live gate stack (leader score, +1.5% post-confirm, turnover/circuit/price floors) | 92 | 1.74 | +2.70% | 86% |
| 4. Shelf needs volume spike AND Rs10 Cr (live)                 | 85  | 1.95 | +3.12%    | 81%  |
| 5. Run-up / winner status from closes (live)                   | 85  | 1.57 | +1.91%    | 118% |
| 6. Stage-1 trail 10% below price (live)                        | 85  | 1.31 | +1.03%    | 175% |
| 7. Dead-money gates on 14 calendar days / 10 sessions (live)   | 85  | 1.24 | +0.81%    | 222% |
| 8. Entry at next open under the live limit-buy ceiling         | 79  | 1.27 | +0.89%    | 210% |
| 9. Stop exits fill at min(open, stop)                          | 79  | 1.24 | +0.79%    | 230% |
| 10. Circuit-lock heuristic                                     | 79  | 1.24 | +0.79%    | 230% |

Each switch also hurts on its own (ablation A in the run output).  "Top-3 share" above 100% means the
three best trades earn more than the whole book; without them the live-faithful book averages -1.06%
(PF 0.69).  By setup: LISTING_BREAKOUT +3.35% (N=36), SHELF_BREAKOUT -1.36% (N=43).
Bootstrap 95% interval for the average trade: -1.8% to +3.8%.

Not modelled (needs intraday history): the live 60-minute hold, the 2.5% pullback ejection and the 14:30
cut-off.  They filter further; the sign of their effect on returns is unknown.

## 3. Account level (Rs 1,00,000, mark-to-market, whole shares)

| Trades            | Costs                         | Return | CAGR  | Max DD | PF   |
|-------------------|-------------------------------|--------|-------|--------|------|
| Legacy, 5/7 slots | none                          | +137%  | +22%  | -26%   | 1.75 |
| Legacy, 5/7 slots | Indian delivery + 0.3%/side   | +87%   | +16%  | -30%   | 1.47 |
| Live-faithful     | none                          | +10%   | +2.2% | -17%   | 1.25 |
| Live-faithful     | Indian delivery + 0.3%/side   | **-3%**| -0.7% | -21%   | 0.93 |
| Live-faithful     | 0.75%/side slippage           | -13%   | -3.1% | -27%   | 0.73 |

Slot policy (5/7 as coded, 5/7 documented, flat 5, flat 10) and 20 random same-day orderings change the
result by a few points only; the book is not slot-limited, it simply has no edge to allocate.
(An earlier -53% drawdown figure was a bug in my equity curve, fixed and regression-tested.)

## 4. Verified discrepancies between the audit dossier / docs and the code

| Claim in the dossier or docs | Verified fact |
|---|---|
| 47.4% win rate, +5.89% avg, +1,154% total, PF 2.24 | Reproduced: 45.41%, +4.48%, +878%, PF 2.27 (yearly table sums to 1,022%) |
| Avg hold about 12 days | 22.4 bars in the legacy backtest |
| Trigger "Close > Listing_Day_High" | Live trigger is intraday high > listing high; entry = max(price, listing high) |
| Listing volume spike 1.5x | 1.8x in strict mode (default) for all types, plus leader score >= 5 and post-confirm move >= 1.5% (Engine 1 window is +1.5% to +3.5%) |
| Upper-wick <= 25% gate | Not in the live code |
| Shelf: 1.8x OR Rs15 Cr | Live: 1.8x AND Rs10 Cr (changelog says Rs5 Cr) |
| Trail: breakeven lock, then 50% / 65% of peak | Live: 10% below price from +3%; winners (>=15% peak) max(1.05 x entry, 0.8 x peak) |
| Velocity gate: day 14 and PnL <= 0 | Also dead-money: peak < 4% at day 14 regardless of PnL; 14 calendar days OR 10 sessions (backtest used 14 bars) |
| Hard cap 7 "only when book is risk-free" | Code admits if score >= 4, tier A (the default) or CORRECTION regime; no risk-free check; check-then-insert was not atomic |
| Corporate Action Guard protects stops | It froze the stop on any drop >= 25% vs the last stored price, indefinitely |
| MILKYMIST Rs 1,184 / 1,222, Rs 1,463 Cr turnover, "89% upper wick" | Prices are Rs 184.89 / 222.11, turnover Rs 463 Cr; Day-2 candle is a 76% bearish body with a 13% wick; Day-6 close was at the upper circuit |
| NAV +104.3%, 7 slots beat 10, friction table | No code in the repo produced them; reproduced now by `core/portfolio_sim.py` with different results |
| "Under 22% of alpha lost at 2% friction" | The dossier's own table shows 44% |
| 17/17 tests passing | Several tests re-implemented the exit logic inline; they could not catch a production regression |
| 848 / 870 IPO universe | 870 listing records, 849 with usable candles |
| (not mentioned) | Hourly `INTRADAY` signals opened ACTIVE capital with no backtest; R/R >= 1.25 gate can never bind; CHANGELOG v3.5.4 repeats the legacy numbers as "verified" |

## 5. What changed in the code (all on the branch, nothing committed)

| Change | Files | Behaviour |
|---|---|---|
| Shared exit rules | `core/exit_rules.py`; `streamlined_ipo_scanner.py` now calls it | Identical to the old inline logic (oracle test on 40,000 random states) |
| Fail-safe corporate-action guard | `core/guards.py`; both guard sites in `streamlined_ipo_scanner.py` | Only split/bonus ratios pause a position; any other big drop runs the normal stop |
| Atomic slot commit | `core/locks.py`; `commit_trade_to_db`, hourly scanner | Mongo mutex; if unavailable the trade is PAPER_ONLY, never uncapped ACTIVE |
| SHELF_BREAKOUT paper-only | `listing_day_breakout_scanner.py` | `LISTING_SHELF_LIVE_CAPITAL_ENABLED=1` restores live capital |
| Hourly INTRADAY paper-only | `hourly_breakout_scanner.py` | `HOURLY_LIVE_CAPITAL_ENABLED=1` restores live capital |
| Order sheet with resting stops | `core/order_sheet.py`, `order_sheet.py` | GTT stop-limit levels, whole-share sizing, total open risk |
| Drawdown kill-switch | `core/risk_guard.py` | Warn at 12%, halt new entries at 20% drawdown (calibrated from the simulation) |
| Pre-registered scorecard | `core/scorecard.py`, `paper_scorecard.py` | STOP / CONTINUE / SCALE_ELIGIBLE rules fixed in advance |
| Honest backtest + portfolio sim | `core/honest_backtest.py`, `core/execution_model.py`, `core/portfolio_sim.py`, `run_honest_backtest.py` | Legacy parity EXACT; flag-driven; costs, gaps, lock heuristic |
| Research tooling | `research_events.py`, `research_exits.py`, `research_walkforward.py`, `research_trigger.py` | Reproducible studies |

Tests: 37 existing + 70 new = 107, all passing.  `AGENTS.md` section 7 records the rules that keep tests,
backtest and production from diverging again.  **Not yet run:** the refactored exit path against the real
MongoDB scan; do one dry scan before relying on it.

## 6. What the research found

| Study | Population | Result |
|---|---|---|
| Live gates (cluster bootstrap) | 865 breakout events | Volume spike 1.8x: -0.07pp; body gate: -0.53pp; post-confirm: +0.37pp; extension cap: +0.57pp, all CIs straddle zero. Liquidity floor (turnover20 >= Rs0.75 Cr): **+4.6pp** (CI +2.1 to +7.1). Upper wick <= 25%: -2.7pp (worse) |
| Paired exit grid | 5,323 any-day signals | 35 variants of trail %, trail start, winner threshold, cushion, velocity, dead-money, stop cap / buffer / lookback: all within +/-0.45pp, none robust |
| Paired exit grid | 829 breakout events | Same: flat plateau |
| Walk-forward entry rules | 752 events, test 2024-26 | Volatility filter: +3.3pp in-sample, +0.01pp out-of-sample. Low IPO supply: +3.9pp (CI -0.1 to +9.1), 2/3 years, 116 trades: a hypothesis, not a rule |
| Walk-forward ridge model | same | Out-of-sample rank-IC +0.05 |
| Trigger vs any IPO-day | 15,475 entries | Baseline -1.04% net; LISTING_BREAKOUT +0.12% (diff +1.20pp); SHELF -3.09% (diff -2.05pp) |

Statistical power: SD 12.5% per trade, 18.6 trades a year in the live-faithful book.  Edge of +1% needs
707 trades, +2% needs 177, +3% needs 79 (about 4 years).

## 7. Where alpha could come from (ranked by expected value per effort)

1. **Information the model has never seen.**  The current features are all price/volume.  Indian IPO
   momentum is known to be driven by pre-listing information that is public and free: QIB / NII / retail
   subscription multiples, listing gain versus issue price, anchor quality and lock-in expiry dates, issue
   size and OFS share, sector, and the Nifty / Smallcap regime.  Add these to the event table
   (`research_events.py`) and re-run `research_walkforward.py`.  This is the most promising lead.
2. **Faster, broader evidence.**  Re-run the four research scripts quarterly on refreshed candles; the wide
   signal set reaches useful power in about 2 years, the live book in about 9.5.
3. **A different trade structure.**  An arbitrary IPO-day loses 0.8% under 12% stops and 10% trails with a 26%
   win rate; the payoff relies on rare large winners.  Test pull-back-to-listing-high entries, cohort-relative
   strength ranking, and position-level volatility sizing on the broad event set before touching live rules.
4. **Intraday history** to model the 60-minute hold properly (currently untested).
5. **Cost control** at Rs 1 lakh: fixed charges are ~0.3-0.5% of a Rs 14k position.  Use limit orders, a
   zero-brokerage delivery plan, and avoid sub-Rs 14k fills that round badly (the order sheet reports them).
6. **Candidate regime filter:** IPO supply in the prior 30 days (see section 6), to be paper-tested only.

## 8. Reproduce

```
python run_honest_backtest.py --verify-legacy --cache data.pkl     # must say EXACT MATCH
python run_honest_backtest.py --bridge --portfolio --cache data.pkl
python research_events.py --cache data.pkl --out events.csv
python research_exits.py --cache data.pkl [--anyday --step 3 --stops]
python research_walkforward.py --events events.csv
python research_trigger.py --cache data.pkl --events events.csv
python order_sheet.py --capital 100000 --peak-capital 100000
python paper_scorecard.py
python -m pytest test_exit_rules.py test_honest_backtest.py test_locks_and_orders.py test_commit_policy.py test_regression_fixes.py test_strategy_evidence_unit.py test_volume_exhaustion.py
```

## 9. Go-live protocol

1. Do not deploy real capital on the current evidence.  Run the system as paper (the defaults now make
   shelf and hourly signals paper-only; set the remaining live positions to minimum size or paper).
2. Run one dry scan of the refactored exit path against the real database and compare with the previous
   day's alerts before relying on it.
3. Decide in advance with `paper_scorecard.py`: STOP at >= 30 trades if the 80% upper bound is below zero;
   SCALE_ELIGIBLE only at >= 100 trades with the 90% lower bound above zero and a positive mean without the
   best trade.  Until then trade minimum size, always with resting stops from the order sheet.
4. Keep the kill-switch on (`--peak-capital`).  Review the strategy if the drawdown reaches 20%.
5. Treat new data (section 7, item 1) as the next research project, evaluated walk-forward on the broad
   signal set, never on the live trade list.

---

## 10. Addendum: per-engine full history, the live book, and learnings (2026-10-12, later)

Full detail in `LEARNINGS.md`; raw outputs in `reports/2026-10-12/` (`README.md` there is the index).

**Per-engine, current rules (60/30 days), full history 2022-05 to 2026-10, net of 1% costs**

| Engine | Trades | Avg net | PF (net) | Win | Rs 1 lakh account |
|---|---|---|---|---|---|
| LISTING_BREAKOUT | 36 | +2.35% | 2.09 | 47% | +10.1%, CAGR +2.3%, max DD -11% |
| SHELF_BREAKOUT | 43 | -2.36% | 0.56 | 28% | -14.9%, CAGR -4.1%, max DD -17.5% |
| Both | 79 | -0.21% | 0.95 | 37% | -3.0%, max DD -21% |

Listing by year: 2022 +2.8% (N=4), 2023 +10.0% (N=9), 2024 -0.1% (N=5), 2025 -2.0% (N=13), 2026 +1.9% (N=5).
One listing trade (+67.5%) is 56% of the engine's profit; without its top 3 the engine averages -0.84% (PF 0.65).
Under the old 730-day lifecycle listing breakouts average -0.06% (N=147, DD -38%), confirming the 60-day cut.

**The live book (read-only inspection):** 9 native positions, all open.  Six are on IPOs older than the current
60-day window (opened under the retired 730-day rule); only AUGMONT, MANIKA and STEAMHOUSE follow today's rules.
Three entries have weekend timestamps (offline/forced runs).  Test documents `COMMIT_CLEAN_TEST` (1 position,
2 signals) were written to production by an unpatched existing test; the test is fixed, the documents still
need deleting.

**New in the repo since sections 1-9:** `research_engines.py`, `research_ablation.py`, `LEARNINGS.md`,
`reports/2026-10-12/` (all outputs and data tables).

---

## 11. Addendum 2026-10-13: corrections to this report

Full detail in `LEARNINGS.md` sections 7-11; raw outputs and index in `reports/2026-10-13/`.

**What changes**

| Statement in this report | After the review |
|---|---|
| Section 1: "No exit, stop or entry-filter change survives" and the trigger "roughly cancels the drag, edge about zero after costs" | Measured with next-open fills after end-of-day gating, which rejects strong breakout days (MVELECTRO by 0.15 points, AUGMONT, SHUKRAPHAR). Must be re-run under an intraday-crossing entry before it is relied on |
| Section 1/3: live-faithful result 79 trades, -0.21% net, account about -3% | Reproduced exactly (control run), but it is the pessimistic corner. Listing breakouts with intraday-crossing fills: N=177, +2.45% net at a +1.5% fill (PF 1.59), +0.57% at +1% worse, -0.93% at +2% worse; native fills average +3.2% above the listing high, so realistic net is about 0% (range about -1% to +1%). Confidence intervals include zero |
| Section 2: audit engine has no known bias against the strategy | It drops trades still open at the end of the data (`core/honest_backtest.py:667`; KSHINTL +183% was dropped), excludes IPOs with fewer than 10 candles (MANIKA, STEAMHOUSE, WEWORK, LGEINDIA), and judges extension on the close |
| Section 6: gate and extension-cap studies show no value | Same entry-model caveat; re-run needed. The 1.8x volume gate again shows no value under the new entry model (variant without it is not worse) |
| Section 9: "do not deploy real capital on the current evidence" | Still right, for a different reason: the evidence is inconclusive, not negative. The decision rests on the pre-registered scorecard and on execution quality |

**What does not change:** every code fix and test in section 5; the legacy-vs-live attribution table in section 2; the
60-day lifecycle cut; SHELF_BREAKOUT and hourly signals staying paper-only (shelf is -2.36% net in the audit and was not
re-tested); the exit engine in `core/exit_rules.py`; the risk controls (atomic slot commit, drawdown kill-switch, resting stops).

**New in the repo:** `research_intraday_proxy.py`, `research_live_parity.py`, `reports/2026-10-13/`.

**Open items:** fix the open-trade drop and add an intraday-crossing entry mode to the engine; refresh the candle cache;
append-only trade ledger and parity monitor; delete the `COMMIT_CLEAN_TEST` leftovers; verify real costs from a contract note.


---

## 12. Addendum 2026-10-14: corrections to this report (full detail in `LEARNINGS.md` section 12)

| Statement in this report | After the 2026-10-14 work |
|---|---|
| Section 5 / 9: the order sheet produces "resting stops" (GTT stop-limit at the trailing level) and live capital should use them | The live stop scan is close-based (6:30 PM, sell next open). An intraday resting stop cost about 2pp per trade against it (paired CI +0.8..+3.5 in favour of the close-based style, 5 of 5 years). `order_sheet.py` now defaults to a close-based alert with an optional disaster GTT 8% below; `--stop-mode resting` keeps the old sheet |
| Sections 2-3: the "live-faithful" engine (`LIVE_FAITHFUL`) reproduces live behaviour | It used `exit_fill="gap_aware"` (intraday stop). The live-faithful exit mode is `eod_next_open`. The code constant is unchanged so the legacy bridge and parity check stay exact; research now selects the exit mode explicitly |
| Sections 6-7: stop/entry-filter studies found nothing | Re-run on the intraday-crossing, causal population with the live stop style (`reports/2026-10-14/`): still nothing adoptable among filters and exits; the one robust lever is execution (pre-placed entry, +1.3 to +1.9pp per trade paired) |
| Risk guard: "halt new entries at a 20% drawdown" | A halt that only blocks entries cannot recover; in the replay it locked the account out for all of 2026. Pre-register a paper-book resume rule before relying on it |

New in the repo: `research_alpha_lab.py`, `research_risk_profile.py`, `test_alpha_lab.py`, `reports/2026-10-14/`; engine additions
(`simulate_symbol_cross`, `preview_resting_entry`, `include_open`, `cat_stop_pct`) and portfolio options are default-off and leave legacy parity EXACT.
