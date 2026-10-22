# -*- coding: utf-8 -*-
"""Tests for core/setup3.py (research setup S3: late breakout of the post-listing high).  Synthetic data only; nothing touches MongoDB.

Synthetic stock: bar 0 listing day (high 100); bars 1-69 drift at 90-95 with a pivot high of 110 at bar 20 (the post-listing high);
bars 70-79 base at 104-106 (closes 105); bar 80 breakout (open 108, high 113 > trigger 110 x 1.015 = 111.65).  Age = calendar days, daily bars.
"""
import os
os.environ.setdefault("DISABLE_TELEGRAM", "1")
os.environ.setdefault("TESTING", "1")

import unittest

import numpy as np
import pandas as pd

from core.honest_backtest import Params, _Bars, _listing_context
from core.setup3 import Setup3Params, _prepare, pivot_arrays, setup3_signal, setup3_state

LISTING = dict(listing_day_high=100.0, listing_day_low=90.0, listing_date=pd.Timestamp("2026-01-01"))


def mk_df(n=90, pivot_bar=20, breakout=80, open_=108.0, high=113.0, vol=1e6):
    dates = pd.date_range("2026-01-01", periods=n, freq="D")
    rows = []
    for i, d in enumerate(dates):
        if i == 0:
            o, h, l, c, v = 95.0, 100.0, 90.0, 98.0, 3e6
        elif i < 70:
            hi = 110.0 if i == pivot_bar else 96.0
            o, h, l, c, v = 92.0, hi, 90.0, 94.0, vol
        elif i < breakout:
            o, h, l, c, v = 105.0, 106.0, 104.0, 105.0, vol
        elif i == breakout:
            o, h, l, c, v = open_, high, 107.0, 112.0, 4e6
        else:
            o, h, l, c, v = 112.0, 114.0, 111.0, 113.0, 2e6
        rows.append(dict(DATE=d, OPEN=o, HIGH=h, LOW=l, CLOSE=c, VOLUME=v))
    return pd.DataFrame(rows)


def parts(df):
    b = _Bars.of(df)
    ld_high, _, list_date = _listing_context(df, LISTING)
    pv, pi = pivot_arrays(b)
    return b, _prepare(df), pv, pi, ld_high, list_date


def state(df, i=80, **kw):
    b, pre, pv, pi, lh, ld = parts(df)
    return setup3_state(b, pre, pv, pi, i, lh, ld, Params(), Setup3Params(**kw))


def sig(df, i=80):
    b, pre, pv, pi, lh, ld = parts(df)
    return setup3_signal(df, b, pre, pv, pi, i, lh, ld, Params(), Setup3Params(), slip_pct=0.3)


class TestS3(unittest.TestCase):
    def test_pivot_is_the_highest_high_since_listing_and_excludes_bar_i(self):
        b, pre, pv, pi, *_ = parts(mk_df())
        self.assertAlmostEqual(pv[80], 110.0)
        self.assertEqual(pi[80], 20)
        self.assertAlmostEqual(pv[21], 110.0)
        self.assertAlmostEqual(pv[20], 100.0)          # bar 20's own high is not known at its open

    def test_fills_at_trigger_with_slippage_and_classifies_long_base(self):
        s = sig(mk_df())
        self.assertIsNotNone(s)
        self.assertAlmostEqual(s["entry"], 110.0 * 1.015, places=6)
        self.assertAlmostEqual(s["fill"], 110.0 * 1.015 * 1.003, places=6)
        self.assertEqual(s["klass"], "LONG_BASE")
        self.assertEqual(s["sess_old"], 60)

    def test_short_base_when_pivot_is_recent(self):
        self.assertEqual(state(mk_df(pivot_bar=60))["klass"], "SHORT_BASE")

    def test_pivot_too_recent_is_a_run_not_a_base(self):
        self.assertIsNone(state(mk_df(pivot_bar=75)))

    def test_age_window(self):
        self.assertIsNone(state(mk_df(), i=55))        # age 55 < 61
        self.assertIsNone(state(mk_df(), i=80, max_age_days=70))   # age 80 > the 70-day cap

    def test_prior_close_must_be_within_10pct_of_pivot(self):
        df = mk_df()
        df.loc[79, "CLOSE"] = 97.0                      # 12% under 110
        self.assertIsNone(state(df))

    def test_prior_bar_volume_floor(self):
        df = mk_df()
        df.loc[79, "VOLUME"] = 1e5
        self.assertIsNone(state(df))

    def test_no_cross_and_gap_above_limit_do_not_fill(self):
        self.assertIsNone(sig(mk_df(high=111.0)))
        self.assertIsNone(sig(mk_df(open_=113.0, high=115.0)))   # open above 110 x 1.025 = 112.75

    def test_state_ignores_bar_i_close_volume_low_and_all_later_bars(self):
        a = state(mk_df())
        df = mk_df()
        df.loc[80, ["CLOSE", "VOLUME", "LOW"]] = [50.0, 1.0, 1.0]
        df.loc[81:, ["HIGH", "CLOSE", "VOLUME"]] = [500.0, 400.0, 1.0]
        b = state(df)
        self.assertEqual({k: a[k] for k in ("pivot", "trig", "klass", "prev_close", "turn")}, {k: b[k] for k in ("pivot", "trig", "klass", "prev_close", "turn")})


if __name__ == "__main__":
    unittest.main()
