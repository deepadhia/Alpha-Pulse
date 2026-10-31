---
trigger: glob
globs: "*.py, **/*.py"
description: "Python rules for the NSE trading research codebase: data integrity, temporal correctness, financial arithmetic, testing, and honest reporting."
---

# Python Trading Research Rules

- Read `AGENTS.md` and `BACKTEST_VALIDATION.md` before changing trading, indicator, data, execution, portfolio, or reporting code.
- Do not invent missing behavior. Inspect callers and tests.
- Use explicit types where they improve clarity; prefer pure functions for indicators and signal predicates.
- Avoid hidden global state, implicit timezone assumptions, silent exception swallowing, and silent fallback to mock data.
- Validate inputs and preserve missing-data meaning. Do not turn missing values into zero/neutral signals without a documented reason.
- Keep data acquisition, normalization, indicators, signals, order simulation, accounting, and metrics in separate layers.
- For time series, make sort order, timezone, duplicate handling, rolling-window alignment, and shift semantics explicit.
- Never use centered rolling windows or future rows in features for historical decisions.
- Signal-at-close / fill-next-open must be explicit in code and tested.
- Avoid look-ahead through `shift`, `merge`, `join`, resampling, cross-sectional ranks, universe filters, normalization, or corporate-action adjustments.
- Handle divide-by-zero, all-gain/all-loss RSI, flat prices, insufficient warm-up, NaN and infinity deliberately.
- Add a focused regression test for each bug fix.
- Do not claim tests passed unless you ran them and inspected output.
- Preserve random seeds and environment/config metadata where randomness exists.
- Use logging that records run IDs and useful diagnostics without exposing secrets.
- Do not suppress warnings broadly. Explain any suppression.
- Keep synthetic fixtures explicitly labelled and isolated from historical-data runs.
