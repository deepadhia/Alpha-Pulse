"""Tests for the causal leader-score volume ratio (LISTING_LEADER_SCORE_CAUSAL_VOLUME)."""
import unittest

import pandas as pd

import listing_day_breakout_scanner as lscan


def make_df(vols):
    return pd.DataFrame({"VOLUME": [float(v) for v in vols]})


class TestCausalVolumeRatio(unittest.TestCase):
    def test_ignores_todays_forming_bar(self):
        base = [1000, 900, 100, 100, 100, 100, 400]            # last completed = 400
        a = lscan._causal_volume_ratio(make_df(base + [5]))      # today's bar: tiny so far
        b = lscan._causal_volume_ratio(make_df(base + [5_000_000]))
        self.assertEqual(a, b)

    def test_value_matches_replay_rule(self):
        # ref = rows before the last completed bar; baseline = Day 2 onwards, last 10 bars
        vols = [1000, 900, 100, 100, 100, 100, 400, 7]
        ref = vols[:-2]                                          # 1000, 900, 100, 100, 100, 100
        expected = 400 / (sum(ref[2:]) / len(ref[2:]))           # 400 / 100
        self.assertAlmostEqual(lscan._causal_volume_ratio(make_df(vols)), expected)
        self.assertAlmostEqual(expected, 4.0)

    def test_two_bar_baseline_uses_day_one(self):
        # ref has exactly 2 rows -> baseline is Day 1 volume
        vols = [1000, 500, 250, 9]                               # ref=[1000,500]; v=250
        self.assertAlmostEqual(lscan._causal_volume_ratio(make_df(vols)), 250 / 500)

    def test_short_history_is_ratio_one(self):
        self.assertAlmostEqual(lscan._causal_volume_ratio(make_df([1000, 700, 3])), 1.0)  # ref has 1 row
        self.assertAlmostEqual(lscan._causal_volume_ratio(make_df([700, 3])), 1.0)        # ref empty

    def test_unusable_input_returns_none(self):
        self.assertIsNone(lscan._causal_volume_ratio(None))
        self.assertIsNone(lscan._causal_volume_ratio(make_df([5])))
        self.assertIsNone(lscan._causal_volume_ratio(pd.DataFrame({"X": [1, 2, 3]})))

    def test_resting_price_lifts_only_when_trigger_reached(self):
        f = lscan._resting_score_price
        # high reached +1.5% trigger (101.5): score sees >= trigger even if the scan price was +0.4%
        self.assertAlmostEqual(f(100.4, 101.8, 100.0, 1.5), 101.5)
        # scan price already above the trigger: unchanged
        self.assertAlmostEqual(f(103.0, 103.2, 100.0, 1.5), 103.0)
        # day high never reached the trigger: no resting fill, price unchanged
        self.assertAlmostEqual(f(100.4, 100.9, 100.0, 1.5), 100.4)
        # bad inputs never raise
        self.assertEqual(f(100.4, None, 100.0, 1.5), 100.4)

    def test_resting_price_changes_leader_score_term(self):
        # strength term: +2 at >= high*1.01, +1 at >= high, 0 below
        legacy = lscan._leader_score(0.4, 0.3, 1.5, 100.4, 100.0, 15.0)
        lifted = lscan._leader_score(0.4, 0.3, 1.5, lscan._resting_score_price(100.4, 101.8, 100.0, 1.5), 100.0, 15.0)
        self.assertEqual(lifted - legacy, 1)

    def test_default_is_off(self):
        # Shipped default must keep legacy behaviour until the owner flips the variable.
        import os
        if "LISTING_LEADER_SCORE_CAUSAL_VOLUME" not in os.environ:
            self.assertFalse(lscan.LISTING_LEADER_SCORE_CAUSAL_VOLUME)

    def test_leader_score_function_unchanged(self):
        # Same inputs, same score: the flag only changes which ratio is passed in.
        self.assertEqual(lscan._leader_score(1.0, 2.0, 1.8, 102.0, 100.0, 15.0), 8)
        self.assertEqual(lscan._leader_score(1.0, 0.4, 1.8, 102.0, 100.0, 15.0), 6)


if __name__ == "__main__":
    unittest.main()
