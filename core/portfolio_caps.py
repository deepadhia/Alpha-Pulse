# -*- coding: utf-8 -*-
"""
core/portfolio_caps.py

Single source of truth for the portfolio slot caps and the equal weight they imply.

Before 2026-10-14 the caps were a soft cap of 5 plus a hard cap of 7 (weight 1/7 = 14.3% in the order sheet).
They are now ONE flat cap of 10 (weight 1/10 = 10%).  Evidence (LEARNINGS.md section 12.3-12.4, reports/2026-10-14):
with the same signals the replay showed about 5-8pp less maximum drawdown at equal or higher CAGR in three of four
fill scenarios (neutral under today's chase-style fills) because each stop-out costs about 1.2% of NAV instead of 1.7%.
It is a risk and diversification choice: it does not change the expectancy of any single trade.

The scanners read these defaults through streamlined_ipo_scanner.MAX_ACTIVE_POSITIONS / HARD_ACTIVE_POSITIONS (still
overridable with the environment variables of the same names); the order sheet reads them for its slot count and weight.
Soft cap == hard cap means there is no band in which only "pristine" setups are admitted: below the cap a signal is
ACTIVE, at or above it the signal is PAPER_ONLY.  That is exactly the policy that was simulated.

Rollback to the old behaviour: set MAX_ACTIVE_POSITIONS=5 and HARD_ACTIVE_POSITIONS=7 in the scanners' environment
(both variables) and pass --hard-cap 7 to order_sheet.py.  Positions already stored as PAPER_ONLY are not promoted.

Pure module: no imports, no I/O.
"""

DEFAULT_MAX_ACTIVE_POSITIONS = 10    # soft cap
DEFAULT_HARD_ACTIVE_POSITIONS = 10   # hard cap (equal to the soft cap: no admission band)


def equal_weight(hard_cap: int = DEFAULT_HARD_ACTIVE_POSITIONS) -> float:
    """Fraction of capital per position when every slot is filled."""
    return 1.0 / max(1, int(hard_cap))
