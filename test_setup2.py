# -*- coding: utf-8 -*-
"""
Tests for core/setup2.py (research setup S2: pivot breakout below the listing high).  Synthetic data only; nothing touches MongoDB.

Layout of the synthetic stock (listing high 100, listing-day low 90):
  bar 0       listing day
  bars 1-9    washed-out drift (low 69, high 73, close 71)
  bars 10-19  base under a pivot: highs 77 except bar 12 (high 80 = the pivot), lows 74, closes 76
  bar 20      breakout: open 79, high 82 (trigger 80 x 1.015 = 81.2), low 78, close 81.5
  bars 21-39  +1% a day
"""
import os
os.environ.setdefault("DISABLE_TELEGRAM", "1")
os.environ.setdefault("TESTING", "1")

import unittest

import numpy as np
import pandas as pd

from core.honest_backtest import Params, _Bars, _listing_context
from core.setup2 import (Setup2Params, _prepare, classify, clean_universe, control_indices, order_days,
                         setup2_signal, setup2_state, simulate_control_entry, simulate_symbol_setup2)

LH = 100.0
LISTING = dict(listing_day_high=LH, listing_day_low=90.0, listing_date=pd.Timestamp("2026-01-01"))


def mk_df(n=40, scale=1.0, pivot_bar=12, breakout=20, open_=79.0, high=82.0, start="2026-01-01"):
    dates = pd.date_range(start, periods=n, freq="D")
    rows, prev_close = [], 98.0
    for i, d in enumerate(dates):
        if i == 0:
            o, h, l, c, v = 95.0, LH, 90.0, 98.0, 3e6
        elif i < 10:
            o, h, l, c, v = 71 * scale, 73 * scale, 69 * scale, 71 * scale, 1e6
        elif i < breakout:
            hi = 80.0 if i == pivot_bar else 77.0
            o, h, l, c, v = 75 * scale, hi * scale, 74 * scale, 76 * scale, 1e6
        elif i == breakout:
            o, h, l, c, v = open_ * scale, high * scale, 78 * scale, 81.5 * scale, 4e6
        else:
            o = prev_close
            c = prev_close * 1.01
            h, l, v = c * 1.005, o * 0.995, 2e6
        rows.append(dict(DATE=d, OPEN=o, HIGH=h, LOW=l, CLOSE=c, VOLUME=v))
        prev_close = c
    return pd.DataFrame(rows)


def sig(df, i=20, listing=LISTING, s2=Setup2Params(), p=Params(), **kw):
    b = _Bars.of(df)
    pre = _prepare(df)
    ld_high, ld_low, list_date = _listing_context(df, listing)
    return setup2_signal(df, b, pre, i, ld_high, list_date, p, s2, **kw)


class TestSignal(unittest.TestCase):
    def test_fills_at_trigger_with_slippage(self):
        s = sig(mk_df(), slip_pct=0.3)
        self.assertIsNotNone(s)
        self.assertAlmostEqual(s["pivot"], 80.0)
        self.assertAlmostEqual(s["entry"], 81.2, places=6)
        self.assertAlmostEqual(s["fill"], 81.2 * 1.003, places=6)
        self.assertEqual(s["klass"], "H3_BASE_YOUNG")
        self.assertAlmostEqual(s["stop"], s["fill"] * 0.88, places=6)       # swing low x 0.97 = 66.9 is below the 12% cap

    def test_gap_up_inside_the_limit_fills_at_the_open_and_beyond_it_does_not(self):
        s = sig(mk_df(open_=81.8, high=83.0))
        self.assertAlmostEqual(s["entry"], 81.8, places=6)
        self.assertIsNone(sig(mk_df(open_=82.6, high=84.0)))                  # open above 80 x 1.025 = 82.0

    def test_no_cross_no_signal(self):
        self.assertIsNone(sig(mk_df(high=81.0)))                              # trigger is 81.2

    def test_prior_close_must_be_more_than_10pct_below_listing_high(self):
        self.assertIsNone(sig(mk_df(scale=1.2)))                              # prior close 91.2 > 90

    def test_pivot_must_be_at_least_five_sessions_old(self):
        self.assertIsNotNone(sig(mk_df(pivot_bar=14)))                        # index 14 = i-6: allowed
        self.assertIsNone(sig(mk_df(pivot_bar=15)))                           # only four sessions old

    def test_prior_close_must_be_within_10pct_of_the_pivot(self):
        df = mk_df()
        df.loc[19, "CLOSE"] = 70.0                                            # 70 < 0.9 x 80
        self.assertIsNone(sig(df))

    def test_liquidity_gates_use_the_prior_bar_only(self):
        df = mk_df()
        df.loc[19, "VOLUME"] = 100000.0
        self.assertIsNone(sig(df))                                            # prior-bar volume below 150,000
        df = mk_df()
        df.loc[20, "VOLUME"] = 10.0                                           # same-day volume is never read
        self.assertIsNotNone(sig(df))

    def test_price_floor_applies_to_the_entry(self):
        self.assertIsNone(sig(mk_df(scale=0.3)))                              # entry about Rs 24

    def test_age_window(self):
        for ld, ok in (("2026-01-01", True), ("2026-01-10", False), ("2025-09-01", True), ("2025-06-01", False)):
            lst = dict(LISTING, listing_date=pd.Timestamp(ld))
            self.assertEqual(sig(mk_df(), listing=lst) is not None, ok, ld)

    def test_classification_partition(self):
        s2 = Setup2Params()
        self.assertEqual(classify(40, 0.50, s2), "H3_BASE_YOUNG")
        self.assertEqual(classify(61, 0.29, s2), "H1_BASE_OLD")
        self.assertEqual(classify(61, 0.30, s2), "H2_RECOVERY")
        old = dict(LISTING, listing_date=pd.Timestamp("2025-09-01"))
        self.assertEqual(sig(mk_df(), listing=old)["klass"], "H1_BASE_OLD")    # 1 - 76 / 100 = 0.24
        deep = mk_df(scale=0.8)
        deep.loc[0, ["OPEN", "HIGH", "LOW", "CLOSE"]] = [95.0, 100.0, 90.0, 98.0]
        self.assertEqual(sig(deep, listing=old)["klass"], "H2_RECOVERY")       # 1 - 60.8 / 100 = 0.39


class TestNoLookahead(unittest.TestCase):
    def test_signal_is_unchanged_by_the_rest_of_the_breakout_day_and_by_the_future(self):
        base = sig(mk_df())
        df = mk_df()
        df.loc[20, ["CLOSE", "LOW", "VOLUME"]] = [60.0, 55.0, 1.0]            # forming-candle values
        df.loc[21:, ["OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"]] = [10.0, 11.0, 9.0, 10.0, 1.0]
        alt = sig(df)
        for k in ("pivot", "entry", "fill", "stop", "klass", "dd60", "age"):
            self.assertEqual(base[k], alt[k], k)

    def test_state_ignores_bar_i_prices(self):
        df = mk_df()
        b, pre = _Bars.of(df), _prepare(df)
        ld_high, ld_low, list_date = _listing_context(df, LISTING)
        s0 = setup2_state(b, pre, 20, ld_high, list_date)
        df.loc[20, ["OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"]] = [1.0, 2.0, 0.5, 1.5, 1.0]
        b, pre = _Bars.of(df), _prepare(df)
        self.assertEqual(s0, setup2_state(b, pre, 20, ld_high, list_date))


class TestSimulation(unittest.TestCase):
    def test_trade_is_marked_open_at_the_end_of_the_data(self):
        out = simulate_symbol_setup2(mk_df(), "SYN", LISTING)
        self.assertEqual(len(out), 1)
        t = out[0]
        self.assertTrue(t["open"])
        self.assertEqual(t["entry_idx"], 20)
        self.assertEqual(t["type"], "S2_H3_BASE_YOUNG")
        self.assertAlmostEqual(t["entry_price"], 81.2 * 1.003, places=6)
        self.assertGreater(t["pnl_pct"], 0.0)                                  # +1% a day after the breakout, marked to market

    def test_close_based_stop_sells_at_the_next_open(self):
        df = mk_df(n=30)
        df.loc[22, ["OPEN", "HIGH", "LOW", "CLOSE"]] = [80.0, 80.5, 60.0, 62.0]  # closes under the stop (about 71.7)
        df.loc[23, ["OPEN", "HIGH", "LOW", "CLOSE"]] = [63.0, 64.0, 61.0, 62.0]
        df.loc[24:, ["OPEN", "HIGH", "LOW", "CLOSE"]] = [62.0, 63.0, 61.0, 62.0]
        t = simulate_symbol_setup2(df, "SYN", LISTING)[0]
        self.assertFalse(t["open"])
        self.assertEqual(t["exit_idx"], 23)
        self.assertAlmostEqual(t["exit_price"], 63.0)                          # next open, not the stop price
        self.assertIn("Stop", t["exit_reason"])

    def test_one_position_per_symbol_and_cooldown(self):
        df = mk_df(n=70)
        df.loc[22:, ["OPEN", "HIGH", "LOW", "CLOSE"]] = [60.0, 61.0, 59.0, 60.0]   # stopped out at bar 22/23
        out = simulate_symbol_setup2(df, "SYN", LISTING)
        self.assertEqual(len(out), 1)

    def test_order_days_do_not_require_the_cross(self):
        df = mk_df(high=81.0)                                                   # no cross on bar 20
        days = order_days(df, LISTING)
        self.assertIn(df["DATE"].iloc[20], days)
        self.assertEqual(simulate_symbol_setup2(df, "SYN", LISTING), [])


class TestControl(unittest.TestCase):
    def test_control_buys_the_open_without_a_trigger(self):
        df = mk_df(high=81.0)
        idx = control_indices(df, LISTING)
        self.assertIn(20, idx)
        t = simulate_control_entry(df, "SYN", LISTING, 20)
        self.assertAlmostEqual(t["entry_price"], 79.0 * 1.003, places=6)
        self.assertEqual(t["entry_idx"], 20)


class TestUniverse(unittest.TestCase):
    def test_placeholder_and_inconsistent_records_are_dropped(self):
        good = mk_df(start="2026-01-01")
        lm = {"OK": dict(LISTING), "PH": dict(LISTING, listing_date=pd.Timestamp("2026-08-17")),
              "OLD": dict(LISTING, listing_date=pd.Timestamp("2021-06-01")),
              "FAR": dict(LISTING, listing_date=pd.Timestamp("2026-03-01")),
              "BADLH": dict(LISTING, listing_day_high=200.0)}
        cm = {k: good for k in lm}
        keep, dropped = clean_universe(lm, cm)
        self.assertEqual(sorted(keep), ["OK"])
        self.assertEqual(set(dropped), {"PH", "OLD", "FAR", "BADLH"})
        self.assertEqual(dropped["PH"], "placeholder listing date")


if __name__ == "__main__":
    unittest.main()
