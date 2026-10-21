# -*- coding: utf-8 -*-
"""
Tests for core.exit_rules and core.guards.

The ORACLE below is a verbatim transcription of the exit / trailing logic that lived inline in
streamlined_ipo_scanner.stop_loss_update_scan before it was extracted (git history, commit
1d7aff3).  The extraction must not change production behaviour, so the new shared module is
compared against the oracle on tens of thousands of random position states.
"""
import os
os.environ["DISABLE_TELEGRAM"] = "1"
os.environ["TESTING"] = "1"

import sys
import unittest

import numpy as np

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core.exit_rules import (ExitParams, evaluate_position, evaluate_volume_exhaustion,
                              grade_stop_pct, next_trailing)
from core.guards import (GENUINE_DROP, LIKELY_CORPORATE_ACTION, NORMAL, assess_price_drop)

P = ExitParams()


# ----------------------------------------------------------------------------- oracle
def oracle(entry_price, old_trailing, current_price, days_held, trading_sessions,
           prev_max_runup, prev_max_drawdown, grade_exit, grade_trail, volumes):
    """Original inline production logic. Returns (exit_reason, new_trailing, new_max_runup)."""
    DEAD_MONEY_DAYS_IPO, DEAD_MONEY_RUNUP_IPO = 14, 4.0
    DEAD_MONEY_DAYS_CONSOL, DEAD_MONEY_RUNUP_CONSOL = 14, 5.0
    DEAD_MONEY_DAYS_OTHER = 30
    MIN_PNL_FOR_TRAIL, MIN_TRAIL_MOVE_PCT = 4.0, 1.0

    pnl = (current_price - entry_price) / entry_price * 100
    new_max_runup = max(prev_max_runup, pnl)
    exit_reason = None
    new_trailing = float(old_trailing)
    is_winner_archetype = (new_max_runup >= 15.0)

    if current_price <= old_trailing:
        exit_reason = "Stop Loss"
    elif not is_winner_archetype:
        grade = grade_exit
        is_ipo_discovery = (grade == "LISTING_BREAKOUT")
        if is_ipo_discovery:
            if days_held >= DEAD_MONEY_DAYS_IPO and new_max_runup < DEAD_MONEY_RUNUP_IPO:
                exit_reason = f"Time Stop - IPO Dead Money (No momentum after {DEAD_MONEY_DAYS_IPO} days)"
        else:
            if days_held >= DEAD_MONEY_DAYS_CONSOL and new_max_runup < DEAD_MONEY_RUNUP_CONSOL:
                exit_reason = f"Time Stop - Consolidation Dead Money (No momentum after {DEAD_MONEY_DAYS_CONSOL} days)"
        if not exit_reason:
            if days_held > DEAD_MONEY_DAYS_OTHER and current_price < entry_price * 0.95:
                exit_reason = f"Time Stop -5% (Underperforming after {DEAD_MONEY_DAYS_OTHER} days)"
            elif days_held > 60 and current_price < entry_price * 0.92:
                exit_reason = "Time Stop -8% (Underperforming after 60 days)"
        if not exit_reason and (days_held >= 14 or trading_sessions >= 10) and pnl <= 0.0:
            exit_reason = f"Time Stop - Dead Money (14-Day Velocity Gate, PnL {pnl:+.1f}%, Peak +{new_max_runup:.1f}%)"
        if not exit_reason and days_held >= 40:
            if pnl <= 0.0 and new_max_runup < 8.0:
                exit_reason = f"Time Stop - Stagnant Position (40d, no momentum: peak +{new_max_runup:.1f}%, PnL {pnl:+.1f}%)"

    _vol_grade = grade_exit
    _vol_min_days = 15 if _vol_grade == "LISTING_BREAKOUT" else 10
    if (not exit_reason and not is_winner_archetype and days_held >= _vol_min_days
            and -3.0 <= pnl < 5.0 and new_max_runup < 8.0):
        _entry_rows = volumes
        if _entry_rows is not None and len(_entry_rows) >= 16:
            _baseline_vol = sum(_entry_rows[:11]) / 11.0
            _recent_vol = sum(_entry_rows[-5:]) / 5.0
            if _baseline_vol >= 50_000 and _baseline_vol > 0:
                _vol_ratio = _recent_vol / _baseline_vol
                if _vol_ratio < 0.45:
                    exit_reason = (f"Volume Exhaustion - Dead volume (ratio: {_vol_ratio:.2f}, "
                                   f"pnl: {pnl:+.1f}%, days: {days_held})")

    if not exit_reason:
        grade = grade_trail
        if is_winner_archetype:
            peak_price = entry_price * (1.0 + new_max_runup / 100.0)
            candidate_trailing = max(entry_price * 1.05, peak_price * 0.80)
            min_trail_move_abs = entry_price * (MIN_TRAIL_MOVE_PCT / 100.0)
            if candidate_trailing > new_trailing and (candidate_trailing - new_trailing) >= min_trail_move_abs:
                new_trailing = candidate_trailing
        else:
            trail_threshold = 3.0 if grade == "LISTING_BREAKOUT" else MIN_PNL_FOR_TRAIL
            if pnl >= trail_threshold:
                grade_stop_pcts = {"A+": 0.05, "A": 0.07, "B": 0.10, "C": 0.12, "D": 0.15, "LISTING_BREAKOUT": 0.10}
                stop_pct = grade_stop_pcts.get(grade, 0.10)
                candidate_trailing = current_price * (1 - stop_pct)
                min_trail_move_abs = entry_price * (MIN_TRAIL_MOVE_PCT / 100.0)
                if candidate_trailing > new_trailing and (candidate_trailing - new_trailing) >= min_trail_move_abs:
                    new_trailing = candidate_trailing
    return exit_reason, new_trailing, new_max_runup


class TestExitRulesMatchOriginalProductionLogic(unittest.TestCase):
    GRADES = ["LISTING_BREAKOUT", "LISTING_BREAKOUT", "LISTING_BREAKOUT", "A+", "A", "B", "C", "D", "N/A", None, "weird"]

    def test_random_states_match_oracle(self):
        rng = np.random.default_rng(20261012)
        mismatches = []
        n_exit = n_trail = 0
        for k in range(40000):
            entry = float(rng.uniform(30, 3000))
            pnl_target = float(rng.choice([rng.uniform(-14, 4), rng.uniform(-3, 12), rng.uniform(5, 45)]))
            price = entry * (1 + pnl_target / 100.0)
            stop_pct = float(rng.uniform(0.04, 0.13))
            trailing = entry * (1 - stop_pct) if rng.random() < 0.7 else entry * float(rng.uniform(0.95, 1.4))
            days = int(rng.integers(0, 90))
            sessions = int(min(days, rng.integers(0, 70)))
            prev_runup = float(max(0.0, pnl_target + rng.uniform(-3, 20))) if rng.random() < 0.8 else 0.0
            grade_exit = self.GRADES[rng.integers(0, len(self.GRADES))]
            grade_trail = grade_exit if grade_exit is not None else "C"
            mode = rng.integers(0, 4)
            if mode == 0:
                vols = None
            elif mode == 1:
                vols = [float(rng.uniform(1e5, 5e5))] * 11 + [float(rng.uniform(1e3, 4e4))] * int(rng.integers(5, 12))
            elif mode == 2:
                vols = list(rng.uniform(2e4, 9e5, size=int(rng.integers(3, 30))))
            else:
                vols = [float(rng.uniform(2e5, 6e5))] * 25
            old = oracle(entry, trailing, price, days, sessions, prev_runup, 0.0, grade_exit, grade_trail, vols)
            new = evaluate_position(entry, trailing, price, days, sessions, prev_runup, 0.0,
                                    grade_exit, grade_trail, volume_provider=lambda v=vols: v, params=P)
            if (old[0] != new.exit_reason or abs(old[1] - new.new_trailing) > 1e-9
                    or abs(old[2] - new.new_max_runup) > 1e-9):
                mismatches.append((k, old, new))
            n_exit += old[0] is not None
            n_trail += (old[0] is None and abs(old[1] - trailing) > 1e-12)
        self.assertEqual(mismatches[:3], [])
        # the random states must actually exercise exits and trailing moves, or the test proves nothing
        self.assertGreater(n_exit, 2000)
        self.assertGreater(n_trail, 1000)

    def test_defaults_equal_live_constants(self):
        import streamlined_ipo_scanner as s
        self.assertEqual(s.EXIT_PARAMS, ExitParams())

    def test_production_uses_shared_module(self):
        import inspect
        import streamlined_ipo_scanner as s
        src = inspect.getsource(s.stop_loss_update_scan)
        self.assertIn("evaluate_position(", src)
        self.assertNotIn("Time Stop - Dead Money (14-Day Velocity Gate", src)   # no private copy of the rule

    def test_volume_provider_is_lazy(self):
        calls = []
        # winner: the volume check must not even be attempted
        evaluate_position(100, 90, 130, 20, 14, 30.0, 0.0, "LISTING_BREAKOUT", "LISTING_BREAKOUT",
                          volume_provider=lambda: calls.append(1), params=P)
        self.assertEqual(calls, [])


class TestExitRuleSemantics(unittest.TestCase):
    def test_stop_hit(self):
        d = evaluate_position(100, 95, 94.5, 3, 3, 0, 0, "LISTING_BREAKOUT", "LISTING_BREAKOUT")
        self.assertEqual(d.exit_reason, "Stop Loss")

    def test_check_stop_false_defers_to_caller(self):
        d = evaluate_position(100, 95, 94.5, 3, 3, 0, 0, "LISTING_BREAKOUT", "LISTING_BREAKOUT", check_stop=False)
        self.assertIsNone(d.exit_reason)

    def test_ipo_dead_money_exits_even_when_slightly_profitable(self):
        # peak below 4% at day 14 is cut regardless of PnL sign (documented gap vs the "PnL <= 0" description)
        d = evaluate_position(100, 88, 102.0, 14, 9, 2.5, 0, "LISTING_BREAKOUT", "LISTING_BREAKOUT")
        self.assertIn("IPO Dead Money", d.exit_reason)

    def test_winner_is_immune_to_time_stops(self):
        d = evaluate_position(100, 95, 101, 45, 31, 22.0, 0, "LISTING_BREAKOUT", "LISTING_BREAKOUT")
        self.assertIsNone(d.exit_reason)

    def test_trail_is_a_ratchet(self):
        t = next_trailing(100, 120, 110, 10.0, 10.0, "LISTING_BREAKOUT")
        self.assertEqual(t, 120)

    def test_listing_trail_is_10pct_below_price(self):
        t = next_trailing(100, 90, 115, 15.0 - 0.01, 14.99, "LISTING_BREAKOUT")
        self.assertAlmostEqual(t, 115 * 0.90)

    def test_winner_trail_floor_and_cushion(self):
        t = next_trailing(100, 90, 150, 50.0, 50.0, "LISTING_BREAKOUT")
        self.assertAlmostEqual(t, 150 * 0.80)
        t2 = next_trailing(100, 90, 116, 16.0, 16.0, "LISTING_BREAKOUT")
        self.assertAlmostEqual(t2, 105.0)    # floor entry * 1.05 beats 20% off the 116 peak (92.8)

    def test_grade_stop_pct_table(self):
        self.assertEqual(grade_stop_pct("A+"), 0.05)
        self.assertEqual(grade_stop_pct("LISTING_BREAKOUT"), 0.10)
        self.assertEqual(grade_stop_pct("unknown"), 0.10)

    def test_volume_exhaustion_helper(self):
        ok, ratio = evaluate_volume_exhaustion([300000] * 11 + [50000] * 5)
        self.assertTrue(ok)
        self.assertLess(ratio, 0.45)
        self.assertEqual(evaluate_volume_exhaustion([300000] * 10), (False, None))      # too short
        self.assertEqual(evaluate_volume_exhaustion([10000] * 20), (False, None))       # noise floor


class TestCorporateActionGuard(unittest.TestCase):
    def test_normal_move(self):
        self.assertEqual(assess_price_drop(100, 90).kind, NORMAL)
        self.assertEqual(assess_price_drop(100, 76).kind, NORMAL)

    def test_bonus_and_split_ratios_pause(self):
        for prev, new, factor in ((1000, 500, 2), (900, 300, 3), (1000, 100, 10), (500, 100, 5)):
            a = assess_price_drop(prev, new)
            self.assertEqual(a.kind, LIKELY_CORPORATE_ACTION, (prev, new))
            self.assertEqual(a.factor, factor)

    def test_real_crash_is_not_paused(self):
        # two lower circuits in a row on a 20% band stock: 100 -> 64 (-36%). Must NOT be treated as a split.
        a = assess_price_drop(100, 64)
        self.assertEqual(a.kind, GENUINE_DROP)
        self.assertEqual(assess_price_drop(100, 70).kind, GENUINE_DROP)
        self.assertEqual(assess_price_drop(100, 55).kind, GENUINE_DROP)

    def test_two_thirds_needs_volume_confirmation(self):
        self.assertEqual(assess_price_drop(150, 100).kind, GENUINE_DROP)                       # no volume info
        self.assertEqual(assess_price_drop(150, 100, prev_avg_volume=1e6, new_volume=1.6e6).kind,
                         LIKELY_CORPORATE_ACTION)
        self.assertEqual(assess_price_drop(150, 100, prev_avg_volume=1e6, new_volume=0.5e6).kind, GENUINE_DROP)

    def test_garbage_inputs_are_safe(self):
        self.assertEqual(assess_price_drop(None, 5).kind, NORMAL)
        self.assertEqual(assess_price_drop(0, 5).kind, NORMAL)
        self.assertEqual(assess_price_drop("x", 5).kind, NORMAL)


if __name__ == "__main__":
    unittest.main()
