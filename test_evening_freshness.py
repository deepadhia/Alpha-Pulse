# -*- coding: utf-8 -*-
"""
test_evening_freshness.py -- the 2026-11-11 fixes: the evening jobs (S3 18:49 IST, P 19:15 IST, settlement) always ran one session behind.

Root cause (reproduced live 2026-11-11 22:04 IST): Upstox's HISTORICAL endpoints (daily, 30-minute, 1-minute) have no bars for the current session; the
INTRADAY endpoints return the complete session.  The 30-minute fallback only asked the historical one, so "45 fetched, 0 updated" every evening.
Second bug: the P refresh did not receive the exchange-holiday predicate, so after a holiday the closed day looked like a skipped session.
"""
import unittest
from datetime import date
from unittest.mock import patch

import pandas as pd

from core import s3_refresh as rf
from core import upstox_quotes as UQ


class Resp:
    def __init__(self, code, candles=None):
        self.status_code, self._c = code, candles or []

    def json(self):
        return {"data": {"candles": self._c}}


def half_hours(day, n=13, base=100.0):
    t = pd.Timestamp(f"{day} 09:15")
    rows = []
    for i in range(n):
        ts = (t + pd.Timedelta(minutes=30 * i)).strftime("%Y-%m-%dT%H:%M:%S+05:30")
        rows.append([ts, base + i, base + i + 2, base + i - 1, base + i + 1, 1000, 0])
    return rows[::-1]


def minutes(day, start="09:15", n=375, base=100.0):
    t = pd.Timestamp(f"{day} {start}")
    return [[(t + pd.Timedelta(minutes=i)).strftime("%Y-%m-%dT%H:%M:%S+05:30"), base, base + 1, base - 1, base + 0.5, 10, 0] for i in range(n)][::-1]


def daily_rows(days, base=100.0):
    return [[f"{d}T00:00:00+05:30", base, base + 2, base - 1, base + 1, 5000, 0] for d in days][::-1]


TODAY = pd.Timestamp("2026-11-11")
HOLIDAY_1110 = lambda d: pd.Timestamp(d).strftime("%Y-%m-%d") == "2026-11-10"


class TestCurrentSessionFrom30m(unittest.TestCase):
    def http(self, calls):
        def get(url, headers=None, timeout=None):
            calls.append(url)
            if "/intraday/" in url:
                return Resp(200, half_hours("2026-11-11"))
            return Resp(200, [])                                         # the historical endpoint: nothing for the current session
        return get

    def test_today_uses_the_intraday_endpoint_when_historical_is_empty(self):
        calls = []
        frame, err = rf.fetch_upstox_day_from_30m("X", "k", "t", "2026-11-11", self.http(calls), today="2026-11-11")
        self.assertIsNone(err)
        self.assertEqual((frame.iloc[0]["OPEN"], frame.iloc[0]["CLOSE"], frame.iloc[0]["VOLUME"]), (100.0, 113.0, 13000.0))
        self.assertTrue(any("/intraday/" in u for u in calls))

    def test_a_past_session_never_touches_the_intraday_endpoint(self):
        calls = []
        frame, err = rf.fetch_upstox_day_from_30m("X", "k", "t", "2026-11-11", self.http(calls), today="2026-11-12")
        self.assertIsNone(frame)
        self.assertFalse(any("/intraday/" in u for u in calls))

    def test_a_session_still_forming_is_refused(self):
        def get(url, headers=None, timeout=None):
            return Resp(200, half_hours("2026-11-11", n=8)) if "/intraday/" in url else Resp(200, [])
        frame, err = rf.fetch_upstox_day_from_30m("X", "k", "t", "2026-11-11", get, today="2026-11-11")
        self.assertIsNone(frame)
        self.assertIn("incomplete", err)

    def test_intraday_http_error_is_reported_not_guessed(self):
        frame, err = rf.fetch_upstox_day_from_30m("X", "k", "t", "2026-11-11", lambda u, headers=None, timeout=None: Resp(500), today="2026-11-11")
        self.assertIsNone(frame)
        self.assertTrue(err)


class TestHolidayCaseEndToEnd(unittest.TestCase):
    """The real situation of 2026-11-11: cache ends Mon 11-09, Tuesday 11-10 is a holiday, the target is Wed 11-11 after the close."""

    def cache(self):
        dates = [d for d in pd.bdate_range(end="2026-11-09", periods=20)]
        return {"X": pd.DataFrame(dict(DATE=dates, OPEN=100.0, HIGH=102.0, LOW=99.0, CLOSE=101.0, VOLUME=5000.0))}

    def http(self, url, headers=None, timeout=None):
        if "/intraday/" in url:
            return Resp(200, half_hours("2026-11-11"))
        if "/minutes/30/" in url:
            return Resp(200, [])
        return Resp(200, daily_rows(["2026-11-06", "2026-11-09"]))      # daily endpoint: nothing for 11-11 yet

    def refresh(self, is_holiday):
        with patch("core.s3_refresh.ist_today", return_value=TODAY):
            return rf.refresh_candles(self.cache(), {"X"}, set(), TODAY, "tok", {"X": "NSE_EQ|X"}, http_get=self.http, sleep=lambda s: None,
                                      is_holiday=is_holiday)

    def test_with_the_holiday_predicate_the_bar_is_built_from_the_intraday_feed(self):
        out, rep = self.refresh(HOLIDAY_1110)
        self.assertEqual(rep["updated"], 1)
        self.assertEqual(rep["from_30m"], ["X"])
        self.assertEqual(pd.Timestamp(out["X"]["DATE"].iloc[-1]).date(), date(2026, 11, 11))

    def test_without_it_the_closed_tuesday_blocks_the_fallback(self):
        # documents the second bug: this is exactly what refresh_p_candles used to do
        out, rep = self.refresh(None)
        self.assertEqual(rep["updated"], 0)
        self.assertEqual(pd.Timestamp(out["X"]["DATE"].iloc[-1]).date(), date(2026, 11, 9))


class TestPRefreshGetsTheHolidayPredicate(unittest.TestCase):
    def test_refresh_p_candles_passes_is_holiday(self):
        import order_sheet as osh
        import s3_paper_alerts as S3A
        seen = {}

        def fake_refresh(candles, listing_syms, held, max_date, token, mapping, **kw):
            seen.update(kw)
            return candles, dict(selected=set(), fetched=0, updated=0, failed={}, note=None, from_30m=[])
        old = S3A._is_holiday
        S3A._is_holiday = HOLIDAY_1110
        try:
            with patch("core.s3_refresh.refresh_candles", fake_refresh), patch("db.get_instrument_key_mapping", return_value={}):
                osh.refresh_p_candles({"X": pd.DataFrame()}, {"X": {}}, pd.Timestamp("2026-11-12"))
        finally:
            S3A._is_holiday = old
        self.assertIs(seen.get("is_holiday"), HOLIDAY_1110)


class TestMinuteBarsOfTheCurrentSession(unittest.TestCase):
    def get_factory(self, calls, intraday_rows):
        def get(url, headers=None, timeout=None):
            calls.append(url)
            return Resp(200, intraday_rows) if "/intraday/" in url else Resp(200, [])
        return get

    def test_today_complete_session_comes_from_the_intraday_endpoint(self):
        calls = []
        df = UQ.fetch_minute_bars("NSE_EQ|X", "2026-11-11", "tok", http_get=self.get_factory(calls, minutes("2026-11-11")), today="2026-11-11")
        self.assertEqual(len(df), 375)
        self.assertEqual(df["TS"].iloc[-1].strftime("%H:%M"), "15:29")
        self.assertTrue(any("/intraday/" in u for u in calls))

    def test_a_session_not_finished_is_empty(self):
        df = UQ.fetch_minute_bars("NSE_EQ|X", "2026-11-11", "tok", http_get=self.get_factory([], minutes("2026-11-11", n=200)), today="2026-11-11")
        self.assertTrue(df.empty)                       # 09:15 + 199 min = 12:34: still forming, never priced as a full day

    def test_a_past_day_never_uses_the_intraday_endpoint(self):
        calls = []
        df = UQ.fetch_minute_bars("NSE_EQ|X", "2026-11-11", "tok", http_get=self.get_factory(calls, minutes("2026-11-11")), today="2026-11-12")
        self.assertTrue(df.empty)
        self.assertFalse(any("/intraday/" in u for u in calls))

    def test_historical_bars_are_used_when_present(self):
        calls = []
        get = lambda url, headers=None, timeout=None: (calls.append(url), Resp(200, minutes("2026-11-10")))[1]
        df = UQ.fetch_minute_bars("NSE_EQ|X", "2026-11-10", "tok", http_get=get, today="2026-11-11")
        self.assertEqual(len(df), 375)
        self.assertEqual(len(calls), 1)

    def test_server_error_still_raises(self):
        with self.assertRaises(RuntimeError):
            UQ.fetch_minute_bars("NSE_EQ|X", "2026-11-11", "tok", http_get=lambda u, headers=None, timeout=None: Resp(500), sleep=lambda s: None,
                                 today="2026-11-11")


class TestOrderMessageDistance(unittest.TestCase):
    def entries(self):
        from core.order_sheet import RestingEntry
        mk = lambda s, trig: RestingEntry(s, 10, trig, trig * 1.01, trig, trig * 10, trig * 0.88, 100.0, 1.0, 40, 1.67, "")
        return [mk("NEAR", 102.0), mk("FAR", 150.0)]

    def build(self, **kw):
        import resting_order_alerts as A
        return A.build_message(self.entries(), [], pd.Timestamp("2026-11-12"), pd.Timestamp("2026-11-11"), pd.Timestamp("2026-11-11"), 0,
                               n_held=1, capital=100000.0, **kw)

    def test_distance_and_summary_are_shown_and_no_order_is_dropped(self):
        m = self.build(last_closes={"NEAR": 100.0, "FAR": 100.0})
        t = m["text"]
        self.assertEqual(m["n_orders"], 2)
        self.assertIn("NEAR", t)
        self.assertIn("FAR", t)
        self.assertIn("+2.0% from last close", t)
        self.assertIn("+50.0% from last close", t)
        self.assertIn("1 of 2", t)                       # one of two within 5%
        self.assertIn("re-place each session", t)
        self.assertIn("about one new entry a week", t)

    def test_without_closes_the_message_is_unchanged_in_content(self):
        m = self.build()
        self.assertEqual(m["n_orders"], 2)
        self.assertNotIn("from last close", m["text"])
        self.assertNotIn("📏", m["text"])

    def test_each_order_is_its_own_spaced_block_with_one_fact_per_line(self):
        lines = self.build(last_closes={"NEAR": 100.0, "FAR": 100.0})["text"].split("\n")
        i = next(k for k, l in enumerate(lines) if "1. NEAR" in l)
        j = next(k for k, l in enumerate(lines) if "2. FAR" in l)
        block = lines[i:j]
        self.assertEqual(lines[i - 1], "")                           # blank line before each order
        self.assertEqual(lines[j - 1], "")
        self.assertEqual(len([l for l in block if l.strip()]), 5)    # header, trigger, limit, stop alert, age/R:R
        for needle in ("Trigger", "Limit", "Stop alert", "Age"):
            self.assertEqual(sum(needle in l for l in block), 1, needle)
        self.assertEqual(sum("Trigger" in l and "Limit" in l for l in block), 0)    # never trigger and limit on one line

    def test_distance_helper(self):
        import resting_order_alerts as A
        self.assertAlmostEqual(A.distance_pct(110.0, 100.0), 10.0)
        self.assertLess(A.distance_pct(95.0, 100.0), 0)
        self.assertIsNone(A.distance_pct(110.0, None))
        self.assertIsNone(A.distance_pct(110.0, 0))


class TestShelfLabels(unittest.TestCase):
    DATA = dict(symbol="ABC", entry_price=100.0, stop_loss=88.0, target_price=120.0, listing_day_high=95.0, base_range_high=96.0, volume_spike=2.0,
                risk_reward=1.7, days_since_listing=14, tier="A", position_size_pct=60)

    def test_paper_only_shelf_alert_does_not_claim_a_60_percent_size(self):
        import listing_day_breakout_scanner as L
        with patch.object(L, "LISTING_SHELF_LIVE_CAPITAL_ENABLED", False):
            txt = L.format_shelf_breakout_alert(dict(self.DATA))
        self.assertIn("PAPER only", txt)
        self.assertNotIn("Size <b>60%", txt)
        with patch.object(L, "LISTING_SHELF_LIVE_CAPITAL_ENABLED", True):
            self.assertIn("Size <b>60%", L.format_shelf_breakout_alert(dict(self.DATA)))


class TestPolicyPSlots(unittest.TestCase):
    def test_only_active_positions_take_a_policy_p_slot(self):
        from core.order_sheet import policy_p_slots_used
        docs = [dict(symbol="A", status="ACTIVE"), dict(symbol="B", status="ACTIVE"), dict(symbol="PARASHLTH", status="PAPER_ONLY"),
                dict(symbol="C", status="CLOSED"), dict(symbol="A", status="ACTIVE"), dict(status="ACTIVE")]
        self.assertEqual(policy_p_slots_used(docs), 2)           # distinct ACTIVE symbols; paper-only, closed and symbol-less rows do not count
        self.assertEqual(policy_p_slots_used([]), 0)

    def test_a_paper_shelf_row_no_longer_cuts_the_order_list(self):
        from core.order_sheet import SheetConfig, build_resting_entries, policy_p_slots_used
        cands = [dict(symbol=f"S{i}", trigger=100.0 + i, limit=102.0 + i, fill=101.0 + i, stop=88.0 + i, risk_pct=12.0, rr=1.67, age=30,
                      last_close=95.0, already_above_trigger=False) for i in range(10)]
        cfg = SheetConfig(capital=100000.0)
        paper_only_book = [dict(symbol="PARASHLTH", status="PAPER_ONLY")]
        entries, skipped, _ = build_resting_entries(cands, slots_used=policy_p_slots_used(paper_only_book), cfg=cfg)
        self.assertEqual(len(entries), 10)                       # was 9 when the paper row was counted as a slot
        entries9, _, _ = build_resting_entries(cands, slots_used=len(paper_only_book), cfg=cfg)
        self.assertEqual(len(entries9), 9)

    def test_message_shows_slots_against_the_cap_and_paper_rows_separately(self):
        import resting_order_alerts as A
        m = A.build_message([], [], pd.Timestamp("2026-11-12"), pd.Timestamp("2026-11-11"), pd.Timestamp("2026-11-11"), 0, n_held=0,
                            capital=100000.0, n_paper=1)
        self.assertIn(f"Slots used 0 of {A._slot_cap()}", m["text"])
        self.assertIn("+1 paper-only, no slot", m["text"])


class TestStillOldIsReported(unittest.TestCase):
    def cache(self):
        dates = pd.bdate_range(end="2026-11-04", periods=20)
        return {"X": pd.DataFrame(dict(DATE=dates, OPEN=100.0, HIGH=102.0, LOW=99.0, CLOSE=101.0, VOLUME=5000.0))}

    def test_a_name_whose_session_cannot_be_built_is_listed_not_silent(self):
        def http(url, headers=None, timeout=None):
            if "/minutes/30/" in url or "/intraday/" in url:
                return Resp(200, half_hours("2026-11-05", n=5))              # an incomplete session: refused
            return Resp(200, daily_rows(["2026-11-03", "2026-11-04"]))        # the daily endpoint has no 11-05
        with patch("core.s3_refresh.ist_today", return_value=pd.Timestamp("2026-11-12")):
            out, rep = rf.refresh_candles(self.cache(), {"X"}, set(), pd.Timestamp("2026-11-05"), "tok", {"X": "NSE_EQ|X"}, http_get=http,
                                          sleep=lambda s: None)
        self.assertEqual(rep["updated"], 0)
        self.assertEqual(rep["still_old"], ["X"])
        self.assertTrue(any("still on an older bar" in l for l in rf.describe(rep)))

    def test_an_updated_name_is_not_listed(self):
        def http(url, headers=None, timeout=None):
            if "/minutes/30/" in url:
                return Resp(200, half_hours("2026-11-05"))
            return Resp(200, daily_rows(["2026-11-03", "2026-11-04"]))
        with patch("core.s3_refresh.ist_today", return_value=pd.Timestamp("2026-11-12")):
            out, rep = rf.refresh_candles(self.cache(), {"X"}, set(), pd.Timestamp("2026-11-05"), "tok", {"X": "NSE_EQ|X"}, http_get=http,
                                          sleep=lambda s: None)
        self.assertEqual((rep["updated"], rep["still_old"]), (1, []))


class TestRetryBeforeSending(unittest.TestCase):
    def test_retries_until_fresh_then_stops(self):
        import resting_order_alerts as A
        lags, waits = iter([1, 1, 0]), []
        calls = []
        def compute():
            calls.append(1)
            return next(lags)
        res = A.compute_with_retry(compute, lambda r: r, retries=2, wait_s=60, sleep=waits.append, log=lambda m: None)
        self.assertEqual(res, 0)
        self.assertEqual((len(calls), waits), (3, [60, 60]))

    def test_gives_up_after_the_retries_and_returns_the_last_result(self):
        import resting_order_alerts as A
        calls = []
        res = A.compute_with_retry(lambda: (calls.append(1), 1)[1], lambda r: r, retries=2, wait_s=5, sleep=lambda s: None, log=lambda m: None)
        self.assertEqual((res, len(calls)), (1, 3))

    def test_a_fresh_first_result_never_waits(self):
        import resting_order_alerts as A
        waits = []
        self.assertEqual(A.compute_with_retry(lambda: 0, lambda r: r, retries=2, sleep=waits.append, log=lambda m: None), 0)
        self.assertEqual(waits, [])

    def test_zero_retries_is_a_single_call(self):
        import resting_order_alerts as A
        calls = []
        A.compute_with_retry(lambda: (calls.append(1), 1)[1], lambda r: r, retries=0, sleep=lambda s: None, log=lambda m: None)
        self.assertEqual(len(calls), 1)


class TestS3StaleWithholdsOrders(unittest.TestCase):
    def entries(self):
        from core.order_sheet import RestingEntry
        return [RestingEntry("KNACK", 43, 225.35, 227.55, 226.0, 9718.0, 198.85, 1167.0, 1.17, 126, 1.67, "")]

    def build(self, data_asof):
        import s3_paper_alerts as S
        return S.build_message([], self.entries(), pd.Timestamp("2026-11-12"), data_asof)

    def test_stale_run_lists_no_orders(self):
        m = self.build(pd.Timestamp("2026-11-09"))
        self.assertTrue(m["stale"])
        self.assertEqual(m["n_orders"], 0)
        self.assertNotIn("KNACK", m["text"])
        self.assertIn("ORDERS WITHHELD", m["text"])

    def test_fresh_run_lists_the_orders_in_blocks(self):
        m = self.build(pd.Timestamp("2026-11-11"))
        self.assertFalse(m["stale"])
        self.assertEqual(m["n_orders"], 1)
        self.assertIn("1. KNACK", m["text"])
        self.assertNotIn("ORDERS WITHHELD", m["text"])


if __name__ == "__main__":
    unittest.main()
