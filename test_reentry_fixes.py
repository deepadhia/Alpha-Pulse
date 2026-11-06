# -*- coding: utf-8 -*-
"""
Tests for the 2026-11-05 re-entry / report fixes (CHANGELOG v3.7.1).  No network, no database writes.

  1. re-entry age cap helpers          (listing_day_breakout_scanner.reentry_listing_age_days)
  2. re-entry trigger = live > peak    (reentry_trigger_crossed)
  3. close-location on the real day low (reentry_close_location; the old gate read a synthetic row whose low was the live price)
  4. alert DNA line only claims the body gate when it ran
  5. a re-entry does not inherit the closed trade's shadow/exit/trap fields (db.stale_trade_fields)
  6. daily report: IST clock, /5 score (/4 for backfilled legacy rows), legacy tag
  7. live day-low capture in get_live_price
"""
import os
os.environ.setdefault("DISABLE_TELEGRAM", "1")
os.environ.setdefault("TESTING", "1")

import unittest
from datetime import date, datetime
from unittest.mock import MagicMock, patch

import listing_day_breakout_scanner as lds
import streamlined_ipo_scanner as sc
import db as dbmod


class TestReentryAge(unittest.TestCase):
    def test_lgeindia_case(self):
        # listed 2025-10-14, re-entry alert 2026-11-05: 387 days, must exceed the 60-day cap
        age = lds.reentry_listing_age_days("2025-10-14 00:00:00+00:00", date(2026, 11, 5))
        self.assertEqual(age, 387)
        self.assertGreater(age, lds.REENTRY_MAX_DAYS_SINCE_LISTING)

    def test_fresh_ipo_passes(self):
        self.assertEqual(lds.reentry_listing_age_days(datetime(2026, 10, 1), date(2026, 11, 5)), 35)
        self.assertLessEqual(35, lds.REENTRY_MAX_DAYS_SINCE_LISTING)

    def test_missing_or_bad_date_is_none(self):
        for bad in (None, float("nan"), "not a date", ""):
            self.assertIsNone(lds.reentry_listing_age_days(bad, date(2026, 11, 5)), bad)

    def test_default_cap_matches_breakout_cap(self):
        self.assertEqual(lds.REENTRY_MAX_DAYS_SINCE_LISTING, lds.MAX_DAYS_SINCE_LISTING_FOR_BREAKOUT)


class TestReentryTrigger(unittest.TestCase):
    def test_faded_breakout_is_not_a_trigger(self):
        # LGEINDIA 2026-11-05: day high 1836 > peak 1825, but live 1823.10 < peak
        self.assertFalse(lds.reentry_trigger_crossed(1823.10, 1825.0))

    def test_above_peak_is_a_trigger(self):
        self.assertTrue(lds.reentry_trigger_crossed(1826.0, 1825.0))

    def test_equal_or_bad_inputs(self):
        self.assertFalse(lds.reentry_trigger_crossed(1825.0, 1825.0))
        self.assertFalse(lds.reentry_trigger_crossed(None, 1825.0))
        self.assertFalse(lds.reentry_trigger_crossed(100.0, 0))


class TestCloseLocation(unittest.TestCase):
    def test_real_low(self):
        self.assertAlmostEqual(lds.reentry_close_location(1823.1, 1790.0, 1836.0), (1823.1 - 1790.0) / (1836.0 - 1790.0))
        self.assertGreater(lds.reentry_close_location(1823.1, 1790.0, 1836.0), 0.5)

    def test_weak_close_is_below_half(self):
        self.assertLess(lds.reentry_close_location(1800.0, 1790.0, 1836.0), 0.5)

    def test_unknown_low_is_none_not_zero(self):
        # the old code produced 0.0 (reject) or skipped silently; now "not evaluated" is explicit
        self.assertIsNone(lds.reentry_close_location(1823.1, None, 1836.0))
        self.assertIsNone(lds.reentry_close_location(1823.1, 1836.0, 1836.0))
        self.assertIsNone(lds.reentry_close_location(1823.1, 0, 1836.0))


class TestDnaLine(unittest.TestCase):
    def test_body_gate_claim_only_when_checked(self):
        base = dict(winner_score=3, volume_spike=5.3, listing_range_pct=8.7)
        self.assertIn("Upper 50% Body ✅", lds._format_dna_section(dict(base, upper_body_checked=True)))
        self.assertIn("not checked", lds._format_dna_section(dict(base, upper_body_checked=False)))
        # other alert types (gate always runs) keep the old text
        self.assertIn("Upper 50% Body ✅", lds._format_dna_section(base))

    def test_score_is_printed(self):
        self.assertIn("Score <b>3/5</b>", lds._format_dna_section(dict(winner_score=3, volume_spike=1, listing_range_pct=1)))


class TestStaleFields(unittest.TestCase):
    OLD = {
        "symbol": "LGEINDIA", "status": "CLOSED", "entry_price": 1800.6, "stop_loss": 1584.5,
        "shadow_status_8pct": "CLOSED", "shadow_exit_price_8pct": 1720.0, "shadow_sl_10pct": 1620.5,
        "exit_version": "3.5.0", "next_day_open": 1715.0, "archetype": "UPPER_WICK_SUPPLY_TRAP",
        "trap_flags": ["X"], "trap_score": 2, "algo_takeaway": "t", "winner_traits": ["a"],
        "peak_price_during_trade": 1836.0, "max_runup_pct": 3.0, "max_drawdown_pct": -4.0, "pnl_pct": -4.4, "days_held": 13,
    }

    def test_lists_old_trade_fields(self):
        got = set(dbmod.stale_trade_fields(self.OLD))
        for k in ("shadow_status_8pct", "shadow_exit_price_8pct", "shadow_sl_10pct", "exit_version", "next_day_open", "archetype",
                  "trap_flags", "trap_score", "algo_takeaway", "winner_traits", "peak_price_during_trade", "max_runup_pct",
                  "max_drawdown_pct", "pnl_pct", "days_held"):
            self.assertIn(k, got)

    def test_keeps_identity_and_entry_fields(self):
        got = set(dbmod.stale_trade_fields(self.OLD))
        for k in ("symbol", "status", "entry_price", "stop_loss"):
            self.assertNotIn(k, got)

    def test_empty(self):
        self.assertEqual(dbmod.stale_trade_fields({}), [])
        self.assertEqual(dbmod.stale_trade_fields(None), [])

    def test_reopen_unsets_only_fields_not_reset_by_payload(self):
        """upsert_position on a re-entry: stale fields are $unset, but fields the new payload sets again are not."""
        col = MagicMock()
        col.find_one.return_value = dict(self.OLD)
        new_doc = {"symbol": "LGEINDIA", "status": "ACTIVE", "entry_price": 1823.1, "stop_loss": 1677.25, "days_held": 0,
                   "max_runup_pct": 0.0, "max_drawdown_pct": 0.0, "entry_date": datetime(2026, 11, 5)}
        with patch.object(dbmod, "positions_col", col), patch.object(dbmod, "_trade_guard_enabled", return_value=False), \
                patch.object(dbmod, "_ledger", MagicMock()), patch.object(dbmod, "_ledger_position_events", MagicMock()):
            self.assertTrue(dbmod.upsert_position(new_doc))
        pipeline = col.update_one.call_args[0][1]
        unset = set(pipeline[1]["$unset"])
        self.assertIn("shadow_status_8pct", unset)
        self.assertIn("archetype", unset)
        self.assertIn("peak_price_during_trade", unset)
        self.assertNotIn("days_held", unset)          # re-set by the payload
        self.assertNotIn("max_runup_pct", unset)
        self.assertIn("exit_price", unset)            # the pre-existing exit-cycle list still applies

    def test_plain_refresh_of_open_position_unsets_nothing_extra(self):
        col = MagicMock()
        col.find_one.return_value = {"status": "ACTIVE", "entry_price": 100.0, "shadow_status_8pct": "ACTIVE", "peak_price_during_trade": 120.0}
        with patch.object(dbmod, "positions_col", col), patch.object(dbmod, "_trade_guard_enabled", return_value=False), \
                patch.object(dbmod, "_ledger", MagicMock()), patch.object(dbmod, "_ledger_position_events", MagicMock()):
            dbmod.upsert_position({"symbol": "X", "status": "ACTIVE", "current_price": 110.0})
        unset = set(col.update_one.call_args[0][1][1]["$unset"])
        self.assertNotIn("shadow_status_8pct", unset)
        self.assertNotIn("peak_price_during_trade", unset)


class TestDailyReport(unittest.TestCase):
    def _report(self, rows, now):
        fake_db = MagicMock()
        fake_db.positions.find.return_value = rows
        with patch("db.db", fake_db), patch.object(sc, "_now_ist", return_value=now):
            return sc.format_alphapulse_portfolio_report()

    def test_clock_is_ist_label_with_ist_time_and_legacy_scheme(self):
        rows = [
            {"symbol": "MILKYMIST", "status": "PAPER_ONLY", "grade": "LISTING_BREAKOUT", "entry_price": 185.0, "current_price": 297.0,
             "trailing_stop": 268.12, "pnl_pct": 60.6, "days_held": 89, "winner_score": 4, "winner_label": "POSSIBLE_WINNER", "_backfilled": True},
            {"symbol": "NEWCO", "status": "ACTIVE", "grade": "LISTING_BREAKOUT", "entry_price": 100.0, "current_price": 105.0,
             "trailing_stop": 92.0, "pnl_pct": 5.0, "days_held": 2, "winner_score": 4, "winner_label": "POSSIBLE_WINNER", "_backfilled": False},
        ]
        text = self._report(rows, datetime(2026, 11, 5, 18, 37))
        self.assertIn("18:37 IST", text)
        self.assertNotIn("13:07", text)
        self.assertIn("4/4", text)          # backfilled row keeps the old 4-criteria scheme
        self.assertIn("4/5", text)          # live row uses the 5-criteria scheme
        self.assertIn("📦", text)
        self.assertIn("backfilled legacy row", text)

    def test_no_legacy_note_when_all_rows_are_live(self):
        rows = [{"symbol": "NEWCO", "status": "ACTIVE", "grade": "LISTING_BREAKOUT", "entry_price": 100.0, "current_price": 105.0,
                 "trailing_stop": 92.0, "pnl_pct": 5.0, "days_held": 2, "winner_score": 3, "_backfilled": False}]
        text = self._report(rows, datetime(2026, 11, 5, 18, 37))
        self.assertNotIn("📦", text)
        self.assertIn("3/5", text)


class TestLiveDayLow(unittest.TestCase):
    def test_low_is_captured_and_cleared(self):
        def fake_upstox(symbol):
            sc._record_day_low(symbol, 1790.0)
            return (1823.1, 1836.0, 900000.0)
        with patch.object(sc, "get_live_price_upstox", fake_upstox):
            price, src, high, vol = sc.get_live_price("ZZTEST")
        self.assertEqual(sc.get_live_day_low("ZZTEST"), 1790.0)
        # next call: no source returns a low -> the old one must not leak
        with patch.object(sc, "get_live_price_upstox", lambda s: (1800.0, 1810.0, 1000.0)), patch.object(sc, "get_live_price_yfinance", lambda s: None):
            sc.get_live_price("ZZTEST")
        self.assertIsNone(sc.get_live_day_low("ZZTEST"))

    def test_bad_low_ignored(self):
        sc._live_day_low.pop("ZZBAD", None)
        sc._record_day_low("ZZBAD", None)
        sc._record_day_low("ZZBAD", "x")
        sc._record_day_low("ZZBAD", 0)
        self.assertIsNone(sc.get_live_day_low("ZZBAD"))


class TestReentryLoop(unittest.TestCase):
    """Drives the re-entry block of scan_listing_day_breakouts with everything external mocked."""

    def _run(self, listing_date, live, day_high, day_low, vol=900000.0, peak=1825.0, now_ist=None):
        import pandas as pd
        today = datetime.now().date()
        listing = pd.DataFrame([
            {"symbol": "DUMMY", "status": "ACTIVE", "listing_date": pd.Timestamp(today), "listing_day_high": 10.0, "listing_day_low": 9.0},
            {"symbol": "REENT", "status": "BREAKOUT", "listing_date": pd.Timestamp(listing_date), "listing_day_high": 1749.0, "listing_day_low": 1650.0},
        ])
        dates = pd.bdate_range(end=pd.Timestamp(today) - pd.Timedelta(days=1), periods=30)
        hist = pd.DataFrame({"DATE": dates, "OPEN": 1790.0, "HIGH": 1800.0, "LOW": 1760.0, "CLOSE": 1795.0, "VOLUME": 400000.0})
        sm = lds.scanner_module
        commits, sent = [], []

        def fake_commit(b):
            commits.append(dict(b))
            b["limit_buy_price"] = round(max(b["entry_price"] * 1.02, b["listing_day_high"] * 1.02), 2)
            return True, False, 3, "WEAK_BULL", 1.0

        pos = {"symbol": "REENT", "entry_price": 1800.0, "peak_price_during_trade": peak, "reentry_count": 0, "signal_id": "BREAKOUT_REENT_X"}
        sm.network_call_made = False
        with patch.object(lds, "update_listing_data_for_new_ipos", lambda: None),                 patch.object(lds, "load_listing_data", lambda: listing),                 patch.object(lds, "_market_is_open_ist", lambda: True),                 patch.object(lds, "check_listing_day_breakout", lambda *a, **k: None),                 patch.object(lds, "load_pending_breakouts", lambda *a, **k: {}),                 patch.object(lds, "save_pending_breakouts", lambda *a, **k: None),                 patch.object(lds, "commit_trade_to_db", fake_commit),                 patch.object(lds, "send_telegram", lambda m, *a, **k: sent.append(m)),                 patch.object(lds, "write_daily_log", lambda *a, **k: None),                 patch.object(sm, "get_bulk_live_prices_upstox", lambda x: {}, create=True),                 patch.object(sm, "get_live_price", lambda s: (live, "upstox", day_high, vol)),                 patch.object(sm, "get_live_day_low", lambda s: day_low, create=True),                 patch.object(sm, "fetch_data", lambda *a, **k: hist.copy()),                 patch.object(sm, "get_market_regime", lambda d: "WEAK_BULL"),                 patch("db.get_reentry_watchlist", lambda: [pos]),                 patch("db.positions_col", MagicMock()),                 patch("time.sleep", lambda s: None):
            ctx = patch.object(lds, "_now_ist", lambda: now_ist) if now_ist is not None else patch.object(lds, "REENTRY_TIME_ADJUST_VOLUME", lds.REENTRY_TIME_ADJUST_VOLUME)
            with ctx:
                lds.scan_listing_day_breakouts()
        return commits, sent

    def test_old_ipo_is_blocked_even_when_everything_else_passes(self):
        commits, sent = self._run(date(2025, 10, 14), live=1830.0, day_high=1836.0, day_low=1790.0)
        self.assertEqual(commits, [])
        self.assertEqual(sent, [])

    def test_faded_breakout_is_not_entered(self):
        commits, sent = self._run(datetime.now().date() - __import__("datetime").timedelta(days=30), live=1823.1, day_high=1836.0, day_low=1790.0)
        self.assertEqual(commits, [])

    def test_valid_reentry_is_committed_with_real_fields(self):
        listing_date = datetime.now().date() - __import__("datetime").timedelta(days=30)
        commits, sent = self._run(listing_date, live=1830.0, day_high=1836.0, day_low=1790.0)
        self.assertEqual(len(commits), 1)
        b = commits[0]
        self.assertEqual(b["days_since_listing"], 30)                  # real age, not a bar count
        self.assertTrue(b["upper_body_checked"])
        self.assertEqual(b["winner_score"], len(b["winner_criteria"]))
        self.assertGreater(b["winner_score"], 0)                        # computed, not hardcoded 0
        self.assertEqual(len(sent), 1)
        self.assertIn("Extension:</b> +0.3%", sent[0])
        self.assertIn("1,866.60", sent[0])                              # the limit commit_trade_to_db stores: max(live*1.02, peak*1.02)
        self.assertNotIn("+-", sent[0])

    def test_weak_close_on_real_low_is_rejected(self):
        listing_date = datetime.now().date() - __import__("datetime").timedelta(days=30)
        commits, _ = self._run(listing_date, live=1826.0, day_high=1836.0, day_low=1700.0)   # (1826-1700)/(136)=0.93 -> passes
        self.assertEqual(len(commits), 1)
        commits, _ = self._run(listing_date, live=1826.0, day_high=2000.0, day_low=1700.0)   # (126)/(300)=0.42 -> rejected
        self.assertEqual(commits, [])

    def test_missing_day_low_is_reported_not_silently_passed(self):
        listing_date = datetime.now().date() - __import__("datetime").timedelta(days=30)
        commits, sent = self._run(listing_date, live=1830.0, day_high=1836.0, day_low=None)
        self.assertEqual(len(commits), 1)
        self.assertFalse(commits[0]["upper_body_checked"])
        self.assertIn("not checked", sent[0])


class TestIntradayVolumeNormalisation(unittest.TestCase):
    """Re-entry volume gates compared volume so far with full-day numbers (2026-11-06)."""

    def test_profile_is_monotonic_and_anchored(self):
        from core import intraday_volume as iv
        self.assertEqual(len(iv.PROFILE), 26)
        self.assertTrue(all(b >= a for a, b in zip(iv.PROFILE, iv.PROFILE[1:])))
        self.assertEqual(iv.PROFILE[-1], 1.0)
        self.assertEqual(iv.cum_volume_fraction(datetime(2026, 11, 6, 15, 30)), 1.0)
        self.assertEqual(iv.cum_volume_fraction(datetime(2026, 11, 6, 18, 45)), 1.0)
        self.assertEqual(iv.cum_volume_fraction(None), 1.0)

    def test_fraction_values_and_floor(self):
        from core import intraday_volume as iv
        self.assertAlmostEqual(iv.cum_volume_fraction(datetime(2026, 11, 6, 10, 45)), 0.353, places=3)
        self.assertAlmostEqual(iv.cum_volume_fraction(datetime(2026, 11, 6, 12, 30)), 0.584, places=3)                      # a table point (195 min)
        self.assertAlmostEqual(iv.cum_volume_fraction(datetime(2026, 11, 6, 12, 37, 30)), (0.584 + 0.611) / 2, places=3)    # halfway between two
        self.assertEqual(iv.cum_volume_fraction(datetime(2026, 11, 6, 9, 16)), iv.MIN_FRACTION)       # earliest minutes are floored, never trusted
        self.assertEqual(iv.cum_volume_fraction(datetime(2026, 11, 6, 8, 0)), iv.MIN_FRACTION)

    def test_metrics_are_like_for_like(self):
        proj, turn, spike = lds.reentry_volume_metrics(240000, 1830.0, 400000.0, 0.353)
        self.assertAlmostEqual(spike, 1.70, places=2)                      # 240k vs 400k x 0.353 = 141k
        self.assertAlmostEqual(proj, 240000 / 0.353, places=0)
        self.assertAlmostEqual(turn, proj * 1830.0, places=0)
        self.assertEqual(lds.reentry_volume_metrics(240000, 1830.0, 400000.0, 1.0)[2], 0.60)     # the old (whole-day) reading
        self.assertEqual(lds.reentry_volume_metrics(240000, 1830.0, 0.0, 0.5)[2], 1.0)          # no average: unchanged behaviour (gate rejects)

    def test_rollback_flag_restores_whole_day_comparison(self):
        with patch.object(lds, "REENTRY_TIME_ADJUST_VOLUME", False):
            self.assertEqual(lds.reentry_volume_fraction(datetime(2026, 11, 6, 10, 45)), 1.0)
        with patch.object(lds, "REENTRY_TIME_ADJUST_VOLUME", True):
            self.assertAlmostEqual(lds.reentry_volume_fraction(datetime(2026, 11, 6, 10, 45)), 0.353, places=3)

    def test_midmorning_surge_now_passes_and_late_day_weak_volume_still_fails(self):
        d = datetime.now().date() - __import__("datetime").timedelta(days=30)
        t = TestReentryLoop()
        # 240k so far at 10:45 against a 400k daily average = 1.7x the pace of a typical day: a real surge (was 0.6x and rejected)
        commits, _ = t._run(d, live=1830.0, day_high=1836.0, day_low=1790.0, vol=240000.0, now_ist=datetime(2026, 11, 6, 10, 45))
        self.assertEqual(len(commits), 1)
        self.assertAlmostEqual(commits[0]["volume_spike"], 1.70, places=2)
        # the same 240k after the close is only 0.6x of a day: still rejected
        commits, _ = t._run(d, live=1830.0, day_high=1836.0, day_low=1790.0, vol=240000.0, now_ist=datetime(2026, 11, 6, 16, 15))
        self.assertEqual(commits, [])

    def test_liquidity_floor_uses_projected_day_volume(self):
        d = datetime.now().date() - __import__("datetime").timedelta(days=30)
        t = TestReentryLoop()
        # 60k shares at 10:45 projects to about 170k for the day: above the 150k floor, but the surge gate (0.43x) still rejects
        commits, _ = t._run(d, live=1830.0, day_high=1836.0, day_low=1790.0, vol=60000.0, now_ist=datetime(2026, 11, 6, 10, 45))
        self.assertEqual(commits, [])
        # 30k shares at 10:45 projects to 85k: below the floor
        commits, _ = t._run(d, live=1830.0, day_high=1836.0, day_low=1790.0, vol=30000.0, now_ist=datetime(2026, 11, 6, 10, 45))
        self.assertEqual(commits, [])


if __name__ == "__main__":
    unittest.main()
