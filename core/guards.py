# -*- coding: utf-8 -*-
"""
core/guards.py

Safety classifiers shared by the live scanners.  Pure functions, no I/O.

Why: the old Corporate Action Guard treated ANY drop of 25% or more against the last stored
price as a "stock split or data error", sent an alert and skipped the position entirely.  That
froze the stop-loss exactly when a real crash happened (a multi-day gap after an outage, a
20%-band stock falling two days in a row, a results or fraud shock).  Failing safe means the
opposite default: only pause when the move looks like a split/bonus ratio, otherwise run the
normal exit logic.
"""

from dataclasses import dataclass
from typing import Optional

# Price ratio signatures of common corporate actions (new price ~= old price / factor).
#   2   : 1:1 bonus, or 10->5 split
#   3   : 2:1 bonus
#   4   : 3:1 bonus, 10->2.5 split
#   5   : 10->2 split
#   10  : 10->1 split
#   20  : 10->0.5 style splits (rare)
PRICE_ONLY_FACTORS = (2.0, 3.0, 4.0, 5.0, 10.0, 20.0)
# 1:2 bonus (ratio 0.667) and 2.5 are easy to hit by chance after a multi-day crash, so they
# are only accepted when volume jumped by roughly the same factor.
VOLUME_CONFIRMED_FACTORS = (1.5, 2.5)

NORMAL = "NORMAL"
LIKELY_CORPORATE_ACTION = "LIKELY_CORPORATE_ACTION"
GENUINE_DROP = "GENUINE_DROP"


@dataclass(frozen=True)
class DropAssessment:
    kind: str
    drop_pct: float
    factor: Optional[float]
    reason: str


def assess_price_drop(prev_price, new_price, prev_avg_volume=None, new_volume=None,
                      threshold_pct: float = -25.0, tolerance: float = 0.03,
                      volume_confirm_ratio: float = 0.6) -> DropAssessment:
    """
    Classify a price move against the previous reference price.

    NORMAL                  : drop is shallower than `threshold_pct` (or inputs unusable).
    LIKELY_CORPORATE_ACTION : the ratio matches a split/bonus factor within `tolerance`.
                              Caller should pause the position and alert for manual check.
    GENUINE_DROP            : a large drop that does NOT look like a split.  Caller must keep
                              running normal exit logic (the stop should fire) and alert loudly.
    """
    try:
        prev = float(prev_price)
        new = float(new_price)
    except (TypeError, ValueError):
        return DropAssessment(NORMAL, 0.0, None, "unusable prices")
    if prev <= 0 or new <= 0:
        return DropAssessment(NORMAL, 0.0, None, "non-positive price")

    ratio = new / prev
    drop_pct = (ratio - 1.0) * 100.0
    if drop_pct > threshold_pct:
        return DropAssessment(NORMAL, drop_pct, None, "within normal range")

    have_volume = (prev_avg_volume is not None and new_volume is not None
                   and float(prev_avg_volume) > 0 and float(new_volume) > 0)

    for factor in PRICE_ONLY_FACTORS:
        if abs(ratio - 1.0 / factor) / (1.0 / factor) <= tolerance:
            return DropAssessment(
                LIKELY_CORPORATE_ACTION, drop_pct, factor,
                f"price ratio {ratio:.3f} matches a {factor:g}:1 split/bonus signature")

    if have_volume:
        for factor in VOLUME_CONFIRMED_FACTORS:
            if abs(ratio - 1.0 / factor) / (1.0 / factor) <= tolerance:
                if float(new_volume) >= volume_confirm_ratio * factor * float(prev_avg_volume):
                    return DropAssessment(
                        LIKELY_CORPORATE_ACTION, drop_pct, factor,
                        f"price ratio {ratio:.3f} and volume jump match a {factor:g}:1 action")

    return DropAssessment(
        GENUINE_DROP, drop_pct, None,
        f"{drop_pct:.1f}% drop does not match any split/bonus ratio: treat as a real crash")
