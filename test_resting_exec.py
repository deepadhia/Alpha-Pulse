"""Tests for core.resting_exec and core.upstox_quotes: realistic quote / bar based fills and exits, circuits, limits, idempotent records."""
import unittest
from datetime import datetime, timedelta, timezone

import pandas as pd

from core import resting_exec as X
from core import upstox_quotes as UQ

IST = timezone(timedelta(hours=5, minutes=30))
EARLY = datetime(2026, 11, 12, 9, 18, tzinfo=IST)
LATE = datetime(2026, 11, 12, 10, 40, tzinfo=IST)
ORDER = dict(symbol="ABC", trigger=101.5, limit=102.5, quantity=20, order_date="2026-11-12", stop_alert=90.0)


def q(open_=101.0, bid=100.9, ask=101.1, ltp=101.0, uc=110.0, lc=90.0):
    return dict(open=open_, high=open_, low=open_, ltp=ltp, bid=bid, ask=ask, bid_qty=100, ask_qty=100, upper_circuit=uc, lower_circuit=lc, ts="t")


class TestEntryAtOpen(unittest.TestCase):
    def test_gap_up_fills_at_best_ask(self):
        d = X.decide_entry_at_open(ORDER, q(open_=102.0, bid=101.9, ask=102.1), EARLY)
        self.assertEqual((d["action"], d["price"], d["model"]), ("FILL", 102.1, "quote_ask_open"))
        self.assertAlmostEqual(d["half_spread_bps"], (102.1 - 101.9) / (102.1 + 101.9) * 1e4)

    def test_opened_below_trigger_is_pending_for_the_minute_bars(self):
        self.assertEqual(X.decide_entry_at_open(ORDER, q(open_=100.0), EARLY)["action"], "PENDING")

    def test_open_above_limit_is_not_filled(self):
        self.assertEqual(X.decide_entry_at_open(ORDER, q(open_=103.0, bid=102.9, ask=103.1), EARLY)["action"], "EXPIRE")

    def test_ask_above_limit_is_not_filled(self):
        d = X.decide_entry_at_open(ORDER, q(open_=102.3, bid=102.2, ask=102.6), EARLY)
        self.assertEqual(d["action"], "EXPIRE")
        self.assertIn("above the limit", d["reason"])

    def test_upper_circuit_no_sellers(self):
        d = X.decide_entry_at_open(ORDER, q(open_=102.0, bid=102.0, ask=None, ltp=110.0, uc=110.0), EARLY)
        self.assertEqual(d["action"], "EXPIRE")
        self.assertIn("no sellers", d["reason"])

    def test_empty_ask_without_circuit_waits(self):
        self.assertEqual(X.decide_entry_at_open(ORDER, q(open_=102.0, ask=None, ltp=102.0, uc=120.0), EARLY)["action"], "WAIT")

    def test_late_quote_is_not_the_open_book(self):
        self.assertEqual(X.decide_entry_at_open(ORDER, q(open_=102.0, bid=101.9, ask=102.1), LATE)["action"], "PENDING")

    def test_no_quote_waits(self):
        self.assertEqual(X.decide_entry_at_open(ORDER, None, EARLY)["action"], "WAIT")
        self.assertEqual(X.decide_entry_at_open(ORDER, q(open_=0.0), EARLY)["action"], "WAIT")


def minutes(rows, start="2026-11-12 09:15"):
    t0 = pd.Timestamp(start)
    return pd.DataFrame([dict(TS=t0 + pd.Timedelta(minutes=i), OPEN=o, HIGH=h, LOW=l, CLOSE=c, VOLUME=1000) for i, (o, h, l, c) in enumerate(rows)])


DAY = dict(OPEN=100.0, HIGH=104.0, LOW=99.0, CLOSE=103.0)


class TestIntradayEntry(unittest.TestCase):
    def test_first_minute_reaching_trigger_with_measured_spread(self):
        m = minutes([(100, 100.5, 99.5, 100), (100, 101.0, 100, 100.8), (100.8, 102.0, 100.8, 101.9), (101.9, 104, 101.8, 103)])
        d = X.settle_intraday_entry(ORDER, m, DAY, 98.0, measured_hs_bps=20.0, tier_hs_bps=60.0)
        self.assertEqual(d["action"], "FILL")
        self.assertEqual(d["model"], "minute_bar_measured_spread")
        self.assertAlmostEqual(d["price"], 101.75, places=2)          # ref 101.5 * 1.002 = 101.70 -> tick up 101.70; see below
        self.assertEqual(pd.Timestamp(d["fill_time"]).strftime("%H:%M"), "09:17")

    def test_tier_model_used_when_no_measured_spread(self):
        m = minutes([(100, 100.5, 99.5, 100), (100.8, 102.0, 100.8, 101.9)])
        d = X.settle_intraday_entry(ORDER, m, DAY, 98.0, measured_hs_bps=None, tier_hs_bps=40.0)
        self.assertEqual(d["model"], "minute_bar_tier_spread")

    def test_price_running_through_the_limit_is_not_filled(self):
        m = minutes([(100, 100.5, 99.5, 100), (103.0, 104.0, 102.9, 103.5)])     # the trigger minute opens above the limit
        d = X.settle_intraday_entry(ORDER, m, DAY, 98.0, 20.0, 60.0)
        self.assertEqual(d["action"], "EXPIRE")
        self.assertIn("ran through the limit", d["reason"])

    def test_spread_pushing_the_fill_above_the_limit_is_not_filled(self):
        m = minutes([(100, 100.5, 99.5, 100), (102.45, 102.6, 102.4, 102.5)])
        d = X.settle_intraday_entry(ORDER, m, DAY, 98.0, 100.0, 60.0)
        self.assertEqual(d["action"], "EXPIRE")

    def test_trigger_never_reached(self):
        m = minutes([(100, 100.5, 99.5, 100), (100, 101.0, 100, 100.8)])
        self.assertEqual(X.settle_intraday_entry(ORDER, m, DAY, 98.0, 20.0, 60.0)["action"], "EXPIRE")

    def test_all_session_upper_circuit_lock(self):
        locked = dict(OPEN=105.0, HIGH=105.0, LOW=105.0, CLOSE=105.0)
        m = minutes([(105, 105, 105, 105)] * 3)
        d = X.settle_intraday_entry(ORDER, m, locked, 100.0, 20.0, 60.0)
        self.assertEqual(d["action"], "EXPIRE")
        self.assertIn("upper-circuit", d["reason"])

    def test_daily_fallback_and_wait(self):
        d = X.settle_intraday_entry(ORDER, None, DAY, 98.0, None, 60.0)
        self.assertEqual((d["action"], d["model"]), ("FILL", "daily_bar_replay_rule"))
        self.assertEqual(X.settle_intraday_entry(ORDER, None, None, None, None, 60.0)["action"], "WAIT")


class TestExit(unittest.TestCase):
    def test_sells_at_the_best_bid(self):
        d = X.decide_exit_at_open(q(open_=95.0, bid=94.8, ask=95.2), EARLY)
        self.assertEqual((d["action"], d["price"], d["model"]), ("SELL", 94.8, "quote_bid_open"))

    def test_lower_circuit_no_bids_defers(self):
        d = X.decide_exit_at_open(q(open_=90.0, bid=None, ask=90.0, ltp=90.0, lc=90.0), EARLY)
        self.assertEqual(d["action"], "DEFER")

    def test_no_bid_without_circuit_waits(self):
        self.assertEqual(X.decide_exit_at_open(q(open_=95.0, bid=None, ltp=95.0, lc=80.0), EARLY)["action"], "WAIT")

    def test_late_quote_is_pending(self):
        self.assertEqual(X.decide_exit_at_open(q(), LATE)["action"], "PENDING")

    def test_daily_fallback_sells_at_open_less_spread(self):
        d = X.settle_exit_from_daily(dict(OPEN=95.0, HIGH=96, LOW=93, CLOSE=94), 100.0, 40.0)
        self.assertEqual(d["action"], "SELL")
        self.assertLessEqual(d["price"], 95.0 * (1 - 0.004) + 1e-9)

    def test_daily_fallback_defers_when_locked_down(self):
        d = X.settle_exit_from_daily(dict(OPEN=94.0, HIGH=94.0, LOW=94.0, CLOSE=94.0), 100.0, 40.0)
        self.assertEqual(d["action"], "DEFER")


class FakeCol:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.updates = []

    def _match(self, row, q):
        for k, v in q.items():
            cur = row
            for part in k.split("."):
                cur = cur.get(part) if isinstance(cur, dict) else None
            if cur != v:
                return False
        return True

    def find(self, q):
        return [r for r in self.rows if self._match(r, q)]

    def find_one(self, q):
        r = self.find(q)
        return r[0] if r else None

    def update_one(self, q, u):
        for r in self.rows:
            if self._match(r, q):
                r.update(u["$set"])
        self.updates.append((q, u))


class TestRecords(unittest.TestCase):
    def test_position_doc_is_shaped_for_the_stop_job_and_the_guard(self):
        fill = dict(price=101.75, model="minute_bar_measured_spread", reason="r", half_spread_bps=20.0)
        doc = X.build_position_doc(ORDER, fill, 90.0, datetime(2026, 11, 12, 9, 17), "3.8.0", 100000.0)
        self.assertEqual(doc["status"], "ACTIVE")
        self.assertEqual(doc["grade"], "LISTING_BREAKOUT")
        self.assertEqual(doc["trailing_stop"], doc["stop_loss"])
        self.assertLess(doc["stop_loss"], doc["entry_price"])
        self.assertEqual(doc["entry_source"], "RESTING_AUTOFILL")
        self.assertEqual(doc["signal_id"], "RESTING_ABC_20261112")
        from core.trade_guard import validate_new_entry
        v = validate_new_entry(doc, now=datetime(2026, 11, 12, 20, 0, tzinfo=IST), is_trading_day=lambda d: True, ref_high=100.0)
        self.assertEqual(v["hard"], [])

    def test_settle_exit_replaces_the_decision_close_and_logs(self):
        pos = dict(symbol="ABC", signal_id="RESTING_ABC_20261112", entry_price=100.0, exit_price=96.0, status="CLOSED")
        col = FakeCol([dict(pos)])
        events = []
        out = X.settle_exit(col, lambda *a: events.append(a), pos, dict(price=94.5, model="quote_bid_open", reason="r", half_spread_bps=10.0), "2026-11-13")
        self.assertAlmostEqual(out["pnl_pct"], -5.5)
        self.assertEqual(out["exit_price_decision"], 96.0)
        self.assertTrue(col.rows[0]["exit_settled"])
        self.assertEqual(events[0][0], "EXIT_SETTLED")
        self.assertEqual(events[0][2]["decision_price"], 96.0)

    def test_orders_newest_event_wins_and_state_is_derived(self):
        led = FakeCol([
            dict(event_type="ORDER_GENERATED", symbol="ABC", seq=1, payload=dict(order_date="2026-11-12", trigger=100.0, limit=101.0, quantity=5)),
            dict(event_type="ORDER_GENERATED", symbol="ABC", seq=2, payload=dict(order_date="2026-11-12", trigger=101.5, limit=102.5, quantity=20)),
            dict(event_type="ORDER_GENERATED", symbol="XYZ", seq=3, payload=dict(order_date="2026-11-12", trigger=50.0, limit=51.0, quantity=10)),
            dict(event_type="ORDER_GENERATED", symbol="OLD", seq=4, payload=dict(order_date="2026-11-11", trigger=1.0, limit=2.0, quantity=1)),
        ])
        orders = X.orders_for_day(led, "2026-11-12")
        self.assertEqual([o["symbol"] for o in orders], ["ABC", "XYZ"])
        self.assertEqual(orders[0]["trigger"], 101.5)
        pos = FakeCol([dict(signal_id="RESTING_ABC_20261112")])
        self.assertEqual(X.order_state(led, pos, orders[0]), "FILLED")
        self.assertEqual(X.order_state(led, pos, orders[1]), "PLACED")
        X.record_expiry(lambda t, s, p: led.rows.append(dict(event_type=t, symbol=s, payload=p)), orders[1], "trigger not reached")
        self.assertEqual(X.order_state(led, pos, orders[1]), "EXPIRED")


class TestUpstoxParsing(unittest.TestCase):
    PAYLOAD = {"data": {"NSE_EQ:ABC": {
        "instrument_token": "NSE_EQ|INE000A01010", "last_price": 102.0, "volume": 5000, "timestamp": "2026-11-12T09:16:01",
        "ohlc": {"open": 102.0, "high": 102.4, "low": 101.8, "close": 99.0}, "upper_circuit_limit": 109.0, "lower_circuit_limit": 89.0,
        "depth": {"buy": [{"price": 101.9, "quantity": 50}, {"price": 101.85, "quantity": 10}], "sell": [{"price": 102.1, "quantity": 40}, {"price": 0, "quantity": 0}]}}}}

    def test_quote_parsing(self):
        out = UQ.parse_quotes(self.PAYLOAD, {"NSE_EQ|INE000A01010": "ABC"})
        self.assertEqual(out["ABC"]["bid"], 101.9)
        self.assertEqual(out["ABC"]["ask"], 102.1)
        self.assertEqual(out["ABC"]["upper_circuit"], 109.0)

    def test_empty_side_of_the_book_is_none(self):
        p = {"data": {"NSE_EQ:ABC": dict(self.PAYLOAD["data"]["NSE_EQ:ABC"], depth={"buy": [{"price": 101.9, "quantity": 50}], "sell": [{"price": 0, "quantity": 0}]})}}
        self.assertIsNone(UQ.parse_quotes(p, {"NSE_EQ|INE000A01010": "ABC"})["ABC"]["ask"])

    def test_falls_back_to_the_response_key(self):
        p = {"data": {"NSE_EQ:ABC": dict(self.PAYLOAD["data"]["NSE_EQ:ABC"], instrument_token="OTHER")}}
        self.assertIn("ABC", UQ.parse_quotes(p, {"NSE_EQ|INE000A01010": "ABC"}))

    def test_minute_candles_are_ascending_naive_ist(self):
        raw = [["2026-11-12T09:16:00+05:30", 2, 3, 1, 2.5, 10, 0], ["2026-11-12T09:15:00+05:30", 1, 2, 1, 1.5, 5, 0]]
        df = UQ.parse_minute_candles(raw)
        self.assertEqual(list(df["OPEN"]), [1.0, 2.0])
        self.assertEqual(df["TS"].iloc[0].strftime("%H:%M"), "09:15")
        self.assertIsNone(df["TS"].iloc[0].tzinfo)

    def test_missing_token_fails_loud(self):
        with self.assertRaises(RuntimeError):
            UQ.fetch_quotes({"ABC": "K"}, None)
        with self.assertRaises(RuntimeError):
            UQ.fetch_minute_bars("K", "2026-11-12", None)

    def test_http_error_is_raised_not_guessed(self):
        class R:
            status_code = 500
            text = "boom"
        with self.assertRaises(RuntimeError):
            UQ.fetch_quotes({"ABC": "K"}, "tok", http_get=lambda *a, **k: R(), sleep=lambda s: None)


if __name__ == "__main__":
    unittest.main()
