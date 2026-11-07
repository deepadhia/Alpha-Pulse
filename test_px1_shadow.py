# -*- coding: utf-8 -*-
"""Tests for core/px1_shadow.py (the PAPER_ONLY X1 forward test): fixed rules, verdict thresholds, pairing and the CLI loader.  No database, no network.

Engine parity (every pair equal to research_alpha_lab.rerun_exits to 0.0pp, 230 of 230 entries of the 2022-26 replay) was verified with
data_20261107.pkl on 2026-11-07 and is not repeated here because it needs the research cache."""
import os
os.environ.setdefault("DISABLE_TELEGRAM", "1")
os.environ.setdefault("TESTING", "1")

import unittest

import numpy as np
import pandas as pd

from core import px1_shadow as px
from core.honest_backtest import Params
from core.setup3 import NO_TIME_EXITS

# Registered with the first shadow release.  If this test fails you changed a shadow rule: bump PX1_SHADOW_VERSION, add a CHANGELOG patch note
# and register the new fingerprint here (AGENTS rule 37) so the forward sample is never blended.
FINGERPRINT_V1 = "397a3c6eeb22"


def pairs_df(gains, live_open=None, x1_open=None, symbols=None, base_live=-2.0):
    n = len(gains)
    return pd.DataFrame(dict(
        symbol=symbols if symbols is not None else [f"S{i}" for i in range(n)],
        entry_date=pd.Timestamp("2026-11-10"), entry_price=100.0,
        live_open=live_open if live_open is not None else [False] * n, live_pnl=[base_live] * n, live_exit_date="2026-11-24",
        live_exit_reason="x", live_days=14, x1_open=x1_open if x1_open is not None else [False] * n,
        x1_pnl=[base_live + g for g in gains], x1_exit_date="2026-12-20", x1_exit_reason="y", x1_days=40, last_candle="2026-12-20"))


class TestRules(unittest.TestCase):
    def test_fingerprint_registered(self):
        self.assertEqual(px.shadow_rules_fingerprint(), FINGERPRINT_V1)
        self.assertEqual(px.PX1_SHADOW_VERSION, "1.0.0")

    def test_x1_only_switches_time_exits_off(self):
        live, x1 = px.live_params(), px.x1_params()
        self.assertEqual(live, Params())
        self.assertEqual(dict(x1.exit_overrides), dict(NO_TIME_EXITS))
        for f in ("min_volume", "min_vol_mult", "max_extension_pct", "max_age", "cooldown_bars", "stop_cap_pct", "swing_lookback", "stop_buffer", "trail_start_pnl"):
            self.assertEqual(getattr(live, f), getattr(x1, f), f)
        for k, v in NO_TIME_EXITS:
            self.assertEqual(v, 9999, k)

    def test_registered_start_is_after_the_research_cache(self):
        self.assertGreaterEqual(pd.Timestamp(px.PX1_START_DATE), pd.Timestamp("2026-11-09"))
        self.assertEqual(px.MIN_PAIRS, 40)


class TestVerdict(unittest.TestCase):
    def test_insufficient_below_40_pairs(self):
        e = px.evaluate(pairs_df([5.0] * 39))
        self.assertEqual(e["verdict"], "INSUFFICIENT")
        self.assertEqual(e["n_pairs"], 39)
        self.assertEqual(px.evaluate(pairs_df([]))["verdict"], "INSUFFICIENT")

    def test_supported_needs_lower_bound_and_ex_largest_positive(self):
        rng = np.random.default_rng(1)
        g = list(3.0 + rng.normal(0, 1.0, 60))
        e = px.evaluate(pairs_df(g))
        self.assertEqual(e["verdict"], "SUPPORTED")
        self.assertGreater(e["ci_lo"], 0)

    def test_one_big_winner_does_not_support(self):
        g = [0.0] * 59 + [200.0]
        e = px.evaluate(pairs_df(g))
        self.assertNotEqual(e["verdict"], "SUPPORTED")
        self.assertAlmostEqual(e["mean_gain_ex_largest"], 0.0)

    def test_rejected_when_mean_gain_not_positive(self):
        e = px.evaluate(pairs_df([-2.0, 1.0] * 30))
        self.assertEqual(e["verdict"], "REJECTED")

    def test_continue_when_interval_spans_zero_but_mean_positive(self):
        rng = np.random.default_rng(3)
        g = list(0.4 + rng.normal(0, 6.0, 50))
        e = px.evaluate(pairs_df(g))
        self.assertEqual(e["verdict"], "CONTINUE")

    def test_pairs_with_a_live_leg_still_open_are_not_counted(self):
        df = pairs_df([5.0] * 45, live_open=[True] * 5 + [False] * 40)
        e = px.evaluate(df)
        self.assertEqual(e["n_pairs"], 40)

    def test_open_x1_legs_are_kept_marked_to_market(self):
        df = pairs_df([5.0] * 40, x1_open=[True] * 10 + [False] * 30)
        e = px.evaluate(df)
        self.assertEqual(e["n_pairs"], 40)
        self.assertEqual(e["n_x1_open"], 10)

    def test_costs_cancel_in_the_gain(self):
        e = px.evaluate(pairs_df([3.0] * 40))
        self.assertAlmostEqual(e["mean_gain"], 3.0)
        self.assertAlmostEqual(e["x1_mean_net"] - e["live_mean_net"], 3.0)

    def test_symbol_cluster_bootstrap_is_wider_than_naive_when_symbols_repeat(self):
        rng = np.random.default_rng(5)
        g = list(rng.normal(5.0, 1.0, 30)) + list(rng.normal(-5.0, 1.0, 30))     # two symbols with very different outcomes
        naive = px.boot_mean_ci(g)
        clustered = px.boot_mean_ci(g, groups=["A"] * 30 + ["B"] * 30)
        self.assertGreater(clustered[1] - clustered[0], naive[1] - naive[0])


class TestPairing(unittest.TestCase):
    def test_forward_pairs_respects_start_and_closed_leg(self):
        fake_df = pd.DataFrame(dict(DATE=pd.date_range("2026-10-01", periods=5), OPEN=1.0, HIGH=1.0, LOW=1.0, CLOSE=1.0, VOLUME=1.0))
        trades = [dict(symbol="AAA", entry_idx=1, entry_date=pd.Timestamp("2026-11-06"), entry_price=100.0, open=False, pnl_pct=-3.0,
                       exit_date="2026-11-20", exit_reason="Time Stop", days_held=14, ext_pct=1.0, vol_ratio=1.0, rr=2.0, days_since_listing=20),
                  dict(symbol="AAA", entry_idx=3, entry_date=pd.Timestamp("2026-11-10"), entry_price=100.0, open=False, pnl_pct=-3.0,
                       exit_date="2026-11-24", exit_reason="Time Stop", days_held=14, ext_pct=1.0, vol_ratio=1.0, rr=2.0, days_since_listing=24)]
        orig_live, orig_re = px.live_entries, px.rerun_exits_for_entry
        try:
            px.live_entries = lambda df, s, li: trades
            px.rerun_exits_for_entry = lambda df, li, t, p: dict(pnl_pct=10.0, exit_date="2026-12-30", exit_reason="Stop", days_held=50)
            out = px.forward_pairs({"AAA": fake_df}, {"AAA": {}}, "2026-11-09")
        finally:
            px.live_entries, px.rerun_exits_for_entry = orig_live, orig_re
        self.assertEqual(len(out), 1)                       # the 2026-11-06 entry is before the registered start
        self.assertEqual(out.iloc[0].entry_date, pd.Timestamp("2026-11-10"))
        self.assertAlmostEqual(out.iloc[0].x1_pnl - out.iloc[0].live_pnl, 13.0)
        self.assertFalse(out.iloc[0].x1_open)

    def test_stale_symbols_flags_held_names_with_old_candles(self):
        df = pairs_df([1.0, 1.0], x1_open=[True, False], symbols=["HELD", "DONE"])
        df["last_candle"] = ["2026-11-02", "2026-11-02"]
        self.assertEqual(px.stale_symbols(df, pd.Timestamp("2026-11-20")), ["HELD"])


if __name__ == "__main__":
    unittest.main()
