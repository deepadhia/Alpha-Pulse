# Audit outputs, 2026-10-12

Raw, unedited outputs of every run behind `HARDENING_REPORT.md` and `LEARNINGS.md`.  Data: 849 symbols with
candles (2022-05-27 to 2026-10-09), 870 listing records, loaded read-only from MongoDB Atlas.
Costs assumed: about 1% round trip (`CostModel` defaults: Indian delivery charges + 0.3% slippage per side).

| File | What it is | Produced by |
|---|---|---|
| `00_legacy_backtest_baseline.txt` | The legacy backtest as it runs today: 196 trades, PF 2.27, +4.48%/trade | `run_latest_rules_backtest.py` |
| `01_bridge_and_account_results.txt` | Legacy -> live-faithful attribution (11 steps) and Rs 1 lakh account results under slot / cost scenarios | `run_honest_backtest.py --portfolio` |
| `02_ablation.txt` | Each divergence alone; live-faithful with one switch reverted; live entry sub-gate ablation | `research_ablation.py` |
| `03_exit_study_breakout_events.txt` | 28 pre-registered exit variants, paired, on 829 liquid breakout signals | `research_exits.py` |
| `04_exit_and_stop_study_any_day.txt` | 35 exit + stop variants, paired, on 5,323 any-day signals | `research_exits.py --anyday --step 3 --stops` |
| `05_walkforward_entry_filters.txt` | Walk-forward single-feature rules and ridge model (test years 2024-26) | `research_walkforward.py` |
| `06_trigger_vs_any_day.txt` | Does the breakout trigger beat an arbitrary IPO-day? (15,475 entries) | `research_trigger.py` |
| `07_per_engine_full_history.txt` | Listing vs shelf, per year, current 60/30-day rules and old 730-day lifecycle | `research_engines.py` |
| `data_events_loosened_gates.csv` | 952 candidate signals under loosened gates with features and live-exit outcomes | `research_events.py` |
| `data_live_faithful_trades.csv` | The 79 live-faithful trades (before costs) | `run_honest_backtest.py --bridge --out` |

Headline: live-faithful PF 1.24 (not 2.27); listing breakout +2.35% net (N=36, fragile, top trade = 56% of profit);
shelf breakout -2.36% net (N=43); no exit, stop or entry-filter change survives out-of-sample.

These files are a dated snapshot.  Re-run the scripts to refresh them; do not edit them by hand.
