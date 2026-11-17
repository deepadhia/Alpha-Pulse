"""Tests for the automatic resting-order alert (pure parts: message, freshness lag, idempotency)."""
import unittest

import pandas as pd

import resting_order_alerts as R
from core.order_sheet import RestingEntry


def entry(sym="ABC", qty=20):
    return RestingEntry(symbol=sym, quantity=qty, trigger=101.5, limit=102.5, est_fill=101.8, est_value=2036.0,
                        stop_alert=90.0, risk_rupees=236.0, risk_pct_of_capital=1.18, age=30, rr=1.67)


def is_td(d):
    return pd.Timestamp(d).weekday() < 5


class FakeCol:
    def __init__(self, doc=None):
        self.doc = doc

    def find_one(self, q):
        return self.doc

    def update_one(self, q, u, upsert=False):
        self.doc = dict(q, **u["$set"])


class TestLag(unittest.TestCase):
    def test_lag_counts_missing_sessions(self):
        exp = pd.Timestamp("2026-11-11")                       # Wednesday
        self.assertEqual(R.session_lag(pd.Timestamp("2026-11-11"), exp, is_td), 0)
        self.assertEqual(R.session_lag(pd.Timestamp("2026-11-10"), exp, is_td), 1)
        self.assertEqual(R.session_lag(pd.Timestamp("2026-11-09"), exp, is_td), 2)

    def test_weekend_is_not_a_missing_session(self):
        exp = pd.Timestamp("2026-11-09")                       # Monday, data from Friday
        self.assertEqual(R.session_lag(pd.Timestamp("2026-11-06"), exp, is_td), 1)

    def test_unknown_data_is_99(self):
        self.assertEqual(R.session_lag(None, pd.Timestamp("2026-11-11"), is_td), 99)


class TestMessage(unittest.TestCase):
    day = pd.Timestamp("2026-11-12")
    exp = pd.Timestamp("2026-11-11")

    def test_orders_listed_and_actionable(self):
        m = R.build_message([entry("ABC"), entry("XYZ", 5)], [], self.day, self.exp, self.exp, 0, [], 3, 100000)
        self.assertEqual(m["n_orders"], 2)
        self.assertTrue(m["actionable"])
        self.assertFalse(m["withheld"])
        self.assertIn("ABC", m["text"])
        self.assertIn("Action: BUY\nQuantity: 20", m["text"])
        self.assertIn("101.50", m["text"])

    def test_no_orders_is_silent(self):
        m = R.build_message([], [], self.day, self.exp, self.exp, 0, [], 0, 100000)
        self.assertEqual(m["n_orders"], 0)
        self.assertFalse(m["actionable"])
        self.assertIn("No eligible", m["text"])

    def test_stale_withholds_orders_and_is_loud(self):
        m = R.build_message([entry()], [], self.day, pd.Timestamp("2026-11-06"), self.exp, 3, [], 0, 100000)
        self.assertTrue(m["withheld"])
        self.assertEqual(m["n_orders"], 0)
        self.assertTrue(m["actionable"])
        self.assertIn("WITHHELD", m["text"])
        self.assertNotIn("Quantity: 20", m["text"])

    def test_one_session_lag_is_a_note_not_a_block(self):
        m = R.build_message([entry()], [], self.day, pd.Timestamp("2026-11-10"), self.exp, 1, [], 0, 100000)
        self.assertFalse(m["withheld"])
        self.assertEqual(m["n_orders"], 1)
        self.assertIn("one session behind", m["text"])

    def test_html_in_dynamic_text_is_escaped(self):
        m = R.build_message([entry("A<B>")], ["X & Y"], self.day, self.exp, self.exp, 0, ["w<1>"], 0, 100000)
        self.assertNotIn("A<B>", m["text"])
        self.assertIn("A&lt;B&gt;", m["text"])
        self.assertIn("X &amp; Y", m["text"])


class TestIdempotency(unittest.TestCase):
    day = pd.Timestamp("2026-11-12")

    def test_fresh_sent_blocks_second_start(self):
        col = FakeCol()
        R.record_run(col, self.day, sent=True, withheld=False, n_orders=2)
        self.assertTrue(R.already_sent(col, self.day))

    def test_withheld_or_unsent_run_allows_retry(self):
        col = FakeCol()
        R.record_run(col, self.day, sent=True, withheld=True, n_orders=0)
        self.assertFalse(R.already_sent(col, self.day))
        R.record_run(col, self.day, sent=False, withheld=False, n_orders=2)
        self.assertFalse(R.already_sent(col, self.day))

    def test_no_heartbeat_means_not_sent(self):
        self.assertFalse(R.already_sent(FakeCol(None), self.day))


class FakeLedger:
    """Stands in for the trade_ledger collection: find_one matches on the flat and dotted fields the job queries."""
    def __init__(self):
        self.rows = []

    def add(self, symbol, order_date, source=R.SOURCE, event_type="ORDER_GENERATED"):
        self.rows.append({"event_type": event_type, "source": source, "symbol": symbol, "payload": {"order_date": order_date}})

    def find_one(self, q):
        for r in self.rows:
            if (r["event_type"] == q["event_type"] and r["source"] == q["source"] and r["symbol"] == q["symbol"]
                    and r["payload"]["order_date"] == q["payload.order_date"]):
                return r
        return None


class TestLedgerIdempotency(unittest.TestCase):
    day = pd.Timestamp("2026-11-12")

    def test_recorded_order_is_detected(self):
        led = FakeLedger()
        led.add("ABC", "2026-11-12")
        self.assertTrue(R.ledger_has_order(led, "ABC", self.day))

    def test_other_symbol_day_source_or_type_is_not_a_match(self):
        led = FakeLedger()
        led.add("ABC", "2026-11-12")
        self.assertFalse(R.ledger_has_order(led, "XYZ", self.day))                         # other symbol
        self.assertFalse(R.ledger_has_order(led, "ABC", pd.Timestamp("2026-11-13")))        # other order day
        led2 = FakeLedger()
        led2.add("ABC", "2026-11-12", source="order_sheet.py")                              # the manual sheet logged it: still ours to record
        self.assertFalse(R.ledger_has_order(led2, "ABC", self.day))
        led3 = FakeLedger()
        led3.add("ABC", "2026-11-12", event_type="NOTE")
        self.assertFalse(R.ledger_has_order(led3, "ABC", self.day))

    def test_missing_collection_means_not_recorded(self):
        self.assertFalse(R.ledger_has_order(None, "ABC", self.day))

    def test_lookup_error_is_not_swallowed(self):
        class Boom:
            def find_one(self, q):
                raise RuntimeError("db down")
        with self.assertRaises(RuntimeError):
            R.ledger_has_order(Boom(), "ABC", self.day)


class TestSettlementFailureCard(unittest.TestCase):
    def test_pause_card_leads_with_impact_and_keeps_errors_in_logs(self):
        report = dict(waiting=[f"{s}: no daily bar for 2026-11-17 yet" for s in
                              ("ADROITIND", "ELEVATE", "FUSIONCX", "GERMAN", "GSPLTRANS", "HDFIRE", "NITYAS", "ORIENTCABL")])
        text = R.settlement_failure_message(pd.Timestamp("2026-11-18"), pd.Timestamp("2026-11-17"), report)
        self.assertIn("P ORDERS PAUSED", text)
        self.assertIn("18 Nov 2026", text)
        self.assertIn("17 Nov 2026", text)
        self.assertIn("Affected names (8)", text)
        self.assertIn("ORDERS WITHHELD", text)
        self.assertNotIn("RuntimeError", text)
        self.assertNotIn("PLACE BEFORE THE OPEN", text)
        for symbol in ("ADROITIND", "ELEVATE", "FUSIONCX", "GERMAN", "GSPLTRANS", "HDFIRE", "NITYAS", "ORIENTCABL"):
            self.assertIn(symbol, text)

    def test_generic_failure_card_does_not_invent_a_data_outage(self):
        text = R.settlement_failure_message(pd.Timestamp("2026-11-18"), pd.Timestamp("2026-11-17"), {})
        self.assertIn("execution log", text)
        self.assertNotIn("Affected names", text)

    def test_dynamic_symbols_are_escaped_and_deduplicated(self):
        report = dict(waiting=["A<B: missing"], exit_waiting=["A<B: recovery incomplete"])
        text = R.settlement_failure_message(pd.Timestamp("2026-11-18"), pd.Timestamp("2026-11-17"), report)
        self.assertIn("Affected names (1)", text)
        self.assertIn("A&lt;B", text)
        self.assertNotIn("A<B", text)

    def test_partial_settlement_activity_is_not_hidden_by_the_pause(self):
        report = dict(filled=["SYNTHETIC fill"], sold=["SYNTHETIC sale"], waiting=["ABC: missing bar"])
        text = R.settlement_failure_message(pd.Timestamp("2026-11-18"), pd.Timestamp("2026-11-17"), report)
        self.assertIn("Already recorded before the pause", text)
        self.assertIn("Paper-forward fills: 1", text)
        self.assertIn("Sales: 1", text)


if __name__ == "__main__":
    unittest.main()
