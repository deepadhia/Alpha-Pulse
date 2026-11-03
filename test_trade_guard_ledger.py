# -*- coding: utf-8 -*-
"""Tests for core/trade_guard.py, core/ledger.py, trade_ledger.py and their wiring into db.upsert_position (no network, no real database)."""

import os
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import pandas as pd

import trade_ledger
from core import ledger
from core.trade_guard import (HARD_RISK_CAP_PCT, IST, previous_trading_day, reconcile_position, to_date, validate_new_entry)

FRI = datetime(2026, 11, 6, 11, 0)           # a Friday
TUE = datetime(2026, 11, 3, 11, 0)           # a past Tuesday (db wiring tests use the real clock)
SAT_PAST = datetime(2026, 10, 31, 11, 0)     # a past Saturday
SAT = datetime(2026, 11, 7, 11, 0)
NOW = datetime(2026, 11, 6, 12, 0, tzinfo=IST)


def weekdays_only(d):
    return d.weekday() < 5


def good(**kw):
    d = dict(symbol="AAA", entry_price=100.0, stop_loss=90.0, limit_buy_price=102.0, current_price=100.5, entry_date=FRI)
    d.update(kw)
    return d


class TestValidateNewEntry(unittest.TestCase):
    def v(self, doc, **kw):
        return validate_new_entry(doc, now=NOW, is_trading_day=weekdays_only, **kw)

    def test_clean_entry_passes(self):
        r = self.v(good())
        self.assertEqual(r, {"hard": [], "warn": []})

    def test_weekend_entry_is_rejected(self):
        r = self.v(good(entry_date=SAT), )
        self.assertTrue(any("not an NSE trading day" in x for x in r["hard"]))

    def test_holiday_is_rejected_through_the_calendar_callable(self):
        r = validate_new_entry(good(), now=NOW, is_trading_day=lambda d: d != date(2026, 11, 6))
        self.assertTrue(r["hard"])

    def test_future_date_is_rejected(self):
        r = self.v(good(entry_date=FRI + timedelta(days=3)))
        self.assertTrue(any("future" in x for x in r["hard"]))

    def test_stop_at_or_above_entry_is_rejected(self):
        self.assertTrue(self.v(good(stop_loss=100.0))["hard"])
        self.assertTrue(self.v(good(stop_loss=105.0))["hard"])
        self.assertTrue(self.v(good(stop_loss=None))["hard"])

    def test_risk_cap(self):
        self.assertEqual(self.v(good(stop_loss=88.0))["hard"], [])                       # 12.0% is the live cap
        self.assertTrue(any("cap" in x for x in self.v(good(stop_loss=85.0))["hard"]))    # 15%
        self.assertLess(HARD_RISK_CAP_PCT, 13)

    def test_bad_price(self):
        self.assertTrue(self.v(good(entry_price=0))["hard"])
        self.assertTrue(self.v(good(entry_price="x"))["hard"])

    def test_chase_over_eight_percent_of_reference_high(self):
        self.assertEqual(self.v(good(), ref_high=95.0)["hard"], [])                      # +5.3%
        r = self.v(good(entry_price=131.61, stop_loss=115.82, limit_buy_price=134.0, current_price=131.0), ref_high=103.95)   # STEAMHOUSE-like
        self.assertTrue(any("above the reference high" in x for x in r["hard"]))

    def test_warnings_do_not_block(self):
        r = self.v(good(limit_buy_price=99.0, current_price=90.0))
        self.assertEqual(r["hard"], [])
        self.assertEqual(len(r["warn"]), 2)

    def test_calendar_checks_can_be_skipped(self):
        r = validate_new_entry(good(entry_date=SAT), now=NOW, is_trading_day=weekdays_only, check_calendar=False)
        self.assertEqual(r["hard"], [])

    def test_aware_utc_midnight_date_is_read_as_the_same_ist_day(self):
        self.assertEqual(to_date(datetime(2026, 11, 6, tzinfo=timezone.utc)), date(2026, 11, 6))
        self.assertEqual(to_date("2026-11-06T00:00:00"), date(2026, 11, 6))
        self.assertIsNone(to_date("not a date"))

    def test_previous_trading_day_skips_weekend(self):
        self.assertEqual(previous_trading_day(date(2026, 11, 9), weekdays_only), date(2026, 11, 6))


class TestReconcile(unittest.TestCase):
    BARS = {"2026-11-05": (98.0, 104.0), "2026-11-06": (99.0, 106.0), "2026-11-10": (100.0, 110.0)}

    def r(self, pos):
        return reconcile_position(pos, self.BARS, is_trading_day=weekdays_only)

    def test_clean_position(self):
        self.assertEqual(self.r(dict(entry_date="2026-11-06", entry_price=101.0, exit_date="2026-11-10", exit_price=105.0, pnl_pct=3.96)), [])

    def test_entry_above_day_high(self):
        f = self.r(dict(entry_date="2026-11-05", entry_price=110.0))
        self.assertTrue(any(x.startswith("ENTRY_ABOVE_DAY_HIGH") for x in f))

    def test_weekend_entry(self):
        f = self.r(dict(entry_date="2026-11-07", entry_price=100.0))
        self.assertTrue(any(x.startswith("ENTRY_NOT_TRADING_DAY") for x in f))

    def test_exit_outside_range_and_pnl_mismatch(self):
        f = self.r(dict(entry_date="2026-11-06", entry_price=100.0, exit_date="2026-11-10", exit_price=120.0, pnl_pct=61.0))
        self.assertIn("EXIT_OUTSIDE_DAY_RANGE", f)
        self.assertTrue(any(x.startswith("PNL_MISMATCH") for x in f))

    def test_no_candles(self):
        self.assertIn("NO_CANDLES", reconcile_position(dict(entry_date="2026-11-06", entry_price=100.0), None, is_trading_day=weekdays_only))

    def test_dataframe_input(self):
        df = pd.DataFrame([dict(DATE="2026-11-06", LOW=99.0, HIGH=106.0)])
        self.assertEqual(reconcile_position(dict(entry_date="2026-11-06", entry_price=100.0), df, is_trading_day=weekdays_only), [])


class FakeLedgerCol:
    def __init__(self):
        self.docs = []

    def insert_one(self, d):
        if any(x["seq"] == d["seq"] for x in self.docs):
            class DuplicateKeyError(Exception):
                pass
            raise DuplicateKeyError("dup")
        self.docs.append(dict(d))

    def find(self, q=None, proj=None):
        q = q or {}
        return [dict(d) for d in self.docs if all(d.get(k) == v for k, v in q.items())]

    def create_index(self, *a, **k):
        pass


class TestLedger(unittest.TestCase):
    def setUp(self):
        self.col = FakeLedgerCol()
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "ledger.jsonl")

    def add(self, t="NOTE", sym="AAA", **payload):
        return ledger.append(self.col, t, sym, payload, "test", code_version="x", local_path=self.path)

    def test_chain_is_built_and_verifies(self):
        a, b, c = self.add(n=1), self.add(n=2), self.add("ENTRY_ACCEPTED", price=100.0)
        self.assertEqual([a["seq"], b["seq"], c["seq"]], [1, 2, 3])
        self.assertEqual(b["prev_hash"], a["hash"])
        self.assertEqual(ledger.verify_chain(ledger.all_events(self.col)), [])

    def test_edit_and_removal_are_detected(self):
        for i in range(4):
            self.add(n=i)
        docs = ledger.all_events(self.col)
        docs[1]["payload"]["n"] = 99                                                   # edited row
        self.assertTrue(any("edited" in p for p in ledger.verify_chain(docs)))
        docs = ledger.all_events(self.col)
        del docs[2]                                                                    # removed row
        self.assertTrue(any("gap" in p for p in ledger.verify_chain(docs)))

    def test_unknown_event_type_is_not_written_and_never_raises(self):
        self.assertIsNone(self.add("SOMETHING_ELSE"))
        self.assertEqual(self.col.docs, [])

    def test_local_mirror_and_local_only_mode(self):
        self.add(n=1)
        self.assertEqual(len(ledger.all_events(None, self.path)), 1)
        ledger.append(None, "NOTE", "BBB", {"n": 2}, "test", local_path=self.path)      # no database: continues the local chain
        ev = ledger.all_events(None, self.path)
        self.assertEqual([e["seq"] for e in ev], [1, 2])
        self.assertEqual(ledger.verify_chain(ev), [])

    def test_sequence_contention_is_retried(self):
        self.add(n=1)
        real_last = ledger._last_event
        calls = {"n": 0}

        def stale_once(col, path):
            calls["n"] += 1
            return None if calls["n"] == 1 else real_last(col, path)                   # first read is stale: seq 1 collides, then retry
        with patch.object(ledger, "_last_event", stale_once):
            ev = self.add(n=2)
        self.assertEqual(ev["seq"], 2)

    def test_payload_is_json_safe(self):
        ev = self.add(when=datetime(2026, 11, 6), n=1.5)
        self.assertIsInstance(ev["payload"]["when"], str)


class FakePositions:
    def __init__(self, docs=None):
        self.docs = {d["symbol"]: dict(d) for d in (docs or [])}
        self.updates = []

    def find_one(self, q, proj=None):
        d = self.docs.get(q.get("symbol"))
        return dict(d) if d else None

    def update_one(self, q, update, upsert=False):
        self.updates.append((q, update))
        sym = q["symbol"]
        if isinstance(update, dict):
            self.docs.setdefault(sym, {"symbol": sym}).update(update.get("$set", {}))
        else:
            for stage in update:
                if "$set" in stage:
                    self.docs.setdefault(sym, {"symbol": sym}).update(stage["$set"])


class TestDbWiring(unittest.TestCase):
    """db.upsert_position: new opens are guarded, rejections and open/stop/close transitions go to the ledger."""

    def setUp(self):
        import db
        self.db = db
        self.led = FakeLedgerCol()
        self.tmp = tempfile.mkdtemp()
        self.env = patch.dict(os.environ, {"TRADE_GUARD_FORCE": "1", "LEDGER_LOCAL_PATH": os.path.join(self.tmp, "l.jsonl")})
        self.env.start()
        self.p_hol = patch("utils.get_dynamic_nse_holidays", lambda *a, **k: set())       # no network, no holiday database
        self.p_hol.start()
        self.p_led = patch.object(db, "trade_ledger_col", self.led)
        self.p_led.start()
        self.p_ev = patch("core.strategy_evidence.record_trade_closure_evidence", lambda *a, **k: None)   # it fetches market data
        self.p_ev.start()

    def tearDown(self):
        self.p_ev.stop()
        self.p_led.stop()
        self.p_hol.stop()
        self.env.stop()

    def use(self, docs=None):
        self.pos = FakePositions(docs)
        p = patch.object(self.db, "positions_col", self.pos)
        p.start()
        self.addCleanup(p.stop)
        m = patch.object(self.db, "increment_metric", lambda *a, **k: None)
        m.start()
        self.addCleanup(m.stop)

    def doc(self, **kw):
        d = dict(symbol="AAA", status="ACTIVE", entry_price=100.0, stop_loss=90.0, trailing_stop=90.0, limit_buy_price=102.0, current_price=100.0,
                 entry_date=TUE, _backfilled=False)
        d.update(kw)
        return d

    def types(self):
        return [e["event_type"] for e in sorted(self.led.docs, key=lambda e: e["seq"])]

    def test_good_open_is_stored_and_ledgered(self):
        self.use()
        self.assertTrue(self.db.upsert_position(self.doc(entry_date=TUE)))
        self.assertEqual(len(self.pos.updates), 1)
        self.assertEqual(self.types(), ["ENTRY_ACCEPTED"])
        self.assertEqual(ledger.verify_chain(self.led.docs), [])

    def test_impossible_open_is_rejected_not_stored(self):
        self.use()
        ok = self.db.upsert_position(self.doc(stop_loss=105.0))
        self.assertFalse(ok)
        self.assertEqual(self.pos.updates, [])
        self.assertEqual(self.types(), ["ENTRY_REJECTED"])

    def test_weekend_open_is_rejected(self):
        self.use()
        ok = self.db.upsert_position(self.doc(entry_date=SAT_PAST))
        self.assertFalse(ok)
        self.assertEqual(self.pos.updates, [])

    def test_backfilled_rows_are_not_blocked(self):
        self.use()
        ok = self.db.upsert_position(self.doc(entry_date=SAT_PAST, _backfilled=True))
        self.assertTrue(ok)
        self.assertEqual(len(self.pos.updates), 1)

    def test_kill_switch_disables_the_guard(self):
        self.use()
        with patch.dict(os.environ, {"TRADE_GUARD_DISABLED": "1"}):
            self.assertTrue(self.db.upsert_position(self.doc(stop_loss=105.0)))

    def test_stop_raise_and_close_are_ledgered_once(self):
        self.use([dict(symbol="AAA", status="ACTIVE", entry_price=100.0, trailing_stop=90.0)])
        self.db.upsert_position(self.doc(trailing_stop=95.0, entry_date=TUE))      # raised: STOP_MOVED
        self.db.upsert_position(self.doc(trailing_stop=95.0, entry_date=TUE))      # not raised again: nothing
        self.pos.docs["AAA"]["status"] = "ACTIVE"
        self.db.upsert_position(self.doc(status="CLOSED", exit_price=94.0, exit_reason="Stop Loss", exit_date=datetime(2026, 11, 4),
                                         pnl_pct=-6.0, entry_date=TUE))
        self.assertIn("STOP_MOVED", self.types())
        self.assertEqual(self.types().count("POSITION_CLOSED"), 1)


class TestLedgerCli(unittest.TestCase):
    def test_check_fill_rules(self):
        order = dict(symbol="AAA", trigger=100.0, limit=101.0, date="2026-11-03")
        when = datetime(2026, 11, 3, 10, 0)
        problems, ext = trade_ledger.check_fill(order, 100.3, when)
        self.assertEqual(problems, [])
        self.assertAlmostEqual(ext, 0.3, places=2)
        self.assertTrue(trade_ledger.check_fill(order, 102.0, when)[0])        # above the limit: cannot fill
        self.assertTrue(trade_ledger.check_fill(order, 99.0, when)[0])         # below the trigger: cannot fill
        self.assertTrue(trade_ledger.check_fill(order, 100.2, datetime(2026, 10, 31, 10, 0))[0])    # Saturday

    def test_latest_order_reads_the_log(self):
        with tempfile.TemporaryDirectory() as t:
            p = os.path.join(t, "o.jsonl")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write('{"symbol":"AAA","trigger":1}\n{"symbol":"AAA","trigger":2}\n{"symbol":"BBB","trigger":3}\n')
            self.assertEqual(trade_ledger.latest_order("aaa", p)["trigger"], 2)
            self.assertIsNone(trade_ledger.latest_order("ZZZ", p))


if __name__ == "__main__":
    unittest.main()
