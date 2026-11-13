"""SYNTHETIC_TEST regressions for the operational audit; no external writes."""
import contextlib
import ast
import html
import io
import logging
from pathlib import Path
import sys
import types
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from contextlib import contextmanager
import threading

sys.path.insert(0, str(Path(__file__).parent))
import pandas as pd
import resting_order_alerts as R
from core import ledger, resting_exec as X, s3_refresh, trade_guard
from core.resting_jobs import _bar_on, _before, run_evening
from test_resting_jobs import D, NOW, World, history, order, quote, with_bar
from core.resting_jobs import run_morning


@contextlib.contextmanager
def fake_main(settlement_error=None, with_candidate=False):
    db = types.ModuleType("db")
    db.positions_col = MagicMock()
    db.positions_col.find.return_value = []
    db.system_audits_col = MagicMock()
    audit_docs = {}
    db.system_audits_col.find_one.side_effect = lambda query: audit_docs.get(query["audit_id"])
    db.system_audits_col.update_one.side_effect = lambda query, update, **kwargs: audit_docs.update({query["audit_id"]: update["$set"]})
    db.trade_ledger_col = MagicMock()
    db.trade_ledger_col.find_one.return_value = None
    db.SCANNER_VERSION = "3.8.0"
    utils = types.ModuleType("utils")
    utils.send_telegram_msg = MagicMock(return_value=True)
    osh = types.ModuleType("order_sheet")
    candidates = [dict(symbol="SYNTHETIC", trigger=101.5, limit=102.5, fill=101.8,
                       stop=90.0, age=30, rr=1.67, last_close=100.0)] if with_candidate else []
    osh.resting_candidates = MagicMock(return_value=(candidates, [], pd.Timestamp("2026-11-12"), None))
    s3 = types.ModuleType("s3_paper_alerts")
    s3.enable_exchange_holidays = lambda: True
    s3.next_session = lambda day: day + pd.Timedelta(days=1)
    s3._closed = lambda day: False
    rep = dict(filled=[], expired=[], waiting=[], sold=[], deferred=[], day0_stops=[], exit_waiting=[], notes=[])
    with patch.dict(sys.modules, {"db": db, "utils": utils, "order_sheet": osh, "s3_paper_alerts": s3}), \
         patch.object(R, "evening_execution", side_effect=settlement_error, return_value=rep), \
         patch.object(trade_guard, "default_is_trading_day", lambda day: day.weekday() < 5), \
         patch.object(trade_guard, "previous_trading_day", lambda day: day - timedelta(days=1)), \
         patch.object(ledger, "ensure_indexes"), patch.object(ledger, "append", return_value={"seq": 1}) as append, \
         contextlib.redirect_stdout(io.StringIO()):
        yield db, utils.send_telegram_msg, append


class AuditRegressions(unittest.TestCase):
    def test_refresh_accepts_timezone_aware_cache(self):
        df = history()
        df["DATE"] = df["DATE"].dt.tz_localize("Asia/Kolkata")
        # Already-current data must not need a network request.
        result, rep = s3_refresh.refresh_candles({"ABC": df}, [], ["ABC"], "2026-11-11", "synthetic", {}, sleep=lambda _: None)
        self.assertEqual(rep["fetched"], 0)

    def test_bar_lookup_accepts_timezone_aware_cache(self):
        df = with_bar(history(), D, 100, 103, 99, 102)
        df["DATE"] = df["DATE"].dt.tz_localize("Asia/Kolkata")
        self.assertIsNotNone(_bar_on(df, D), "present bar silently treated as missing")

    def test_history_slice_accepts_timezone_aware_cache(self):
        df = history()
        df["DATE"] = df["DATE"].dt.tz_localize("Asia/Kolkata")
        self.assertEqual(len(_before(df, D)), len(df))

    def test_evening_retries_yesterdays_unsettled_order(self):
        w = World(orders=[order(day="2026-11-11")], daily={"ABC": with_bar(history(), D, 100, 103, 99, 102)})
        run_evening(w.deps, D)
        previous = X.orders_for_day(w.ledger, "2026-11-11")[0]
        self.assertNotEqual(X.order_state(w.ledger, w.positions, previous), "PLACED", "old order never revisited after delayed data or a failed evening")

    def test_main_records_completion_after_sending(self):
        with fake_main() as (db, send, append):
            R.main(["--today", "2026-11-12"])
            db.system_audits_col.update_one.assert_called_once()

    def test_retry_does_not_send_same_alert_twice(self):
        with fake_main() as (db, send, append):
            failures = []
            for _ in range(2):
                try:
                    R.main(["--today", "2026-11-12"])
                except Exception as exc:
                    failures.append(f"{type(exc).__name__}: {exc}")
            self.assertEqual(send.call_count, 1, f"sent {send.call_count} times; failures={failures}")

    def test_failed_settlement_withholds_new_orders(self):
        with fake_main(RuntimeError("synthetic settlement failure"), with_candidate=True) as (db, send, append):
            try:
                R.main(["--today", "2026-11-12"])
            except Exception:
                pass
            self.assertFalse(append.called, "new ORDER_GENERATED written after settlement failure")
            self.assertNotIn("PLACE BEFORE THE OPEN", send.call_args.args[0])

    def test_fill_respects_portfolio_cap_after_other_scanner_fills(self):
        held = [dict(symbol=f"HELD{i}", status="ACTIVE", entry_price=100.0) for i in range(10)]
        w = World(orders=[order()], positions=held, daily={"ABC": history()}, quotes={"ABC": quote(102.0, 101.9, 102.1)})
        run_morning(w.deps, NOW)
        active = [p for p in w.positions.rows if p.get("status") == "ACTIVE"]
        self.assertLessEqual(len(active), 10, "autofill opens an 11th ACTIVE position without checking or locking the portfolio")

    def test_portfolio_report_keeps_modelled_forward_fill_out_of_live_book(self):
        w = World(orders=[order()], daily={"ABC": history()}, quotes={"ABC": quote(102.0, 101.9, 102.1)})
        run_morning(w.deps, NOW)
        source = Path(__file__).with_name("streamlined_ipo_scanner.py")
        tree = ast.parse(source.read_text(encoding="utf-8-sig"))
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "format_alphapulse_portfolio_report")
        # Execute the real formatter only; skip scanner module import and its runtime initialization.
        scope = dict(datetime=datetime, pd=pd, logger=logging.getLogger("SYNTHETIC_TEST"), _now_ist=lambda: NOW)
        exec(compile(ast.Module(body=[fn], type_ignores=[]), str(source), "exec"), scope)
        db = types.ModuleType("db")
        db.db = types.SimpleNamespace(positions=w.positions)
        # Formatter uses find() without a query.
        db.db = types.SimpleNamespace(positions=types.SimpleNamespace(find=lambda: w.positions.rows))
        utils = types.ModuleType("utils")
        utils.escape_html_text = lambda text: html.escape(str(text or ""))
        with patch.dict(sys.modules, {"db": db, "utils": utils}):
            report = scope["format_alphapulse_portfolio_report"]()
        self.assertIn("LIVE BOOK (0 Active)", report, "automatic PAPER-forward fill mislabelled LIVE BOOK")
        self.assertIn("P RESTING PAPER FORWARD (1 Active)", report)


class TestRecoveryAndExposure(unittest.TestCase):
    def test_original_session_fill_day_stop_and_next_open_sale_are_recovered(self):
        hist = history().iloc[:-1]
        daily = with_bar(with_bar(hist, "2026-11-11", 102, 104, 88, 89), D, 90, 92, 89, 91)
        w = World(orders=[order(day="2026-11-11")], daily={"ABC": daily})
        rep = run_evening(w.deps, D)
        pos = w.positions.find_one({"symbol": "ABC"})
        self.assertEqual(pos["entry_date"].date().isoformat(), "2026-11-11")
        self.assertEqual(pos["exit_date"].date().isoformat(), "2026-11-12")
        self.assertEqual(pos["recovery_asof"], "2026-11-11")
        self.assertEqual(pos["status"], "CLOSED")
        self.assertTrue(pos["exit_settled"])
        self.assertLess(pos["exit_price"], 90)
        self.assertEqual(len(rep["sold"]), 1)
        self.assertEqual(run_evening(w.deps, D)["filled"], [])

    def test_recovery_gap_stays_pending_and_resumes_without_a_second_fill(self):
        daily = with_bar(history().iloc[:-1], "2026-11-11", 102, 104, 100, 103)
        w = World(orders=[order(day="2026-11-11")], daily={"ABC": daily})
        rep = run_evening(w.deps, D)
        pos = w.positions.find_one({"symbol": "ABC"})
        self.assertTrue(pos["recovery_pending"])
        self.assertTrue(rep["waiting"])
        w._daily["ABC"] = with_bar(daily, D, 103, 106, 102, 105)
        rep2 = run_evening(w.deps, D)
        self.assertFalse(w.positions.find_one({"symbol": "ABC"})["recovery_pending"])
        self.assertEqual(rep2["filled"], [])
        self.assertEqual(len(w.positions.rows), 1)

    def test_ledger_fill_survives_symbol_reentry(self):
        w = World(orders=[order(day="2026-11-11")], positions=[dict(symbol="ABC", signal_id="RESTING_ABC_20261112")])
        w._append("ENTRY_ACCEPTED", "ABC", dict(signal_id="RESTING_ABC_20261111"))
        self.assertEqual(X.unsettled_orders_through(w.ledger, w.positions, D), [])

    def test_pending_sale_still_consumes_capacity(self):
        held = [dict(symbol=f"H{i}", status="ACTIVE") for i in range(9)]
        held.append(dict(symbol="UNSOLD", status="CLOSED", entry_source=X.ENTRY_SOURCE, exit_settled=False))
        w = World(orders=[order()], positions=held, daily={"ABC": history()}, quotes={"ABC": quote(102, 101.9, 102.1)})
        rep = run_morning(w.deps, NOW)
        self.assertEqual(rep["filled"], [])
        self.assertIn("slot", rep["expired"][0])

    def test_unavailable_commit_lock_keeps_order_pending(self):
        @contextmanager
        def unavailable():
            yield False
        w = World(orders=[order()], daily={"ABC": history()}, quotes={"ABC": quote(102, 101.9, 102.1)})
        w.deps.commit_lock = unavailable
        with self.assertRaisesRegex(RuntimeError, "lock unavailable"):
            run_morning(w.deps, NOW)
        self.assertEqual(w.positions.rows, [])
        self.assertEqual(w.events, [])

    def test_two_fills_racing_for_last_slot_cannot_both_open(self):
        mutex = threading.Lock()
        @contextmanager
        def locked():
            with mutex:
                yield True
        w = World(positions=[dict(symbol=f"H{i}", status="ACTIVE") for i in range(9)])
        w.deps.commit_lock = locked
        results, errors = [], []
        def attempt(sym):
            from core.resting_jobs import _open_position
            try:
                event = order(sym=sym)["payload"]
                results.append(_open_position(w.deps, event, dict(price=102.1, model="SYNTHETIC_TEST"), NOW.replace(tzinfo=None), history()))
            except Exception as exc:
                errors.append(exc)
        threads = [threading.Thread(target=attempt, args=(s,)) for s in ("ABC", "XYZ")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(results.count(None), 1)
        self.assertEqual(len(w.positions.rows), 10)

    def test_aware_and_mixed_session_dates_preserve_the_ist_day(self):
        from core.session_dates import daily_dates
        values = pd.Series(["2026-11-11T18:30:00Z", "2026-11-12T00:00:00+05:30", "2026-11-12"])
        self.assertEqual(list(daily_dates(values)), [pd.Timestamp(D)] * 3)
        now = datetime(2026, 11, 12, 13, 0, tzinfo=__import__('datetime').timezone.utc)
        self.assertEqual(s3_refresh.last_complete_date(now, pd.Timestamp(D)), pd.Timestamp(D))


class TestForwardCohorts(unittest.TestCase):
    def test_sources_versions_setups_and_pending_sales_are_separate(self):
        from core.forward_cohorts import split_cohorts
        base = dict(symbol="SYNTHETIC", entry_date="2026-11-11", version="3.8.0", status="CLOSED", pnl_pct=-4, grade="LISTING_BREAKOUT")
        docs = [base, dict(base, entry_source=X.ENTRY_SOURCE, resting_version="1.0.0", exit_settled=True),
                dict(base, entry_source=X.ENTRY_SOURCE, resting_version="1.1.0", exit_settled=False),
                dict(base, version="3.7.0"), dict(base, status="PAPER_CLOSED", signal_id="SHELF_SYNTHETIC_20261111"),
                dict(base, _backfilled=True), dict(base, version="unknown")]
        groups, excluded = split_cohorts(docs)
        self.assertEqual(len(groups), 5)
        self.assertEqual(excluded, 2)
        forward = groups["3.8.0 | PAPER FORWARD / P RESTING 1.1.0 | LISTING_BREAKOUT"]
        self.assertEqual((len(forward["closed"]), len(forward["pending"])), (0, 1))
        self.assertFalse(any(key.startswith("LIVE") for key in groups))

    def test_cutoff_uses_exchange_date_for_aware_entries(self):
        from core.forward_cohorts import split_cohorts
        docs = [dict(entry_date=pd.Timestamp("2026-11-11T18:30:00Z"), version="3.8.0", status="ACTIVE")]
        groups, excluded = split_cohorts(docs, "2026-11-12")
        self.assertEqual((len(groups), excluded), (1, 0))


class TestPortfolioReportFormatting(unittest.TestCase):
    def test_stop_loss_risk_and_cushion_display(self):
        import db as db_module
        from streamlined_ipo_scanner import format_alphapulse_portfolio_report

        mock_positions = [
            {
                "symbol": "EDELALTS",
                "status": "PAPER_ONLY",
                "entry_price": 1852.90,
                "current_price": 1974.20,
                "stop_loss": 1630.55,
                "trailing_stop": 1630.55,
                "pnl_pct": 6.5,
                "days_held": 0,
                "signal_id": "SHELF_EDELALTS_20261113",
                "version": "3.8.0",
            }
        ]
        with patch.object(db_module.db.positions, "find", return_value=mock_positions):
            rep = format_alphapulse_portfolio_report()
            self.assertIn("SL: ₹1,630.55 (-12.0% from entry · cushion 17.4%)", rep)


if __name__ == "__main__":
    unittest.main(verbosity=2)
