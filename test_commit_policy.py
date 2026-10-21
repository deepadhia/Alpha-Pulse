# -*- coding: utf-8 -*-
"""
Drives the REAL listing_day_breakout_scanner.commit_trade_to_db with the database mocked, to pin the
portfolio policy: paper-only shelf engine, atomic commit lock, fail-closed behaviour.
"""
import os
os.environ["DISABLE_TELEGRAM"] = "1"
os.environ["TESTING"] = "1"

import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import listing_day_breakout_scanner as lds


def breakout(kind="BREAKOUT", symbol="TESTCO", tier="A"):
    return {
        "symbol": symbol, "type": kind, "entry_price": 200.0, "current_price": 200.0, "stop_loss": 180.0,
        "target_price": 250.0, "volume_spike": 2.1, "volume_vs_listing_day": 1.0, "listing_range_pct": 8.0,
        "risk_reward": 2.5, "days_since_listing": 12, "listing_day_high": 195.0, "base_range_high": 195.0,
        "tier": tier, "winner_score": 4, "market_regime": "BULL", "position_size_pct": 60,
    }


class FakeDb:
    """Just enough of the db module surface that commit_trade_to_db touches."""

    def __init__(self, active=2, lock_free=True):
        self.positions = MagicMock()
        self.positions.count_documents.return_value = active
        self.locks_coll = MagicMock()
        self.lock_free = lock_free
        self.pending = MagicMock()

    def __getitem__(self, name):
        if name == "locks":
            from pymongo.errors import DuplicateKeyError

            def update_one(flt, upd, upsert=False):
                if not self.lock_free:
                    raise DuplicateKeyError("busy")
            self.locks_coll.update_one.side_effect = update_one
            return self.locks_coll
        if name == "pending_states":
            return self.pending
        return MagicMock()


class TestCommitPolicy(unittest.TestCase):
    def _commit(self, bo, active=2, lock_free=True, shelf_enabled=False):
        fake = FakeDb(active=active, lock_free=lock_free)
        saved = {}
        with patch("db.db", fake), \
             patch("db.has_active_position", return_value=False), \
             patch("db.upsert_signal", side_effect=lambda d: saved.setdefault("signal", d)), \
             patch("db.upsert_position", side_effect=lambda d: saved.setdefault("position", d)), \
             patch.object(lds, "LISTING_SHELF_LIVE_CAPITAL_ENABLED", shelf_enabled), \
             patch("core.locks.time.sleep", lambda s: None),              patch.dict(os.environ, {"FORCE_OFFLINE_SCAN": "1"}):   # scoped: must not leak into other test modules
            result = lds.commit_trade_to_db(bo)
        return result, saved

    def test_listing_breakout_goes_live_when_slots_free(self):
        (ok, full, active, mr, mult), saved = self._commit(breakout("BREAKOUT"), active=2)
        self.assertTrue(ok)
        self.assertFalse(full)
        self.assertEqual(saved["position"]["status"], "ACTIVE")

    def test_shelf_breakout_is_paper_only_by_default(self):
        (ok, full, active, mr, mult), saved = self._commit(breakout("SHELF_BREAKOUT"), active=2)
        self.assertTrue(ok)
        self.assertTrue(full)
        self.assertEqual(saved["position"]["status"], "PAPER_ONLY")
        self.assertEqual(saved["signal"]["status"], "PAPER_ONLY")

    def test_shelf_breakout_can_be_re_enabled(self):
        (ok, full, *_), saved = self._commit(breakout("SHELF_BREAKOUT"), active=2, shelf_enabled=True)
        self.assertFalse(full)
        self.assertEqual(saved["position"]["status"], "ACTIVE")

    def test_hard_cap_blocks_live_capital(self):
        (ok, full, *_), saved = self._commit(breakout("BREAKOUT"), active=lds.scanner_module.HARD_ACTIVE_POSITIONS)
        self.assertTrue(full)
        self.assertEqual(saved["position"]["status"], "PAPER_ONLY")

    def test_busy_lock_fails_closed_to_paper(self):
        # another scanner holds the commit lock for the whole wait -> never open uncapped ACTIVE capital
        import core.locks as locks
        orig = locks.portfolio_commit_lock

        def fast(db, *a, **k):
            k.setdefault("wait_s", 0)
            return orig(db, *a, **k)

        with patch.object(locks, "portfolio_commit_lock", fast):
            (ok, full, *_), saved = self._commit(breakout("BREAKOUT"), active=1, lock_free=False)
        self.assertTrue(ok)
        self.assertTrue(full)
        self.assertEqual(saved["position"]["status"], "PAPER_ONLY")


if __name__ == "__main__":
    unittest.main()
