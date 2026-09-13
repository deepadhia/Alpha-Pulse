#!/usr/bin/env python3
"""
test_strategy_evidence_unit.py

Unit tests for core/strategy_evidence.py forensic setup DNA extraction,
archetype classification, and system diagnostics report generation.
"""

import unittest
from datetime import datetime, timezone
import sys
import os

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

from core.strategy_evidence import build_trade_evidence_doc, generate_system_diagnostics_report

class TestStrategyEvidence(unittest.TestCase):

    def test_high_volume_runner_archetype(self):
        trade = {
            "symbol": "MOCKWIN",
            "entry_date": datetime(2026, 8, 1, 0, 0),
            "status": "PAPER_CLOSED",
            "pnl_pct": 25.5,
            "days_held": 10,
            "entry_price": 100.0,
            "current_price": 125.5,
            "signal_id": "BREAKOUT_MOCKWIN_20260801",
            "grade": "LISTING_BREAKOUT",
            "volume_ratio": 4.5,
            "listing_range_pct": 10.0,
            "upper_wick_pct": 12.0
        }
        doc = build_trade_evidence_doc(trade)
        self.assertEqual(doc["evidence_id"], "EV_MOCKWIN_20260801")
        self.assertEqual(doc["engine_type"], "LISTING_DAY_BREAKOUT")
        self.assertEqual(doc["forensics"]["archetype"], "HIGH_VOL_MOMENTUM_RUNNER")
        self.assertTrue(doc["outcome"]["is_win"])
        self.assertIn("Massive volume burst", doc["forensics"]["learning_summary"])

    def test_upper_wick_trap_archetype(self):
        trade = {
            "symbol": "MOCKTRAP",
            "entry_date": datetime(2026, 8, 1, 0, 0),
            "status": "CLOSED",
            "pnl_pct": -8.5,
            "days_held": 4,
            "entry_price": 100.0,
            "current_price": 91.5,
            "signal_id": "BREAKOUT_MOCKTRAP_20260801",
            "grade": "LISTING_BREAKOUT",
            "volume_ratio": 2.0,
            "listing_range_pct": 12.0,
            "upper_wick_pct": 42.0
        }
        doc = build_trade_evidence_doc(trade)
        self.assertEqual(doc["forensics"]["archetype"], "UPPER_WICK_SUPPLY_TRAP")
        self.assertFalse(doc["outcome"]["is_win"])
        self.assertIn("Long upper wick", doc["forensics"]["learning_summary"])
        self.assertIn("Upper 50% Candle Body Gate", doc["forensics"]["algo_takeaway"])

    def test_stagnant_dead_money_archetype(self):
        trade = {
            "symbol": "MOCKSTALE",
            "entry_date": datetime(2026, 8, 1, 0, 0),
            "status": "PAPER_CLOSED",
            "pnl_pct": -3.2,
            "days_held": 18,
            "entry_price": 100.0,
            "current_price": 96.8,
            "signal_id": "CONSOL_MOCKSTALE_20260801_10",
            "grade": "B",
            "volume_ratio": 1.2,
            "consolidation_range_pct": 14.0,
            "upper_wick_pct": 15.0
        }
        doc = build_trade_evidence_doc(trade)
        self.assertEqual(doc["engine_type"], "CONSOLIDATION")
        self.assertEqual(doc["forensics"]["archetype"], "STAGNANT_DEAD_MONEY_BLEED")
        self.assertIn("14-Day Velocity Speed Gate", doc["forensics"]["algo_takeaway"])
        self.assertIn("stagnated for 18 days", doc["forensics"]["learning_summary"])


if __name__ == "__main__":
    unittest.main()
