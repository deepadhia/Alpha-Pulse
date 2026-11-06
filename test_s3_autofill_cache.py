# -*- coding: utf-8 -*-
"""
Tests for the 2026-11-06 open-item fixes (CHANGELOG v3.7.2): S3 automatic paper fills (core/s3_autofill.py), the stale-cache rebuild
(core/cache_refresh.py), the S3 alert dedupe, and the IST stamping of signal_date / entry_date.  No network, no database.
"""
import os
os.environ.setdefault("DISABLE_TELEGRAM", "1")
os.environ.setdefault("TESTING", "1")

import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pandas as pd

from core import cache_refresh as cr
from core import s3_autofill as af
from core import s3_store
from s3_paper_alerts import build_message
from test_s3_paper import FakeCol

UTC = timezone.utc


def order(sym="AAA", day="2026-11-04", trig=100.0, lim=102.5, qty=50, created=None):
    od = datetime.fromisoformat(day).replace(tzinfo=UTC)
    return dict(symbol=sym, order_date=od, trigger=trig, limit=lim, quantity=qty, status="PLACED", pivot=98.5, klass="LONG_BASE", turn20=5.0,
                created_at=created or (od - timedelta(hours=4)))          # stored the evening before: valid


def candles(sym_days):
    """{sym: DataFrame} from {sym: [(date, open, high, low, close)]}"""
    out = {}
    for sym, rows in sym_days.items():
        out[sym] = pd.DataFrame([dict(DATE=pd.Timestamp(d), OPEN=o, HIGH=h, LOW=l, CLOSE=c, VOLUME=1e6) for d, o, h, l, c in rows])
    return out


class TestDecide(unittest.TestCase):
    def test_fill_at_trigger_with_replay_slippage(self):
        d = af.decide(order(), dict(OPEN=99.0, HIGH=101.0))
        self.assertEqual(d["action"], "FILL")
        self.assertAlmostEqual(d["price"], round(100.0 * 1.003, 2))

    def test_gap_up_fills_at_open(self):
        d = af.decide(order(), dict(OPEN=101.5, HIGH=104.0))
        self.assertEqual(d["action"], "FILL")
        self.assertAlmostEqual(d["price"], round(101.5 * 1.003, 2))

    def test_price_is_capped_at_the_limit(self):
        d = af.decide(order(lim=102.5), dict(OPEN=102.4, HIGH=104.0))
        self.assertEqual(d["action"], "FILL")
        self.assertLessEqual(d["price"], 102.5)

    def test_trigger_not_reached_expires(self):
        d = af.decide(order(), dict(OPEN=98.0, HIGH=99.9))
        self.assertEqual(d["action"], "EXPIRE")
        self.assertIn("trigger not reached", d["reason"])

    def test_open_above_limit_expires(self):
        d = af.decide(order(), dict(OPEN=103.0, HIGH=106.0))
        self.assertEqual(d["action"], "EXPIRE")
        self.assertIn("above the limit", d["reason"])

    def test_no_bar_waits(self):
        self.assertEqual(af.decide(order(), None)["action"], "WAIT")

    def test_order_stored_after_the_open_is_not_valid(self):
        late = order(created=datetime(2026, 11, 4, 17, 59, tzinfo=UTC))          # 23:29 IST on the session day itself
        d = af.decide(late, dict(OPEN=99.0, HIGH=105.0))
        self.assertEqual(d["action"], "EXPIRE")
        self.assertIn("after the session opened", d["reason"])

    def test_order_stored_at_the_open_boundary(self):
        edge = order(created=datetime(2026, 11, 4, 3, 44, tzinfo=UTC))           # 09:14 IST: still before the open
        self.assertEqual(af.decide(edge, dict(OPEN=99.0, HIGH=105.0))["action"], "FILL")


class TestAutofill(unittest.TestCase):
    def setUp(self):
        self.orders, self.pos = FakeCol(), FakeCol()

    def test_fill_creates_position_and_marks_order(self):
        self.orders.docs.append(order())
        c = candles({"AAA": [("2026-11-04", 99.0, 101.0, 98.5, 100.5)]})
        res = af.autofill(self.orders, self.pos, c, "2026-11-04")
        self.assertEqual(len(res["filled"]), 1)
        p = self.pos.docs[0]
        self.assertEqual((p["symbol"], p["status"], p["quantity"]), ("AAA", "OPEN", 50))
        self.assertTrue(p["auto_filled"])
        self.assertIn("auto:", p["fill_model"])
        self.assertAlmostEqual(p["entry_price"], round(100.0 * 1.003, 2))
        self.assertEqual(self.orders.docs[0]["status"], "FILLED")
        self.assertEqual(self.orders.docs[0]["fill_price"], p["entry_price"])

    def test_unfilled_order_expires_with_reason(self):
        self.orders.docs.append(order())
        res = af.autofill(self.orders, self.pos, candles({"AAA": [("2026-11-04", 98.0, 99.0, 97.0, 98.5)]}), "2026-11-04")
        self.assertEqual(self.pos.docs, [])
        self.assertEqual(self.orders.docs[0]["status"], "EXPIRED")
        self.assertIn("trigger not reached", self.orders.docs[0]["expired_reason"])
        self.assertEqual(len(res["expired"]), 1)

    def test_missing_candle_waits_then_closes_out(self):
        self.orders.docs.append(order())
        res = af.autofill(self.orders, self.pos, {}, "2026-11-05")
        self.assertEqual(res["waiting"], ["AAA 2026-11-04"])
        self.assertEqual(self.orders.docs[0]["status"], "PLACED")
        res = af.autofill(self.orders, self.pos, {}, "2026-11-20")
        self.assertEqual(self.orders.docs[0]["status"], "EXPIRED")

    def test_order_on_a_holiday_expires_at_once_when_a_newer_session_exists(self):
        self.orders.docs.append(order(day="2026-11-10"))                                   # Tuesday 2026-11-10: NSE holiday, no candle
        c = candles({"AAA": [("2026-11-09", 99.0, 101.0, 98.5, 100.5), ("2026-11-11", 99.0, 101.0, 98.5, 100.5)]})
        res = af.autofill(self.orders, self.pos, c, "2026-11-11")
        self.assertEqual(res["waiting"], [])
        self.assertEqual(self.orders.docs[0]["status"], "EXPIRED")
        self.assertIn("no session candle", self.orders.docs[0]["expired_reason"])
        self.assertEqual(self.pos.docs, [])

    def test_order_with_no_newer_bar_still_waits(self):
        self.orders.docs.append(order(day="2026-11-10"))
        c = candles({"AAA": [("2026-11-09", 99.0, 101.0, 98.5, 100.5)]})
        res = af.autofill(self.orders, self.pos, c, "2026-11-10")
        self.assertEqual(res["waiting"], ["AAA 2026-11-10"])
        self.assertEqual(self.orders.docs[0]["status"], "PLACED")

    def test_second_order_for_a_held_symbol_is_skipped(self):
        self.orders.docs += [order(day="2026-11-04"), order(day="2026-11-05")]
        c = candles({"AAA": [("2026-11-04", 99.0, 101.0, 98.0, 100.0), ("2026-11-05", 100.0, 103.0, 99.0, 102.0)]})
        res = af.autofill(self.orders, self.pos, c, "2026-11-05")
        self.assertEqual(len(self.pos.docs), 1)
        self.assertEqual(len(res["filled"]), 1)
        self.assertEqual(len(res["skipped"]), 1)
        self.assertEqual(sorted(d["status"] for d in self.orders.docs), ["FILLED", "SKIPPED"])

    def test_dry_run_writes_nothing(self):
        self.orders.docs.append(order())
        res = af.autofill(self.orders, self.pos, candles({"AAA": [("2026-11-04", 99.0, 101.0, 98.5, 100.5)]}), "2026-11-04", apply=False)
        self.assertEqual(len(res["filled"]), 1)
        self.assertEqual(self.pos.docs, [])
        self.assertEqual(self.orders.docs[0]["status"], "PLACED")

    def test_already_filled_manually_is_untouched(self):
        o = order()
        o["status"] = "FILLED"
        self.orders.docs.append(o)
        res = af.autofill(self.orders, self.pos, candles({"AAA": [("2026-11-04", 99.0, 101.0, 98.5, 100.5)]}), "2026-11-04")
        self.assertEqual(res, dict(filled=[], expired=[], waiting=[], skipped=[]))
        self.assertEqual(self.pos.docs, [])

    def test_rerun_is_idempotent(self):
        self.orders.docs.append(order())
        c = candles({"AAA": [("2026-11-04", 99.0, 101.0, 98.5, 100.5)]})
        af.autofill(self.orders, self.pos, c, "2026-11-04")
        af.autofill(self.orders, self.pos, c, "2026-11-04")
        self.assertEqual(len(self.pos.docs), 1)


class TestMessageAndDedupe(unittest.TestCase):
    def test_message_lists_fills_and_expiries(self):
        m = build_message([], [], pd.Timestamp("2026-11-06"), pd.Timestamp("2026-11-05"), [],
                          dict(filled=["SWARAJ 2026-11-04: BUY 24 @ 402.10 (x)"], expired=["GYFTR 2026-11-05: trigger not reached"]))
        self.assertIn("PAPER FILLS RECORDED", m["text"])
        self.assertIn("SWARAJ", m["text"])
        self.assertIn("DID NOT FILL", m["text"])
        self.assertTrue(m["actionable"])

    def test_no_fills_means_no_extra_sections(self):
        m = build_message([], [], pd.Timestamp("2026-11-06"), pd.Timestamp("2026-11-05"), [], dict(filled=[], expired=[]))
        self.assertNotIn("PAPER FILLS", m["text"])

    def test_run_already_sent(self):
        runs = FakeCol()
        self.assertFalse(s3_store.run_already_sent(runs, "2026-11-05"))
        s3_store.record_run(runs, "2026-11-05", sent=True, stale=False)
        self.assertTrue(s3_store.run_already_sent(runs, "2026-11-05"))
        s3_store.record_run(runs, "2026-11-06", sent=True, stale=True)          # a stale run may be repeated
        self.assertFalse(s3_store.run_already_sent(runs, "2026-11-06"))
        s3_store.record_run(runs, "2026-11-09", sent=False, stale=False)
        self.assertFalse(s3_store.run_already_sent(runs, "2026-11-09"))


# ----------------------------------------------------------------------------------------------------- cache refresh

def frame(start="2026-06-01", n=30, tz=None, bad=False):
    dates = pd.bdate_range(start=start, periods=n)
    if tz:
        dates = dates.tz_localize(tz)
    df = pd.DataFrame(dict(DATE=dates, OPEN=100.0, HIGH=102.0, LOW=98.0, CLOSE=101.0, VOLUME=1e5))
    if bad:
        df.loc[3, "HIGH"] = 90.0
    return df


class TestCacheRefresh(unittest.TestCase):
    def test_expected_last_session(self):
        # Thursday 2026-11-05: before the close -> Wednesday; after the close -> Thursday; Saturday -> Friday
        self.assertEqual(cr.expected_last_session(datetime(2026, 11, 5, 11, 0)), date(2026, 11, 4))
        self.assertEqual(cr.expected_last_session(datetime(2026, 11, 5, 18, 30)), date(2026, 11, 5))
        self.assertEqual(cr.expected_last_session(datetime(2026, 11, 7, 12, 0)), date(2026, 11, 6))
        self.assertEqual(cr.expected_last_session(datetime(2026, 11, 5, 18, 30), lambda d: d == date(2026, 11, 5)), date(2026, 11, 4))

    def test_find_stale(self):
        listings = [dict(symbol="OLD", listing_date=pd.Timestamp("2026-02-01")),         # age 277: in window
                    dict(symbol="FRESH", listing_date=pd.Timestamp("2026-09-01")),
                    dict(symbol="NOCACHE", listing_date=pd.Timestamp("2026-08-01")),
                    dict(symbol="ANCIENT", listing_date=pd.Timestamp("2024-01-01")),     # beyond 400 days
                    dict(symbol="NODATE", listing_date=None)]
        last = dict(OLD="2026-10-21", FRESH="2026-11-04", ANCIENT="2026-01-01")
        got = cr.find_stale(listings, last, date(2026, 11, 5), min_lag_days=4, today=date(2026, 11, 5))
        self.assertEqual({r["symbol"] for r in got}, {"OLD", "NOCACHE"})
        self.assertEqual(next(r for r in got if r["symbol"] == "OLD")["lag_days"], 15)

    def test_validate_frame_accepts_tz_aware_upstox_dates(self):
        df = frame(start="2026-10-01", n=25, tz="Asia/Kolkata")
        ok, why = cr.validate_frame(df, pd.Timestamp("2026-10-01"), date(2026, 11, 4))
        self.assertTrue(ok, why)

    def test_validate_frame_rejects(self):
        exp = date(2026, 11, 4)
        self.assertFalse(cr.validate_frame(None, pd.Timestamp("2026-10-01"), exp)[0])
        self.assertFalse(cr.validate_frame(frame(start="2026-10-01", n=25, bad=True), pd.Timestamp("2026-10-01"), exp)[0])
        self.assertIn("ends", cr.validate_frame(frame(start="2026-06-01", n=30), pd.Timestamp("2026-06-01"), exp)[1])       # ends mid-July
        self.assertIn("starts", cr.validate_frame(frame(start="2026-10-01", n=25), pd.Timestamp("2026-08-01"), exp)[1])

    def test_completed_records_excludes_todays_forming_bar(self):
        df = frame(start="2026-10-12", n=17)                      # ends 2026-11-03 + ... check below
        last_day = pd.Timestamp(df["DATE"].iloc[-1]).date()
        before_close = datetime.combine(last_day, datetime.min.time()).replace(hour=11)
        last, rec = cr.completed_records(df, before_close)
        self.assertEqual(len(rec), len(df) - 1)
        self.assertLess(last, str(last_day))
        after_close = before_close.replace(hour=18)
        last, rec = cr.completed_records(df, after_close)
        self.assertEqual(len(rec), len(df))
        self.assertEqual(last, str(last_day))
        self.assertEqual(set(rec[0]), {"DATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME", "LTP"})

    def test_rebuild_symbol_dry_run_and_apply(self):
        df = frame(start="2026-10-01", n=25, tz="Asia/Kolkata")
        end = pd.Timestamp(df["DATE"].iloc[-1]).tz_localize(None).date()
        now = datetime.combine(end, datetime.min.time()).replace(hour=19)
        store = MagicMock()
        r = cr.rebuild_symbol("X", pd.Timestamp("2026-10-01"), end, now, lambda s, a, b: df, store, apply=False)
        self.assertTrue(r["ok"])
        store.assert_not_called()
        r = cr.rebuild_symbol("X", pd.Timestamp("2026-10-01"), end, now, lambda s, a, b: df, store, apply=True)
        self.assertTrue(r["ok"])
        store.assert_called_once()
        sym, last, rec = store.call_args[0]
        self.assertEqual((sym, last, len(rec)), ("X", str(end), 25))

    def test_rebuild_refuses_bad_data_and_survives_fetch_errors(self):
        store = MagicMock()
        end = date(2026, 11, 4)
        now = datetime(2026, 11, 5, 19, 0)
        bad = cr.rebuild_symbol("X", pd.Timestamp("2026-10-01"), end, now, lambda s, a, b: frame(start="2026-10-01", n=25, bad=True), store, apply=True)
        self.assertFalse(bad["ok"])
        def boom(*a):
            raise RuntimeError("net")
        err = cr.rebuild_symbol("X", pd.Timestamp("2026-10-01"), end, now, boom, store, apply=True)
        self.assertFalse(err["ok"])
        store.assert_not_called()


# ------------------------------------------------------------------------------------------------------ IST stamping

class TestIstStamping(unittest.TestCase):
    def test_listing_commit_stamps_ist_not_runner_clock(self):
        import test_commit_policy as tcp
        import listing_day_breakout_scanner as lds
        import db as dbmod
        ist_now = datetime(2026, 11, 5, 11, 54, 36)                       # 11:54 IST
        fake_utc_runner_now = datetime(2026, 11, 5, 6, 24, 36)           # what datetime.now() returns on the UTC runner
        t = tcp.TestCommitPolicy()
        with patch.object(lds, "_now_ist", return_value=ist_now), patch("listing_day_breakout_scanner.datetime") as dt:
            dt.now.return_value = fake_utc_runner_now
            dt.fromisoformat = datetime.fromisoformat
            dt.today.return_value = fake_utc_runner_now
            (ok, *_), saved = t._commit(tcp.breakout("BREAKOUT"), active=2)
        self.assertTrue(ok)
        self.assertEqual(saved["signal"]["signal_date"], ist_now)
        self.assertEqual(saved["position"]["entry_date"], ist_now)
        self.assertEqual(saved["signal"]["signal_id"], "BREAKOUT_TESTCO_20261105")
        # and the database layer turns that naive IST value into the true UTC instant (06:24:36 UTC), not 00:54:36
        self.assertEqual(dbmod.make_utc(saved["position"]["entry_date"]), datetime(2026, 11, 5, 6, 24, 36, tzinfo=UTC))


# ------------------------------------------------------------------------------------------- S3 refresh: 30-minute fallback

class Resp:
    def __init__(self, code, candles=None):
        self.status_code, self._c = code, candles or []

    def json(self):
        return {"data": {"candles": self._c}}


def half_hours(day, n=13, base=100.0):
    """Upstox-style 30-minute rows [ts, o, h, l, c, v, oi], newest first."""
    rows = []
    t = pd.Timestamp(f"{day} 09:15")
    for i in range(n):
        ts = (t + pd.Timedelta(minutes=30 * i)).strftime("%Y-%m-%dT%H:%M:%S+05:30")
        rows.append([ts, base + i, base + i + 2, base + i - 1, base + i + 1, 1000, 0])
    return rows[::-1]


def daily_rows(days, base=100.0):
    return [[f"{d}T00:00:00+05:30", base, base + 2, base - 1, base + 1, 5000, 0] for d in days][::-1]


class TestRefreshFallback(unittest.TestCase):
    def cache(self):
        dates = pd.bdate_range(end="2026-11-04", periods=20)
        return {"X": pd.DataFrame(dict(DATE=dates, OPEN=100.0, HIGH=102.0, LOW=99.0, CLOSE=101.0, VOLUME=5000.0))}

    def run_refresh(self, http_get):
        from core import s3_refresh as rf
        return rf.refresh_candles(self.cache(), {"X"}, {"X"}, pd.Timestamp("2026-11-05"), "tok", {"X": "NSE_EQ|X"}, http_get=http_get, sleep=lambda s: None)

    def test_30m_session_aggregation(self):
        from core import s3_refresh as rf
        frame, err = rf.fetch_upstox_day_from_30m("X", "k", "t", "2026-11-05", lambda u, headers=None, timeout=None: Resp(200, half_hours("2026-11-05")))
        self.assertIsNone(err)
        r = frame.iloc[0]
        self.assertEqual((r["OPEN"], r["CLOSE"]), (100.0, 113.0))                 # first open, last close
        self.assertEqual((r["HIGH"], r["LOW"]), (114.0, 99.0))
        self.assertEqual(r["VOLUME"], 13000.0)

    def test_incomplete_or_failed_30m_is_refused(self):
        from core import s3_refresh as rf
        for resp in (Resp(200, half_hours("2026-11-05", n=5)), Resp(400), Resp(200, [])):
            frame, err = rf.fetch_upstox_day_from_30m("X", "k", "t", "2026-11-05", lambda u, headers=None, timeout=None, r=resp: r)
            self.assertIsNone(frame)
            self.assertTrue(err)

    def test_missing_daily_bar_is_built_from_30m(self):
        def http(url, headers=None, timeout=None):
            if "/minutes/30/" in url:
                return Resp(200, half_hours("2026-11-05"))
            return Resp(200, daily_rows(["2026-11-03", "2026-11-04"]))               # the daily endpoint has no 11-05 yet
        out, rep = self.run_refresh(http)
        self.assertEqual(rep["updated"], 1)
        self.assertEqual(rep["from_30m"], ["X"])
        self.assertEqual(rep["failed"], {})
        self.assertEqual(pd.Timestamp(out["X"]["DATE"].iloc[-1]).date(), date(2026, 11, 5))
        self.assertTrue(any("30-minute" in l for l in __import__("core.s3_refresh", fromlist=["describe"]).describe(rep)))

    def test_range_retry_finds_the_bar_without_30m(self):
        calls = []
        def http(url, headers=None, timeout=None):
            calls.append(url)
            if "/minutes/30/" in url:
                return Resp(500)
            if url.endswith("2026-11-04"):                                              # from = last bar: the quirky short range
                return Resp(200, daily_rows(["2026-11-04"]))
            return Resp(200, daily_rows(["2026-11-04", "2026-11-05"]))                  # an earlier start returns the new bar
        out, rep = self.run_refresh(http)
        self.assertEqual(rep["updated"], 1)
        self.assertEqual(rep["from_30m"], [])
        self.assertFalse(any("/minutes/30/" in u for u in calls))

    def test_no_data_anywhere_stays_stale_and_is_reported(self):
        out, rep = self.run_refresh(lambda url, headers=None, timeout=None: Resp(200, daily_rows(["2026-11-04"])) if "/day/" in url else Resp(500))
        self.assertEqual(rep["updated"], 0)
        self.assertEqual(pd.Timestamp(out["X"]["DATE"].iloc[-1]).date(), date(2026, 11, 4))

    def test_multi_session_gap_is_not_papered_over_by_one_30m_day(self):
        from core import s3_refresh as rf
        cache = self.cache()
        cache["X"] = cache["X"].iloc[:-3].copy()                                        # cache ends 2026-10-30: three sessions behind
        calls = []
        def http(url, headers=None, timeout=None):
            calls.append(url)
            return Resp(200, daily_rows(["2026-10-30"]))
        out, rep = rf.refresh_candles(cache, {"X"}, {"X"}, pd.Timestamp("2026-11-05"), "tok", {"X": "NSE_EQ|X"}, http_get=http, sleep=lambda s: None)
        self.assertFalse(any("/minutes/30/" in u for u in calls))
        self.assertEqual(rep["from_30m"], [])

    def test_a_holiday_between_is_not_a_skipped_session(self):
        from core import s3_refresh as rf
        last, mx = pd.Timestamp("2026-11-09"), pd.Timestamp("2026-11-11")                   # Tuesday 11-10 is closed
        self.assertTrue(rf._skipped_session_gap(last, mx))                                  # weekends only: looks like a two-day gap
        self.assertFalse(rf._skipped_session_gap(last, mx, lambda d: d.strftime("%Y-%m-%d") == "2026-11-10"))
        self.assertTrue(rf._skipped_session_gap(last, mx, lambda d: (_ for _ in ()).throw(RuntimeError("source down"))))   # a failing source never hides a gap

    def test_extra_calls_stop_when_the_budget_is_spent_but_held_names_still_get_them(self):
        from core import s3_refresh as rf
        ticks = iter(range(0, 10000, 100))                                              # every clock() call advances 100 s
        def http(url, headers=None, timeout=None):
            if "/minutes/30/" in url:
                return Resp(200, half_hours("2026-11-05"))
            return Resp(200, daily_rows(["2026-11-03", "2026-11-04"]))
        for held, expect_30m in ((set(), []), ({"X"}, ["X"])):
            out, rep = rf.refresh_candles(self.cache(), {"X"}, held, pd.Timestamp("2026-11-05"), "tok", {"X": "NSE_EQ|X"}, http_get=http,
                                          sleep=lambda s: None, extra_budget_s=50, clock=lambda: next(ticks))
            self.assertEqual(rep["from_30m"], expect_30m, held)


if __name__ == "__main__":
    unittest.main()


class TestHolidayAwareSessions(unittest.TestCase):
    def tearDown(self):
        import s3_paper_alerts as al
        al._is_holiday = None

    def test_next_and_previous_session_skip_listed_holidays(self):
        import s3_paper_alerts as al
        al._is_holiday = lambda d: pd.Timestamp(d).strftime("%Y-%m-%d") == "2026-11-10"
        self.assertEqual(al.next_session(pd.Timestamp("2026-11-09")), pd.Timestamp("2026-11-11"))
        self.assertEqual(al.previous_session(pd.Timestamp("2026-11-11")), pd.Timestamp("2026-11-09"))
        self.assertTrue(al._closed(pd.Timestamp("2026-11-10")))
        self.assertTrue(al._closed(pd.Timestamp("2026-11-14")))           # Saturday

    def test_default_is_weekends_only_and_a_broken_source_is_ignored(self):
        import s3_paper_alerts as al
        self.assertEqual(al.next_session(pd.Timestamp("2026-11-09")), pd.Timestamp("2026-11-10"))
        al._is_holiday = lambda d: 1 / 0
        self.assertEqual(al.next_session(pd.Timestamp("2026-11-09")), pd.Timestamp("2026-11-10"))

    def test_enable_uses_the_real_static_baseline(self):
        import s3_paper_alerts as al
        with patch("utils.get_dynamic_nse_holidays", return_value={"2026-11-10"}):
            self.assertTrue(al.enable_exchange_holidays())
            self.assertEqual(al.next_session(pd.Timestamp("2026-11-09")), pd.Timestamp("2026-11-11"))
        with patch("utils.get_dynamic_nse_holidays", side_effect=RuntimeError("down")):
            self.assertFalse(al.enable_exchange_holidays())
            self.assertIsNone(al._is_holiday)
