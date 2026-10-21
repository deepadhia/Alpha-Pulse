# AlphaPulse: what we learned (2026-10-12 audit, reviewed 2026-10-13)

> Resuming? `SESSION_HANDOFF.md` is the entry point; sections 12-16 below are the latest work (2026-10-14/15) and override anything earlier they contradict.

Companion to `HARDENING_REPORT.md` (evidence and code changes).  This file is the short, durable version:
what is true about the strategy, about the process, and what to do next.  Raw outputs are in
`reports/2026-10-12/` (the audit) and `reports/2026-10-13/` (the review of the audit).

> **UPDATE 2026-10-13 - read sections 7-11 first.**  A review of the audit found that the audit engine was
> biased AGAINST the strategy in three measurable ways (end-of-day gating, dropped open trades, candle gaps;
> section 7).  Sections 1-6 below are kept as written for the record; where they conflict with sections 7-11,
> sections 7-11 win.  Corrected, the listing engine is **weakly positive to break-even per trade, not proven,
> and dominated by the fill price** (section 8).  It is neither the "PF 2.27 strong system" of the legacy
> backtest nor the "no edge, -3% account" of the first audit.

---

## 1. About the strategy

1. **The advertised result was not what the live scanners do.**  The legacy backtest (PF 2.27, 196 trades,
   +4.48%/trade) used different rules from production.  Reproduced exactly, then converted step by step to
   live rules, it becomes **79 trades, PF 1.24, +0.79% gross (-0.21% net)**.
2. **Engine 1 (listing breakout) is a candidate edge, not a proven one.**  Current rules, full history:
   36 trades, +2.35% net, PF 2.09, account +10% over 4.4 years (max DD -11%).  But one trade (+67.5%) is 56%
   of the profit, the other 35 trades without the top 3 average -0.84%, and 2025 lost money (win rate 23%).
3. **Engine 2 (shelf breakout) has no evidence of edge under live rules.**  43 trades, -2.36% net, PF 0.56,
   negative in 4 of 5 years.  Its backtest PF 2.17 came from a detector off-by-one (it ignored yesterday's bar)
   and an OR volume gate that live does not have.
4. **The trigger carries a small real signal.**  Breakout days beat an arbitrary liquid IPO-day by +1.2pp per
   trade (CI -0.17 to +2.66, 4 of 5 years).  But an arbitrary IPO-day under these exits loses -1.04% net
   (win rate 26%), so the trigger roughly cancels a structural drag.
5. **Tuning will not fix it.**  35 exit/stop variants on 5,323 paired signals, 8 entry filters walk-forward and a
   regularised model: nothing robust.  The volatility filter looked like +3.3pp in-sample and was +0.01pp
   out-of-sample.  The existing parameters sit on a flat plateau.
6. **Most "quality gates" show no measurable value** (volume spike 1.8x, body gate, post-confirm, extension
   cap).  Only the liquidity floor clearly helps (+4.6pp, CI +2.1 to +7.1).
7. **The old 730-day lifecycle was worse** (147 listing trades, -0.06% net, DD -38%).  The 60-day cut in
   v3.5.4 was right.
8. **The edge is regime dependent.**  2023 supplies most of the profit in every version of the test;
   2024 and 2026 are negative for the combined book.
9. **Live trading cannot validate this quickly.**  ~19 trades a year, SD 12.5% per trade: confirming a +2%
   edge needs ~177 trades (~9.5 years).  The broad signal set needs ~2 years.

## 2. About the live book (read-only inspection, 2026-10-12)

* 9 real native positions, all open, none closed; the oldest is 14 days.
* **6 of 9 are on IPOs older than the current 60-day window** (WEWORK 355d, ADVANCE 360d, LGEINDIA 355d,
  AEROPLANE 185d, SEIL 168d, KNACK 89d), opened under the retired 730-day rule.  Only AUGMONT (+29.8%),
  MANIKA (+20.7%) and STEAMHOUSE (-5.9%) follow today's rules: N=3.
* Three entries carry weekend timestamps (Oct 3-4), so they came from an offline or forced run, not a
  market-hours trigger.  Stored `signal_time` strings do not agree with the stored datetimes; treat entry
  times in the DB as unreliable.
* Early unrealised results (+5.6% average) are compatible with both a good system and a lucky fortnight.

## 3. About the process (the real lessons)

1. **A backtest that "binds to production constants" is not the same as one that runs production logic.**
   Constants matched; the logic did not (bars vs calendar days, intraday highs vs closes, trail definition,
   gate stack, fill model).  Fix: one shared definition (`core/exit_rules.py`) called by production, backtest and
   tests, plus an exact-parity check (`run_honest_backtest.py --verify-legacy`).
2. **Tests that mirror the logic inline cannot catch regressions.**  Several existing tests copied the exit
   rules instead of calling them.  An oracle test now compares production with the original logic on 40,000
   random states.
3. **Tests wrote to the production database.**  An existing test called `commit_trade_to_db` with the real DB
   unpatched and left a `COMMIT_CLEAN_TEST` position and signals in Atlas (earlier on 10-10, and again during
   this audit).  Fixed; a full test run now leaves positions, signals and locks unchanged.  The three leftover
   test documents still need deleting (needs the owner's go-ahead).
4. **Check the sanity of your own tools before reporting.**  A drawdown of -53% was a bug in the equity curve
   (new positions valued at zero on their entry day).  An event study's p-values were misleading when only 1-3
   stocks changed between variants; effects must be spread across many symbols to count.
5. **Pre-register variants, compare paired, cluster by symbol, validate walk-forward.**  Without this, 35
   exit variants and 23 features would have produced several "findings" by chance.
6. **Do not tune on the live-faithful book (79 trades).**  A hint there (tighter trail, PF 1.63) disappeared on
   5,323 paired signals.
7. **Spec drift is silent.**  The audit dossier, README and code disagreed on 20 points (see the report).
   Treat the code as the specification and generate documents from it.

## 4. Known limits of this analysis

* End-of-day approximation: the live 60-minute hold, 2.5% pullback ejection and 14:30 cut-off are not modelled
  (no intraday history).  They may help or hurt; unknown.
* Candle history is 2022-05-27 to 2026-10-09; the 2021 IPO boom and the newest listings (<10 candles) are not in
  the cache.
* Friction is assumed (about 1% round trip at Rs 14k positions) and must be checked against a contract note.
* Small samples: 36 and 43 trades per engine.  Confidence intervals are wide throughout.
* Market-cap and the anchor-lock-in dates are not available historically, so those gates/filters are untested.

## 5. What to do next (ordered)

1. **Delete the three `COMMIT_CLEAN_TEST` documents** (1 position, 2 signals) from Atlas.
2. **Dry-run the refactored exit path against the real DB** and compare with the previous day's alerts.
3. **Collect closed native trades under the current rules** and judge them only with `paper_scorecard.py`
   (STOP at >= 30 trades if the 80% upper bound is below 0; SCALE_ELIGIBLE only at >= 100 trades).
4. **Build a parity monitor:** replay every new live signal and rejection through the honest engine and flag
   differences.  This reveals what the intraday filter actually does.
5. **Add new information and re-run the walk-forward study:** QIB / NII / retail subscription multiples, listing
   gain vs issue price, anchor quality and unlock dates, sector, Nifty/Smallcap regime, intraday bars.
6. **Test a different trade structure on the broad event set:** pull-back entries, cohort-relative strength,
   volatility-scaled sizing.
7. **Keep shelf and hourly signals paper-only** until they have live-faithful out-of-sample evidence
   (`LISTING_SHELF_LIVE_CAPITAL_ENABLED`, `HOURLY_LIVE_CAPITAL_ENABLED`).
8. **Re-run the research scripts quarterly** on refreshed candles (`research_engines.py --refresh` first).

## 6. How to reproduce

```
python run_honest_backtest.py --verify-legacy --cache data.pkl   # must report EXACT MATCH
python run_honest_backtest.py --bridge --portfolio --cache data.pkl
python research_engines.py --cache data.pkl --refresh
python research_events.py --cache data.pkl --out events.csv
python research_exits.py --cache data.pkl [--anyday --step 3 --stops]
python research_walkforward.py --events events.csv
python research_trigger.py --cache data.pkl --events events.csv
python paper_scorecard.py
python order_sheet.py --capital 100000 --peak-capital 100000
python -m pytest test_exit_rules.py test_honest_backtest.py test_locks_and_orders.py test_commit_policy.py test_regression_fixes.py test_strategy_evidence_unit.py test_volume_exhaustion.py
# added 2026-10-13
python research_intraday_proxy.py --cache data.pkl > reports/2026-10-13/00_intraday_proxy_and_dropped_trades.txt
python research_live_parity.py --cache data.pkl --days 30 --paths MVELECTRO AUGMONT > reports/2026-10-13/01_live_book_parity.txt
```

---

## 7. Review of the audit (2026-10-13): what was wrong with the audit itself

Trigger: the live book showed promising trades (AUGMONT +23%, ADVANCE +23%, MANIKA +14%, MVELECTRO +21%) that the
audit engine had never traded.  Only 5 of the 28 live symbols appear in its output.  Tracing why found:

1. **End-of-day gating vs an intraday trigger (biased against the strategy).**  The live scanner decides on the
   CURRENT price when it fires (`entry_price = max(current_price, listing_day_high)`, extension and post-confirm gates
   on that price, `listing_day_breakout_scanner.py` ~1500-1541) and buys during the signal day.  The audit engine
   (`core/honest_backtest.py:318` and `:446`) decides on the day's CLOSE and buys the NEXT open.  A breakout day that
   closes more than 3.5% over the pivot is rejected as "extended", although the live scanner would have bought it
   near the pivot.  Days that remain eligible are the ones that faded, so the filter selects against follow-through.
   * MVELECTRO, 2026-08-27: 15.6x volume, high 672, close 646.8 = +3.65% over the 624 pivot against a 3.5% cap.
     Rejected by 0.15 points.  Live bought about 641; the stock reached 777.
   * AUGMONT, 2026-09-29: closed at the day's high, +4.2% over the pivot, 2.4x volume.  Rejected on the 3.5% cap.
   * SHUKRAPHAR, 2026-09-03: opened 47.37 just above the shelf pivot 47.24 on 6.3x volume and closed 16% over it.  The
     evening look sees "extended"; the intraday scanner sees "just crossed".
2. **Trades still open at the end of the data are silently dropped** (`core/honest_backtest.py:667`,
   `if trade is None: ... break`).  One trade was dropped: KSHINTL, entry 360 on 2026-02-09, last close 1019.8
   (+183%), larger than any trade counted (the best counted was +67.5%).  Candles checked: real, smooth data.  On the
   audit's own numbers, adding it as a mark-to-market trade moves listing-only gross from +3.35% (N=36) to about
   +8.2% (N=37); it is unrealised, so indicative only.  Right-censoring always removes the current runners, which
   are the trades a trend-following system lives on.
3. **Candle cache gaps.**  `load_dataset()` skips symbols with fewer than 10 candles.  MANIKA, STEAMHOUSE, WEWORK and
   LGEINDIA have listing records but no candles; MANBRO has neither.  The newest IPOs are the live book.
4. **Backfilled positions are not evidence, and their stored dates are unreliable.**  Of 29 positions, 19 are
   `_backfilled` (research replays) and 10 are native (one of them the `COMMIT_CLEAN_TEST` leftover).
   * MILKYMIST (+69%): stored entry 2026-08-12, but the stock listed on 2026-08-18 (age -6).  The stored entry price
     184.89 is its day-2 close, after it had already gone 165 -> 211.  Current rules would never buy that.
   * ORIRAIL: stored entry one day before listing.  SHUKRAPHAR (+51%): stored entry 2026-08-19 at 44.18, which is the
     08-26 close, a day that closed at the low (close location 0.04) and fails the body gate.
   * LOTUSDEV (+54%), PNGSREVA (+25%) entries are 143-336 days after listing (old 730-day rule).
   These "winners" must not be quoted as live proof of the algorithm.

What the review did NOT find: the exit rules, costs model and legacy-parity machinery are sound (control run
reproduces N=36 / N=79 exactly).  The research conclusions 3.5 and 3.6 of the audit (quality gates and extension cap
"show no value", "trigger +1.2pp") were measured with next-open fills, i.e. the entry model found biased here.
**They must be re-run under an intraday-crossing entry before anyone relies on them.**

## 8. Corrected estimate of the LISTING_BREAKOUT engine (intraday-crossing fills)

`research_intraday_proxy.py`.  Entry = `max(open, listing_high * 1.015)` on any day whose high reaches it, skipped if
the day opens above `listing_high * 1.035` (live gates +1.5% / +3.5%); EOD volume gate 1.8x, liquidity, stop, R:R,
leader score and the live exit engine unchanged; NO condition on the day's close; costs 1.0% round trip; one position
at a time per symbol; still-open trades marked to market and reported separately.  No parameter tuned.  Control run
reproduces the audit exactly.  Full output: `reports/2026-10-13/00_intraday_proxy_and_dropped_trades.txt`.

| Variant (listing breakouts only)                  | N   | Win   | Net/trade | PF   | Median | 95% CI (net)   |
|---------------------------------------------------|-----|-------|-----------|------|--------|----------------|
| Audit engine as shipped (listing only)            | 36  | 47.2% | +2.35%    | 2.09 | -0.08% | -1.2 .. +7.1   |
| P1 fill at pivot +1.5%                            | 177 | 41.8% | +2.45%    | 1.59 | -1.93% | -0.3 .. +5.7   |
| P2 fill +1% worse                                 | 177 | 36.7% | +0.57%    | 1.12 | -3.00% | -1.9 .. +3.6   |
| P3 fill +2% worse                                 | 177 | 33.9% | -0.93%    | 0.84 | -4.14% | -3.4 .. +2.1   |
| P1, closed trades only (drops 12 open runners)    | 165 | 39%   | +0.91%    | 1.21 |        |                |
| P2, closed trades only                            | 167 | 35%   | -0.11%    | 0.98 |        |                |
| P5 P1 without the volume gate                     | 244 | 41.8% | +2.95%    | 1.77 | -1.73% | +0.5 .. +5.9   |
| P6 P1 with age >= 5 days                          | 169 | 43.2% | +3.01%    | 1.74 | -1.76% | +0.1 .. +6.3   |
| P4 P1 + EOD body gate (**hindsight, not achievable**) | 148 | 46.6% | +4.18% | 2.24 |      | +0.9 .. +7.8   |

Reading it:
* **The fill price is the whole edge.**  Every 1 point of worse entry costs about 1.5-1.9 points of net expectancy.
  The empirical native fill extension is **+3.2% above the listing high** (8 native entries, range +1.7% to +5.6%,
  section 9) versus the +1.5% assumed in P1.  After allowing 0.3% already inside the 1% cost, the realistic net is
  about **0% per trade (range roughly -1% to +1%)**, not +2.45%.  A pre-placed stop-limit buy at pivot +1.5%
  instead of buying after the 60-minute hold at about +3% is therefore worth about one to two points per trade.
* **Payoff profile (P1, closed, net):** win rate 39%, average win +13.4%, average loss -7.2%, payoff 1.86, median
  trade -2.2%, 37.5 trades a year, longest losing streak 7, top-5 winners = 49% of gross profit, 39% of losers are
  -8% or worse (full stops).  Exit mix of the 165 closed trades: 119 trailing stops (+2.8% avg), 35 IPO dead-money
  time stops (-4.9%), 8 other dead-money exits (-2.2%), 3 volume-exhaustion exits (+2.1%).  This is a fat-tailed
  trend profile, not a 50-60% win-rate / small-loss profile.
* **By year (P1, net):** 2022 -2.9% (N=10), 2023 +9.3% (30), 2024 +2.1% (41), 2025 -2.9% (48), 2026 +5.1% (48).  2025 lost
  in every variant: regime dependence is real and the system will have long flat or losing stretches.
* The volume gate adds nothing here (P5 >= P1), consistent with the audit's finding that the 1.8x gate has no value.
* Confidence intervals include zero in P1-P3.  Nothing here is proof of edge.  It does show the audit's "-0.21%, account -3%"
  was a pessimistic corner, and that the true value sits between about -1% and +2.5% per trade depending on execution.

Unmodelled in both engines: the 60-minute hold, the 2.5% pullback ejection, and the true trigger time (need intraday
bars).  EOD volume exceeds volume at trigger time, so the proxy volume gate is somewhat generous.  Costs are assumed.

## 9. The live book, reviewed 2026-10-13 (`research_live_parity.py`, read-only)

* 29 positions: 19 backfilled (11 PAPER_CLOSED, 5 CLOSED, 2 PAPER_ONLY, 1 ACTIVE) and 10 native (6 ACTIVE,
  4 PAPER_ONLY; one is the `COMMIT_CLEAN_TEST` leftover, still in Atlas).
* **Entered in the last 30 days (stored dates >= 2026-09-13): 15 positions, 7 up / 8 down, mean +2.0%, median -2.3%
  before costs.**  The 5 closed ones are all losers (RIR, ARCIL, KUSUMGAR, BLUESTONE, JAINREC; mean -6.2%); the 10 open
  ones average +6.1% unrealised.  Closed-losers / open-winners is how a trend system looks mid-flight (losses are cut
  fast, winners stay open) but it also means realised and unrealised numbers must not be mixed in one headline.
* **Native only (9 positions, all open, all unrealised): mean +6.5%, median +3.5%, 6 up / 3 down.**  Standouts AUGMONT
  +23.3%, ADVANCE +22.9%, MANIKA +13.9%.  Caveats: N=9 over about two weeks; 6 of the 9 (WEWORK, ADVANCE, LGEINDIA,
  AEROPLANE, SEIL, KNACK) are on IPOs older than the 60-day window; four (ADVANCE, LGEINDIA, AEROPLANE on Sat 2026-10-03,
  MANIKA on Sun 2026-10-04) have weekend entry timestamps (offline or forced runs; section 2 above said three).
* **Native fill extension over the stored listing high:** AEROPLANE +5.62%, AUGMONT +3.36%, WEWORK +1.98%, ADVANCE +2.33%,
  LGEINDIA +2.95%, MANIKA +4.94%, KNACK +2.78%, SEIL +1.66% (mean +3.20%, n=8).  STEAMHOUSE shows +26.6%: its stored
  listing high (103.95) is probably a stale or wrong listing record, or it was a shelf entry.  Check.
* Data hygiene still open: stored `entry_date` is unreliable (see section 7.4), five live symbols have no candles in
  the cache, and the test documents have not been deleted.

## 10. What it takes to be an alpha machine you can trust with live capital

The target (50-60% win rate, great winners, small losses, low drawdown, good compounding) is a payoff profile; the
system today produces 39% / +13.4% / -7.2%.  A higher win rate comes from better SELECTION, not from exit tuning (the
audit's 35 exit variants were all flat).  Four constraints shape the plan:

1. **Frequency caps compounding.**  About 38 trades a year.  With a +1.5% net edge: about 8% a year at 14% positions,
   about 5% a year at 1% risk per trade (8% positions).  With a zero edge, zero.  The IPO engine alone cannot compound;
   a larger event universe is needed.
2. **A 1-2 month forward test is about 6 trades.**  It validates plumbing (fills, orders, parity, bugs), NOT the edge.
   The edge must be judged on the broad event set (about 40-175 events a year) plus the pre-registered scorecard.
3. **Execution is the edge** (section 8).  Fix the fill model before tuning anything.
4. **Measurement must match live** (section 7) or every decision inherits the bias.

Layers, in order:

| # | Layer | Work | Status |
|---|-------|------|--------|
| 1 | Measurement | Stop dropping open trades (mark to market, flag as open); evaluate gates at the trigger price; refresh candles incl. the newest IPOs; pull intraday history (Upstox minute candles, check coverage for 2022+ IPOs) to model the 60-minute hold and real trigger time; make that engine the parity monitor that replays every live trigger/rejection | TODO |
| 2 | Execution | Pre-placed stop-limit buy at pivot +1.5% (limit +2%) instead of buying after the hold at about +3%; resting broker stops; append-only trade ledger (trigger price/time, order id, fill price/time, contract-note reconciliation); stop trusting DB entry timestamps | TODO |
| 3 | Risk | Risk-based sizing (about 0.5% of equity risked per trade: size = risk / stop distance); cap total open risk at about 4%; keep the 20% drawdown kill-switch; treat same-month IPOs as one correlated cohort | TODO |
| 4 | Alpha | (a) new information: QIB/NII/retail subscription, listing gain vs issue price, anchor lock-in dates, issue size/OFS, sector, Nifty/smallcap regime; (b) the same breakout framework on a much broader universe (small/mid-cap breakouts) so validation takes months; (c) payoff shaping (partial profits, break-even stops) judged on drawdown and median trade, not mean | TODO |

Gates for live capital:

| Phase | Duration | Proves | Pass if |
|-------|----------|--------|---------|
| Forward test | 1-2 months | the plumbing | fills within 2% of the pivot; every live trigger replays in the parity engine; stops rest at the broker; zero ledger/data errors |
| Small live | months 3-8 | execution cost | 0.25-0.5% risk per trade; broad paper event set running alongside |
| Scale | after that | the edge | `paper_scorecard.py` rules: at >= 30 trades STOP if the 80% upper bound < 0; SCALE_ELIGIBLE only at >= 100 trades with 90% lower bound > 0 and a positive mean without the best trade |

## 11. Updated learnings and next steps

Process lessons added by the review:
1. **Audit the auditor.**  A pessimistic backtest is as wrong as an optimistic one.  The first audit fixed every
   optimistic bias it found and never asked which of its own simplifications leaned the other way.
2. **When live and backtest disagree on specific names, trace each one.**  The disagreement (23 of 28 live symbols
   absent from the engine) pointed straight at the three biases.
3. **Never discard right-censored trades.**  Mark to market, flag, and report them separately.
4. **A gate must be evaluated at the time of the decision.**  Conditioning on the day's close selects against
   follow-through in a breakout system.
5. **Report a range, not a point, when one assumption dominates.**  Here the fill price moves net expectancy from
   about +2.5% to about -1%.
6. **Stored research rows are not live evidence.**  Backfilled rows carried impossible dates and hindsight entries.

Not done, to do next (ordered):
1. Delete the three `COMMIT_CLEAN_TEST` documents (owner go-ahead needed); reconcile STEAMHOUSE's listing record.
2. Fix `simulate_symbol` to emit open trades (flagged) without breaking the `--verify-legacy` exact match.
3. Add an intraday-crossing entry mode to `core/honest_backtest.py` (what `research_intraday_proxy.py` approximates) and
   re-run the exit grid, gate ablation, walk-forward and trigger studies under it.
4. Refresh the candle cache (newest IPOs), then check Upstox intraday history coverage and build the 60-minute-hold model.
5. Build the append-only ledger and parity monitor; start the 1-2 month forward test with the section 10 pass criteria.
6. Start the new-information research (section 10, layer 4a) and the broader-universe test (4b).
7. Verify real costs against a broker contract note; the 1% round-trip figure is an assumption.


---

## 12. 2026-10-14: execution, stop style and what the data does and does not support

Reproduce: `research_alpha_lab.py` and `research_risk_profile.py` (outputs in `reports/2026-10-14/`; candles to 2026-10-13,
850 symbols).  Engine additions are in `core/honest_backtest.py` (`simulate_symbol_cross`, `cross_entry`,
`preview_resting_entry`, `include_open`, `cat_stop_pct`, `breakeven_*`) and `core/portfolio_sim.py`
(`risk_budget_pct`, `dd_half_pct`, `dd_halt_pct`).  Legacy parity is still EXACT (198 trades, 0 mismatches) and the old
research outputs are unchanged.  All figures are net of a flat 1.0% round-trip cost.  Trades still open at the end of the
data are kept and marked to market; "closed-only" figures are given next to them.

### 12.1 Two modelling errors found and fixed

1. **The "live-faithful" exit model assumed an intraday stop that live does not use.**  `stop_loss_update` runs once a day at
   6:30 PM IST (`cloudflare-dispatcher/src/index.js`), compares the CLOSE with the stop and alerts; you sell at the NEXT OPEN.
   `LIVE_FAITHFUL` used `exit_fill="gap_aware"` (a resting stop filled when the low touches it).  The faithful mode is
   `exit_fill="eod_next_open"`.  Every earlier baseline therefore understated the live exit rule.
2. **Same-day volume was used in the entry gates** (volume floor, spike gate, leader-score volume term).  A pre-placed order
   cannot know today's volume.  `causal=True` removes every use of it (the prior bar stands in).  Paired comparisons are safe
   (both sides share the gating); absolute levels are not, so use the causal population for anything about a resting order.

### 12.2 What the data supports (all paired, symbol-cluster bootstrap, 90% CI)

| Finding | Evidence | Status |
|---|---|---|
| **Close-based stops beat intraday resting stops** (the live style) | +2.03pp per trade (CI +0.79..+3.54); ex top-3 trades +0.86 (+0.29..+1.50); better in 5 of 5 years; same under a +1.0% fill (+2.23pp) | Adopted: keep the live style, do not default to GTT-at-the-stop |
| **A pre-placed stop-limit entry beats buying after the alert** | +1.33pp per trade under live stops (CI +1.13..+1.54), +1.86pp under intraday stops (CI +1.32..+2.53); positive every year; shrinks to about +0.8 if price jumps through the limit intraday (not visible in daily bars) | Adopted as tooling: `order_sheet.py` section 3 |
| **12% initial stop cap** | cap 10%: -0.04pp under live stops (no gain), -1.0pp (CI -1.8..-0.3) under intraday stops | Keep 12%; tighter stops do not help |
| **Gap-up breakouts are the strongest trades** | trades whose open was above the walk-forward gap threshold (1-2% over the prior close) average +13.1% (CI +3.0..+24.9, n=31) | Do NOT add a gap filter |

### 12.3 What the data does not support (pre-registered, tested, not adopted)

* **Quality filters.**  IPO supply, cohort breadth, gap, liquidity (walk-forward, thresholds chosen on earlier years): in
  every case the trades the filter would REMOVE had a positive mean (+6.1%, +6.1%, +13.1%, +8.8%) and the kept share was 37-89%.
  The 2026-10-12 "low IPO supply" lead (+3.9pp) did not replicate.  Dropping trades also lowers total profit because the book
  is not slot-limited (about 51 trades a year, three or four open at a time).
* **Exit tweaks** (trail 8%/13%, winner cushion 0.85/0.75, dead-money 10/21 days, trail start +5%, sell a third at +20%,
  breakeven stop): none clears the bar.  Breakeven after +8% passes the letter (+0.04pp) and is immaterial.
* **Loosening exits together** (trail 13% + cushion 0.75 + dead-money 21 days) looks like +4.75pp (CI +0.6..+10.3) under intraday
  stops, but three trades (KAYNES +611pp, CPPLUS +267pp, OMNI +145pp) produce 94% of it; without them +0.27pp (CI -0.39..+1.00).
  Not adopted.
* **Risk-based sizing and a drawdown throttle.**  Both looked good under intraday stops and flip sign under the live stop
  style (halving size at a 10% drawdown: Calmar 0.64 to 0.51 at 1/7 weights; under a +1.0% fill CAGR falls from 10.7% to 1.9%;
  at 1/10 weights it is roughly neutral, Calmar 0.81 to 0.88).  Account-level results are single paths; do not adopt either
  as an alpha claim.
* **Hard drawdown halt.**  A halt that only blocks new entries cannot recover (NAV only recovers through trades): in the replay
  a 20% halt locked the account out for the whole of 2026.  If the kill-switch trips, keep logging the resting orders as paper
  trades and resume when the paper book recovers (rule to be pre-registered; not implemented).

### 12.4 Policy P and its honest numbers

Policy P = listing breakout, age 5-60 days, resting stop-limit buy at listing high +1.5% (fill +0.3%, none above +2.5%), no
same-day volume gate, live exits (close-based stops, 12% cap), live liquidity gates.  227 trades 2022-05 to 2026-10.

| Measure | Policy P | Chase today (+1.7% fills) |
|---|---|---|
| Net per trade, incl. 22 open runners at MTM | +5.29% (CI +2.6..+8.2) | +3.74% |
| Net per trade, closed trades only | +2.66% (CI +0.1..+5.5) | not computed (closed PF 1.36) |
| Profit factor: incl. open / closed-only / closed ex top-3 | 2.45 / 1.67 / 1.14 | 1.88 / 1.36 / 0.93 |
| PF by entry year (incl. open) | 2022 0.35, 2023 4.88, 2024 2.24, 2025 1.12, 2026 4.33 | 2022 0.26, 2025 0.91 |
| Win rate, average win, average loss | 38%, +17.5%, -6.4% (payoff 2.72) | 34%, +18.1%, -6.9% |
| Rs 1 lakh, 1/7 weights, costs on | +124% total, CAGR 20.4%, max DD -32%, PF 2.20 | CAGR 13.4%, max DD -35% |
| Same with 1/10 weights, 10 slots | CAGR 20.6%, max DD -25%, Calmar 0.81 | CAGR 10.8%, max DD -30% |
| Same, closed trades only | 1/7: CAGR 8.6%, DD -33%; 1/10: CAGR 10.3%, DD -26% | not run |

**What a profit factor near 2 requires and what to expect.**  PF = win rate x payoff / (1 - win rate).  At a 38% win rate a PF of
2.0 needs a payoff of 3.26; Policy P has 2.72.  The gap is closed only by the right tail: the closed PF of 1.67 becomes 1.14 without
the three best trades, and PF is 0.35 in 2022 and 1.12 in 2025.  Over the next 100 trades the bootstrap PF has a median of 1.64
and a 30% chance of reaching 2.0; over the next 40 trades there is a 25% chance it is below 1.0.  A PF near 2 is therefore a
property of a good run of runners, not a level this evidence lets anyone promise.

**Loss streaks (accepted by design).**  Longest closed losing streak in the replay: 11.  Over the next 40 trades the median longest
streak is 6, with a 26% chance of 8 or more.  The worst 10 consecutive closed trades summed to -83pp: -8.3% of NAV at 10% weights,
-11.9% at 14%.  Stops stay at the live 12% cap with close-based exits; nothing in this section tightens a stop to avoid a streak.
Smaller per-position weights are the only cost-free way to make a streak cheaper.

**Capital capacity.**  Median 20-day turnover at entry is Rs 101 Cr but the lower quartile is Rs 18.6 Cr and the 10th percentile Rs 5.1 Cr.
At 14% weights a Rs 50 lakh account is above 2% of daily turnover on 6.6% of trades, Rs 1 crore on 14% (above 5% on 4.8%).
Beyond about Rs 50 lakh a liquidity-scaled size cap is needed; the cost model (0.3% slippage) is not valid above 2% participation.

### 12.5 Changes made

* `order_sheet.py`: stops default to the close-based alert (`--stop-mode resting` restores the old sheet); new pre-market
  **resting entry orders** from the same gates as the backtest (`preview_resting_entry`, tested for parity); `--log` appends the
  orders to `logs/resting_orders.jsonl` so fills can be reconciled.
* **Slot caps 5/7 -> 10/10 (2026-10-14, on request):** `core/portfolio_caps.py` is the single source for the scanners' soft/hard cap and
  the order sheet's slot count and 10% weight. This is a risk/diversification change (about 5-8pp less max drawdown at equal or higher
  CAGR in three of four fill scenarios; neutral with today's chase-style fills), not an alpha change. New signals only; rollback is
  `MAX_ACTIVE_POSITIONS=5`, `HARD_ACTIVE_POSITIONS=7`. Pinned by `test_portfolio_caps.py`.
* No exit-rule, stop, entry-gate or capital-policy default (shelf and hourly stay PAPER_ONLY) was changed.

### 12.6 Next, ordered

1. Place the pre-market orders for a month with `--log`, record fill price and time for every fill, and compare with the replay
   (fill within +0.5% of the trigger is the pass mark).  Keep the alert-based path running in parallel.
2. (Done 2026-10-14) 10 slots / 10% weights. Watch the first 20 trades: with up to 10 live names, the total stop risk if all stop out at 12% is about 12% of NAV.
3. Pull Upstox minute bars to model the 60-minute hold and measure how often price jumps through the limit.
4. Pre-register a resume rule for the drawdown halt (paper-book recovery) before relying on it.
5. Subscription multiples, issue price and anchor data are still the untested information source; broaden the universe for power.


---

## 13. 2026-10-14: the right tail (outliers) and what can capture more of it

Reproduce: `python research_tail_capture.py --cache data.pkl --trades reports/2026-10-14/data_base_trades_causal_resting_eod.csv`
(output `reports/2026-10-14/04_tail_capture.txt`).  Population: policy P (section 12.4).  Owner's stated goal: win rate is
secondary, outliers must compensate for the losers.

**Anatomy.**  24 of 227 trades (10.6%) returned 25% or more and produced 77% of gross profit; 7 trades (3.1%) returned 75% or more and
produced 42%.  The best were held 100 to 400 days (OMNI +155%, ARFIN +154% still open, SIGNATURE +139%, JYOTICNC +138%, AZAD +113%).
Trades that peaked at 30% or more kept 57% of their peak.  13 of the 35 trades above +15% are still open at the end of the data.
The median trade is -1.4%; the profile is a lottery on leaders, which is what the owner asked for and what the numbers show.

**Outliers cannot be selected in advance with this data.**  Across 18 entry features the rank AUC for "peak run-up >= 30%" is 0.39 to
0.59 (0.50 = no information).  The only mildly stable signal is a tight prior base (10-day range, AUC about 0.61 inverted in both
2022-24 and 2025-26), far too weak to filter on.  Any quality filter would therefore remove outliers with the losers, which is exactly
what section 12.3 showed (the removed trades were profitable).  Taking every valid breakout is the correct design.

**Ways to capture more of each outlier, pre-registered and tested:**

| Lever | Result | Status |
|---|---|---|
| Re-entry cooldown 30 -> 20 / 10 / 5 bars | 20: -5.96% on 17 extra trades; 10: +7.58% on 43 extra (CI -0.2..+16.5); 5: +3.22% on 69 (CI -1.3..+8.6). Not monotone, every CI includes 0 | Not adopted |
| Pyramid: add 50% after the first close +10% over entry | adds on 46% of trades; +0.64pp per trade (CI -0.35..+1.73); return per capital-day 0.83x policy P | Not adopted: the added capital earns less than the base |
| Tight-base conviction sizing (1.5x / 1.0x / 0.5x), walk-forward | -0.37pp per trade at equal exposure (CI -1.8..+0.9) | Not adopted |
| E11: drop the "peak < 4% at day 14" dead-money clause (`DEAD_MONEY_RUNUP_IPO=0`) | +1.18pp per trade (CI -0.13..+3.11); +0.23 (CI -0.17..+0.90) without the largest mover; 9 of 227 trades change: 2 improve (KAYNES +218pp, BHARTIHEXA +78pp), 7 worsen (average -3.8pp) | Not adopted by the bar; see below |

**E11 is the one lever that matches the stated goal, and it is a bounded bet, not proof.**  The live system cuts a trade at day 14 if its
peak run-up never reached 4%, even when it is up a little; the velocity gate (day 14 and PnL <= 0) already protects capital on its
own.  Dropping the extra clause keeps small-positive sleepers alive.  Only 4% of trades are affected, the downside on each is small
and bounded by the 12% stop and the velocity, volume-exhaustion and 40-day guards, and the change breaks even if 2.5% of the affected
trades become runners (2 of 9 did).  The evidence is two trades, so it fails the pre-registered bar (CI includes 0, shrinks to +0.23
without KAYNES).  It is a reversible environment variable, `DEAD_MONEY_RUNUP_IPO=0` (live exit engine and shadow tracking both read
it); nothing was changed by default.  A narrower clause (peak < 2%) is strictly worse (-0.08pp, CI excludes 0 on the wrong side).

**What actually protects the tail.**  (1) Taking every valid breakout (frequency is the only source of tail draws: about 51 a year);
(2) the pre-placed entry so the leaders are not bought late (section 12); (3) close-based stops so wicks do not shake the leaders out
(section 12.2); (4) not tightening stops or adding filters; (5) smaller weights (10 slots) so the long losing runs between outliers
stay affordable.  More universe (small and mid-cap breakouts, subscription data) is still the untested way to add draws.


---

## 14. 2026-10-14: dead money, setup patterns, the shelf breakout and capital rotation

Reproduce: `python research_dead_money.py --cache data.pkl` (output `reports/2026-10-14/05_dead_money_patterns_shelf_rotation.txt`).
Question from the owner: dead money is the issue, so check which well-known setup (listing-day breakout, shelf breakout) is the
better pattern.  Population: policy P (section 12.4); the shelf breakout is now modelled under the same corrected entry and stop
assumptions (`simulate_symbol_cross(..., setup="shelf")`, geometry from the live shelf detector).

**Size of the problem.**  81 of 227 trades (36%) end as dead money (IPO dead money, velocity gate, volume exhaustion, stagnant guard):
median 14 days held, mean peak run-up 2.2%, mean -4.6% net, -373pp in total against +1,200pp for all trades, 14% of capital-days
(0.9 positions open at a time, about 9% of capital).  The share is 28-43% in every year.  Only 14% of them were still positive at the exit.

**No pattern separates dead money from the outliers that follow it.**  First cross vs re-cross (34% vs 37% dead), days pressing the high,
time spent above the listing high, age since listing, volume dry-up into the break, listing-day close: dead-money rates sit between 29% and
47% in nearly every bucket, runner rates between 7% and 29%, and the buckets that look best have 20 to 45 trades.

**Shelf breakout vs listing-high breakout (causal, resting entry, close-based stops).**

| | Listing (policy P) | Shelf (cap pivot +5%) |
|---|---|---|
| Trades, net per trade | 227, +5.29% (CI +2.6..+8.2) | 210, +3.36% (CI +0.8..+6.3) |
| PF incl. open / closed | 2.45 / 1.67 | 1.78 / 1.60 |
| Dead-money share (capital-days) | 36% (14%) | 44% (18%) |
| Outliers (+25% or more) | 10.6% | 9.0% |
| By entry year | positive 4 of 5 | negative in 2022 and 2025 |
| Rs 1 lakh, 10 slots: CAGR, max DD, Calmar | 20.6%, -25%, 0.81 | 12.3%, -27%, 0.46 |
| Listing + shelf together (listing wins overlaps) | 18.8%, -26%, 0.72 | |

Under a +1.0% fill the shelf falls to +1.72% (CI -0.6..+4.3).  The earlier audit's -2.36% for the shelf came from end-of-day gating, next-open
fills and an intraday-stop assumption; corrected it is a positive pattern, but a weaker one with more dead money, and adding it to the book
displaces listing trades.  Tight shelves (range 8% or less) are the worst (51% dead money); the loosest (12-18%) look best (25% dead, 34%
runners) on only 32 trades, which is a reason to look again with more data, not a rule.  **Decision: the shelf stays PAPER_ONLY.**
The live volume gate (1.8x spike) lowers the listing dead-money share from 36% to 30% but leaves profit per trade unchanged (+5.49% vs +5.29%)
and cuts trades by 27%, so it removes opportunities without adding edge.

**Is capital the constraint?**  The book holds 6.0 positions on average and is at the 10-slot cap on 22.8% of days.  Cash binds more
than slots: with 10 slots 52 of 227 signals were skipped (7 for the cap, 45 for cash or whole-share size), averaging +3.7% and including 6 outliers
(ARSSBL, KISSHT, JSFB, HEXAGON, MVELECTRO, MOLBIO); with the old 7 slots 84 were skipped, averaging +5.7%, including 12 outliers.  That is the
quantified case for 10 slots.

**Rotating capital out of dead money fails.**  Pre-registered rule: when a signal cannot be funded, sell the worst position held 7 days or more
with a peak close run-up under 4% and PnL of +2% or less.  It is worse in every scenario (Rs 1 lakh, resting fills: +126.0% to +92.6% at 7 days
and +112.7% at 10 days; the same direction at Rs 10 lakh and under a +1.0% fill).  The positions it sold went out at -2.5% on average and would
have ended at +12.8%: SIGNATURE (+138%), SHUKRAPHAR (+51%) and EPACKPEB (+30%) all looked dead on day 7 to 10.  The same fact explains why the
dead-money gate costs outliers (KAYNES) and why E11 (section 13) is the logical lever: the outliers incubate inside the positions that look dead.

**Conclusion.**  Dead money is the structural price of a right-tail strategy: 30-45% of trades in every pattern tested, a bounded cost
(-4.6% each, 14% of capital-days), not selectable by pattern and not recoverable by rotation.  The levers that remain are cheap bets rather than
filters: E11 (`DEAD_MONEY_RUNUP_IPO=0`, section 13), 10 slots (done), and more draws from a wider universe.  Nothing in this section changes a
live default.


---

## 15. 2026-10-14: quality over quantity, and what a profit factor above 2 requires

Owner's target: quality over quantity, profit factor above 2, winners are outliers.  The data answer the first part directly.

**Selecting fewer trades lowered the profit factor.**  Every pre-registered quality rule (walk-forward, thresholds chosen on earlier years;
`reports/2026-10-14/02_lab_PRIMARY_...`) produced a kept subset with a lower PF than taking every trade (all trades PF 2.28 out of sample):

| Rule | Kept PF | Rejected trades' PF | Kept share |
|---|---|---|---|
| Low IPO supply | 1.93 | 2.65 | 49% |
| Cohort breadth | 1.80 | 2.55 | 37% |
| No gap-up | 1.85 | 4.00 | 82% |
| Liquidity floor | 2.14 | 3.52 | 89% |

The rejected trades were the better ones.  With a profit that is 77% outliers and no entry feature that predicts them (section 13: AUC 0.39-0.59),
each trade removed is as likely to be an outlier as any other, so thinning at random cuts the outliers in proportion and PF does not rise.
In this system quantity is how the outliers get drawn; quality comes from execution (section 12: pre-placed entry, close-based stops) and
from not missing the outliers (10 slots, section 14), not from selection.

**What the profit factor of policy P is, with its uncertainty** (`core.scorecard.pf_report`, reproduces on the replay trades):

| | PF | 90% bootstrap interval | P(PF >= 2) |
|---|---|---|---|
| Incl. 22 open runners at mark to market (227 trades) | 2.45 | 1.67 to 3.43 (symbol-cluster) | 81% |
| Closed trades only (205) | 1.67 | 1.02 to 2.50 | 24% |
| Closed, without the best 1 / 3 / 5 winners | 1.48 / 1.14 / 0.88 | | |

**PF is a right-tail statistic here.**  The 14 closed outliers (+25% or more) earned +1,035pp; the other 191 closed trades netted -489pp (wins
+328pp, losses -817pp).  A closed PF of 2.0 needs about 1,306pp of outlier profit, 26% more than the replay had; with 0.75x of it PF is 1.35 and
with 0.5x it is 1.03.  A PF above 2 is therefore met when the open runners are counted and when outliers arrive at the historical rate; on closed
trades alone it is a roughly one-in-four outcome.  Nothing here can guarantee it.

**What was added: a monitor, not a gate.**  `paper_scorecard.py` now prints, for the native forward cohort, PF closed-only, PF including open
positions at mark to market with a 90% interval and P(PF >= target), PF without the top three, and the count and profit share of closed outliers
(`--pf-target`, default 2.0).  It never changes the STOP / CONTINUE / SCALE decision.  Judge the target on this output once there are 30+ closed
trades and 5+ closed outliers; before that the interval is too wide to say anything.  The only honest ways to raise the PF are more outlier draws
(a wider universe, more trades per year), fewer lost outliers (E11, section 13) and better fills; none of them is a filter.


---

## 16. 2026-10-15: running policy P on Rs 50,000 to Rs 1,00,000 without a blowup

Reproduce: `python research_small_account.py --cache data.pkl --sims 250` (output `reports/2026-10-14/06_small_account.txt`).  Account model:
`core.portfolio_sim` with the full Indian delivery cost model, whole shares, open positions marked to market.  History 2022-06 to 2026-10.

**Blowup (NAV at or below 50% of the start): 0 of 41 real 12-month windows and 0 of 250 resampled histories in every cell** (12 and 24 months,
Rs 50k and Rs 1 lakh, three fill scenarios, four slot configurations).  The reason is structural, not luck: a position is 10% of the account, the
stop caps the loss at 12% and the worst replayed closed trade was -17.4%, so one trade costs about 1.2% of the account (worst replayed 1.7%) and
all ten positions at the worst case at once would cost about 17%.  Ruin needs a long run of full stop-outs: the longest replayed losing streak was
11-15 trades at an average of -6.5%, about -7% to -10% of the account.

**The first year can still lose money.**  Real 12-month windows (41 overlapping starts, 10 slots x 10%):

| | Median return | 5th percentile | P(loss) | Median max DD | P(DD >= 20%) | P(DD >= 30%) |
|---|---|---|---|---|---|---|
| Rs 1 lakh | +5.7% to +7.7% | -11% to -15% | 41-44% | -11% to -12% | 10-15% | 0% |
| Rs 50k | +3.7% to +5.6% | -14% to -18% | 46-49% | -12% to -13% | 15-22% | 0% |

Resampled from random 3-month blocks (250 histories) the first-year picture is better because the real sequence includes the flat 2024-25 stretch
and the bad 2022: 12 months at Rs 1 lakh median +11% to +18%, P(loss) 18-23%; at Rs 50k median +8% to +14.5%, P(loss) 24-32%; 24 months at
Rs 1 lakh median +29% (P(loss) 12%, 5th percentile -13%), at Rs 50k median +22% (P(loss) 19%, 5th percentile -19%).  Treat 20-45% as the honest range for
a losing first year; plan for 6-12 flat or negative months (PF was 0.35 in 2022 and 1.12 in 2025).

**Rs 50k is a harder version of the same system, not a different one.**  Fixed charges cost 1.78% of a Rs 5,000 position round trip against 1.15%
at Rs 10,000 and 0.65% at Rs 50,000; that, plus a lower trade count per slot, is the whole gap between the two sizes.  Whole shares are not a
problem (median entry Rs 326 per share, 95th percentile Rs 1,191, none above Rs 5,000).  Starting at Rs 1 lakh, or topping Rs 50k up to Rs 75k, helps.

**Slot configuration for a small account (resampled, moderate fill, 12 months, Rs 50k / Rs 1 lakh):**

| Config | Signals taken | Median return | P(DD >= 20%) | P(DD >= 30%) |
|---|---|---|---|---|
| 10 slots x 10% | 48 of 52 | +8.0% / +11.0% | 5% / 3% | 0% / 0% |
| 7 slots x 14.3% | 41 of 52 | +9.8% / +12.9% | 20% / 16% | 1% / 1% |
| 5 slots x 20% | 34 of 52 | +13.0% / +15.3% | 34% / 30% | 5% / 4% |
| 10 slots x 6% | | +2.9% / +5.9% | 0% / 0% | 0% / 0% |

Fewer, larger slots earn more in the median but miss a third of the signals (outliers included) and put a 30% drawdown on the table in 5-15% of
histories; 6% positions are so small that charges eat the return.  **10 x 10% is the right balance for a limited account.**

**Loss-handling protocol this evidence supports** (the strategy cannot blow up; a person can):
1. Every position is at most 10% of the account; no leverage, no F&O, no averaging down.
2. Stops are decided on the CLOSE and executed at the NEXT OPEN (`order_sheet.py`, section 12).  Missing a stop is the only realistic path to a
   large loss; the optional disaster GTT 8% below the alert level is insurance, not the plan.
3. The risk guard halts new entries at a 20% drawdown from the peak (`order_sheet.py --peak-capital`); at Rs 1 lakh that happened in 10-15% of real
   12-month windows.  A halt cannot recover by itself (section 12.3): keep logging the orders as paper trades and resume when they recover.
4. Skip stocks that locked at a circuit in the last 15 days (already a live gate): a lower-circuit lock cannot be sold, but at 10% weight even a
   -30% lock costs 3% of the account.
5. Do not judge the system on fewer than 30 closed trades or fewer than 5 closed outliers (`paper_scorecard.py` PF monitor).

Limits: 4.4 years of one market regime; no 2008 or 2020 style crash; correlated IPO cohorts; stops assumed to be honoured.  The structural
bound (position size x stop) is the argument that holds outside the sample; the simulated percentages are descriptive.


---

## 17. 2026-10-17: explosive moves, quality over quantity, minute bars (reports/2026-10-17/)

Owner goal restated three times: catch the explosive IPO moves (+20% to +50% in days), be selective, cut losers early, keep the profit factor high.  What the data says:

**17.1 Base rate and regimes.**  Among liquid IPO-days aged 5-60, 13.8% see a +20% high within 10 sessions and 2.6% a +40% high; by month this ranged 2% to 40%.  Sep-Oct 2026 was a hot window, so AUGMONT, HTEL, DEEPA and STEAMHOUSE are one regime cluster, not four independent confirmations.

**17.2 Capture.**  Policy P (causal resting entry) holds a position in 35% of the 396 +20% episodes and 35% of the 97 +40% episodes.  43% of episodes start more than 10% below the listing high, so a listing-high breakout cannot reach them; the shelf setup adds 8pp of capture (PF 1.77, negative in 2025) and stays PAPER_ONLY.  With the end-of-day versions of the live gates (the best case for them) capture falls to 25%.

**17.3 The live gates read partial-day values.**  With minute bars: the 150k volume floor read at the trigger minute rejects 40% of policy-P trades, including 46% of the GOOD ones (rejected mean net +7.7%, 11 outliers of +25% or more); the 60-minute hold test rejects 54% (50% of GOOD); the close-location gate at cross+60 separates a little (BAD 44% vs GOOD 26% rejected) but still removes winners.  No gate meets the bar.  Confirm-then-buy (enter 60 minutes after the cross) costs -0.08pp with no condition, -4.65pp with the hold test and -5.49pp with hold test plus the +3.5% chase cap (CIs exclude 0; 72% of GOOD trades missed).  HTEL was lost to scan latency plus the chase cap (crossed at 12:24, +3.5% by 12:31, +6% at the next scan), NOT to the volume floor (that 09:52 rejection came while price was below the listing high).  Resting orders avoid both.  Measured resting-order fills average +0.06% over the trigger (replay assumed +0.30%): an idealised floor on slippage, jump-throughs are invisible because the population is already capped.

**17.4 Entry-time selection and early cuts: negative results.**  (a) 18 daily features, walk-forward: best lift over random rejection 6pp.  (b) Volume dry-up plus tight consolidation near the listing high (the owner hypothesis): the dry-up group nets +2.8% vs +9.4% for the rest and the combined rule has kept-minus-rejected CI -9.0..-2.0, i.e. the direction is inverted.  (c) First-60-minute features: none passes.  (d) Early loser cut, R(N days, -x%) next-open exit, 9 variants: 1.9% of GOOD trades ever close below -6% in 5 sessions vs 57% of BAD, so losers are separable, but the best gain is +0.12pp (CI -0.16..+0.40) and -4% variants lose -0.8pp: losses are already small (BAD mean -8.2%); nothing to harvest.

**17.5 The one selection signal.**  A gradient-boosting model (depth 2, 150 trees) on 263 resting-eligible breakout events, trained walk-forward by year, predicts +20% within 10 sessions with out-of-sample AUC 0.70 (permutation null max 0.626, p = 0.000; stable across 15 seed/depth fits at 0.69-0.73); the top 30% by score hits 21.7% vs a 13.6% base (1.6x) in all three test years.  Drivers: opening gap (+), listing-day range (+), prior 5-day return (-), turnover.  A logistic model is at chance (AUC 0.43).  BUT the link to realised trade net is unproven: 63 matched trades, top 30% +9.85% vs rest +3.29% (CI -5.5..+19.5), 2025 reversed, excluding the three best +0.2% vs -3.5%.  Verdict: NOT adopted as a gate.  It is LOGGED ONLY (core/shadow_score.py, order_sheet.py --log field "shadow", shadow_score.py after the session) so live out-of-sample days can settle it; decide with 40-60 crossed rows after the model train_end 2026-09-28.

**17.6 Honest PF statement.**  Policy P on the refreshed data: PF 2.43 including open runners, 1.71 closed only, 1.91 excluding the top 3 (all trades); net +5.22% per trade; by year 2022 0.35, 2023 4.88, 2024 2.24, 2025 1.09, 2026 4.30.  Nothing tested this session lifts that without a look-ahead; "very good PF" is not something the evidence can promise, only measure going forward.

**17.7 Data notes.**  Upstox V3 1-minute candles (data_minute/, local, gitignored) exist for 833 of 851 listings since 2022 (18 stale instrument keys); 2022-23 sessions are often partial (full-session share 67% and 75%).  114 symbols carry placeholder listing dates (2026-08-17/18; see EXPERIMENT_CHANGELOG): they account for only 5 of 228 policy-P trades and 6 of 263 events, and removing them leaves PF at 2.43.  The venv and the system python have different pandas versions: build events with the venv, fit models with the system python (scikit-learn lives there).

Scripts: research_gate_replay.py, research_capture_gaps.py, research_recall_precision.py, research_coil.py, research_early_cut.py, research_event_model.py (+ _robust), fetch_minute_bars.py, research_minute_probe.py, research_minute_coverage.py, research_minute_q1_q4.py, train_shadow_model.py, shadow_score.py; outputs in reports/2026-10-17/.

**17.8 Anatomy of the explosive post-listing movers (research_winner_anatomy.py, reports/2026-10-17/winner_anatomy_output.txt).**  462 eligible IPOs listed 2022-06 to 2026-08 (price >= 25, median turnover of sessions 1-10 >= Rs 0.75 Cr, real listing date).  131 (28.4%) rose 30% or more above the listing-day close within 60 days (55 = 11.9% rose 50% or more; median 27 days to +30%), so the explosive mover is common, not rare, and moves in waves (2025 20%, 2026 35%).  Winners versus ALL others: at the listing-day close nothing separates them (AUC 0.46-0.56 for range, close location, wick, open-to-close, turnover, price, supply, recent-IPO returns); at session 5, 10 and 20 after listing the winners look like everyone else (median pullback from the listing high -12.6% vs -12.1%, volume dry-up 0.60 vs 0.57, 5-session range 12.9% vs 11.3% at session 10).  The owner pattern (volume dry-up + tight range + close within -10..+5% of the listing high + listing-day low held, DRYCOIL) matches only 15-21 IPOs per checkpoint, 2-4% of the winners, with hit rates 13-22% against a 20% base: no edge.  The ONE repeatable trait is strength confirmation: at session 20, IPOs above their listing high (or up since listing) reach +30% within the next 30 sessions 27% of the time vs 15.7% for the rest (AUC 0.59-0.60, same sign in each of 2023-2026, year hit rates 16-43%); at session 10 the same split is 23.5% vs 18.5%.  Even that bucket is far from "always": 73% of its names do not make +30%, and it captures only 45% of the winners.  No pattern that always works exists in this data; the engine that the data supports is "be in many strength-confirmed breakouts with resting orders and let the right tail pay" (what policy P already is).  Not testable here: issue price, subscription, GMP, anchor and fundamentals are not in the database.

**17.9 Issue size, subscription and issue price (fetch_ipo_meta.py, research_ipo_meta_signal.py, reports/2026-10-17/ipo_meta_signal_output.txt).**  The database has none of these, so they were scraped from Trendlyne public monthly IPO pages (52 requests, 2 s apart, robots.txt allows /ipo/ with a 1 s delay, raw HTML cached in data_ipo_meta/, gitignored).  Not used: Chittorgarh (its free tier returns only 5 records for a past year, the full history is behind its paid product, nothing was bypassed) and the Investorgain subscription page (shows "No data available" for 2024).  354 mainboard IPO rows were matched to 298 of the 462 eligible database IPOs (65%, by listing date plus name similarity or listing-close price; the unmatched are mostly SME or renamed listings absent from the mainboard screener).  Pre-registered features: log issue size, log issue price, log total / QIB / HNI / retail subscription, listing-day close versus issue price.  Result: no quant signal.  Test A (IPO rose 30% within 60 days, n=298, 89 winners): every AUC 0.49-0.55, all CIs include 0.5 (total subscription 0.515; hit rate by subscription tercile 26% / 32% / 31%; issue size 31% / 25% / 33%).  Test B (+30% in the next 30 sessions from session 20 or 10): AUCs 0.43-0.52, the lower-subscription end looks slightly better (QIB AUC 0.427, CI 0.36..0.50 at session 10) but the sign flips by listing year (2024 opposite).  Test C (policy-P trades, 158 matched, 39 GOOD): AUCs 0.46-0.59; issue price 0.588 (CI 0.51..0.67) is the only interval excluding 0.5, with a non-monotonic pattern (mean net low -1.6%, mid +9.1%, high +6.9%); with 7 features x 4 tests one such pass is expected by chance.  Verdict: subscription multiples and issue size do not predict post-listing explosive moves in this sample (2022-2026, n about 300); they are NOT worth integrating.  The pre-registered bar (AUC outside 0.40..0.60 with CI excluding 0.5, same sign in 3 of 4 years) was met by none.  Remaining untested information: grey market premium history, anchor lock-in dates, sector, fundamentals, index regime.

**17.10 The 3-4 month big movers: +50% and +100% within 120 days (research_big_movers.py, research_gap_cap.py, reports/2026-10-17/big_movers_output.txt, gap_cap_output.txt).**  437 eligible IPOs listed 2022-06 to 2026-06: 84 (19%) rose 50% or more above the listing-day close within 120 days (median 55 days to +50%), 26 (6%) rose 100% or more; the W50 rate by listing quarter ranges from 5% to 30%+ (2022Q4 5%, 2023Q3-Q4 30%, 2025Q3 9%, 2026 36%), so hot cohorts matter.  Common traits: (1) NOT subscription or size: the 26 +100% names ranged from 1x to 161x total subscription (OMNI 1x, BLACKBUCK 2x, SEDEMAC 3x, IREDA 39x, DIFFNKG 115x, PLAZACABLE 161x) and Rs 71 Cr to Rs 2,150 Cr issue size; W50 AUCs for log size, price, total/QIB/HNI/retail subscription are 0.48-0.51 (CI includes 0.5), W100 subscription tercile hit rates 3.2% / 5.4% / 6.5% (CI 0.40..0.70).  (2) Early price strength, but largely by construction: 88% of the +100% names were up at session 20 (all IPOs 45%) and 73% were up 10%+ (24%); the forward-only version (from the session-20 close, does the next 60 sessions reach +50%?) lifts the hit rate from 11.1% to 20.2% for IPOs up 10%+ at session 20 (AUC 0.58, CI 0.51..0.66; session 60 AUC 0.60), not a certainty.  (3) Exploratory only (26 names, one of several features checked): lower listing-day turnover (median Rs 38 Cr vs Rs 274 Cr, AUC 0.378, CI 0.29..0.46); it does not show up for the +50% group (AUC 0.484), so treat as a hypothesis to watch, not a rule.  Engine capture: policy P held a position in 46% of the +100% names (12 of 26) and 55% of the +50% names (46 of 84); those 12 trades averaged +68% net (median available move 117%, so about 55-60% of the move captured) and produced 41% of ALL gross wins.  Why 14 +100% names had no trade: 10 gapped above the +2.5% entry limit on their first eligible breakout day (IREDA, DIFFNKG, RBZJEWEL, FORCEMOT, PLAZACABLE, PTCIL, RAMRAT, SCILAL, GENSOL, MUFIN), 4 crossed within the limit but were stopped by other gates.  Widening the cap does NOT pay: limits of 5%, 8%, 12%, 20% and none add 22, 36, 45, 64 and 81 trades with mean net -0.4%, -2.5%, -2.8%, +0.1% and +1.6% (CIs include 0; win rate 22-33%, median -3.7% to -4.6%); the whole book PF falls from 2.43 to 2.29, 2.00, 1.92, 1.95, 1.99 (closed-only 1.71 to 1.42-1.49, ex-top-3 1.91 to 1.48-1.79), and with 1.0% slippage on gap fills the no-cap added trades average +0.5% (PF 1.09) while the 5-12% caps average -1.2% to -3.5%.  More outliers are caught (20 of 26 +100% names with no cap) but the extra trades net about zero.  The entry cap stays.

**17.11 The single quiet day (research_quiet_day.py, reports/2026-10-17/quiet_day_output.txt).**  Owner observation: the 20-30% moves came after a quiet day (volume dry-up and a very narrow range) near the listing high.  Tested on every liquid IPO day aged 5-60 (10,397 days, 492 symbols; QUIET = narrowest range of 7 sessions AND volume <= 0.6x the prior 10-session mean; NEAR = close within -10..+5% of the listing high).  Case-control: 12.1% of the days that preceded a +20% high within 10 sessions were quiet versus 12.5% of the days that did not; hit rate when quiet 11.3% vs 11.7% (lift 0.97x), on NEAR days 11.1% vs 10.2% (1.08x, CI 8.0..14.6); NR7 alone 0.97x, volume dry-up alone 0.89x.  41% of the days before +20% moves had a dry-up volume day, but 44% of the days before no move did: a dry-up day is routine (44% of all IPO days), which is why it is remembered before the moves that happened.  Tradable version (buy stop above the quiet-day high for 3 sessions, stop at the quiet-day low, +20% target, 15 sessions): QUIET and NEAR 56 fills, target 7.1%, stop 69.6%, mean net -0.86%, PF 0.75, versus the same mechanics on NEAR non-quiet days: 392 fills, target 15.6%, mean net +0.32%, PF 1.08 (difference -1.17pp, CI -3.0..+0.7); the +30% target version is also no better (-0.95pp).  Verdict: not supported as a rule; together with 17.4(b) (multi-day dry-up plus tight range, inverted) and 17.8 (DRYCOIL at sessions 5/10/20, no edge) this is the third independent test of the compression idea.


---

## 18. 2026-10-21: a second setup for moves that start below the listing high (S2) fails its pre-registered bar (reports/2026-10-21/)

**Verdict.**  S2, a resting stop-limit buy above a 10-session pivot while the stock is more than 10% below its listing high, does NOT meet the pre-registered bar and is not adopted, not even as a paper candidate: +0.41% net per trade (90% symbol-cluster CI -2.6 to +3.9, N = 146, 125 symbols) against policy P's +5.22%; it fails B2 (CI includes 0), B3 (-0.61% without the largest trade), B4 (negative in 2025 and 2026, which hold 93 of its 146 trades) and B5 (adding it to policy P lowers the account result in both slippage cases).  Evidence tier: REPLAY of the honest engine.  The data cannot exclude a small positive expectancy (the CI reaches +3.9pp); they do exclude a policy-P-sized edge, and nothing here supports building it.  A null result is a valid result; it was the likely one.

**What was tested.**  Pre-registration, definitions and the bar are in the docstring of `research_setup2.py` (written before any outcome was computed; the engine is `core/setup2.py`, tests `test_setup2.py`, 18 pass).  Setup: age 15-200 calendar days, prior close at or below 0.90 x listing high, pivot = highest high of the last 10 completed sessions (at least 5 sessions old), prior close within 10% of the pivot, policy P's causal liquidity gates (prior-bar volume, turnover floor, price floor, circuit days), buy trigger pivot +1.5% with a +2.5% limit, fill +0.3%, and policy P's exits exactly (close-based stops sold at the next open, 12% cap, trail, dead-money gates), costs 1.0% flat.  Three fixed, disjoint hypotheses: H1 base breakout of a 61-200 day IPO (drawdown from the 60-session high under 30%), H2 recovery reclaim (drawdown 30% or more), H3 the same rule at age 15-60.  Primary test = pooled S2 (one test).  Before freezing, only outcome-blind event counts were examined; a 20-session pivot, a listing-close relative-strength split and a 40-session full-pivot reclaim were dropped because they produced 0-20 signals, not because of results.  Nothing was tuned.  Data hygiene: 676 of 858 symbols are usable (94 placeholder listing dates, 63 with under 40 candles, 15 with an inconsistent listing high, 7 listed before 2022-05, 3 with a first candle far from the listing date).  Harness check: policy P on the same engine reproduces 228 trades, PF 2.43 / 1.71 / 1.91 (incl. open / closed only / ex-top-3).

**Results (all net of 1.0% round trip; open trade marked to market; CI = 90% symbol-cluster bootstrap).**

| | N | Mean net (CI) | Mean without the largest trade | PF incl. open / closed only / ex-top-3 (all) | Dead money (capital-days) | Outliers >= +25% |
|---|---|---|---|---|---|---|
| Policy P (reference) | 228 | +5.22% (+2.5..+8.1) | +4.57% | 2.43 / 1.71 / 1.91 | 36% (14%) | 23 = 10.1% of trades, 79% of gross profit |
| **S2 pooled, slip 0.3%** | 146 | **+0.41% (-2.6..+3.9)** | **-0.61%** | **1.07 (0.56-1.72) / 1.08 (0.57-1.73) / 0.58 (0.23-1.17)** | 49% (20%) | 10 = 6.8%, 89% of gross profit |
| S2 pooled, slip 1.0% | 146 | -0.55% (-3.5..+2.8) | -1.56% | 0.91 / 0.92 / 0.46 | 49% (21%) | 9 = 6.2% |
| H1 base, age 61-200 | 75 | -0.40% (-3.9..+3.6) | -1.95% | 0.92 / 0.92 / 0.26 | 45% | 4 |
| H2 recovery | 25 | -1.47% (-6.5..+4.6) | -4.35% | 0.76 / 0.76 / 0.04 | 60% | 2 |
| H3 base, age 15-60 | 46 | +2.75% (-4.4..+11.4) | -0.48% | 1.48 / 1.51 / 0.16 | 50% | 4 |

* H2 is UNDERPOWERED (N = 25 < 40): no claim either way.  H1 and H3 do not meet B2-B4 (H3's point estimate is positive, but its interval spans -4.4 to +11.4 and it is negative without its top trade).
* **Year by year (pooled S2, entry year: N, mean net):** 2022 2, -1.3%; 2023 17, +22.5%; 2024 34, +1.6%; 2025 60, -3.2%; 2026 33, -5.6%.  One cohort (2023, 17 trades) carries the whole average; 2025 (the year with the most signals) and 2026 are negative.
* **Control (not gating):** the same state (age band, below the listing high, near a pivot, liquid) bought at a random eligible day's open, one day per symbol, 10 draws, same stop and exits: +3.44% per trade (N = 2,870 over 287 symbols).  S2 minus control = -3.03pp (CI -6.3 to +0.2).  So the trigger did not add anything; if anything buying the state beat waiting for the cross.  Do not read the control as an edge: it is not tradable as stated (you would need to know which day), it rests on a survivors-only universe and one strong year (2022 +41.9% on 62 draws, 2023 +12.3%), and it is negative in 2025 and 2026 like S2.
* **Entry quality independent of the exit rules (post-hoc, descriptive, added after the first run; it does not affect the bar):** share of entries whose peak high within 20 sessions reached +20% / +40%: policy P 35.4% / 9.1% (N = 209), S2 22.4% / 4.2% (N = 143), control 25.9% / 5.7%.  The weakness is in the entries, not only in P's exit design.
* **Capture versus profit.**  Among the 355 +20% episodes (age 5-60) in the clean universe, 148 (42%) begin more than 10% below the listing high.  Policy P holds 28 of those (19%), S2 holds 25 (17%), the two together 49 (33%).  S2 does reach episodes P cannot, but holding an episode window is not the same as profiting from it: those S2 trades net about zero.
* **Does it add independent draws?**  Timing is mostly independent: only 37 of 146 S2 trades are in a symbol that ever had a P trade, 4 overlapped a P holding in time, and the monthly cohort rank correlation with P is +0.10 on mean net and +0.19 on trade counts (44 months with both).  Independent draws of a near-zero-expectancy trade add variance, not alpha.
* **Account (Rs 1 lakh, flat 10 slots x 10%, whole shares, costs on, P wins ties and same-symbol overlaps):** P alone +124.6%, CAGR 20.4%, max drawdown -25.0%, Calmar 0.82.  P + S2 (S2 slip 0.3%) +90.2%, CAGR 15.9%, max drawdown -33.2%, Calmar 0.48; P + S2 (slip 1.0%) +76.9%, 14.0%, -31.0%, 0.45; S2 alone +0.2% with a -41.5% drawdown.  S2 took 88 trades (mean net +1.11%) and displaced 41 P trades (mean net +8.82%, 5 of them outliers of +25% or more).  Net contribution at equal weights -264pp (CI -800 to +284), -320pp at slip 1.0% (CI -808 to +201): the interval includes zero, but both signs are negative and the accounting is single-path (LEARNINGS 12.3), so this is a necessary check that failed, not a measurement of the loss.
* **Order load if it were ever used:** mean 3.9 resting orders per business day (95th percentile 11, maximum 24; 6.1 over the last 250 business days): feasible for a manual sheet, which is not the problem.

**What is NOT established (read before acting on this).**  (1) Power: about 3-4pp per trade is the smallest effect this sample can see, so "no demonstrated edge" is the claim, not "proven zero".  (2) Only one pivot definition (10 sessions, 5 sessions old, 10% proximity) was tested; no neighbours were tried, deliberately, so the verdict cannot be tuned away or into existence.  (3) The exits were designed for listing-high breakouts; the post-hoc peak-run-up table says the entries themselves are weaker, but a different exit design for a base setup was not tested.  (4) Survivors only (delisted names are absent), 4.4 years, strong cohort waves.  (5) The 20-session pivot, listing-close strength split and a full 40-session reclaim were not testable (counts 0-20), so they are untested, not rejected.  (6) Fills are the idealised resting-order model; jump-throughs are invisible in daily bars.

**Decision.**  Nothing changes live; S2 is not added to `order_sheet.py` or any scanner, not even PAPER_ONLY.  Add to the do-not-retest list (SESSION_HANDOFF 12.4): 10-session pivot breakouts below the listing high at age 15-200 (pooled, base, recovery) and the "capture the far episodes by a pivot breakout" idea; retest only with a new information source or a pre-registered redesign of exits for base setups.  The explosive moves that start well below the listing high remain unreachable by any rule found so far; the remaining honest levers are unchanged (forward test of policy P, a wider universe with new information, shadow-score evidence).  Single next action that most reduces uncertainty: the live resting-order forward test (SESSION_HANDOFF 12.1/12.2).

Reproduce: `.venv\Scripts\python.exe research_setup2.py --cache data.pkl --out-dir reports/2026-10-21` (about 40 seconds; output `reports/2026-10-21/setup2_output.txt`, trades in `setup2_trades_slip0.3.csv`, `setup2_trades_slip1.0.csv`, `setup2_control_trades.csv`, `setup2_policyP_reference_trades.csv`).  Tests: `python -m pytest test_setup2.py` and the SESSION_HANDOFF 11.5 command (167 pass with it); `run_honest_backtest.py --verify-legacy` still prints EXACT MATCH (203 trades, 0 mismatches).


---

## 19. 2026-10-21: anchor lock-in expiries (day ~30 and ~90): a small, probably real supply effect that is too small to trade (reports/2026-10-21/)

**Verdict.**  Both pre-registered Stage 1 tests PASS: abnormal returns are lower in the 10 sessions around the nominal anchor unlocks than in the same stocks' other windows, by -1.74pp (U30) and -1.26pp (U90) on average and about -2pp at the median.  A post-hoc audit supports that the dips are tied to the two dates and not just an age profile.  But the effect is small against a 1.0% round-trip cost, cannot be shorted by this account, gives no entry signal, and the policy-P trades entered just before an unlock were not worse (Stage 2 skip rule FAILS).  Nothing changes live.  Evidence tier: event-study REPLAY with NOMINAL dates.

**Design (pre-registered in the docstring of `research_anchor_unlock.py` before any return was computed).**  Unlock dates are nominal (no allotment dates in the database): listing + 28 days (U30) and + 88 days (U90), blur about +/- 3 days.  Event = close[e+5] / close[e-5] - 1 minus the equal-weight clean-universe return over the same dates; D = event abnormal return minus the mean of the same stock's placebo windows centred 14-38 days later (at least 2 valid; policy-P price and turnover floors).  Bar per test: N >= 100, mean D negative with the 90% CI excluding 0, still negative without the most negative stock, negative in at least 3 listing years with N >= 15.  Stage 2 (only if Stage 1 passed): a skip rule for P entries 0-10 days before an unlock, adopted only if the removed trades have a negative mean net with a CI excluding 0.  Code defect found on the first run (zero events: a window guard demanded 21+ sessions of history, so every U30 event was rejected); fixed before any result existed and covered by `test_anchor_unlock.py`.

**Stage 1 results** (N = stocks; 90% bootstrap over stocks):

| | N | Event window | Placebo windows | D | Median D | Without the most negative stock | By listing year (D, pp) |
|---|---|---|---|---|---|---|---|
| U30 (listing + 28d) | 475 | -0.30% | +1.43% | **-1.74 (CI -2.61..-0.85)** | -2.16 | -1.64 (IREDA -44.8) | 2022 -1.83, 2023 -1.77, 2024 -2.42, 2025 -1.44, 2026 -1.17 |
| U90 (listing + 88d) | 446 | -1.42% | -0.16% | **-1.26 (CI -2.11..-0.34)** | -2.06 | -1.17 (AIIL -40.4) | 2022 +5.50, 2023 +0.23, 2024 -3.90, 2025 -2.04, 2026 -0.43 |

U30 is negative in all five years.  U90 clears the year rule only narrowly (3 of 5 years negative; 2022-23 are not): treat its size as uncertain.  In event time the U90 abnormal return falls from +0.03% at session e-5 to -1.58% at e and stays flat afterwards (placebo flat), a decline concentrated in the three sessions before the nominal date.

**Post-hoc audit** (`research_anchor_unlock_audit.py`; added AFTER seeing Stage 1, cannot adopt anything).  (A) The same contrast at every age K = 14..130: D(K) is negative only for windows touching the two unlock dates (minimum -2.36pp at K=26 and -1.57pp at K=84, 2-4 days BEFORE the nominal dates, so the true unlocks are probably a few days earlier than assumed) and positive elsewhere (mean +0.55, range -0.58..+1.60 over 31 non-unlock ages): none of them is as negative as D(28) or D(88).  Neighbouring windows overlap, so that is about ten independent looks, not a p-value.  (B) A two-sided U90 test against placebo windows BEFORE the event gives D = -2.77pp (CI -3.55..-1.93), negative in all five years.  (C) Medians (-2.16, -2.06), 5% trimmed means (-1.89, -1.76) and means without the five most negative stocks (-1.33, -0.88) keep the sign.  The raw market-adjusted 10-session return by centre age shows the same shape: a hump of about +2% at ages 38-58 with local dips of -0.7% around day 26 and -1.4% around days 82-90 (medians -2.4 to -2.6).

**Stage 2.**  Policy-P trades entered 0-10 days before a nominal unlock: N = 27, mean net +1.10% (CI -3.7..+6.7, PF 1.26) against +6.03% for the other 182.  The skip bar needs a NEGATIVE mean with a CI excluding 0: FAIL, no skip rule.

**Why it is not tradable.**  About -1.3 to -1.7pp on average (-2pp median) over a 10-session window is smaller than the 1.0% round trip it would cost to sell before the date and buy back after, the account cannot short these stocks, and at 27 near-unlock P trades (SD about 17% per trade) no trade-level effect of this size is detectable.  Outliers incubate inside positions that look dead (LEARNINGS 14), which is why exit tweaks have failed before; exit timing around unlocks was not pre-registered and is not recommended on a gross effect this size.  It also does not touch the owner's goal of catching 20-30% moves: it says nothing about which stock will run.

**What is and is not established.**  Established: a repeatable, date-linked dip of roughly 1-2.5pp around the two anchor unlocks in 2022-2026 IPOs (two pre-registered tests, both passed; each carries a 10% chance of a false pass at the 90% level).  Not established: exact unlock dates (nominal), the true size (U90 depends on 2024-25), any benefit after costs, any use for entries.  Not tested: the 6-month pre-IPO and promoter lock-in expiries (about listing + 178 days, inside the 200-day window), exit timing around unlocks, holdings of anchors by name.  Decision: record as a calendar fact for position management (expect softness around days ~26 and ~84 after listing, 2-4 days before the nominal dates) but no rule, no gate, no live change.

Reproduce: `.venv\Scripts\python.exe research_anchor_unlock.py --cache data.pkl --out-dir reports/2026-10-21` then `research_anchor_unlock_audit.py` (about 2 minutes each); outputs `anchor_unlock_output.txt`, `anchor_unlock_audit_output.txt`, `anchor_unlock_U30.csv`, `anchor_unlock_U90.csv`.  Tests: `test_anchor_unlock.py` (5), full suite 175 pass.
