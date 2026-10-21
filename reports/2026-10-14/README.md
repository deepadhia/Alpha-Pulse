# Research outputs, 2026-10-14

Raw outputs of the pre-registered alpha search and the risk profile.  Data: the MongoDB candle cache (850 symbols, 871 listing
records, candles 2022-05-27 to 2026-10-13), loaded read-only.  Costs: flat 1.0% round trip for trade-level figures; Indian delivery
charges plus 0.3% exit slippage for account simulations.  Findings and conclusions are in `LEARNINGS.md` section 12.

| File | What it is | Command |
|---|---|---|
| `00_lab_reference_eod_volume_intraday_stops.txt` | Reference run: end-of-day volume gates, intraday stops (the earlier modelling choices) | `python research_alpha_lab.py --cache data.pkl --out-dir reports/2026-10-14` |
| `01_lab_causal_resting_intraday_stops.txt` | Causal population (no same-day volume), resting entry, intraday stops | `... --causal --no-vol-gate --base-slip 0.3 --base-cap 2.5 --tag _causal_resting` |
| `02_lab_PRIMARY_causal_resting_live_stops.txt` | **Primary:** causal population, resting entry, live close-based stops | `... --causal --no-vol-gate --base-slip 0.3 --base-cap 2.5 --tag _causal_resting_eod --exit-mode eod_alert` |
| `03_risk_profile_stops_streaks_account_capacity.txt` | Stop style (paired), profit factor three ways, loss streaks, rolling-PF bootstrap, account configurations, capacity | `python research_risk_profile.py --cache data.pkl` |
| `04_tail_capture.txt` | Tail anatomy (where profit comes from), outlier separation by entry feature, re-entry cooldown, pyramid, conviction sizing, E11 dead-money clause | `python research_tail_capture.py --cache data.pkl --trades reports/2026-10-14/data_base_trades_causal_resting_eod.csv` |
| `05_dead_money_patterns_shelf_rotation.txt` | Dead-money anatomy and pattern buckets, shelf vs listing breakout under the corrected model, capital constraint, rotation of dead-money positions | `python research_dead_money.py --cache data.pkl` |
| `06_small_account.txt` | Rs 50k / Rs 1 lakh accounts: frictions, real rolling 12-month windows, resampled 12/24-month histories, blowup and drawdown probabilities by slot configuration, structural loss bound | `python research_small_account.py --cache data.pkl --sims 250` |
| `data_base_trades*.csv` | The trades behind each lab run, with entry-time features | written by the lab |

Sections in the lab output: 0 baseline and execution value, F pre-registered entry filters (walk-forward), E pre-registered exit
variants (paired), X2 execution robustness, G exploratory screens (NOT adoptable), S account level, VERDICT against the adoption bar.

Headline: a pre-placed stop-limit entry beats buying after the alert by +1.3 to +1.9pp per trade (paired, every year); the live
close-based stop beats an intraday resting stop by +2.0pp per trade (paired, every year); no pre-registered quality filter or exit
variant is adoptable; profit factor of the recommended policy is 1.67 closed-only, 2.45 with open runners at mark to market and 1.14
without the three best trades.

The `data.pkl` cache is not stored in the repository (build it with `python run_honest_backtest.py --verify-legacy --cache data.pkl`).
These files are a dated snapshot: re-run the scripts to refresh them and do not edit them by hand.
