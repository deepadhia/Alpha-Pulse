# -*- coding: utf-8 -*-
"""Tests for core.locks (portfolio commit mutex) and core.order_sheet."""
import os
os.environ["DISABLE_TELEGRAM"] = "1"
os.environ["TESTING"] = "1"

import sys
import threading
import unittest
from datetime import datetime, timedelta, timezone

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from pymongo.errors import DuplicateKeyError

from core.locks import acquire_blocking, portfolio_commit_lock, release_owner, try_acquire
from core.scorecard import CONTINUE, INSUFFICIENT, SCALE, STOP, GateConfig, scorecard
from core.portfolio_caps import equal_weight
from core.risk_guard import GuardConfig, HALT, OK, WARN, consecutive_losses, evaluate
from core.order_sheet import (SheetConfig, build_sheet, format_sheet, round_tick, size_shares,
                               stop_order_levels)


class FakeLocks:
    """In-memory stand-in implementing the exact update_one/delete_one semantics the mutex relies on."""

    def __init__(self):
        self.docs = {}
        self._mx = threading.Lock()

    def update_one(self, flt, update, upsert=False):
        with self._mx:
            doc = self.docs.get(flt["_id"])
            if doc is not None:
                cond = flt.get("expires_at", {}).get("$lt")
                if cond is not None and doc["expires_at"] < cond:
                    doc.update(update["$set"])
                    return
                raise DuplicateKeyError("E11000 duplicate key")
            if not upsert:
                return
            new = {"_id": flt["_id"]}
            new.update(update["$set"])
            self.docs[flt["_id"]] = new

    def delete_one(self, flt):
        with self._mx:
            d = self.docs.get(flt["_id"])
            if d is not None and d.get("owner") == flt.get("owner", d.get("owner")):
                del self.docs[flt["_id"]]


class FakeDB:
    def __init__(self):
        self.locks = FakeLocks()

    def __getitem__(self, name):
        assert name == "locks"
        return self.locks


class TestLocks(unittest.TestCase):
    def test_only_one_owner_wins(self):
        db = FakeDB()
        self.assertTrue(try_acquire(db["locks"], "x", "a"))
        self.assertFalse(try_acquire(db["locks"], "x", "b"))

    def test_expired_lock_can_be_taken(self):
        db = FakeDB()
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.assertTrue(try_acquire(db["locks"], "x", "a", ttl_s=10, now=t0))
        self.assertFalse(try_acquire(db["locks"], "x", "b", ttl_s=10, now=t0 + timedelta(seconds=5)))
        self.assertTrue(try_acquire(db["locks"], "x", "b", ttl_s=10, now=t0 + timedelta(seconds=11)))

    def test_release_only_by_owner(self):
        db = FakeDB()
        owner = acquire_blocking(db, wait_s=0)
        self.assertIsNotNone(owner)
        release_owner(db, "someone-else")
        self.assertIsNone(acquire_blocking(db, wait_s=0))      # still held
        release_owner(db, owner)
        self.assertIsNotNone(acquire_blocking(db, wait_s=0))   # free again

    def test_context_manager_yields_false_when_busy_and_none_db(self):
        db = FakeDB()
        owner = acquire_blocking(db, wait_s=0)
        with portfolio_commit_lock(db, wait_s=0, sleep=lambda s: None) as held:
            self.assertFalse(held)
        release_owner(db, owner)
        with portfolio_commit_lock(db, wait_s=0) as held:
            self.assertTrue(held)
        with portfolio_commit_lock(None) as held:
            self.assertFalse(held)

    def test_concurrent_commits_never_exceed_cap(self):
        """The scenario the lock exists for: many scans racing for the last slots."""
        db = FakeDB()
        state = {"active": 5}
        cap = 7
        opened = []

        def scan(i):
            with portfolio_commit_lock(db, wait_s=5, poll_s=0.001) as held:
                if not held:
                    return
                n = state["active"]            # count
                if n < cap:                    # decide
                    threading.Event().wait(0.002)
                    state["active"] = n + 1    # insert
                    opened.append(i)

        threads = [threading.Thread(target=scan, args=(i,)) for i in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(state["active"], cap)
        self.assertEqual(len(opened), 2)


class TestOrderSheet(unittest.TestCase):
    def test_tick_rounding(self):
        self.assertEqual(round_tick(103.27, direction="down"), 103.25)
        self.assertEqual(round_tick(103.27, direction="up"), 103.30)
        self.assertEqual(round_tick(103.27), 103.25)

    def test_stop_levels(self):
        trig, lim = stop_order_levels(96.03, 2.0)
        self.assertEqual(trig, 96.00)
        self.assertLess(lim, trig)
        self.assertAlmostEqual(lim, 94.05, places=2)

    def test_whole_share_sizing(self):
        shares, why = size_shares(100000, 1800.0, 1 / 7)
        self.assertEqual(shares, 7)                       # 14,285 target -> 7 shares = 12,600 (88%)
        shares, why = size_shares(100000, 2914.0, 1 / 7)
        self.assertEqual(shares, 4)                       # 11,656 = 81% of target
        shares, why = size_shares(100000, 15000.0, 1 / 7)
        self.assertEqual(shares, 0)
        self.assertIn("one share costs", why)

    def test_min_fill_fraction_skips_badly_rounded_positions(self):
        shares, why = size_shares(100000, 7000.0, 1 / 7)    # 2 shares = 14,000 ok ; 7000 -> 2 shares
        self.assertEqual(shares, 2)
        shares, why = size_shares(100000, 9000.0, 1 / 7, min_fill_fraction=0.5)
        self.assertEqual(shares, 1)                         # 63% of target passes the 50% rule
        shares, why = size_shares(100000, 7300.0, 1 / 7, min_fill_fraction=0.99)
        self.assertEqual(shares, 0)
        self.assertIn("price too high", why)

    def test_sheet_with_positions_and_new_signal(self):
        positions = [
            dict(symbol="AAA", quantity=100, entry_price=100, trailing_stop=107.0, current_price=120.0),
            dict(symbol="BBB", quantity=0, entry_price=50, trailing_stop=44.0, current_price=47.0),
        ]
        signals = [dict(symbol="NEW", entry_price=200, limit_buy_price=204, stop_loss=180.0)]
        cfg = SheetConfig(capital=100000)
        res = build_sheet(positions, signals, cfg)
        self.assertEqual(len(res.stops), 2)
        self.assertEqual(len(res.entries), 1)
        e = res.entries[0]
        self.assertEqual(e.quantity, int(100000 * equal_weight(SheetConfig().hard_cap) // 204))
        self.assertLess(e.stop_limit, e.stop_trigger)
        self.assertGreater(res.open_risk_rupees, 0)
        self.assertIn("enter your held quantity", res.stops[1].note)
        text = format_sheet(res, cfg)
        self.assertIn("RESTING STOPS", text)
        self.assertIn("NEW ENTRIES", text)

    def test_stop_at_or_above_price_is_flagged(self):
        res = build_sheet([dict(symbol="X", quantity=10, entry_price=100, trailing_stop=95.0, current_price=94.0)], [])
        self.assertTrue(any("at or above last price" in w for w in res.warnings))

    def test_book_full_skips_new_entries(self):
        positions = [dict(symbol=f"P{i}", quantity=1, entry_price=100, trailing_stop=90, current_price=100)
                     for i in range(SheetConfig().hard_cap)]
        res = build_sheet(positions, [dict(symbol="NEW", entry_price=100, stop_loss=90)])
        self.assertEqual(res.entries, [])
        self.assertTrue(any("book full" in s for s in res.skipped))

    def test_expensive_signal_skipped(self):
        res = build_sheet([], [dict(symbol="BIG", entry_price=25000, stop_loss=22000)], SheetConfig(capital=100000))
        self.assertEqual(res.entries, [])
        self.assertTrue(res.skipped)

    def test_total_risk_warning(self):
        positions = [dict(symbol=f"P{i}", quantity=100, entry_price=100, trailing_stop=80, current_price=100) for i in range(5)]
        res = build_sheet(positions, [], SheetConfig(capital=100000, max_total_open_risk_pct=5.0))
        self.assertTrue(any("Total open risk" in w for w in res.warnings))


class TestRiskGuard(unittest.TestCase):
    def test_levels(self):
        self.assertEqual(evaluate(95000, 100000).level, OK)
        self.assertEqual(evaluate(87000, 100000).level, WARN)
        st = evaluate(79000, 100000)
        self.assertEqual(st.level, HALT)
        self.assertTrue(st.halt_new_entries)

    def test_losing_streak(self):
        self.assertEqual(consecutive_losses([5, -1, -2, 0.0, -3]), 4)
        self.assertEqual(consecutive_losses([-1, -2, 3]), 0)
        self.assertEqual(evaluate(100000, 100000, [-1.0] * 13).level, HALT)
        self.assertEqual(evaluate(100000, 100000, [-1.0] * 9).level, OK)

    def test_daily_loss_limit(self):
        self.assertTrue(evaluate(94000, 100000, day_change_pct=-6.5).halt_new_entries)

    def test_halt_blocks_new_entries_but_keeps_stops(self):
        positions = [dict(symbol="AAA", quantity=10, entry_price=100, trailing_stop=95.0, current_price=110.0)]
        signals = [dict(symbol="NEW", entry_price=200, limit_buy_price=204, stop_loss=180.0)]
        res = build_sheet(positions, signals, SheetConfig(capital=100000), guard=evaluate(75000, 100000))
        self.assertEqual(res.entries, [])
        self.assertEqual(len(res.stops), 1)                       # exits and stops still managed
        self.assertTrue(any("halted by the risk guard" in s for s in res.skipped))
        self.assertTrue(any("RISK GUARD HALT" in w for w in res.warnings))


class TestPfMonitor(unittest.TestCase):
    def test_pf_three_ways(self):
        from core.scorecard import pf_report
        closed = [30.0, 10.0, 8.0, -5.0, -6.0, -4.0, -3.0, 2.0, 1.0, 0.5]       # gross; 1% friction deducted inside
        r = pf_report(closed, [20.0], GateConfig(friction_pct=1.0), n_boot=300)
        net = [x - 1.0 for x in closed]
        gp = sum(x for x in net if x > 0)
        gl = -sum(x for x in net if x < 0)
        self.assertAlmostEqual(r.pf_closed, gp / gl, places=6)
        self.assertGreater(r.pf_incl_open, r.pf_closed)                          # an open winner raises it
        self.assertLess(r.pf_closed_ex_top3, r.pf_closed)                        # the right tail carries it
        self.assertEqual(r.outlier_count, 1)
        self.assertGreater(r.outlier_share_of_profit, 0.4)
        self.assertTrue(any("only" in n for n in r.notes))                       # too few trades to say much

    def test_deterministic_and_empty(self):
        from core.scorecard import format_pf_report, pf_report
        a = pf_report([5.0, -2.0, 9.0, -3.0] * 10, n_boot=200, seed=3)
        b = pf_report([5.0, -2.0, 9.0, -3.0] * 10, n_boot=200, seed=3)
        self.assertEqual((a.lo90, a.hi90, a.prob_ge_target), (b.lo90, b.hi90, b.prob_ge_target))
        self.assertLessEqual(a.lo90, a.pf_incl_open)
        self.assertGreaterEqual(a.hi90, a.pf_incl_open)
        self.assertIn("no closed trades", format_pf_report(pf_report([])))

    def test_flags_target_met_only_with_open_runners(self):
        from core.scorecard import pf_report
        closed = [4.0, -3.0, 5.0, -3.0, 3.0, -4.0, 2.0, -3.0] * 5
        r = pf_report(closed, [60.0, 80.0], target=1.2, n_boot=200)
        if r.pf_closed < 1.2 <= r.pf_incl_open:
            self.assertTrue(any("open runners" in n for n in r.notes))
        self.assertGreater(r.pf_incl_open, r.pf_closed)

    def test_decision_logic_is_untouched(self):
        self.assertEqual(scorecard([30.0] * 10).status, INSUFFICIENT)


class TestScorecard(unittest.TestCase):
    def test_no_trades(self):
        self.assertEqual(scorecard([]).status, INSUFFICIENT)

    def test_too_few_trades_never_decide(self):
        self.assertEqual(scorecard([30.0] * 10).status, INSUFFICIENT)
        self.assertEqual(scorecard([-20.0] * 10).status, INSUFFICIENT)

    def test_stop_when_confidently_negative(self):
        rng_vals = [(-8.0 if i % 3 else 4.0) for i in range(40)]
        sc = scorecard(rng_vals)
        self.assertEqual(sc.status, STOP)
        self.assertLess(sc.upper80, 0)

    def test_noisy_small_sample_continues_instead_of_stopping(self):
        # mean slightly negative but huge noise: not enough evidence to stop
        vals = [25.0, -12.0, -12.0, 18.0, -12.0, -12.0, 30.0, -12.0, -12.0, -12.0] * 3
        sc = scorecard(vals)
        self.assertEqual(sc.status, CONTINUE)

    def test_scale_needs_100_trades_and_no_single_trade_dependence(self):
        good = [6.0, 4.0, -3.0, 5.0, -2.0] * 20                      # 100 trades, steady edge
        self.assertEqual(scorecard(good).status, SCALE)
        lucky = [-1.0] * 99 + [180.0]                                # one outlier carries the mean
        sc = scorecard(lucky)
        self.assertNotEqual(sc.status, SCALE)
        self.assertLess(sc.mean_ex_best, 0)

    def test_costs_are_deducted(self):
        self.assertAlmostEqual(scorecard([5.0] * 25, GateConfig(friction_pct=1.0)).mean, 4.0)

    def test_scale_requires_100_trades(self):
        good = [6.0, 4.0, -3.0, 5.0, -2.0] * 12                      # 60 trades
        self.assertEqual(scorecard(good).status, CONTINUE)


if __name__ == "__main__":
    unittest.main()
