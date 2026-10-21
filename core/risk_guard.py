# -*- coding: utf-8 -*-
"""
core/risk_guard.py

Account-level kill-switch.  The strategy has no demonstrated edge after costs, so the first job of
the live system is to bound the damage if that is true.  Pure functions, advisory: the order sheet
refuses to list NEW entries while the guard is in HALT; exits and stops keep working.

Calibration (live-faithful simulation, ~79 trades, Rs 1 lakh, 1/7 weights):
  * simulated max drawdown          : about 12% (trade-sequence) to 21% (mark-to-market)
  * longest losing streak           : 10 trades; random-sequence 95th percentile is 13
So a consecutive-loss rule would trip on normal variance and is only a late, loose backstop here;
the drawdown limits are the operative control.  All thresholds are user-tunable.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Sequence

OK = "OK"
WARN = "WARN"
HALT = "HALT"


@dataclass(frozen=True)
class GuardConfig:
    warn_drawdown_pct: float = 12.0        # inside the simulated normal range: be careful
    halt_drawdown_pct: float = 20.0        # at/over the simulated worst case: stop opening trades
    max_consecutive_losses: int = 13       # random-sequence 95th percentile at a 37% win rate
    daily_loss_limit_pct: float = 6.0      # NAV fall today; stops new entries for the day


@dataclass
class GuardStatus:
    level: str = OK
    halt_new_entries: bool = False
    drawdown_pct: float = 0.0
    consecutive_losses: int = 0
    reasons: List[str] = field(default_factory=list)


def consecutive_losses(net_pcts: Sequence[float]) -> int:
    """Length of the current losing streak at the end of a chronological list of trade results."""
    n = 0
    for x in reversed(list(net_pcts)):
        if x <= 0:
            n += 1
        else:
            break
    return n


def evaluate(current_nav: float, peak_nav: float, closed_net_pcts: Optional[Sequence[float]] = None,
             day_change_pct: Optional[float] = None, cfg: GuardConfig = GuardConfig()) -> GuardStatus:
    st = GuardStatus()
    if peak_nav and peak_nav > 0 and current_nav is not None:
        st.drawdown_pct = max(0.0, (1.0 - current_nav / peak_nav) * 100.0)
    st.consecutive_losses = consecutive_losses(closed_net_pcts or [])

    if st.drawdown_pct >= cfg.halt_drawdown_pct:
        st.level, st.halt_new_entries = HALT, True
        st.reasons.append(f"drawdown {st.drawdown_pct:.1f}% >= halt limit {cfg.halt_drawdown_pct:.0f}%")
    elif st.drawdown_pct >= cfg.warn_drawdown_pct:
        st.level = WARN
        st.reasons.append(f"drawdown {st.drawdown_pct:.1f}% >= warn level {cfg.warn_drawdown_pct:.0f}%")

    if st.consecutive_losses >= cfg.max_consecutive_losses:
        st.level, st.halt_new_entries = HALT, True
        st.reasons.append(f"{st.consecutive_losses} consecutive losing trades >= {cfg.max_consecutive_losses}")

    if day_change_pct is not None and day_change_pct <= -cfg.daily_loss_limit_pct:
        st.level, st.halt_new_entries = HALT, True
        st.reasons.append(f"today's NAV change {day_change_pct:.1f}% <= -{cfg.daily_loss_limit_pct:.0f}%")
    return st
