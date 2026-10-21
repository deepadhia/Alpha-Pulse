# -*- coding: utf-8 -*-
"""
core/exit_rules.py

THE single definition of the exit decision and the trailing-stop update.

Production (`streamlined_ipo_scanner.stop_loss_update_scan`), the honest backtest
(`core.honest_backtest`) and the tests all call these functions, so the rules cannot drift
apart again.  Previously the backtest and several "regression tests" each carried their own
copy of this logic, and the copies had diverged (calendar days vs bars, intraday highs vs
closes, entry-risk trail vs 10% trail).

Pure functions: no DB, no network, no clock.  The caller supplies days held, trading sessions
and, lazily, post-entry volumes.
"""

from dataclasses import dataclass
from typing import Callable, Optional, Sequence


@dataclass(frozen=True)
class ExitParams:
    """Defaults mirror the live constants (asserted equal in test_exit_rules.py)."""
    winner_runup_pct: float = 15.0
    dead_money_days_ipo: int = 14
    dead_money_runup_ipo: float = 4.0
    dead_money_days_consol: int = 14
    dead_money_runup_consol: float = 5.0
    dead_money_days_other: int = 30
    velocity_days: int = 14
    velocity_sessions: int = 10
    stagnant_days: int = 40
    stagnant_runup_pct: float = 8.0
    long_underwater_days: int = 60
    long_underwater_floor: float = 0.92       # exit if price < entry * 0.92 after 60 days
    minor_loser_floor: float = 0.95           # exit if price < entry * 0.95 after dead_money_days_other
    vol_min_days_ipo: int = 15
    vol_min_days_consol: int = 10
    vol_flat_pnl_low: float = -3.0
    vol_flat_pnl_high: float = 5.0
    vol_max_runup: float = 8.0
    vol_ratio_threshold: float = 0.45
    vol_abs_floor: float = 50_000.0
    min_pnl_for_trail: float = 4.0            # consolidation grades
    listing_trail_start_pnl: float = 3.0      # LISTING_BREAKOUT grade
    min_trail_move_pct: float = 1.0
    winner_min_lock_mult: float = 1.05        # winners keep at least entry * 1.05
    winner_cushion_mult: float = 0.80         # ... or 20% below the peak
    trail_pct_override: Optional[float] = None  # research only: replaces the per-grade trail distance


GRADE_STOP_PCTS = {
    "A+": 0.05, "A": 0.07, "B": 0.10, "C": 0.12, "D": 0.15,
    "LISTING_BREAKOUT": 0.10,
}
DEFAULT_STOP_PCT = 0.10


def grade_stop_pct(grade) -> float:
    """Trailing distance below price for a grade (same table as calculate_grade_based_stop_loss)."""
    return GRADE_STOP_PCTS.get(grade, DEFAULT_STOP_PCT)


@dataclass(frozen=True)
class ExitDecision:
    exit_reason: Optional[str]
    new_trailing: float
    pnl_pct: float
    new_max_runup: float
    new_max_drawdown: float
    is_winner: bool
    trailing_raised: bool


def evaluate_volume_exhaustion(post_entry_volumes: Optional[Sequence[float]], params: ExitParams = ExitParams()):
    """
    `post_entry_volumes`: daily volumes AFTER the entry day (entry day excluded), oldest first.
    Returns (exhausted: bool, ratio: Optional[float]).
    """
    if post_entry_volumes is None or len(post_entry_volumes) < 16:
        return False, None
    vols = [float(v) for v in post_entry_volumes]
    baseline = sum(vols[:11]) / 11.0
    recent = sum(vols[-5:]) / 5.0
    if baseline < params.vol_abs_floor or baseline <= 0:
        return False, None
    ratio = recent / baseline
    return ratio < params.vol_ratio_threshold, ratio


def next_trailing(entry_price: float, old_trailing: float, current_price: float, pnl_pct: float,
                  new_max_runup: float, grade_trail, params: ExitParams = ExitParams()) -> float:
    """Ratcheting trailing stop. Never lowers the stop; ignores sub-threshold improvements."""
    if new_max_runup >= params.winner_runup_pct:
        peak_price = entry_price * (1.0 + new_max_runup / 100.0)
        candidate = max(entry_price * params.winner_min_lock_mult, peak_price * params.winner_cushion_mult)
    else:
        start = params.listing_trail_start_pnl if grade_trail == "LISTING_BREAKOUT" else params.min_pnl_for_trail
        if pnl_pct < start:
            return old_trailing
        trail_pct = params.trail_pct_override if params.trail_pct_override is not None else grade_stop_pct(grade_trail)
        candidate = current_price * (1.0 - trail_pct)
    min_move = entry_price * (params.min_trail_move_pct / 100.0)
    if candidate > old_trailing and (candidate - old_trailing) >= min_move:
        return candidate
    return old_trailing


def evaluate_position(entry_price: float, old_trailing: float, current_price: float,
                      days_held: int, trading_sessions: int,
                      prev_max_runup: float, prev_max_drawdown: float,
                      grade_exit, grade_trail,
                      volume_provider: Optional[Callable[[], Optional[Sequence[float]]]] = None,
                      params: ExitParams = ExitParams(), check_stop: bool = True) -> ExitDecision:
    """
    Decide whether to exit an ACTIVE / PAPER_ONLY position and, if not, where the stop goes.

    check_stop=False lets a caller that models stop fills itself (the backtest) reuse every
    other rule; the live scanner always uses check_stop=True (price at or below trailing stop).
    `grade_exit` and `grade_trail` are separate on purpose: production reads the grade with
    different defaults in the exit path ("N/A") and the trailing path ("C"); this reproduces
    that exactly rather than "fixing" it silently.
    """
    pnl = (current_price - entry_price) / entry_price * 100.0
    new_max_runup = max(float(prev_max_runup), pnl)
    new_max_drawdown = min(float(prev_max_drawdown), pnl)
    winner = new_max_runup >= params.winner_runup_pct

    reason = None
    if check_stop and current_price <= old_trailing:
        reason = "Stop Loss"
    elif not winner:
        if grade_exit == "LISTING_BREAKOUT":
            if days_held >= params.dead_money_days_ipo and new_max_runup < params.dead_money_runup_ipo:
                reason = f"Time Stop - IPO Dead Money (No momentum after {params.dead_money_days_ipo} days)"
        else:
            if days_held >= params.dead_money_days_consol and new_max_runup < params.dead_money_runup_consol:
                reason = f"Time Stop - Consolidation Dead Money (No momentum after {params.dead_money_days_consol} days)"

        if not reason:
            if days_held > params.dead_money_days_other and current_price < entry_price * params.minor_loser_floor:
                reason = f"Time Stop -5% (Underperforming after {params.dead_money_days_other} days)"
            elif days_held > params.long_underwater_days and current_price < entry_price * params.long_underwater_floor:
                reason = "Time Stop -8% (Underperforming after 60 days)"

        if not reason and (days_held >= params.velocity_days or trading_sessions >= params.velocity_sessions) and pnl <= 0.0:
            reason = (f"Time Stop - Dead Money (14-Day Velocity Gate, PnL {pnl:+.1f}%, "
                      f"Peak +{new_max_runup:.1f}%)")

        if not reason and days_held >= params.stagnant_days:
            if pnl <= 0.0 and new_max_runup < params.stagnant_runup_pct:
                reason = (f"Time Stop - Stagnant Position (40d, no momentum: peak +{new_max_runup:.1f}%, "
                          f"PnL {pnl:+.1f}%)")

    # Volume exhaustion: flat, never-moved positions whose volume has dried up.
    if not reason and not winner:
        vol_min_days = params.vol_min_days_ipo if grade_exit == "LISTING_BREAKOUT" else params.vol_min_days_consol
        if (days_held >= vol_min_days
                and params.vol_flat_pnl_low <= pnl < params.vol_flat_pnl_high
                and new_max_runup < params.vol_max_runup
                and volume_provider is not None):
            exhausted, ratio = evaluate_volume_exhaustion(volume_provider(), params)
            if exhausted:
                reason = (f"Volume Exhaustion - Dead volume "
                          f"(ratio: {ratio:.2f}, pnl: {pnl:+.1f}%, days: {days_held})")

    if reason:
        return ExitDecision(reason, float(old_trailing), pnl, new_max_runup, new_max_drawdown, winner, False)

    new_trailing = next_trailing(entry_price, float(old_trailing), current_price, pnl,
                                 new_max_runup, grade_trail, params)
    return ExitDecision(None, new_trailing, pnl, new_max_runup, new_max_drawdown, winner,
                        new_trailing > float(old_trailing))
