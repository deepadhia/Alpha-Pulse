"""Tests for core.realistic_friction (spread / impact port, tick rounding, circuit rules, limit check)."""
import math
import unittest

import numpy as np
import pandas as pd

from core import realistic_friction as RF


def bars(rows):
    return pd.DataFrame(rows, columns=["OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"])


def flat_history(n=30, px=100.0, vol=1_000_000):
    return [(px, px * 1.01, px * 0.99, px, vol)] * n


class TestSlippage(unittest.TestCase):
    def test_tiers(self):
        self.assertEqual(RF.spread_bps(2e8), 8.0)
        self.assertEqual(RF.spread_bps(5e7), 15.0)
        self.assertEqual(RF.spread_bps(2e7), 25.0)
        self.assertEqual(RF.spread_bps(7e6), 40.0)
        self.assertEqual(RF.spread_bps(3e6), 60.0)
        self.assertEqual(RF.spread_bps(1e6), 90.0)
        self.assertEqual(RF.spread_bps(None), 90.0)

    def test_hand_calculated_value(self):
        # ADT Rs 5 Cr -> 15 bps; order Rs 10k -> participation 2e-4; sigma 0.03 -> impact 0.7*0.03*sqrt(2e-4)*1e4 = 2.97 bps
        got = RF.slippage_fraction(5e7, 100.0, 1e4, 0.03)
        self.assertAlmostEqual(got * 1e4, 15.0 + 0.7 * 0.03 * math.sqrt(2e-4) * 1e4, places=6)

    def test_mult_scales_and_cap(self):
        base = RF.slippage_fraction(5e7, 100.0, 1e4, 0.03)
        self.assertAlmostEqual(RF.slippage_fraction(5e7, 100.0, 1e4, 0.03, mult=2.0), 2 * base, places=9)
        self.assertLessEqual(RF.slippage_fraction(1e5, 10.0, 1e9, 0.5), RF.MAX_FRACTION)

    def test_tick_floor_for_cheap_stock(self):
        # Rs 5 stock: half a tick = 50 bps, above the 8 bps tier
        self.assertGreaterEqual(RF.slippage_fraction(2e8, 5.0, 1e3, 0.0001) * 1e4, 50.0)

    def test_tick_rounding_against_the_trader(self):
        self.assertAlmostEqual(RF.tick_up(100.01), 100.05)
        self.assertAlmostEqual(RF.tick_down(100.04), 100.0)
        self.assertAlmostEqual(RF.tick_up(100.05), 100.05)


class TestPointInTime(unittest.TestCase):
    def test_adt_uses_only_prior_bars(self):
        df = bars(flat_history(10) + [(100, 100, 100, 100, 9_999_999_999)])
        self.assertAlmostEqual(RF.adt_before(df, 10), 100.0 * 1_000_000)      # the huge bar 10 is not seen
        self.assertIsNone(RF.adt_before(df, 2))                                # too short (2 prior sessions)
        self.assertIsNotNone(RF.adt_before(df, 3))                             # the earliest bar the engine can enter on


def trade(entry_idx, exit_idx, entry_price, exit_price, open_=False):
    return dict(entry_idx=entry_idx, exit_idx=exit_idx, entry_price=entry_price, exit_price=exit_price, open=open_)


class TestReprice(unittest.TestCase):
    def setUp(self):
        self.hist = flat_history(30)

    def test_normal_trade_costs_more_than_nothing(self):
        df = bars(self.hist + [(101.5, 103.0, 101.0, 102.5, 2_000_000), (110, 111, 109, 110, 2_000_000)])
        r = RF.reprice_trade(df, trade(30, 31, 101.5 * 1.003, 110.0), ld_high=100.0, position_value=10000.0)
        self.assertTrue(r.taken)
        self.assertLess(r.net_pct, (110.0 - 101.5) / 101.5 * 100.0)           # frictions only subtract
        self.assertGreater(r.fee_pct, 0.0)
        self.assertGreaterEqual(r.entry_fill, 101.5)
        self.assertLessEqual(r.exit_fill, 110.0)

    def test_upper_circuit_entry_is_blocked(self):
        locked = (105.0, 105.0, 105.0, 105.0, 1_000_000)                      # single price, +5% over the prior close of 100
        df = bars(self.hist + [locked, (106, 107, 105, 106, 1_000_000)])
        r = RF.reprice_trade(df, trade(30, 31, 101.5 * 1.003, 106.0), ld_high=100.0, position_value=10000.0)
        self.assertFalse(r.taken)
        self.assertEqual(r.reason, "UPPER_CIRCUIT")

    def test_fill_above_limit_does_not_happen(self):
        # reference fill already at the +2.5% limit: any spread pushes it above -> no fill
        df = bars(self.hist + [(102.5, 103.0, 102.0, 102.8, 2_000_000), (110, 111, 109, 110, 2_000_000)])
        r = RF.reprice_trade(df, trade(30, 31, 102.5 * 1.003, 110.0), ld_high=100.0, position_value=10000.0)
        self.assertFalse(r.taken)
        self.assertEqual(r.reason, "ABOVE_LIMIT")

    def test_lower_circuit_exit_is_deferred_to_next_open(self):
        down = (94.0, 94.0, 94.0, 94.0, 1_000_000)                             # locked at -6% vs the prior close of 100
        df = bars(self.hist + [(101.5, 103.0, 101.0, 102.5, 2_000_000), (100.0, 100.0, 100.0, 100.0, 2_000_000), down, (90.0, 91.0, 89.0, 90.0, 1_000_000)])
        t = trade(30, 32, 101.5 * 1.003, 94.0)
        r = RF.reprice_trade(df, t, ld_high=100.0, position_value=10000.0)
        self.assertTrue(r.taken)
        self.assertEqual(r.exit_deferred, 1)
        self.assertLess(r.exit_fill, 90.5)                                     # sold near the next session's 90 open, not at 94

    def test_open_trade_is_marked_not_dropped(self):
        df = bars(self.hist + [(101.5, 103.0, 101.0, 102.5, 2_000_000)])
        r = RF.reprice_trade(df, trade(30, 30, 101.5 * 1.003, 102.5, open_=True), ld_high=100.0, position_value=10000.0)
        self.assertTrue(r.taken)

    def test_too_small_for_one_share(self):
        df = bars(self.hist + [(101.5, 103.0, 101.0, 102.5, 2_000_000), (110, 111, 109, 110, 2_000_000)])
        r = RF.reprice_trade(df, trade(30, 31, 101.5 * 1.003, 110.0), ld_high=100.0, position_value=50.0)
        self.assertFalse(r.taken)
        self.assertEqual(r.reason, "NO_SHARES")


if __name__ == "__main__":
    unittest.main()
