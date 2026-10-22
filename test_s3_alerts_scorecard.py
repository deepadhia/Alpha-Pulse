# -*- coding: utf-8 -*-
"""Tests for s3_paper_alerts.py (pure message builder, session dates) and core/s3_scorecard.py (forward verdict rules).  No database, no network."""
import os
os.environ.setdefault("DISABLE_TELEGRAM", "1")
os.environ.setdefault("TESTING", "1")

import unittest
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import s3_paper_alerts as al
from core import s3_scorecard as sc
from core.order_sheet import RestingEntry


def entry(sym="AAA"):
    return RestingEntry(sym, 50, 111.65, 112.75, 112.0, 5600.0, 98.5, 670.0, 0.67, 80, 0.0, "")


def pos_row(sym, alert=False, status="OPEN", **kw):
    r = dict(symbol=sym, status=status, alert=alert, last_close=100.0, trailing=98.0, pnl_pct=-1.5, max_runup=3.0, entry_price=101.5, quantity=10, days_held=9)
    r.update(kw)
    return r


class TestSessions(unittest.TestCase):
    def test_next_and_previous_session_skip_weekends(self):
        fri, mon = pd.Timestamp("2026-10-23"), pd.Timestamp("2026-10-26")
        self.assertEqual(al.next_session(fri), mon)
        self.assertEqual(al.next_session(pd.Timestamp("2026-10-22")), fri)
        self.assertEqual(al.previous_session(mon), fri)
        self.assertEqual(al.previous_session(pd.Timestamp("2026-10-27")), mon)


class TestMessage(unittest.TestCase):
    ORDER_DAY = pd.Timestamp("2026-10-23")

    def build(self, rows=(), entries=(), asof="2026-10-22", warnings=None):
        return al.build_message(list(rows), list(entries), self.ORDER_DAY, pd.Timestamp(asof) if asof else None, warnings)

    def test_prefix_and_separation_from_policy_p(self):
        m = self.build(entries=[entry()])
        self.assertIn("S3 PAPER", m["text"])
        self.assertIn("not P", m["text"])
        self.assertEqual((m["n_orders"], m["n_alerts"]), (1, 0))
        self.assertTrue(m["actionable"])

    def test_stop_alert_is_called_out_with_the_command(self):
        m = self.build(rows=[pos_row("XYZ", alert=True, last_close=95.0, trailing=98.0), pos_row("OK1")])
        self.assertEqual(m["n_alerts"], 1)
        self.assertEqual(m["n_open"], 2)
        self.assertIn("STOP ALERT", m["text"])
        self.assertIn("python s3_paper.py exit XYZ PRICE", m["text"])
        self.assertIn("OPEN POSITIONS", m["text"])

    def test_quiet_day_is_not_actionable(self):
        m = self.build(rows=[pos_row("OK1")])
        self.assertFalse(m["actionable"])
        self.assertIn("No S3 orders for the next session", m["text"])

    def test_stale_data_is_flagged_and_actionable(self):
        m = self.build(asof="2026-10-20", entries=[entry()])
        self.assertTrue(m["stale"])
        self.assertIn("STALE", m["text"])
        self.assertFalse(self.build(asof="2026-10-22")["stale"])
        self.assertTrue(self.build(asof=None)["stale"])

    def test_errors_and_engine_exits_are_reported_and_text_is_escaped(self):
        m = self.build(rows=[dict(symbol="A<B", error="no <candles>"), pos_row("GONE", status="EXITED", exit_date="2026-10-21", exit_price=90.0,
                                                                                exit_reason="Stop Loss (EOD alert)", pnl_pct=-11.0)])
        self.assertIn("CANNOT EVALUATE", m["text"])
        self.assertIn("A&lt;B", m["text"])
        self.assertIn("ENGINE SAYS EXITED", m["text"])
        self.assertTrue(m["actionable"])


def closed(pnl, sym="S"):
    return dict(symbol=sym, status="CLOSED", pnl_pct=pnl)


class TestScorecard(unittest.TestCase):
    def test_no_verdict_below_40_closed(self):
        e = sc.evaluate([closed(30.0)] * 39)
        self.assertEqual(e["verdict"], "INSUFFICIENT")
        self.assertEqual(sc.evaluate([])["verdict"], "INSUFFICIENT")

    def test_good_needs_ci_pf_and_ex_best(self):
        rng = np.random.default_rng(1)
        wins = list(rng.normal(25, 5, 20)) + list(rng.normal(-8, 2, 20))          # about +8.5% gross mean, solid
        e = sc.evaluate([closed(x) for x in wins])
        self.assertEqual(e["verdict"], "GOOD", e)
        self.assertGreaterEqual(e["pf_closed"], 1.3)

    def test_one_outlier_cannot_make_it_good(self):
        pnl = [-6.0] * 39 + [400.0]                                                # mean positive only because of one trade
        e = sc.evaluate([closed(x) for x in pnl])
        self.assertNotEqual(e["verdict"], "GOOD")
        self.assertLess(e["mean_ex_best"], 0)

    def test_bad_when_mean_not_positive_or_ci_excludes_the_replay(self):
        e = sc.evaluate([closed(x) for x in ([10.0] * 15 + [-9.0] * 25)])          # net mean about -3%
        self.assertEqual(e["verdict"], "BAD")
        rng = np.random.default_rng(2)
        e2 = sc.evaluate([closed(x) for x in rng.normal(1.5, 3, 60)])               # net mean about +0.5%, CI upper < 2
        self.assertEqual(e2["verdict"], "BAD", e2)

    def test_costs_are_charged(self):
        e = sc.evaluate([closed(1.0)] * 40)
        self.assertAlmostEqual(e["mean"], 0.0, places=6)
        self.assertEqual(e["verdict"], "BAD")                                       # +1% gross is 0% net

    def test_fill_stats(self):
        orders = [dict(status="FILLED", trigger=100.0, fill_price=100.4), dict(status="PLACED", trigger=50.0), dict(status="FILLED", trigger=10.0)]
        f = sc.fill_stats(orders)
        self.assertEqual((f["placed"], f["filled"], f["missing_fill_price"]), (3, 2, 1))
        self.assertAlmostEqual(f["mean_slip_vs_trigger"], 0.4, places=6)

    def test_operations_coverage(self):
        today = pd.Timestamp("2026-10-23")
        days = sc.business_days(today, 10)
        self.assertEqual(len(days), 10)
        self.assertTrue(all(d.weekday() < 5 for d in days))
        runs = [dict(run_date=datetime(d.year, d.month, d.day, tzinfo=timezone.utc), stale=False) for d in days[:9]]
        o = sc.operations(runs, today)
        self.assertEqual(o["days_with_run"], 9)
        self.assertTrue(o["ok"])
        self.assertFalse(sc.operations(runs[:5], today)["ok"])


if __name__ == "__main__":
    unittest.main()
