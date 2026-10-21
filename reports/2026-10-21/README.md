# Research outputs, 2026-10-21: second setup S2 (pivot breakout below the listing high)

Outcome: **S2 fails its pre-registered bar** (146 trades, +0.41% net per trade, 90% CI -2.6..+3.9, PF 1.07 incl. open / 1.08 closed only / 0.58 ex-top-3; negative in 2025 and 2026;
adding it to policy P lowers the account replay).  Findings and the verdict are in `LEARNINGS.md` section 18.  Pre-registration, definitions and the adoption bar are in the docstring of
`research_setup2.py`.  Engine: `core/setup2.py` (tests: `test_setup2.py`).  Data: `data.pkl` (candles to 2026-10-15), 676 of 858 symbols usable after the hygiene filter.

| File | What it is |
|---|---|
| `setup2_output.txt` | Full console output: harness check (policy P reproduces 228 trades, PF 2.43 / 1.71 / 1.91), primary test, cells H1-H3, slippage 1.0% stress, random-day control, overlap with P, capture, order load, account simulations, the bar, and a post-hoc descriptive section 6 |
| `setup2_trades_slip0.3.csv` | S2 trades (primary, resting fill +0.3%) |
| `setup2_trades_slip1.0.csv` | S2 trades under 1.0% slippage |
| `setup2_control_trades.csv` | Random eligible-day control trades (10 draws) |
| `setup2_policyP_reference_trades.csv` | Policy P trades from the same run (causal resting, unchanged) |

Reproduce: `.venv\Scripts\python.exe research_setup2.py --cache data.pkl --out-dir reports/2026-10-21` (about 40 seconds).  Costs: flat 1.0% round trip for trade statistics; the delivery cost model with
0.3% exit slippage inside the account simulation.  Evidence tier: REPLAY of the honest engine.  Section 6 of the output (peak run-up within 20 sessions) was added after the first run and is descriptive only.

## Anchor lock-in study (added later the same day; LEARNINGS 19)

| File | What it is |
|---|---|
| `anchor_unlock_output.txt` | Stage 1 (U30 and U90 event study, event-time paths) and Stage 2 (policy-P near-unlock entries) from `research_anchor_unlock.py` |
| `anchor_unlock_U30.csv`, `anchor_unlock_U90.csv` | Per-stock event and placebo abnormal returns and D |
| `anchor_unlock_audit_output.txt` | Post-hoc audit: age-profile null D(K), two-sided U90 placebo, robustness (from `research_anchor_unlock_audit.py`) |

Evidence tier: event-study replay with NOMINAL unlock dates (listing + 28 / + 88 days).  Result: a small date-linked dip (about -1.3 to -1.7pp per 10 sessions), below costs; no rule adopted.
