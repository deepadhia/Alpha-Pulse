# Review outputs, 2026-10-13

Raw, unedited outputs of the review of the 2026-10-12 audit (`../2026-10-12/`).  Candle data is the same MongoDB cache
(848 symbols with candles to 2026-10-12, 871 listing records, loaded read-only).  Costs assumed: 1.0% round trip.
Findings and conclusions are in `LEARNINGS.md` sections 7-11 and `HARDENING_REPORT.md` section 11.

| File | What it is | Produced by |
|---|---|---|
| `00_intraday_proxy_and_dropped_trades.txt` | (0) control: audit engine as shipped, N=36 listing / N=79 all, must match the 2026-10-12 report; (1) trades the audit engine silently drops because they are still open at the end of the data (KSHINTL, +183%); (2) listing breakouts re-run with intraday-crossing fills, variants P1-P7 (fill slippage 0 / +1 / +2%, body gate, no volume gate, age >= 5d), with year split and closed-only figures; (3) payoff profile, top trades, 2026 trade list | `research_intraday_proxy.py --cache data.pkl` |
| `01_live_book_parity.txt` | (A) every MongoDB position vs what the honest engine does for that symbol (age at stored entry date, engine signals and trades); (B) fill extension of native entries over the listing high; (C) positions entered in the last 30 days; plus price paths with the live-gate quantities for MILKYMIST, SHUKRAPHAR, MVELECTRO, AUGMONT | `research_live_parity.py --cache data.pkl --days 30 --paths ...` |

Headline: the audit's -0.21% net / account -3% was a pessimistic corner.  With intraday-crossing fills the listing engine
is +2.45% net per trade at a +1.5% fill and about -1% at a +2% worse fill; native fills average +3.2% above the listing
high, so realistic net is about 0%.  Confidence intervals include zero.  The fill price is the edge.

Caveats that apply to everything here: the 60-minute hold, the 2.5% pullback ejection and the true trigger time are not
modelled (need intraday bars); end-of-day volume is generous versus volume at trigger time; costs are assumed; the
`--paths` and parity sections read the live database on the date shown (2026-10-13) and will differ on later runs.

These files are a dated snapshot.  Re-run the scripts to refresh them; do not edit them by hand.
