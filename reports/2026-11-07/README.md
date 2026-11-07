# Max-history replay of policy P and S3 (2026-11-07)

Tier: REPLAY (honest engine, resting entries, close-based stop sold next open, flat 1.0% round-trip cost, open trades marked to market).
No rule or parameter was changed or searched. Pre-registration is in each script's docstring.

## Reproduce
```
python build_panel_ipo_universe.py --out-dir data_panel_w1200          # system python (pyarrow); 20-year NSE panel -> IPO-like listings
.venv\Scripts\python.exe run_honest_backtest.py --verify-legacy --cache data_20261107.pkl      # rebuild Atlas cache (EXACT MATCH, 209 trades)
.venv\Scripts\python.exe research_max_history.py --atlas data_20261107.pkl --panel-dir data_panel_w1200 --out-dir reports/2026-11-07
.venv\Scripts\python.exe research_p_control.py --atlas data_20261107.pkl --panel-dir data_panel_w1200 --out-dir reports/2026-11-07
.venv\Scripts\python.exe research_s3_history.py --atlas data_20261107.pkl --panel-dir data_panel_w1200
.venv\Scripts\python.exe research_max_history_extras.py --atlas data_20261107.pkl --panel-dir data_panel_w1200 --out-dir reports/2026-11-07
```
Outputs: `max_history_output.txt`, `p_control_output.txt`, `s3_history_output.txt`, `sensitivity_and_recent_output.txt`, `alpha_lab_output.txt` (the standard lab run on the refreshed cache), trade CSVs.

## Data
* Atlas cache rebuilt 2026-11-07: candles to 2026-11-05, 878 symbols, coverage 46/46 = 100%.
* Older history: Learning-Lab `clean_panel.parquet` (NSE bhavcopy 2006-2026, raw prices, delisted names included) with split/bonus/demerger factors applied inside each 1,200-day window. A listing is the first EQ/BE row of a symbol (SME-first symbols, renames and continuations removed): 2,108 listings 2006-07..2026.
* **Proxy validation (2022-05-27+):** 747 proxy listings vs 870 true Atlas listings, overlap 708 (precision 95%, covers 81%). Policy P on the proxy reproduces the Atlas replay: closed net +3.55% vs +3.35%, PF closed 1.90 vs 1.87, same sign in every year.
* Known artifacts: unadjusted corporate actions create impossible one-day jumps. Trades with a one-day close move above +60% are flagged (`flag_jumps`) and removed from S3 headline numbers; P had none. Down-jumps (SPECTACLE 2010, -80%, lower-circuit lock) are kept: they are real or at worst pessimistic.
* Not covered: BSE-only listings, 2006 first-half panel ambiguity, cross-listings of old companies inside the proxy list, pre-2022 fills (idealised: trigger +0.3%).

## Policy P (listing-high breakout, resting orders)
| Cohort | Closed N | Net mean (90% CI) | Win | Median | PF closed / incl. open / closed ex-top-3 |
|---|---|---|---|---|---|
| Atlas 2022-05..2026-11 (true list) | 226 (+4 open) | +3.35% (+0.9, +6.0) | 41% | -1.6% | 1.87 / 2.14 / 1.38 |
| Panel proxy 2022-26 (validation) | 216 (+4 open) | +3.55% (+1.0, +6.3) | 41% | -1.7% | 1.90 / 2.20 / 1.40 |
| Panel 2006-07..2022-05 (older) | 189 | +6.38% (+1.7, +11.7) | 29% | -2.8% | 2.20 / 2.20 / 1.53 |
| Combined 2006-2026 | 415 | +4.73% (+2.3, +7.6) | 36% | n/a | 2.05 / 2.17 / 1.69 |

Account (Rs 1 lakh, 10 x 10% slots, charges + 0.3% exit slip, no cash yield): 2006-2026 CAGR +9.6%, max drawdown -26.4%, 363 of 419 signals taken, top-3 trades 30% of profit; pre-2022 CAGR +6.6% (DD -26.4%), 2022-26 CAGR +19.1% (DD -25.4%).
Years with at least 8 trades: positive in 6 of 11 pre-2022 (2007, 2008, 2010, 2017, 2022 negative). 2006 (8 trades, +63%) and 2020 (+46%) carry the old era; excluding 2006 the older cohort is +2.7% per trade, PF 1.50.
Fill sensitivity (closed net, PF closed): resting +1.0% 2022-26 +2.67% / 1.63; chase +1.7% +2.38% / 1.55; chase +2.5% +1.73% (CI -0.6..+4.4) / 1.37. Older era: chase +2.5% +3.57% (CI -1.0..+8.7).

### Does the breakout trigger add anything? (research_p_control.py)
* Look-ahead-free control (random day 5-60 d after listing, every eligible IPO, same stop/exits/cost): **2022-26 +0.47% (PF 1.09), 2006-22 -0.02% (PF 1.00)**. P minus control: +3.83pp (2022-26), +6.40pp (2006-22). The edge is not the exit rules plus IPO drift: random IPO entry with the same exits earns about nothing.
* Matched control (same symbols that later broke out, random day): P minus control +0.90pp (CI -1.3..+3.0) and +2.58pp (CI -0.7..+6.3). Given that a stock will break its listing high, the timing within the window adds little: the value is in WHICH stocks (the breakout is the selector).

## S3 (late breakout, PAPER_ONLY) on 2006-2022 (out of sample for the rule and the Rs 25 Cr cap)
* Proxy check 2022-26: paper rule +7.22% (after removing 5 up-jump artifacts) vs Atlas +7.55%.
* 2006-07..2022-05, paper rule, up-jump artifacts removed (CHOICEIN +2,929%, COHANCE, TI): N=251, closed net **+4.93% (CI +1.4, +8.6)**, PF 1.76, closed ex-top-3 PF 1.37 (interval 0.89..2.02), positive in 4 of 9 years with at least 8 trades.
* Paper rule minus matched capped control: +1.25pp (CI -2.4..+4.9) before 2022; +3.82pp (CI +0.7..+7.1) in 2022-26 (in-sample for the cap). Uncapped S3 pre-2022 +5.60% beats the capped rule +4.93%: the turnover cap did NOT help out of sample (nominal-rupee caveat).
* Pre-registered pass mark (net CI above 0, half the years positive, positive ex-top-3, beats control): CI above 0 yes, ex-top-3 positive yes, **years rule fails (4 of 9)**, and the control difference (+1.25pp) is positive but its interval spans 0. S3 is not shown to generalise; it is not shown to be bad.

## Recent months (replay of the current rules)
* Entries since 2026-07-05: N=37 (3 open), closed net -1.03%, PF closed 0.73, incl. open 1.87.
* Entries since 2026-09-29: N=8, closed net -4.43%, PF 0.16 (FREDUN -17.4%, SSRETAIL -16.8% stop-outs beyond the 12% stop because the close-based exit sells at the next open). N is far too small to read; 2025 shows the same kind of flat stretch (-0.4% per trade).

## Later studies (same day)
* improve_history_output.txt, improve_followup_output.txt, improve_x1_pooled_output.txt: exit variants and X1 (LEARNINGS 22.1)
* tail_levers_output.txt (22.3), vs_index_output.txt (22.4), outlier_anatomy_output.txt (22.5), ipo_outliers_10y_output.txt (22.6), deep_dive_output.txt and followthrough_output.txt (22.7), regime_lever_output.txt (22.8), ipo_waves_output.txt (22.9)
* run build_market_breadth.py (system python) before research_regime_lever.py and research_ipo_waves.py
