# Minute-bar test plan (written 2026-10-17, nothing run yet)

Goal (owner): catch most of the explosive IPO moves (+20% to +50% in days) and reject the bad trades.
Why minute bars: every unresolved question needs intra-session information that daily bars cannot give.
No live code changes are part of this plan.  Everything below is additive research tooling that reads the DB and writes only local files.

## 1. What daily data could not settle (2026-10-17 replays, reports/2026-10-17/)

| Question | Daily-bar result | Gap |
|---|---|---|
| Does the live mid-session volume floor (150k shares) reject winners? | EOD version removes trades averaging +6.9% (CI +2.6 to +11.7), capture of +20% episodes 35% -> 28% | CORRECTED after the probe: HTEL was NOT lost to the volume floor.  The 9:52 IST floor rejection (143,668 shares by minute bars vs 140,529 logged) happened while price was 73.59, below the 78.75 listing high.  HTEL crossed its trigger at 12:24, was above the +3.5% chase cap (81.51) by 12:31, and the next scan saw +6.0%.  The cause is scan latency plus the chase cap, which a resting order avoids. |
| Does the upper-50% close-location gate help? | EOD version lifts PF 2.43 -> 3.03 but uses the day close, which a pre-placed order cannot know | Need to know whether a time-of-day version (for example the 60-minute alert time) keeps the gain without look-ahead. |
| Can any entry feature separate GOOD from BAD? | 18 daily features: none beats random rejection (best lift 6pp); volume dry-up and tight coil point the wrong way | Untested: first-60-minute behaviour after the trigger, volume pace, VWAP position. |
| Fill realism of resting stop-limit orders | Modelled slip 0.3%, no fill above LH +2.5%; estimate +0.8 to +1.9pp per trade | Need measured trigger-cross times, gap-throughs and fills. |

## 2. Data

* Source: Upstox Historical Candle Data V3, 1-minute interval.  Minute data is available from January 2022 (all 2022-05+ IPOs are covered) and each request spans at most one month for 1 to 15 minute intervals.  Verify the date-range and parameter order on a live call before the bulk pull.
  Docs: https://upstox.com/developer/api-documentation/v3/get-historical-candle-data/
* Scope: each IPO's first 60 calendar days (the engine's age window), 850 symbols with candles, about 1 to 3 requests per symbol (about 1,000 to 2,500 calls).  Start with a 10-symbol coverage probe, then the 228 policy-P trade symbols, then the rest.
* Prerequisites the owner must provide: a valid Upstox token in the environment (I will not read `.env`).  Instrument keys: the collection is keyed by `ipo_symbol` (871 rows) and has rows for all 10 probe symbols; an earlier note here that STEAMHOUSE, HTEL, AUGMONT and DEEPA had no key was wrong (wrong field name in my query).  The Atlas daily candle cache still lacks 13 recent IPOs; minute bars can fill that gap.
* Storage: local parquet or pickle under `data_minute/` (gitignored).  No writes to MongoDB.

## 2b. Probe results (2026-10-17, `research_minute_probe.py`, 10 symbols, 28 requests, nothing written to the DB)

* 10 of 10 symbols returned data (HTTP 200, no 429 at 0.2 s spacing): HTEL, DEEPA, AUGMONT, STEAMHOUSE, MVELECTRO, OMNI, ARFIN, SIGNATURE, AZAD, NUVAMA, covering 2023-09 to 2026-10.  Median 375 one-minute candles per session; listing days start 09:36 to 09:42.
* Minute volume sums agree with the daily candle volume: median ratio 1.00 (p10 0.997) for 2025-26 listings, 0.94 to 0.99 for the 2023 listings (a few early sessions have only 60 minutes).  The last minute close differs from the daily close in most sessions, as expected (the exchange close is a last-30-minute average); do not use it as a check.
* One-month limit confirmed: a 45-day request returns HTTP 400 UDAPI1148 "Invalid date range"; 28-day chunks work (about 3 requests per IPO for 60 days).  Pre-2022 listings return 0 rows, as documented.
* Minute data also fixes holes in the daily cache: STEAMHOUSE has 7,854 minute rows (no daily candles in the DB) and AUGMONT minutes run to 2026-10-16 while its daily cache stops at 2026-10-01.
* Reconstruction of the live gate readings works: HTEL cumulative volume 143,668 at 09:52 (log 140,529; it crossed 150k at 09:55); DEEPA close location 47.2% at 11:52 (log 46.6%).  So Q2 can replay the live gates from minute bars.
* Bulk estimate: about 850 symbols x 3 requests = 2,550 calls, roughly 10 to 15 minutes at 0.25 s spacing; storage about 15k rows per symbol.
* Not yet seen: rate-limit headers (none returned) and behaviour under sustained load; run the bulk fetch with back-off on 429.

## 3. Pre-registered questions (hypotheses and bars fixed before any result is seen)

Labels (same as the 2026-10-17 replays): GOOD = net >= +20% or peak run-up >= 25%; BAD = closed loss with peak run-up < 5%.  Population: policy P trades (about 228 and growing) plus, for Q2, every IPO-day where price crossed the trigger.

* **Q1 Execution.**  For every trigger cross: time of cross, whether the next minute prints above the limit (+2.5%), fill price under a resting stop-limit, and the realised slip.  Output: measured slip distribution, share of gap-throughs, replacement of the +0.8 to +1.9pp estimate.  Bar: none (measurement); update `core/execution_model.py` assumptions only if the measured mean slip differs from 0.3% by more than 0.5pp.
* **Q2 Audit of the live mid-session gates.**  Replay what the live scanner read at its alert time (volume so far, close location of the forming candle, the 60-minute PENDING confirmation and the 2.5% pullback ejection) for each trade and each crossed IPO-day.  Output: share of GOOD trades rejected, share of BAD rejected, and mean net of the rejected set, per gate.  Bar to keep a gate: it rejects at least 35% of BAD while keeping at least 80% of GOOD, with the rejected trades' mean net below zero.  Otherwise recommend removal (the owner decides; this plan changes nothing live).
* **Q3 Entry-time filters from the first 60 minutes after the trigger.**  Fixed feature list (6 at most, to limit multiple testing): minutes from open to cross; volume so far projected to a full day divided by 20-day average volume; price vs session VWAP at the cross; depth of the deepest pullback below the trigger in the first 60 minutes; share of the first 60 minutes spent above the trigger; opening gap.  Walk-forward on 2024, 2025, 2026 with thresholds chosen on earlier years to keep at least 80% of GOOD.  Bar: out-of-sample recall of GOOD >= 75%, BAD rejected >= 35%, lift over random rejection >= 10pp, rejected mean net < 0, at most one worse year.  Pre-specified action if it passes: delay or skip the entry (never tighten stops, never rotate out of positions).
* **Q4 Confirm-then-buy vs resting order.**  Paired on the same signals: buy at the 60-minute confirmation instead of at the trigger.  This is the cost of the live PENDING step in pp per trade and in captured episodes.  Bar: paired CI of the difference excludes 0 and the result holds without the top 3 trades.

## 4. Statistics and safeguards

* About 54 GOOD trades in the replay population means only large effects are detectable; report intervals (symbol-cluster bootstrap, 90%) and do not claim more.
* Report PF three ways (closed only, including open at mark to market, ex-top-3) and the capture rate of +20% and +40% episodes next to every result.
* Exploratory screens are labelled and never adopted.  Anything that passes is then checked on the forward log (`logs/resting_orders.jsonl`) before it touches live rules.
* The 2026-10-17 findings stay in force until minute data says otherwise: no volume dry-up or coil filter, no quality filters from daily features, no tighter stops.

## 5. Work order

1. Coverage probe: 10 symbols (including HTEL, DEEPA, AUGMONT, STEAMHOUSE), confirm key resolution, one-month limit, rate limits, session times.  Stop and report if coverage is poor.
2. Bulk fetch for the 228 policy-P symbols, then the full universe; coverage report (symbols, days, missing minutes).
3. Q1 and Q2 first (they answer the live-gate question the owner raised), then Q3, then Q4.
4. Report in `reports/<date>/` and add a section to `LEARNINGS.md`; update `AGENTS.md` only for adopted rules.

Estimated effort: one session for steps 1 and 2 (limited by API rate and the token), one for Q1 to Q4.
