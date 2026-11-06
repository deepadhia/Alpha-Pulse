# -*- coding: utf-8 -*-
"""
core/intraday_volume.py

Time-of-day normalisation of live (partial-day) volume, 2026-11-06.

Why: the live scanners compare the volume traded SO FAR today with an average FULL-day volume (a 1.5x surge gate, a 150k-share floor).  Early in the
session that compares a fraction of a day with a whole day: at 10:45 IST the typical IPO session (days 5-60 after listing) has traded only 35% of its
day volume, at 09:45 only 16%.  A genuine 1.5x day therefore looks like 0.5x at 10:45 and is rejected; the gate passes only late in the day or on an
extreme day.  The same bias hits the absolute floors.

What: `cum_volume_fraction(now_ist)` = the share of the day's volume that a typical IPO session has traded by that clock time.  The table is the MEDIAN
cumulative volume share by minute over 23,710 sessions (days 5-60 after listing, full 1-minute coverage, 848 IPOs, data_minute/ from fetch_minute_bars.py,
2022-2026), sampled every 15 minutes; values in between are interpolated.  It is a measured profile, not a tuned threshold.  Expected volume so far =
average full-day volume x fraction; projected day volume = volume so far / fraction.  The fraction is floored at MIN_FRACTION (a projection from the first
minutes is noise), which makes the early-morning test stricter, never looser.  After 15:30 the fraction is 1.0 and nothing changes.

Spread: the 25th-75th percentile band at 10:45 is 0.24-0.47, so a single day can sit well away from the median; this removes the systematic bias, it does
not make the early-day reading exact.
"""

from datetime import datetime
from typing import Optional

# minutes after 09:15 IST -> median cumulative share of the day's volume (measured, see module docstring)
PROFILE_STEP_MIN = 15
PROFILE = [0.010, 0.098, 0.163, 0.220, 0.269, 0.313, 0.353, 0.391, 0.427, 0.462, 0.493, 0.525, 0.555, 0.584, 0.611, 0.639, 0.664, 0.690,
           0.716, 0.743, 0.770, 0.799, 0.832, 0.868, 0.928, 1.000]        # index 25 = 15:30
MIN_FRACTION = 0.15                                                       # about 09:45 IST: earlier readings are floored to this
SESSION_OPEN_MIN = 9 * 60 + 15
SESSION_CLOSE_MIN = 15 * 60 + 30


def cum_volume_fraction(now_ist: Optional[datetime]) -> float:
    """Share (MIN_FRACTION..1.0) of a typical session's volume traded by `now_ist` (naive IST).  None or any time from 15:30 on gives 1.0."""
    if now_ist is None:
        return 1.0
    m = now_ist.hour * 60 + now_ist.minute + now_ist.second / 60.0
    if m >= SESSION_CLOSE_MIN:
        return 1.0
    if m <= SESSION_OPEN_MIN:
        return MIN_FRACTION
    x = (m - SESSION_OPEN_MIN) / PROFILE_STEP_MIN
    i = int(x)
    frac = PROFILE[i] + (PROFILE[i + 1] - PROFILE[i]) * (x - i)
    return max(MIN_FRACTION, min(1.0, frac))
