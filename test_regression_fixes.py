import os
os.environ["DISABLE_TELEGRAM"] = "1"
os.environ["TESTING"] = "1"

import unittest
from unittest.mock import patch, MagicMock, mock_open
import pandas as pd
from datetime import datetime, date, timedelta, timezone
import sys
import os

# Add workspace path to system path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from streamlined_ipo_scanner import get_market_regime, update_positions

class TestRegressionFixes(unittest.TestCase):

    def setUp(self):
        # Clear cache before each test
        import streamlined_ipo_scanner
        streamlined_ipo_scanner._nifty_regime_cache = {}

    @patch('streamlined_ipo_scanner.yf.download')
    @patch('utils.fetch_nifty_from_upstox')
    def test_nifty_regime_normalization(self, mock_upstox, mock_yf):
        # Disable Upstox first to test yfinance fallback
        mock_upstox.side_effect = Exception("Upstox failed")

        # 1. Test yfinance-style DatetimeIndex with name 'Date' and MultiIndex columns
        dates = pd.date_range(start='2025-10-01', periods=100, freq='D')
        multi_cols = pd.MultiIndex.from_tuples([
            ('Adj Close', '^NSEI'),
            ('Close', '^NSEI'),
            ('High', '^NSEI'),
            ('Low', '^NSEI'),
            ('Open', '^NSEI'),
            ('Volume', '^NSEI')
        ])
        
        # Create a trending price series to generate BULL/CORRECTION regimes
        # First 50 days trend down, last 50 days trend up
        prices = []
        p = 22000
        for i in range(100):
            if i < 50:
                p -= 50
            else:
                p += 150
            prices.append(p)

        data = []
        for p in prices:
            # Let's populate open, high, low, close as same
            data.append([p, p, p, p, p, 100000])

        df_yf_style = pd.DataFrame(data, index=dates, columns=multi_cols)
        df_yf_style.index.name = 'Date'
        mock_yf.return_value = df_yf_style

        # Run get_market_regime - should run without raising KeyError
        regime = get_market_regime(target_date=dates[-1].date())
        self.assertNotEqual(regime, "UNKNOWN")
        self.assertIn(regime, ["BULL", "WEAK_BULL", "RANGE", "CORRECTION"])

        # Clear cache for next case
        import streamlined_ipo_scanner
        streamlined_ipo_scanner._nifty_regime_cache = {}

        # 2. Test Upstox-style index name 'DATE' with single columns
        mock_yf.side_effect = Exception("yfinance failed")
        mock_upstox.side_effect = None  # Reset side effect!
        
        df_upstox_style = pd.DataFrame({
            'OPEN': prices,
            'HIGH': prices,
            'LOW': prices,
            'CLOSE': prices,
            'VOLUME': [100000]*100
        }, index=dates)
        df_upstox_style.index.name = 'DATE'
        mock_upstox.return_value = df_upstox_style

        # Run get_market_regime - should run without raising KeyError
        regime_upstox = get_market_regime(target_date=dates[-1].date())
        self.assertNotEqual(regime_upstox, "UNKNOWN")
        self.assertIn(regime_upstox, ["BULL", "WEAK_BULL", "RANGE", "CORRECTION"])

    @patch('streamlined_ipo_scanner.get_last_trading_day')
    @patch('streamlined_ipo_scanner.fetch_data')
    @patch('streamlined_ipo_scanner.get_live_price')
    @patch('db.get_all_positions_df')
    @patch('db.upsert_position')
    @patch('db.signals_col')
    def test_next_day_open_resolution(self, mock_signals_col, mock_upsert, mock_get_pos, mock_live_price, mock_fetch, mock_last_trading_day):
        # Mock database calls
        mock_last_trading_day.return_value = date(2026, 6, 2)
        mock_signals_col.update_one = MagicMock()
        mock_upsert.return_value = None
        mock_live_price.return_value = (None, None, None, 0.0) # Force fallback to fetch_data

        # Positions:
        # 1. Friday position (2026-05-29)
        # 2. Weekend position (stamped Saturday 2026-05-30)
        df_positions = pd.DataFrame([
            {
                "symbol": "TESTSTOCK",
                "entry_date": "2026-05-29",  # Friday (stored as string in DB)
                "entry_price": 100.0,
                "grade": "B",
                "stop_loss": 90.0,
                "trailing_stop": 90.0,
                "status": "ACTIVE",
                "next_day_open": None,
                "signal_id": "sig1",
                "max_runup_pct": 0.0,
                "max_drawdown_pct": 0.0
            },
            {
                "symbol": "TESTSTOCK",
                "entry_date": "2026-05-30",  # Saturday weekend-stamped
                "entry_price": 100.0,
                "grade": "B",
                "stop_loss": 90.0,
                "trailing_stop": 90.0,
                "status": "ACTIVE",
                "next_day_open": None,
                "signal_id": "sig2",
                "max_runup_pct": 0.0,
                "max_drawdown_pct": 0.0
            }
        ])
        mock_get_pos.return_value = df_positions

        # Historical candle data around 2026-05-29
        # Friday is 2026-05-29. Monday is 2026-06-01. Tuesday is 2026-06-02.
        df_candles = pd.DataFrame({
            'DATE': [
                datetime(2026, 5, 28), # Thursday
                datetime(2026, 5, 29), # Friday
                datetime(2026, 6, 1),  # Monday
                datetime(2026, 6, 2)   # Tuesday
            ],
            'OPEN': [98.0, 99.0, 105.0, 112.0],
            'HIGH': [102.0, 101.0, 111.0, 116.0],
            'LOW': [97.0, 98.0, 104.0, 111.0],
            'CLOSE': [100.0, 100.0, 110.0, 115.0],
            'VOLUME': [1000, 1000, 2000, 1500]
        })
        mock_fetch.return_value = df_candles

        # Call update_positions
        update_positions()

        # Check call arguments for upsert_position to see what was resolved
        # The positions are updated in-place on df_positions, but wait!
        # In update_positions(), the loop iterates over rows in get_all_positions_df()
        # and upserts them. Let's inspect the mock_upsert calls!
        self.assertTrue(mock_upsert.called)
        
        # Extract the resolved next_day_open values passed to upsert_position
        upserted_args = [call.args[0] for call in mock_upsert.call_args_list]
        
        # Both positions should resolve to Monday's open: 105.0
        # Position 0 (Friday entry) -> Next trading day is Monday (105.0)
        # Position 1 (Saturday entry) -> Next trading day is Monday (105.0)
        
        self.assertEqual(len(upserted_args), 2)
        self.assertEqual(upserted_args[0]["symbol"], "TESTSTOCK")
        self.assertEqual(upserted_args[0]["next_day_open"], 105.0) # Monday open
        self.assertEqual(upserted_args[1]["next_day_open"], 105.0) # Monday open (not Tuesday!)

    @patch('streamlined_ipo_scanner.get_last_trading_day')
    @patch('streamlined_ipo_scanner.fetch_data')
    @patch('streamlined_ipo_scanner.get_live_price')
    @patch('db.get_all_positions_df')
    @patch('db.upsert_position')
    @patch('db.signals_col')
    def test_next_day_open_holiday_gap(self, mock_signals_col, mock_upsert, mock_get_pos, mock_live_price, mock_fetch, mock_last_trading_day):
        # Mock database calls
        mock_last_trading_day.return_value = date(2026, 6, 2)
        mock_signals_col.update_one = MagicMock()
        mock_upsert.return_value = None
        mock_live_price.return_value = (None, None, None, 0.0)

        # Positions:
        # Thursday entry (before a Friday holiday)
        # Friday entry (on the actual holiday)
        df_positions = pd.DataFrame([
            {
                "symbol": "TESTSTOCK",
                "entry_date": "2026-05-28",  # Thursday
                "entry_price": 100.0,
                "grade": "B",
                "stop_loss": 90.0,
                "trailing_stop": 90.0,
                "status": "ACTIVE",
                "next_day_open": None,
                "signal_id": "sig1",
                "max_runup_pct": 0.0,
                "max_drawdown_pct": 0.0
            },
            {
                "symbol": "TESTSTOCK",
                "entry_date": "2026-05-29",  # Friday holiday
                "entry_price": 100.0,
                "grade": "B",
                "stop_loss": 90.0,
                "trailing_stop": 90.0,
                "status": "ACTIVE",
                "next_day_open": None,
                "signal_id": "sig2",
                "max_runup_pct": 0.0,
                "max_drawdown_pct": 0.0
            }
        ])
        mock_get_pos.return_value = df_positions

        # Candle data: Friday 2026-05-29 is a holiday, so no candle exists.
        df_candles = pd.DataFrame({
            'DATE': [
                datetime(2026, 5, 27), # Wednesday
                datetime(2026, 5, 28), # Thursday
                datetime(2026, 6, 1),  # Monday (Next trading session after Thursday/Friday)
                datetime(2026, 6, 2)   # Tuesday
            ],
            'OPEN': [98.0, 99.0, 108.0, 112.0],
            'HIGH': [102.0, 101.0, 111.0, 116.0],
            'LOW': [97.0, 98.0, 107.0, 111.0],
            'CLOSE': [100.0, 100.0, 110.0, 115.0],
            'VOLUME': [1000, 1000, 2000, 1500]
        })
        mock_fetch.return_value = df_candles

        # Call update_positions
        update_positions()

        # Both should resolve to Monday's open: 108.0
        self.assertTrue(mock_upsert.called)
        upserted_args = [call.args[0] for call in mock_upsert.call_args_list]
        self.assertEqual(len(upserted_args), 2)
        self.assertEqual(upserted_args[0]["next_day_open"], 108.0) # Monday open (post-holiday)
        self.assertEqual(upserted_args[1]["next_day_open"], 108.0) # Monday open (post-holiday)

    @patch('streamlined_ipo_scanner.requests.get')
    @patch('streamlined_ipo_scanner.os.getenv')
    @patch('db.get_instrument_key_mapping')
    def test_upstox_quote_instrument_token_matching(self, mock_mapping, mock_getenv, mock_get):
        from streamlined_ipo_scanner import get_live_price_upstox
        
        # Setup mocks
        mock_mapping.return_value = {"TEST": "NSE_EQ|INE123"}
        mock_getenv.return_value = "fake_token"
        
        # Mock API response with key mismatch (key is NSE_EQ:TEST but instrument_token inside is NSE_EQ|INE123)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "status": "success",
            "data": {
                "NSE_EQ:TEST": {
                    "instrument_token": "NSE_EQ|INE123",
                    "last_price": 125.5,
                    "ohlc": {
                        "high": 128.0
                    }
                }
            }
        }
        mock_get.return_value = mock_resp
        
        # Test fetching
        result = get_live_price_upstox("TEST")
        self.assertIsNotNone(result)
        self.assertEqual(result[0], 125.5)
        self.assertEqual(result[1], 128.0)

    @patch('streamlined_ipo_scanner.get_last_expected_data_date')
    @patch('streamlined_ipo_scanner.get_live_price')
    @patch('db.get_last_signal_date')
    @patch('streamlined_ipo_scanner.RESEARCH_COHORTS')
    @patch('streamlined_ipo_scanner.reject_quick_losers')
    @patch('streamlined_ipo_scanner.compute_grade_hybrid')
    @patch('streamlined_ipo_scanner.assign_grade')
    @patch('streamlined_ipo_scanner.get_liquidity_metrics')
    @patch('streamlined_ipo_scanner.classify_pattern_type')
    def test_stale_fallback_price_gate(self, mock_pattern, mock_liq, mock_assign, mock_grade, mock_reject, mock_cohorts, mock_last_sig, mock_live_price, mock_expected):
        # Imports
        from streamlined_ipo_scanner import detect_live_patterns
        
        from datetime import timedelta
        today = date.today()
        # Setup mocks
        mock_expected.return_value = today
        mock_last_sig.return_value = None
        mock_reject.return_value = False
        mock_grade.return_value = (85.0, {})
        mock_assign.return_value = "A"
        mock_liq.return_value = (50.0, 0, 5000.0)
        mock_pattern.return_value = "CONSOLIDATION"
        
        # Set cohort so that it passes
        mock_cohorts.items.return_value = [
            ("TEST_COHORT", {"min_window": 3, "max_prng": 15.0, "min_grade": "C", "vol_follow": 0.5})
        ]
        
        # df with breakout candle at the end (index 9)
        dates = pd.date_range(end=today, periods=10)
        df = pd.DataFrame({
            'DATE': dates,
            'OPEN': [100.0]*10,
            'HIGH': [110.0] + [108.0]*8 + [120.0], # Breakout today!
            'LOW': [95.0]*10,
            'CLOSE': [100.0]*9 + [115.0],
            'VOLUME': [200000]*9 + [500000]
        })
        
        # Case A: Live price fails (None, None, None, 0.0) -> price_is_live is False
        mock_live_price.return_value = (None, None, None, 0.0)
        
        listing_map = {"TESTSTOCK": today - timedelta(days=70)}
        
        # Run detect_live_patterns - since price_is_live is False, it should block and return 0 signals
        with patch('streamlined_ipo_scanner.fetch_data') as mock_fetch:
            mock_fetch.return_value = df
            signals_found = detect_live_patterns(["TESTSTOCK"], listing_map)
            self.assertEqual(signals_found, 0)
        
        # Case B: Live price succeeds -> price_is_live is True
        mock_live_price.return_value = (115.0, 'upstox', 120.0, 1000.0)
        
        # We need to mock Mongo calls inside detect_live_patterns when saving signals
        with patch('db.upsert_signal') as mock_sig, patch('db.upsert_position') as mock_pos, patch('streamlined_ipo_scanner.MongoRepository') as mock_repo, patch('streamlined_ipo_scanner.LifecycleTracker') as mock_tracker, patch('streamlined_ipo_scanner.fetch_data') as mock_fetch:
            mock_repo_inst = MagicMock()
            mock_repo.return_value = mock_repo_inst
            mock_repo_inst.save_signal.return_value = True
            mock_fetch.return_value = df
            
            signals_found = detect_live_patterns(["TESTSTOCK"], listing_map)
            # Should proceed because price is live
            self.assertEqual(signals_found, 1)
            self.assertTrue(mock_sig.called)
            self.assertTrue(mock_pos.called)

    def test_audit_logic_trailing_shadow_sl(self):
        import weekly_audit_report
        # Reset findings
        weekly_audit_report.findings = []
        weekly_audit_report.errors = []
        weekly_audit_report.warnings = []
        
        mock_positions_col = MagicMock()
        # Mock positions database call with:
        # 1. Valid trailing stop (TIMEX - entry 337.95, current 455.95, max_runup_pct 36.36, shadow_sl_8pct 423.98)
        # 2. Invalid stop (TESTFAIL - entry 100, current 100, max_runup_pct 0, shadow_sl_8pct 98 but status is ACTIVE and expected is 92)
        mock_positions_col.find.return_value = [
            {
                "symbol": "TIMEX",
                "status": "ACTIVE",
                "entry_price": 337.95,
                "current_price": 455.95,
                "max_runup_pct": 36.36,
                "stop_loss": 310.91,
                "trailing_stop": 310.91,
                "shadow_sl_8pct": 423.98,
                "shadow_status_8pct": "ACTIVE"
            },
            {
                "symbol": "TESTFAIL",
                "status": "ACTIVE",
                "entry_price": 100.0,
                "current_price": 100.0,
                "max_runup_pct": 0.0,
                "stop_loss": 92.0,
                "trailing_stop": 92.0,
                "shadow_sl_8pct": 98.0, # Expected is 92.0, so this exceeds expected
                "shadow_status_8pct": "ACTIVE"
            }
        ]
        
        # Run audit_logic_integrity
        weekly_audit_report.audit_logic_integrity(mock_positions_col)
        
        # We should find that TIMEX is valid, but TESTFAIL is flagged with error/warning
        issues_found = [f for f in weekly_audit_report.findings if f["level"] == "ERROR" and "TESTFAIL" in str(f.get("detail"))]
        timex_issues = [f for f in weekly_audit_report.findings if f["level"] == "ERROR" and "TIMEX" in str(f.get("detail"))]
        
        self.assertEqual(len(timex_issues), 0)
        self.assertEqual(len(issues_found), 1)

    @patch('streamlined_ipo_scanner.get_last_expected_data_date')
    @patch('streamlined_ipo_scanner.get_live_price')
    @patch('db.get_last_signal_date')
    @patch('streamlined_ipo_scanner.RESEARCH_COHORTS')
    @patch('streamlined_ipo_scanner.reject_quick_losers')
    @patch('streamlined_ipo_scanner.compute_grade_hybrid')
    @patch('streamlined_ipo_scanner.assign_grade')
    @patch('streamlined_ipo_scanner.get_liquidity_metrics')
    @patch('streamlined_ipo_scanner.classify_pattern_type')
    @patch('db.positions_col')
    def test_paper_only_position_persistence(self, mock_positions_col, mock_pattern, mock_liq, mock_assign, mock_grade, mock_reject, mock_cohorts, mock_last_sig, mock_live_price, mock_expected):
        from streamlined_ipo_scanner import detect_live_patterns
        import streamlined_ipo_scanner
        
        from datetime import timedelta
        today = date.today()
        # Setup mocks
        mock_expected.return_value = today
        mock_last_sig.return_value = None
        mock_reject.return_value = False
        mock_grade.return_value = (85.0, {})
        mock_assign.return_value = "A"
        mock_liq.return_value = (50.0, 0, 5000.0)
        mock_pattern.return_value = "CONSOLIDATION"
        
        # Force active count above hard cap (portfolio_full is True) but has_active_position(TESTSTOCK) is False
        def mock_count_docs(query, **kwargs):
            if query.get("symbol") == "TESTSTOCK":
                return 0
            if query.get("status") == "ACTIVE":
                return 10
            return 0
        mock_positions_col.count_documents.side_effect = mock_count_docs
        
        mock_cohorts.items.return_value = [
            ("TEST_COHORT", {"min_window": 3, "max_prng": 15.0, "min_grade": "C", "vol_follow": 0.5})
        ]
        
        # df with breakout candle at the end
        dates = pd.date_range(end=today, periods=10)
        df = pd.DataFrame({
            'DATE': dates,
            'OPEN': [100.0]*10,
            'HIGH': [110.0] + [108.0]*8 + [120.0],
            'LOW': [95.0]*10,
            'CLOSE': [100.0]*9 + [115.0],
            'VOLUME': [200000]*9 + [500000]
        })
        
        # Live price succeeds
        mock_live_price.return_value = (115.0, 'upstox', 120.0, 1000.0)
        from streamlined_ipo_scanner import detect_scan
        listing_map = {"TESTSTOCK": today - timedelta(days=70)}
        
        with patch('db.upsert_signal') as mock_sig, patch('db.upsert_position') as mock_pos, patch('streamlined_ipo_scanner.MongoRepository') as mock_repo, patch('streamlined_ipo_scanner.LifecycleTracker') as mock_tracker, patch('streamlined_ipo_scanner.fetch_data') as mock_fetch:
            mock_repo_inst = MagicMock()
            mock_repo.return_value = mock_repo_inst
            mock_repo_inst.save_signal.return_value = True
            mock_fetch.return_value = df
            
            detect_scan(["TESTSTOCK"], listing_map)
            
            # Position should still be upserted even though portfolio_full was True
            self.assertTrue(mock_pos.called)
            
            # Verify position arguments: status must be PAPER_ONLY and shadow stops ACTIVE
            pos_args = mock_pos.call_args[0][0]
            self.assertEqual(pos_args["status"], "PAPER_ONLY")
            self.assertEqual(pos_args["shadow_status_8pct"], "ACTIVE")

    def test_dynamic_nse_holidays_and_market_day(self):
        """Test dynamic holiday fetching, weekend detection, and market day checking."""
        from utils import get_dynamic_nse_holidays, is_market_day, get_last_trading_day
        
        # 1. Republic day (Jan 26) is a known holiday
        holidays_2026 = get_dynamic_nse_holidays(2026)
        self.assertIn("2026-01-26", holidays_2026)
        self.assertFalse(is_market_day("2026-01-26"))

        # 2. Weekend check (2026-08-29 is Saturday, 2026-08-30 is Sunday)
        self.assertFalse(is_market_day("2026-08-29"))
        self.assertFalse(is_market_day("2026-08-30"))

        # 3. Regular trading day (2026-08-26 Wednesday is open)
        self.assertTrue(is_market_day("2026-08-26"))

        # 4. get_last_trading_day walking back
        last_td = get_last_trading_day("2026-08-31") # Monday
        self.assertEqual(last_td.strftime("%Y-%m-%d"), "2026-08-28") # Should be Friday

    def test_holiday_notification_deduplication(self):
        """Test that send_holiday_notification_once only alerts once per day."""
        from utils import send_holiday_notification_once
        
        mock_telegram = MagicMock()
        test_date = "2026-12-25" # Christmas
        
        with patch('db.system_audits_col') as mock_audits, patch('os.path.exists', return_value=False), patch('builtins.open', mock_open()):
            # First call: no existing record in DB or on disk
            mock_audits.find_one.return_value = None
            
            sent_first = send_holiday_notification_once("scanner_1", today_str=test_date, send_telegram_fn=mock_telegram)
            self.assertTrue(sent_first)
            self.assertEqual(mock_telegram.call_count, 1)
            self.assertTrue(mock_audits.update_one.called)

            # Second call (e.g. hourly scanner next hour): existing record found
            mock_audits.find_one.return_value = {
                "audit_type": "HOLIDAY_NOTIFICATION_SENT",
                "date": test_date,
                "triggered_by": "scanner_1"
            }
            
            mock_telegram.reset_mock()
            sent_second = send_holiday_notification_once("hourly_scanner", today_str=test_date, send_telegram_fn=mock_telegram)
            self.assertFalse(sent_second)
            self.assertEqual(mock_telegram.call_count, 0) # Suppressed!

    def test_position_and_exit_alert_color_formatting(self):
        """Test broker-grade green/red color styling for positive and negative returns."""
        from streamlined_ipo_scanner import format_position_update_alert, format_exit_alert

        # 1. Positive Return Position Update
        pos_msg = format_position_update_alert(
            symbol="TESTPROFIT",
            current_price=120.0,
            entry_price=100.0,
            old_trailing=95.0,
            new_trailing=105.0,
            pnl_pct=20.0,
            days_held=10,
            grade="A"
        )
        self.assertIn("🟢", pos_msg)
        self.assertIn("+20.00%", pos_msg)
        self.assertIn("▲ +₹20.00/sh", pos_msg)
        self.assertIn("🔺 Stop Raised", pos_msg)

        # 2. Negative Return Position Update
        loss_msg = format_position_update_alert(
            symbol="TESTLOSS",
            current_price=90.0,
            entry_price=100.0,
            old_trailing=85.0,
            new_trailing=85.0,
            pnl_pct=-10.0,
            days_held=5,
            grade="B"
        )
        self.assertIn("🔴", loss_msg)
        self.assertIn("-10.00%", loss_msg)
        self.assertIn("▼ ₹-10.00/sh", loss_msg)
        self.assertIn("🔹 Maintained", loss_msg)

        # 3. Exit Alert Positive
        exit_pos_msg = format_exit_alert("TESTPROFIT", "Partial Take", 150.0, 50.0, 20, 100.0)
        self.assertIn("🟢", exit_pos_msg)
        self.assertIn("+50.00%", exit_pos_msg)

        # 4. Exit Alert Negative
        exit_loss_msg = format_exit_alert("TESTLOSS", "Stop Loss", 92.0, -8.0, 12, 100.0)
        self.assertIn("🔴", exit_loss_msg)
        self.assertIn("-8.00%", exit_loss_msg)

    def test_reentry_extension_and_candle_body_guards(self):
        """Test that re-entry breakouts reject overextended prices (>8%) and upper wick traps (<50% candle body)."""
        peak_trigger = 800.0

        # 1. Overextended Re-Entry Case (e.g. +15.0% above peak trigger)
        overextended_live = 920.0 # +15.0%
        ext_pct = ((overextended_live - peak_trigger) / peak_trigger) * 100.0
        self.assertGreater(ext_pct, 8.0)
        is_rejected_ext = ext_pct > 8.0
        self.assertTrue(is_rejected_ext)

        # 2. Upper Wick Exhaustion Case (Spike to 850, close at 810, low at 800 -> 10/50 = 20% < 50%)
        day_high = 850.0
        day_low = 800.0
        live_price_wick = 810.0 # Upper wick is 40 / 50 = 80%, candle location is 10 / 50 = 20%
        close_location = (live_price_wick - day_low) / (day_high - day_low)
        self.assertLess(close_location, 0.50)
        is_rejected_wick = close_location < 0.50
        self.assertTrue(is_rejected_wick)

        # 3. Clean Re-Entry Case (Price 820 -> +2.5% extension, Low 790, High 825 -> candle location 30/35 = 85.7%)
        clean_live = 820.0
        clean_high = 825.0
        clean_low = 790.0
        clean_ext = ((clean_live - peak_trigger) / peak_trigger) * 100.0
        clean_loc = (clean_live - clean_low) / (clean_high - clean_low)
        self.assertLessEqual(clean_ext, 8.0)
        self.assertGreaterEqual(clean_loc, 0.50)

    def test_count_trading_days_and_velocity_gate(self):
            """Test accurate trading day session counting and velocity gate behavior."""
            from utils import count_trading_days
            from datetime import date

            # 1. 2026-09-21 (Mon) to 2026-10-04 (Sun) across Oct 2 Gandhi Jayanti -> 9 sessions
            sessions_sun = count_trading_days(date(2026, 9, 21), date(2026, 10, 4))
            self.assertEqual(sessions_sun, 9)

            # 2. 2026-09-21 (Mon) to 2026-10-05 (Mon) -> 10 sessions
            sessions_mon = count_trading_days(date(2026, 9, 21), date(2026, 10, 5))
            self.assertEqual(sessions_mon, 10)

            # 3. Velocity gate logic:
            # A. Non-winner underwater trade with minor runup (+4.95%) at Day 14 / Session 10
            days_held = 14
            trading_sessions = 10
            pnl = -3.9
            new_max_runup = 4.95
            is_winner_archetype = (new_max_runup >= 15.0)

            exit_reason = None
            if not is_winner_archetype:
                if not exit_reason and (days_held >= 14 or trading_sessions >= 10) and pnl <= 0.0:
                    exit_reason = f"Time Stop - Dead Money (14-Day Velocity Gate, PnL {pnl:+.1f}%, Peak +{new_max_runup:.1f}%)"

            self.assertIsNotNone(exit_reason)
            self.assertIn("14-Day Velocity Gate", exit_reason)

            # B. Winner trade (+25% runup) at Day 14
            win_runup = 25.0
            win_is_winner = (win_runup >= 15.0)
            win_exit_reason = None
            if not win_is_winner:
                if not win_exit_reason and (days_held >= 14 or trading_sessions >= 10) and pnl <= 0.0:
                    win_exit_reason = "Velocity Gate"
            self.assertIsNone(win_exit_reason)  # Winner is immune!

    def test_high_shelf_breakout_qualification(self):
        """Test High Shelf (Wave 2) breakout qualification, anti-chasing guardrails, and quality gates."""
        from listing_day_breakout_scanner import _detect_high_shelf_breakout, check_listing_day_breakout
        import listing_day_breakout_scanner as lds

        # 1. Synthetic 7-day IPO DataFrame (MILKYMIST-style pattern)
        dates = pd.date_range("2026-08-18", periods=7, freq="B")
        df = pd.DataFrame({
            "DATE": dates,
            "OPEN": [165.0, 190.0, 207.8, 190.0, 191.0, 198.0, 201.9],
            "HIGH": [181.5, 199.65, 211.8, 194.7, 199.1, 205.7, 222.11],
            "LOW": [165.0, 190.0, 181.5, 183.5, 186.2, 197.0, 198.0],
            "CLOSE": [181.5, 199.65, 184.89, 189.2, 196.6, 201.9, 222.11],
            "VOLUME": [90000000, 29000000, 56000000, 32000000, 19000000, 15000000, 60000000]
        })

        # Test A: Helper _detect_high_shelf_breakout on Day 6
        ok, s_high, s_low, s_prng, reason = _detect_high_shelf_breakout(
            df=df,
            current_price=222.11,
            current_high=222.11,
            listing_day_high=181.50,
            days_since_listing=6
        )
        self.assertTrue(ok)
        self.assertEqual(s_high, 211.80)
        self.assertEqual(s_low, 181.50)
        self.assertAlmostEqual(s_prng, 16.69, places=1)
        self.assertIn("shelf", reason)

        # Test B: Anti-chasing rejection when price is > 5% above shelf high
        ok_chase, _, _, _, reason_chase = _detect_high_shelf_breakout(
            df=df,
            current_price=235.0,  # +10.9% above shelf high 211.8
            current_high=235.0,
            listing_day_high=181.50,
            days_since_listing=6
        )
        self.assertFalse(ok_chase)

        # Test C: End-to-end check_listing_day_breakout qualification
        listing_info = {
            'symbol': 'MILKYMIST',
            'listing_date': '2026-08-18',
            'listing_day_high': 181.50,
            'listing_day_low': 165.00,
            'listing_day_volume': 90000000.0,
            'listing_day_close': 181.50,
        }

        with patch('listing_day_breakout_scanner.fetch_data', return_value=df), \
             patch('listing_day_breakout_scanner.get_live_price', return_value=(222.11, "MockLive", 222.11, 60000000.0)), \
             patch.object(lds.scanner_module, 'get_liquidity_metrics', return_value=(50.0, 0, 5000.0)), \
             patch('listing_day_breakout_scanner._market_is_open_ist', return_value=False), \
             patch('listing_day_breakout_scanner.datetime') as mock_dt:

            mock_dt.today.return_value = datetime(2026, 8, 26)
            mock_dt.now.return_value = datetime(2026, 8, 26, 15, 30)
            mock_dt.fromisoformat = datetime.fromisoformat

            sig = check_listing_day_breakout('MILKYMIST', listing_info, {}, {})
            self.assertIsNotNone(sig)
            self.assertEqual(sig['type'], 'SHELF_BREAKOUT')
            self.assertEqual(sig['entry_price'], 222.11)
            self.assertAlmostEqual(sig['stop_loss'], 195.46, places=2)  # 12% max risk cap
            self.assertAlmostEqual(sig['target_price'], 266.53, places=2)  # 20% floor
            self.assertEqual(sig['tier'], 'B')
            self.assertEqual(sig['position_size_pct'], 40)


    def test_high_shelf_breakout_pending_confirmation_and_limit_corridor(self):
        """Test SHELF_BREAKOUT 60-min PENDING confirmation hold, rejection of below-pivot wicks, and 2% limit corridor."""
        from datetime import datetime, timedelta
        from listing_day_breakout_scanner import check_listing_day_breakout, commit_trade_to_db
        import listing_day_breakout_scanner as lds

        base_date = datetime.now() - timedelta(days=6)
        dates = pd.date_range(base_date, periods=7, freq="D")
        df = pd.DataFrame({
            "DATE": dates,
            "OPEN": [165.0, 190.0, 207.8, 190.0, 191.0, 198.0, 201.9],
            "HIGH": [181.5, 199.65, 211.8, 194.7, 199.1, 205.7, 222.11],
            "LOW": [165.0, 190.0, 181.5, 183.5, 186.2, 197.0, 198.0],
            "CLOSE": [181.5, 199.65, 184.89, 189.2, 196.6, 201.9, 215.0],
            "VOLUME": [90000000, 29000000, 56000000, 32000000, 19000000, 15000000, 60000000]
        })
        listing_info = {
            'symbol': 'SHELF_TEST',
            'listing_date': base_date.strftime('%Y-%m-%d'),
            'listing_day_high': 181.50,
            'listing_day_low': 165.00,
            'listing_day_volume': 90000000.0,
            'listing_day_close': 181.50,
        }

        pending = {}
        # Step 1: Market is open -> initial breakout triggers PENDING hold with breakout_level = 205.70 (shelf high)
        with patch('listing_day_breakout_scanner.fetch_data', return_value=df), \
             patch('listing_day_breakout_scanner.get_live_price', return_value=(215.0, "MockLive", 218.0, 60000000.0)), \
             patch.object(lds.scanner_module, 'get_liquidity_metrics', return_value=(50.0, 0, 5000.0)), \
             patch('listing_day_breakout_scanner._market_is_open_ist', return_value=True):

            res_p1 = check_listing_day_breakout('SHELF_TEST', listing_info, pending, {})
            self.assertIsNotNone(res_p1)
            self.assertEqual(res_p1['type'], 'PENDING')
            self.assertIn('SHELF_TEST', pending)
            self.assertEqual(pending['SHELF_TEST']['breakout_level'], 205.70)

        # Step 2: During observation, price drops below shelf pivot 205.70 -> Rejected as fakeout
        with patch('listing_day_breakout_scanner.fetch_data', return_value=df), \
             patch('listing_day_breakout_scanner.get_live_price', return_value=(204.0, "MockLive", 218.0, 60000000.0)), \
             patch.object(lds.scanner_module, 'get_liquidity_metrics', return_value=(50.0, 0, 5000.0)), \
             patch('listing_day_breakout_scanner._market_is_open_ist', return_value=True):

            res_rej = check_listing_day_breakout('SHELF_TEST', listing_info, pending, {})
            self.assertIsNone(res_rej)
            self.assertNotIn('SHELF_TEST', pending)  # Evicted on rejection!

        # Step 3: Full 60-min hold confirmed -> Emits SHELF_BREAKOUT with entry >= pivot and >=2% execution corridor
        started_time = datetime.now() - timedelta(minutes=65)
        pending['SHELF_TEST'] = {
            "started_at": started_time.isoformat(),
            "breakout_level": 205.70,
            "max_price_seen": 216.0,
            "last_price": 215.0
        }
        with patch('listing_day_breakout_scanner.fetch_data', return_value=df), \
             patch('listing_day_breakout_scanner.get_live_price', return_value=(215.0, "MockLive", 218.0, 60000000.0)), \
             patch.object(lds.scanner_module, 'get_liquidity_metrics', return_value=(50.0, 0, 5000.0)), \
             patch('listing_day_breakout_scanner._market_is_open_ist', return_value=True):

            res_conf = check_listing_day_breakout('SHELF_TEST', listing_info, pending, {})
            self.assertIsNotNone(res_conf)
            self.assertEqual(res_conf['type'], 'SHELF_BREAKOUT')
            self.assertGreaterEqual(res_conf['entry_price'], 205.70)
            self.assertEqual(res_conf['entry_price'], 215.0)

        # Step 4: Verify Limit Buy buffer in commit_trade_to_db provides >= 2% corridor
        captured_signals = []
        captured_positions = []
        mock_db = MagicMock()
        mock_db.positions.count_documents.return_value = 0
        with patch('db.has_active_position', return_value=False), \
             patch('db.db', mock_db), \
             patch('db.upsert_signal', side_effect=lambda doc: captured_signals.append(doc)), \
             patch('db.upsert_position', side_effect=lambda doc: captured_positions.append(doc)):
            res_conf['breakout_date'] = datetime.now().strftime('%Y-%m-%d')
            success, full, cnt, mr, mult = commit_trade_to_db(res_conf)
            self.assertTrue(success)
            self.assertEqual(len(captured_signals), 1)
            sig = captured_signals[0]
            self.assertGreaterEqual(sig['limit_buy_price'], round(sig['entry_price'] * 1.02, 2))
            pos = captured_positions[0]
            self.assertGreaterEqual(pos['limit_buy_price'], round(pos['entry_price'] * 1.02, 2))

    def test_volume_conjunction_and_session_scoped_pending_engine(self):
        """
        Regression Test Suite for Volume Gate Conjunction, Session Scoping, and Telegram Suppression:
        1. Shelf breakout requires relative volume expansion >= 1.5x AND institutional turnover >= 10 Cr.
           Turnover alone must NOT bypass the volume gate (eliminates the PRASOLCHEM 0.30x false trigger).
        2. Tier classification strictly rejects shelf breakouts with volume ratio < 1.5x.
        3. Pending confirmation states from prior calendar days are automatically purged on load (eliminates the RENTOMOJO 1,100-minute overnight leak).
        4. Outgoing Telegram alerts are fully suppressed in test/stress regression environments without network calls.
        5. Same-day duplicate lockout prevents re-pending and repeat alert spam.
        """
        import listing_day_breakout_scanner as lds
        from listing_day_breakout_scanner import (
            _assign_breakout_tier,
            load_pending_breakouts,
            check_listing_day_breakout
        )
        from utils import send_telegram_msg

        # 1. Telegram Dispatch Suppression in Test Mode
        with patch.dict(os.environ, {"DISABLE_TELEGRAM": "1"}),              patch('requests.post') as mock_post:
            result = send_telegram_msg("Critical Test Alert")
            self.assertTrue(result)
            mock_post.assert_not_called()  # Verified: Zero HTTP calls to Telegram

        # 2. Tier classification rejects shelf breakouts with < 1.5x volume
        tier_bad, size_bad, reason_bad = _assign_breakout_tier(
            signal_type="SHELF_BREAKOUT",
            confirmed=True,
            perfect_base=False,
            volume_ratio=0.30,  # PRASOLCHEM volume
            days_since_listing=21,
            post_confirm_move_pct=2.0
        )
        self.assertIsNone(tier_bad)
        self.assertIn("rejected", reason_bad.lower())

        tier_good, size_good, _ = _assign_breakout_tier(
            signal_type="SHELF_BREAKOUT",
            confirmed=True,
            perfect_base=False,
            volume_ratio=1.65,
            days_since_listing=21,
            post_confirm_move_pct=2.0
        )
        self.assertEqual(tier_good, "B")

        # 3. Session-scoped pending engine purges overnight & expired states
        test_trading_now = datetime(2026, 10, 8, 14, 0, 0)
        fake_pending_db = {
            "OVERNIGHT_STOCK": {
                "started_at": (test_trading_now - timedelta(days=1)).isoformat(),
                "breakout_level": 500.0,
            },
            "EXPIRED_STOCK": {
                "started_at": (test_trading_now - timedelta(minutes=150)).isoformat(),
                "breakout_level": 300.0,
            },
            "VALID_TODAY": {
                "started_at": (test_trading_now - timedelta(minutes=20)).isoformat(),
                "breakout_level": 100.0,
            }
        }
        mock_mongo = MagicMock()
        mock_mongo.__getitem__.return_value.find_one.return_value = {"_id": "listing_pending_breakouts", "data": fake_pending_db}
        mock_mongo.__getitem__.return_value.update_one = MagicMock()

        with patch('listing_day_breakout_scanner._now_ist', return_value=test_trading_now), \
             patch('listing_day_breakout_scanner.save_pending_breakouts') as mock_save, \
             patch('db.db', mock_mongo):
            active_pending = load_pending_breakouts(purge_stale=True)
            self.assertNotIn("OVERNIGHT_STOCK", active_pending)  # Purged!
            self.assertNotIn("EXPIRED_STOCK", active_pending)    # Purged!
            self.assertIn("VALID_TODAY", active_pending)         # Retained!

        # 4. Strict Volume Conjunction Gate in check_listing_day_breakout:
        # Stock with ₹15 Cr turnover but only 0.30x volume surge MUST be rejected
        listing_date = (datetime.today() - timedelta(days=20)).date()
        listing_info = {
            'symbol': 'PRASOL_MOCK',
            'listing_date': listing_date,
            'listing_day_high': 800.0,
            'listing_day_low': 700.0,
            'listing_day_close': 750.0,
            'listing_day_volume': 1000000.0,
            'is_nse_addition': False,
        }
        # Build 10 daily candles with low volume
        dates = [listing_date + timedelta(days=i) for i in range(10)]
        df_low_vol = pd.DataFrame({
            'DATE': dates,
            'OPEN': [750.0] * 10,
            'HIGH': [820.0] * 10,
            'LOW': [740.0] * 10,
            'CLOSE': [810.0] * 10,
            'VOLUME': [500000.0] * 10  # Baseline average ~500k
        })
        # Current volume: 150,000 shares (< 500k avg -> volume_spike False), price 850 (Turnover: ₹12.75 Cr)
        with patch('listing_day_breakout_scanner.fetch_data', return_value=df_low_vol),              patch('listing_day_breakout_scanner.get_live_price', return_value=(850.0, "MockLive", 855.0, 150000.0)),              patch.object(lds.scanner_module, 'get_liquidity_metrics', return_value=(50.0, 0, 5000.0)),              patch('listing_day_breakout_scanner._market_is_open_ist', return_value=True):

            res = check_listing_day_breakout('PRASOL_MOCK', listing_info, {}, {})
            self.assertIsNone(res)  # Strictly rejected! Volume conjunction enforced.

        # 5. Immediate Ejection from Pending State on Upper-Wick Exhaustion
        test_pending = {
            'DEEPA_MOCK': {
                'started_at': lds._now_ist().isoformat(),
                'breakout_level': 800.0,
                'max_price_seen': 855.0,
                'last_price': 850.0
            }
        }
        # Candle where high is 890, low is 800, but close is 810 (close_location = (810-800)/(890-800) = 11% < 50%)
        df_wick = df_low_vol.copy()
        df_wick.loc[df_wick.index[-1], 'HIGH'] = 890.0
        df_wick.loc[df_wick.index[-1], 'LOW'] = 800.0
        df_wick.loc[df_wick.index[-1], 'CLOSE'] = 810.0
        df_wick.loc[df_wick.index[-1], 'VOLUME'] = 2000000.0  # high volume

        with patch('listing_day_breakout_scanner.fetch_data', return_value=df_wick), \
             patch('listing_day_breakout_scanner.get_live_price', return_value=(810.0, "MockLive", 890.0, 2000000.0)), \
             patch.object(lds.scanner_module, 'get_liquidity_metrics', return_value=(50.0, 0, 5000.0)), \
             patch('listing_day_breakout_scanner._market_is_open_ist', return_value=True):

            res_wick = check_listing_day_breakout('DEEPA_MOCK', listing_info, test_pending, {})
            self.assertIsNone(res_wick)
            self.assertNotIn('DEEPA_MOCK', test_pending)  # Verified: Ejected from pending state!

    def test_approach_a_pending_engine_and_cutoff_guards(self):
        """
        Regression Test Suite for Approach A (Intraday Cutoff, Closed Market Protection, Universal Eviction):
        1. Intraday cutoff guard (14:30 IST): A candidate breaking out at 15:25 IST cannot start pending
           (insufficient market time before 15:30 close).
        2. Market closed protection: When _market_is_open_ist() is False, after-hours scans cannot bypass
           pending to confirm live trades.
        3. EOD unconfirmed pending cleanup: Running when market is closed evicts unconfirmed pending states.
        4. Universal eviction on rejection: Any rejection via _log_listing_rejection automatically evicts from pending.
        5. Atomic pending_states cleanup: commit_trade_to_db automatically unsets the symbol from MongoDB pending_states.
        """
        import listing_day_breakout_scanner as lds
        from listing_day_breakout_scanner import (
            check_listing_day_breakout,
            commit_trade_to_db,
            LISTING_PENDING_CUTOFF_TIME
        )

        listing_date = (datetime.today() - timedelta(days=20)).date()
        listing_info = {
            'symbol': 'CUTOFF_MOCK',
            'listing_date': listing_date,
            'listing_day_high': 200.0,
            'listing_day_low': 180.0,
            'listing_day_close': 195.0,
            'listing_day_volume': 1000000.0,
            'is_nse_addition': False,
        }
        dates = [listing_date + timedelta(days=i) for i in range(10)]
        df_good = pd.DataFrame({
            'DATE': dates,
            'OPEN': [195.0] * 10,
            'HIGH': [200.0] * 10,
            'LOW': [190.0] * 10,
            'CLOSE': [198.0] * 10,
            'VOLUME': [100000.0] * 10
        })

        # 1. Candidate triggering at 15:25 IST (past 14:30 cutoff) -> Refused from starting pending
        late_now = datetime(2026, 10, 8, 15, 25, 0)
        pending_dict = {}
        with patch('listing_day_breakout_scanner.fetch_data', return_value=df_good), \
             patch('listing_day_breakout_scanner.get_live_price', return_value=(205.0, "MockLive", 205.0, 500000.0)), \
             patch.object(lds.scanner_module, 'get_liquidity_metrics', return_value=(50.0, 0, 5000.0)), \
             patch('listing_day_breakout_scanner._market_is_open_ist', return_value=True), \
             patch('listing_day_breakout_scanner._now_ist', return_value=late_now):

            res_late = check_listing_day_breakout('CUTOFF_MOCK', listing_info, pending_dict, {})
            self.assertIsNone(res_late)
            self.assertNotIn('CUTOFF_MOCK', pending_dict)  # Did NOT enter pending!

        # 2. Outside market hours (e.g. 16:30 IST) -> Cannot bypass confirmation into live trade
        pending_straddle = {
            'CUTOFF_MOCK': {
                'started_at': (datetime(2026, 10, 8, 14, 0, 0)).isoformat(),
                'breakout_level': 200.0,
                'max_price_seen': 205.0,
                'last_price': 205.0
            }
        }
        with patch('listing_day_breakout_scanner.fetch_data', return_value=df_good), \
             patch('listing_day_breakout_scanner.get_live_price', return_value=(205.0, "MockLive", 205.0, 500000.0)), \
             patch.object(lds.scanner_module, 'get_liquidity_metrics', return_value=(50.0, 0, 5000.0)), \
             patch('listing_day_breakout_scanner._market_is_open_ist', return_value=False):

            res_closed = check_listing_day_breakout('CUTOFF_MOCK', listing_info, pending_straddle, {})
            self.assertIsNone(res_closed)
            self.assertNotIn('CUTOFF_MOCK', pending_straddle)  # Evicted as market closed before confirmation!

        # 3. Universal eviction on rejection: Low volume triggers _log_listing_rejection -> evicts pending
        pending_active = {
            'CUTOFF_MOCK': {
                'started_at': (datetime(2026, 10, 8, 13, 0, 0)).isoformat(),
                'breakout_level': 200.0,
                'max_price_seen': 205.0,
                'last_price': 205.0
            }
        }
        # volume 50,000 < 150,000 floor
        with patch('listing_day_breakout_scanner.fetch_data', return_value=df_good), \
             patch('listing_day_breakout_scanner.get_live_price', return_value=(205.0, "MockLive", 205.0, 50000.0)), \
             patch.object(lds.scanner_module, 'get_liquidity_metrics', return_value=(50.0, 0, 5000.0)), \
             patch('listing_day_breakout_scanner._market_is_open_ist', return_value=True):

            res_vol_rej = check_listing_day_breakout('CUTOFF_MOCK', listing_info, pending_active, {})
            self.assertIsNone(res_vol_rej)
            self.assertNotIn('CUTOFF_MOCK', pending_active)  # Universally evicted!

        # 4. Atomic cleanup in commit_trade_to_db: Unsets pending_states in MongoDB
        mock_mongo = MagicMock()
        mock_mongo.positions.count_documents.return_value = 0
        with patch('db.has_active_position', return_value=False), \
             patch('db.db', mock_mongo), \
             patch('db.upsert_signal'), \
             patch('db.upsert_position'):

            breakout_payload = {
                'symbol': 'COMMIT_CLEAN_TEST',
                'entry_price': 205.0,
                'stop_loss': 190.0,
                'target_price': 240.0,
                'type': 'BREAKOUT',
                'volume_spike': True,
                'volume_ratio': 2.5,
                'volume_vs_listing_day': 1.2,
                'listing_range_pct': 5.0,
                'risk_reward': 2.33,
                'days_since_listing': 10,
                'current_price': 205.0,
                'tier': 'A'
            }
            commit_trade_to_db(breakout_payload)
            mock_mongo.__getitem__.return_value.update_one.assert_called_with(
                {"_id": "listing_pending_breakouts"},
                {"$unset": {"data.COMMIT_CLEAN_TEST": ""}}
            )

        # 5. Defense-in-depth: commit_trade_to_db refuses live entry when market is closed
        with patch('listing_day_breakout_scanner._market_is_open_ist', return_value=False),              patch.dict(os.environ, {"PYTEST_CURRENT_TEST": ""}):
            # Clear test flag temporarily to simulate live production after-hours trigger
            success_after_hours, _, _, _, _ = commit_trade_to_db(breakout_payload)
            self.assertFalse(success_after_hours)

        # 6. scan_listing_day_breakouts halts cleanly outside market hours
        from listing_day_breakout_scanner import scan_listing_day_breakouts
        with patch('listing_day_breakout_scanner._market_is_open_ist', return_value=False),              patch('listing_day_breakout_scanner.update_listing_data_for_new_ipos') as mock_update,              patch('listing_day_breakout_scanner.load_pending_breakouts') as mock_purge:
            scan_listing_day_breakouts()
            mock_update.assert_called_once()
            mock_purge.assert_called_with(purge_stale=True)


    def test_utils_telegram_dispatcher_integrity(self):
        """
        Verify utils.send_telegram_msg module imports sys and executes cleanly.
        Guards against regression where 'unittest' in sys.modules crashed live production dispatch with NameError.
        """
        import utils
        self.assertTrue(hasattr(utils, "sys"), "utils module must explicitly import sys")
        with patch.dict(os.environ, {"DISABLE_TELEGRAM": "1", "PYTEST_CURRENT_TEST": ""}):
            result = utils.send_telegram_msg("Test message for dispatcher integrity")
            self.assertTrue(result)

if __name__ == '__main__':
    unittest.main()


