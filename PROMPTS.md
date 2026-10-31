# Antigravity Prompt Pack

Use these as task prompts. Keep tasks narrow; do not ask the agent to rewrite the entire system in one pass.

## 1. Verify instruction loading (read-only)
Do not modify files. Confirm whether AGENTS.md, BACKTEST_VALIDATION.md, the Python rule, and the audit-backtest skill are discoverable. Read them. Summarize five applicable constraints and cite the actual paths. Inspect the repository structure and report actual entry points, tests, data source, fill assumptions, and unknowns. Do not guess.

## 2. Baseline audit (read-only)
Use the audit-backtest skill. Do not change code or parameters. Trace one trade from input data through the final metrics. List findings by severity with exact evidence. Run only safe, relevant existing tests and report actual command/output. If no tests exist, state that.

## 3. One bug fix at a time
Fix only this finding: [paste one finding]. Before editing, identify the relevant code and write a regression test that fails before the fix. Implement the smallest correction. Run the test and relevant suite. Show the diff and report actual outcomes. Do not alter strategy rules or unrelated files.

## 4. Independent metric verification
Without changing the backtester, load the saved trade/order ledger and equity series for run [RUN_ID]. Recalculate net P&L, costs, win rate, profit factor, expectancy, drawdown and CAGR independently using documented definitions. Reconcile with the original report. If source artifacts are absent or insufficient, report INCOMPLETE instead of reconstructing fictional data.

## 5. Research design review
Do not optimize parameters. Review data provenance, point-in-time universe, corporate actions, chronology, holdout separation, execution, fees, benchmark and multiple-testing risks. State what can be concluded now, what cannot, and the cheapest next test that would resolve the largest uncertainty.

## 6. Final change review
Review the full diff. Check for unrelated edits, changed strategy thresholds, weakened tests, synthetic fallbacks, swallowed exceptions, secrets, new dependencies, unverified claims, and tests that were not actually run. Return PASS/FAIL/INCOMPLETE for each relevant check.
