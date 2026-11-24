"""Opening-spread provenance regressions, with synthetic audit rows only."""
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd

from core import resting_runtime as R


class TestMeasuredSpread(unittest.TestCase):
    def test_late_and_future_quotes_do_not_displace_valid_opening_samples(self):
        def row(day, hour, hs):
            # MongoDB datetimes are naive UTC: 03:48 UTC = 09:18 IST.
            return dict(timestamp=datetime.fromisoformat(f"{day}T{hour}"),
                        candle_timestamp=datetime.fromisoformat(f"{day}T{hour}"),
                        details=dict(day=day, half_spread_bps=hs))
        late = [row("2026-11-23", "13:41:00", 900)] * 5
        rows = [row("2026-11-25", "03:48:00", 800)] + late + [row("2026-11-23", "03:48:00", 12)]
        cursor = MagicMock()
        cursor.sort.return_value = cursor
        cursor.limit.return_value = rows[:R.MEASURED_SAMPLES]
        cursor.__iter__.return_value = iter(rows)
        logs = MagicMock()
        logs.find.return_value = cursor
        with patch.dict("sys.modules", {"db": SimpleNamespace(logs_col=logs)}):
            self.assertEqual(R.measured_half_spread("ABC", "2026-11-23"), 12)

    def test_only_late_samples_fall_back_to_the_tier_model(self):
        rows = [dict(timestamp=datetime(2026, 11, 23, 13, 41, tzinfo=timezone.utc),
                     details=dict(day="2026-11-23", half_spread_bps=900))]
        cursor = MagicMock()
        cursor.sort.return_value = cursor
        cursor.limit.return_value = rows
        cursor.__iter__.return_value = iter(rows)
        logs = MagicMock()
        logs.find.return_value = cursor
        with patch.dict("sys.modules", {"db": SimpleNamespace(logs_col=logs)}):
            self.assertIsNone(R.measured_half_spread("ABC", "2026-11-23"))

    def test_late_morning_report_says_skipped_without_decorative_borders(self):
        now = datetime(2026, 11, 23, 13, 41, tzinfo=timezone.utc).astimezone(R.X.IST)
        text = R.format_morning_message(dict(day="2026-11-23", quotes=0, skipped="after the open window"), now)
        self.assertIn("Status: SKIPPED", text)
        self.assertNotIn("Nothing to execute", text)
        self.assertNotIn("━", text)


class TestEveningCacheBootstrap(unittest.TestCase):
    def test_missing_or_empty_cache_fetches_history_before_settlement(self):
        import resting_order_alerts as A
        from core import resting_jobs as J, s3_refresh as F
        from test_evening_freshness import daily_rows
        for cached in (None, pd.DataFrame()):
            with self.subTest(cache="missing" if cached is None else "empty"), \
                 patch.dict("sys.modules", {"db": SimpleNamespace(get_instrument_key_mapping=lambda: {"ABC": "key"}),
                                            "s3_paper_alerts": SimpleNamespace(_is_holiday=lambda d: False)}), \
                 patch.dict("os.environ", {"UPSTOX_ACCESS_TOKEN": "SYNTHETIC_TEST"}), \
                 patch.object(R, "build_deps") as build, patch.object(R, "symbols_needed", return_value=["ABC"]), \
                 patch.object(R, "daily_from_cache", return_value=cached), \
                 patch.object(F, "fetch_upstox_daily", return_value=(daily_rows(["2026-11-23"]), None)) as fetch, \
                 patch.object(F, "refresh_candles", side_effect=lambda candles, *a, **k: (candles, {})), \
                 patch.object(F, "describe", return_value=[]), patch.object(J, "run_evening", return_value={}):
                A.evening_execution(pd.Timestamp("2026-11-23"), 100000, True)
                fetch.assert_called_once()
                self.assertEqual(build.call_args.kwargs["candles"]["ABC"].iloc[-1]["CLOSE"], 101)
