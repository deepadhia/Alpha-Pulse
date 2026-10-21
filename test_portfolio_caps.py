# -*- coding: utf-8 -*-
"""
Pins the 2026-10-14 slot policy: one flat cap of 10 positions, 10% equal weight, a single source of truth
(core/portfolio_caps.py) shared by the scanners, the order sheet and the simulator, and a working rollback.

Drives the REAL listing_day_breakout_scanner.commit_trade_to_db with the database mocked (same harness as
test_commit_policy.py).  Nothing touches MongoDB or the network.
"""
import os
os.environ["DISABLE_TELEGRAM"] = "1"
os.environ["TESTING"] = "1"

import sys
import unittest
from unittest.mock import patch

import pandas as pd

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import listing_day_breakout_scanner as lds
from core.order_sheet import SheetConfig, build_resting_entries, build_sheet, format_sheet
from core.portfolio_caps import DEFAULT_HARD_ACTIVE_POSITIONS, DEFAULT_MAX_ACTIVE_POSITIONS, equal_weight
from core.portfolio_sim import PortfolioConfig, run_portfolio
from test_commit_policy import FakeDb, breakout


def commit(bo, active):
    fake = FakeDb(active=active)
    saved = {}
    with patch("db.db", fake), \
         patch("db.has_active_position", return_value=False), \
         patch("db.upsert_signal", side_effect=lambda d: saved.setdefault("signal", d)), \
         patch("db.upsert_position", side_effect=lambda d: saved.setdefault("position", d)), \
         patch("core.locks.time.sleep", lambda s: None), \
         patch.dict(os.environ, {"FORCE_OFFLINE_SCAN": "1"}):
        result = lds.commit_trade_to_db(bo)
    return result, saved


def non_pristine():
    bo = breakout("BREAKOUT")
    bo.update(tier="B", winner_score=0, market_regime="BULL")
    return bo


class TestCapConstants(unittest.TestCase):
    def test_policy_is_one_flat_cap_of_ten(self):
        self.assertEqual(DEFAULT_MAX_ACTIVE_POSITIONS, 10)
        self.assertEqual(DEFAULT_HARD_ACTIVE_POSITIONS, 10)
        self.assertAlmostEqual(equal_weight(), 0.10)

    def test_scanner_reads_the_shared_defaults(self):
        if os.getenv("MAX_ACTIVE_POSITIONS") or os.getenv("HARD_ACTIVE_POSITIONS"):
            self.skipTest("cap override present in the environment")
        self.assertEqual(lds.scanner_module.MAX_ACTIVE_POSITIONS, DEFAULT_MAX_ACTIVE_POSITIONS)
        self.assertEqual(lds.scanner_module.HARD_ACTIVE_POSITIONS, DEFAULT_HARD_ACTIVE_POSITIONS)
        self.assertEqual(lds.DEFAULT_MAX_ACTIVE_POSITIONS, DEFAULT_MAX_ACTIVE_POSITIONS)
        self.assertEqual(lds.DEFAULT_HARD_ACTIVE_POSITIONS, DEFAULT_HARD_ACTIVE_POSITIONS)

    def test_order_sheet_uses_the_same_cap_and_weight(self):
        cfg = SheetConfig(capital=100000)
        self.assertEqual(cfg.hard_cap, DEFAULT_HARD_ACTIVE_POSITIONS)
        self.assertEqual(cfg.soft_cap, DEFAULT_MAX_ACTIVE_POSITIONS)
        self.assertIn("per-slot weight 10.0%", format_sheet(build_sheet([], [], cfg), cfg))


@unittest.skipIf(os.getenv("MAX_ACTIVE_POSITIONS") or os.getenv("HARD_ACTIVE_POSITIONS"), "cap override present in the environment")
class TestScannerBoundaries(unittest.TestCase):
    def test_slots_one_to_ten_are_live_and_the_eleventh_is_paper(self):
        for active in (0, 4, 5, 6, 7, 8, 9):                  # 5, 6 and 7 were PAPER_ONLY or soft-capped before
            (ok, full, *_), saved = commit(breakout("BREAKOUT"), active)
            self.assertFalse(full, f"active={active}")
            self.assertEqual(saved["position"]["status"], "ACTIVE", f"active={active}")
        for active in (10, 11, 15):
            (ok, full, *_), saved = commit(breakout("BREAKOUT"), active)
            self.assertTrue(full, f"active={active}")
            self.assertEqual(saved["position"]["status"], "PAPER_ONLY", f"active={active}")

    def test_no_pristine_band_below_the_cap(self):
        # before: from the 6th position on, a non-pristine setup was PAPER_ONLY; now it is live until the cap
        for active in (5, 6, 9):
            (ok, full, *_), saved = commit(non_pristine(), active)
            self.assertEqual(saved["position"]["status"], "ACTIVE", f"active={active}")
        (ok, full, *_), saved = commit(non_pristine(), 10)
        self.assertEqual(saved["position"]["status"], "PAPER_ONLY")

    def test_shelf_and_busy_lock_rules_are_unchanged(self):
        (ok, full, *_), saved = commit(breakout("SHELF_BREAKOUT"), 2)
        self.assertEqual(saved["position"]["status"], "PAPER_ONLY")      # shelf stays paper-only below the cap


class TestRollback(unittest.TestCase):
    """The old behaviour (soft 5 / hard 7) is restored by setting the two constants (env vars of the same names)."""

    def test_old_caps_reproduce_the_old_behaviour(self):
        with patch.object(lds.scanner_module, "MAX_ACTIVE_POSITIONS", 5), \
             patch.object(lds.scanner_module, "HARD_ACTIVE_POSITIONS", 7):
            (_, full, *_), saved = commit(breakout("BREAKOUT"), 4)
            self.assertEqual(saved["position"]["status"], "ACTIVE")
            (_, full, *_), saved = commit(non_pristine(), 6)              # soft cap breached, not pristine
            self.assertEqual(saved["position"]["status"], "PAPER_ONLY")
            (_, full, *_), saved = commit(breakout("BREAKOUT"), 6)        # tier A bypasses the soft cap
            self.assertEqual(saved["position"]["status"], "ACTIVE")
            (_, full, *_), saved = commit(breakout("BREAKOUT"), 7)        # hard cap
            self.assertEqual(saved["position"]["status"], "PAPER_ONLY")


class TestSheetAndSimulatorWeights(unittest.TestCase):
    def test_sheet_sizes_at_ten_percent_and_stops_at_ten_slots(self):
        cfg = SheetConfig(capital=100000)
        nine = [dict(symbol=f"P{i}", quantity=1, entry_price=100, trailing_stop=90, current_price=100) for i in range(9)]
        res = build_sheet(nine, [dict(symbol="NEW", entry_price=100, limit_buy_price=100, stop_loss=90)], cfg)
        self.assertEqual(len(res.entries), 1)
        self.assertEqual(res.entries[0].quantity, 100)                    # Rs 10,000 / Rs 100
        ten = nine + [dict(symbol="P9", quantity=1, entry_price=100, trailing_stop=90, current_price=100)]
        res = build_sheet(ten, [dict(symbol="NEW", entry_price=100, limit_buy_price=100, stop_loss=90)], cfg)
        self.assertEqual(res.entries, [])
        self.assertTrue(any("book full (10/10" in s for s in res.skipped))

    def test_resting_orders_respect_the_free_slots(self):
        cands = [dict(symbol=f"C{i}", trigger=101.5, limit=102.5, fill=101.8, stop=89.6, risk_pct=12.0, rr=1.67, age=20,
                      last_close=95.0 + i, already_above_trigger=False) for i in range(5)]
        entries, skipped, _ = build_resting_entries(cands, slots_used=8, cfg=SheetConfig(capital=1e6))
        self.assertEqual(len(entries), 2)
        self.assertEqual(len(skipped), 3)

    def test_simulator_flat_ten_slots_buys_ten_percent_each(self):
        dates = list(pd.date_range("2026-03-02", "2026-03-06"))
        trades = []
        for i in range(11):
            path = [(d, 100.0, 90.0) for d in dates]
            trades.append(dict(symbol=f"S{i}", type="LISTING_BREAKOUT", entry_date="2026-03-02", exit_date="2026-03-06",
                               entry_price=100.0, exit_price=100.0, year=2026, exit_reason="x", pnl_pct=0.0, path=path,
                               initial_risk_pct=10.0, vol_ratio=1.0 + i / 100.0))
        r = run_portfolio(trades, PortfolioConfig(capital=1e6, soft_cap=10, hard_cap=10, cap_policy="flat"))
        self.assertEqual(len(r["log"]), 10)                               # the 11th is skipped for the cap
        self.assertEqual(r["summary"]["skipped"]["cap"], 1)
        for v in r["log"].buy_value:
            self.assertAlmostEqual(v / 1e6, 0.10, delta=0.001)


if __name__ == "__main__":
    unittest.main()
