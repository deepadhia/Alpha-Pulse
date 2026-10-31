# BACKTEST_VALIDATION.md — Mandatory Acceptance Standard

## 1. Purpose
This file defines when a historical trading simulation is technically credible enough for further research. It cannot establish future profitability.

## 2. Required strategy specification
For each engine, record and freeze:
- Indicator formulas and initialization conventions.
- Entry and exit predicates.
- Signal timestamp.
- Earliest execution timestamp and fill model.
- Stop/target/trailing-stop rules and priority.
- Position sizing and risk budget.
- Max positions, per-symbol allocation, cash constraints, re-entry rules.
- Fees, taxes, slippage, impact assumptions.
- Universe definition and historical eligibility rules.
- Benchmark, date range, warm-up, and test partitions.

If any item is undefined or conflicting, mark the experiment INCOMPLETE until resolved. Never silently infer the missing rule.

## 3. Data acceptance
For every symbol and run:
- Record source, retrieval timestamp, date coverage, interval, adjustment policy, and instrument identity.
- Check duplicate timestamps, missing sessions/candles, timezone consistency, invalid or impossible OHLC values, zero/negative prices, stale data, and volume anomalies.
- Validate split/dividend/corporate-action treatment and distinguish adjusted analytical prices from executable prices.
- Document survivorship, delisting, symbol-change, and historical-universe limitations.
- Calculate liquidity/price filters using only information available at each decision time.
- Log rejected symbols and exclusion reasons.
- Fail loudly if required data is missing. Missing data is not equivalent to no signal.
- Synthetic fixtures are allowed for software tests only. Every synthetic run must be labelled `SYNTHETIC_TEST`; never report it as historical strategy performance.

## 4. Temporal correctness and fills
For close-based signals with next-open execution:
1. Indicators and signal use only completed data through session t close.
2. The order becomes eligible only after that signal exists.
3. Fill is simulated at session t+1 open or the explicitly documented realistic alternative.
4. Position size uses the simulated fill price and applicable stop distance.
5. Slippage and costs are charged.
6. P&L starts only after the entry fill.

Tests must detect:
- Same-bar signal/return leakage.
- Negative shifts, centered rolling windows, future joins, accidental backward fill, future ranks/normalization.
- Features built using data later revised or unavailable at the historical timestamp.
- Orders filled before signal generation.
- Signals or positions accidentally carried across symbols/dates.

If a daily candle crosses both stop and target, ordering is unknowable from OHLC alone. Use a conservative declared rule or higher-frequency data. A stop crossed by a gap must not automatically fill at the stop price.

## 5. Indicator validation
Use hand-calculated examples and/or an independent implementation for:
- RSI, including all-gain, all-loss, flat-series, warm-up, and missing-value cases.
- ATR and its initialization.
- IBS when high equals low.
- SMA/EMA and rising-trend conditions.
- Prior highs/lows and rolling range.
- Relative volume and rolling median turnover.
- Cross-sectional ranks and breadth under missing symbols.
- NaN/inf behavior and insufficient history.

Document exact formulas and library conventions. Independent checks should not simply call the same implementation twice.

## 6. Execution and portfolio accounting
Model:
- Next eligible session, open gaps, slippage, fees, partial fills if relevant, and unavailable/locked prices if the data supports them.
- Applicable brokerage, STT, exchange transaction charges, GST, stamp duty, SEBI charges and other relevant levies, with a dated fee schedule or explicit assumption.
- Integer quantities for ordinary cash equities.
- Available cash, capital allocation, risk per trade, max positions, per-stock cap, duplicate-entry prevention, re-entry rules, and concurrent signals.
- Corporate actions and holdings adjustments consistently.

Reconciliation assertions:
- Closing cash = opening cash + signed cash flows - costs.
- Equity = closing cash + marked-to-market value of open positions.
- Trade-ledger realized P&L + open-position unrealized P&L - applicable costs reconcile to equity change, with documented treatment of dividends/corporate actions.
- No negative cash or exposure-limit breach unless the strategy explicitly permits it.
- Each order has a unique ID, signal timestamp, fill timestamp, quantity, fill price, costs, and reason.

## 7. Costs and stress tests
Show gross and net metrics. Explain source/rationale for fees and slippage; never call a guessed cost schedule verified.
At minimum compare:
- Base documented costs.
- 1.5× base costs.
- 2× base costs.
- A one-session later entry or an equivalent adverse execution stress.
- Higher slippage/liquidity stress where relevant.

A strategy that only works under optimistic costs is not robust.

## 8. Test design and anti-overfitting
- Use chronological development, validation, and final holdout partitions.
- The final holdout is used only after rules and parameters are frozen.
- If holdout results inform a change, that period is now development data; obtain a new untouched period or state that independent validation is unavailable.
- Use walk-forward tests where appropriate.
- Record all experiments, parameter combinations, rejected configurations, and selection criteria.
- Do not optimize to a target metric or present only the best result.
- Report performance by year, regime, symbol, engine, and concentration of P&L where sample sizes permit.
- Compare against a suitable benchmark using identical dates and clearly stated exposure/capital conventions.
- Disclose multiple-testing and selection bias.
- Account for overlapping positions and shared portfolio capital; never treat each signal as an independent fully funded trade.

## 9. Required report
Report:
- Run ID, code commit/version, config hash, data source and date range.
- Universe definition, exclusions, data quality, warm-up, partitions.
- Entry/exit and execution assumptions.
- Capital, sizing, max positions, fees and slippage.
- Trade count, turnover, market exposure, gross/net P&L, CAGR, max drawdown and recovery, profit factor, expectancy, win rate, average win/loss, Sharpe/Sortino conventions, annual results, benchmark results, and cost sensitivity.
- Trade ledger, order ledger, daily equity curve, rejected-signal log, warnings and exclusions.
- Confidence/uncertainty limitations; do not imply that historical results predict future performance.

All metrics must be calculated from saved artifacts. Include definitions and formula conventions. Never hardcode headline results.

## 10. Minimum automated test suite
1. Indicator hand-checks and edge cases.
2. No-lookahead/temporal perturbation tests.
3. Next-open fill test.
4. Gap-through-stop test.
5. Same-bar stop-and-target ambiguity test.
6. Fees and slippage arithmetic tests.
7. Position-sizing and allocation-limit tests.
8. Cash/equity/P&L reconciliation tests.
9. Missing-data failure and synthetic-mode labelling tests.
10. Determinism test for identical input/configuration.
11. Regression tests for every fixed bug.
12. Metric calculation from a small known synthetic trade ledger.
13. Test that current-universe data is not silently treated as point-in-time history.
14. Test that holdout data is not used by optimization code.

## 11. Acceptance labels
- `INVALID`: critical logic, temporal, execution, or accounting error exists.
- `INCOMPLETE`: data, specification, or required evidence is missing.
- `VALID_HISTORICAL_SIMULATION`: stated correctness checks pass; this is not a profitability guarantee.
- `ROBUSTNESS_NOT_ESTABLISHED`: basic simulation passes but independent/sensitivity evidence is insufficient.
- `RESEARCH_CANDIDATE`: initial holdout and robustness evidence justify further paper trading; live profitability remains unproven.

Never use `PROVEN_PROFITABLE` or `GUARANTEED_PROFITABLE`.

## 12. Run manifest
Save a machine-readable manifest for each run containing run ID, timestamp, git commit, config hash, strategy version, data version/source, date range, universe, costs, fill model, partitions, commands, test outcomes, output artifact paths, and limitations.
