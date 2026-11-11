"""End-to-end tests of core.resting_jobs with fakes: morning quote fills / exits, evening minute-bar fills, day-0 stop, deferrals, idempotency."""
import unittest
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from core import resting_exec as X
from core.resting_jobs import Deps, run_evening, run_morning, target_sale_day

IST = timezone(timedelta(hours=5, minutes=30))
D = date(2026, 11, 12)
NOW = datetime(2026, 11, 12, 9, 18, tzinfo=IST)


class Col:
    def __init__(self, rows=None):
        self.rows = [dict(r) for r in (rows or [])]

    @staticmethod
    def _get(row, key):
        cur = row
        for part in key.split("."):
            cur = cur.get(part) if isinstance(cur, dict) else None
        return cur

    def find(self, q):
        return [r for r in self.rows if all(self._get(r, k) == v for k, v in q.items())]

    def find_one(self, q):
        r = self.find(q)
        return r[0] if r else None

    def update_one(self, q, u):
        for r in self.find(q):
            r.update(u["$set"])


def history(n=30, px=100.0):
    dates = pd.bdate_range(end="2026-11-11", periods=n)
    return pd.DataFrame(dict(DATE=dates, OPEN=px, HIGH=px * 1.01, LOW=px * 0.99, CLOSE=px, VOLUME=1_000_000.0))


def with_bar(df, day, o, h, l, c):
    return pd.concat([df, pd.DataFrame([dict(DATE=pd.Timestamp(day), OPEN=o, HIGH=h, LOW=l, CLOSE=c, VOLUME=2_000_000.0)])], ignore_index=True)


def order(sym="ABC", day="2026-11-12", trig=101.5, lim=102.5, qty=20, seq=1):
    return dict(event_type="ORDER_GENERATED", symbol=sym, seq=seq, payload=dict(symbol=sym, order_date=day, trigger=trig, limit=lim, quantity=qty, stop_alert=90.0))


class World:
    """Fakes wired into Deps; `events` collects ledger appends, `ledger` mirrors them so order_state sees notes."""

    def __init__(self, orders=(), positions=(), daily=None, quotes=None, minutes=None, measured=None, guard_ok=True, trading=lambda d: d.weekday() < 5):
        self.ledger = Col(orders)
        self.positions = Col(positions)
        self.events = []
        self._daily, self._quotes, self._minutes, self._measured, self.guard_ok = daily or {}, quotes or {}, minutes or {}, measured or {}, guard_ok
        self.audits = []
        self.deps = Deps(ledger_col=self.ledger, positions_col=self.positions, ledger_append=self._append, upsert_position=self._upsert,
                         quotes=self._get_quotes, minute_bars=lambda s, d: self._minutes.get(s, pd.DataFrame(columns=["TS", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"])),
                         daily=lambda s: self._daily.get(s), audit_quote=lambda s, q, d: self.audits.append((s, q)),
                         measured_hs=lambda s, d: self._measured.get(s), is_trading_day=trading)

    def _append(self, t, s, p):
        self.events.append((t, s, p))
        self.ledger.rows.append(dict(event_type=t, symbol=s, payload=p, seq=len(self.ledger.rows) + 100))
        return {"ok": True}

    def _upsert(self, doc):
        if not self.guard_ok and doc.get("status") == "ACTIVE":
            return False
        old = self.positions.find_one({"symbol": doc["symbol"]})
        if old is not None:
            old.update(doc)
        else:
            self.positions.rows.append(dict(doc))
        return True

    def _get_quotes(self, syms):
        if isinstance(self._quotes, Exception):
            raise self._quotes
        return {s: self._quotes[s] for s in syms if s in self._quotes}


def quote(o, bid, ask, ltp=None, uc=120.0, lc=80.0):
    return dict(open=o, high=o, low=o, ltp=ltp or o, bid=bid, ask=ask, bid_qty=1, ask_qty=1, upper_circuit=uc, lower_circuit=lc, ts="t")


class TestMorningEntries(unittest.TestCase):
    def test_gap_up_fills_at_the_ask_and_is_idempotent(self):
        w = World(orders=[order()], daily={"ABC": history()}, quotes={"ABC": quote(102.0, 101.9, 102.1)})
        rep = run_morning(w.deps, NOW)
        self.assertEqual(len(rep["filled"]), 1)
        pos = w.positions.find_one({"symbol": "ABC"})
        self.assertEqual(pos["entry_price"], 102.1)
        self.assertEqual(pos["entry_source"], "RESTING_AUTOFILL")
        self.assertEqual(pos["fill_model"], "quote_ask_open")
        self.assertLess(pos["stop_loss"], pos["entry_price"])
        self.assertEqual(len(w.audits), 1)
        rep2 = run_morning(w.deps, NOW)                                      # a second start the same morning does nothing
        self.assertEqual(rep2["filled"], [])
        self.assertEqual(len(w.positions.rows), 1)

    def test_opened_below_trigger_is_left_for_the_evening(self):
        w = World(orders=[order()], daily={"ABC": history()}, quotes={"ABC": quote(100.0, 99.9, 100.1)})
        rep = run_morning(w.deps, NOW)
        self.assertEqual((len(rep["pending"]), len(w.positions.rows), w.events), (1, 0, []))

    def test_open_above_limit_expires_with_a_ledger_note(self):
        w = World(orders=[order()], daily={"ABC": history()}, quotes={"ABC": quote(104.0, 103.9, 104.1)})
        rep = run_morning(w.deps, NOW)
        self.assertEqual(len(rep["expired"]), 1)
        self.assertEqual(w.events[0][0], "NOTE")
        self.assertEqual(w.events[0][2]["kind"], "RESTING_ORDER_EXPIRED")
        self.assertEqual(run_morning(w.deps, NOW)["expired"], [])             # no second note

    def test_symbol_already_held_does_not_open_a_second_position(self):
        w = World(orders=[order()], positions=[dict(symbol="ABC", status="ACTIVE", signal_id="BREAKOUT_ABC_20261110")],
                  daily={"ABC": history()}, quotes={"ABC": quote(102.0, 101.9, 102.1)})
        rep = run_morning(w.deps, NOW)
        self.assertEqual(len(rep["expired"]), 1)
        self.assertEqual(len(w.positions.rows), 1)

    def test_entry_guard_refusal_is_recorded_not_lost(self):
        w = World(orders=[order()], daily={"ABC": history()}, quotes={"ABC": quote(102.0, 101.9, 102.1)}, guard_ok=False)
        rep = run_morning(w.deps, NOW)
        self.assertIn("guard", rep["expired"][0])

    def test_quote_outage_fails_loud(self):
        w = World(orders=[order()], daily={"ABC": history()}, quotes=RuntimeError("Upstox quote HTTP 500"))
        with self.assertRaises(RuntimeError):
            run_morning(w.deps, NOW)
        self.assertEqual(w.events, [])

    def test_holiday_is_skipped(self):
        w = World(orders=[order()], trading=lambda d: False)
        self.assertEqual(run_morning(w.deps, NOW)["skipped"], "not an NSE trading day")


def closed_pos(sym="ABC", decision="2026-11-11", entry=100.0, decision_px=96.0, deferred=0):
    return dict(symbol=sym, signal_id=f"RESTING_{sym}_20261110", entry_source="RESTING_AUTOFILL", status="CLOSED", entry_price=entry, exit_price=decision_px,
                exit_date=datetime(2026, 11, 11, tzinfo=timezone.utc), quantity=20, exit_deferred_days=deferred)


class TestMorningExits(unittest.TestCase):
    def test_exit_sells_at_the_best_bid_and_replaces_the_decision_close(self):
        w = World(positions=[closed_pos()], daily={"ABC": history()}, quotes={"ABC": quote(95.0, 94.8, 95.2)})
        rep = run_morning(w.deps, NOW)
        self.assertEqual(len(rep["sold"]), 1)
        p = w.positions.find_one({"symbol": "ABC"})
        self.assertEqual((p["exit_price"], p["exit_price_decision"], p["exit_settled"]), (94.8, 96.0, True))
        self.assertAlmostEqual(p["pnl_pct"], -5.2)
        self.assertEqual(w.events[0][0], "EXIT_SETTLED")
        self.assertEqual(run_morning(w.deps, NOW)["sold"], [])                # settled exits are not sold twice

    def test_lower_circuit_defers_to_the_next_session(self):
        w = World(positions=[closed_pos()], daily={"ABC": history()}, quotes={"ABC": quote(80.0, None, 80.0, ltp=80.0, lc=80.0)})
        rep = run_morning(w.deps, NOW)
        self.assertEqual(len(rep["deferred"]), 1)
        p = w.positions.find_one({"symbol": "ABC"})
        self.assertFalse(p.get("exit_settled"))
        self.assertEqual(p["exit_deferred_days"], 1)
        nxt = datetime(2026, 11, 13, 9, 18, tzinfo=IST)
        w._quotes = {"ABC": quote(78.0, 77.9, 78.1)}
        rep2 = run_morning(w.deps, nxt)                                       # the next trading day sells at its own open bid
        self.assertEqual(len(rep2["sold"]), 1)
        self.assertEqual(w.positions.find_one({"symbol": "ABC"})["exit_price"], 77.9)

    def test_target_sale_day_skips_non_trading_days(self):
        td = lambda d: d.weekday() < 5
        self.assertEqual(target_sale_day(date(2026, 11, 13), 0, td), date(2026, 11, 16))   # Friday decision -> Monday
        self.assertEqual(target_sale_day(date(2026, 11, 11), 1, td), date(2026, 11, 13))


class TestReentry(unittest.TestCase):
    def test_a_new_trade_does_not_inherit_the_previous_trades_exit_state(self):
        old = dict(closed_pos(), exit_settled=True, exit_price_decision=96.0, exit_fill_model="quote_bid_open", exit_deferred_days=2)
        w = World(orders=[order()], positions=[old], daily={"ABC": history()}, quotes={"ABC": quote(102.0, 101.9, 102.1)})
        run_morning(w.deps, NOW)
        p = w.positions.find_one({"symbol": "ABC"})
        self.assertEqual((p["status"], p["exit_settled"], p["exit_deferred_days"], p["exit_price_decision"]), ("ACTIVE", False, 0, None))
        # and when this new trade is closed by the stop job, its sale is settled with the right decision price
        p.update(status="CLOSED", exit_price=99.0, exit_date=datetime(2026, 11, 13, tzinfo=timezone.utc))
        w._quotes = {"ABC": quote(98.0, 97.9, 98.1)}
        rep = run_morning(w.deps, datetime(2026, 11, 16, 9, 18, tzinfo=IST))
        self.assertEqual(len(rep["sold"]), 1)
        self.assertEqual(w.positions.find_one({"symbol": "ABC"})["exit_price_decision"], 99.0)


class TestEvening(unittest.TestCase):
    def minutes(self, rows):
        t0 = pd.Timestamp("2026-11-12 09:15")
        return pd.DataFrame([dict(TS=t0 + pd.Timedelta(minutes=i), OPEN=o, HIGH=h, LOW=l, CLOSE=c, VOLUME=1000) for i, (o, h, l, c) in enumerate(rows)])

    def test_intraday_trigger_is_priced_from_the_minute_bars_with_the_measured_spread(self):
        daily = with_bar(history(), "2026-11-12", 100.0, 104.0, 99.0, 103.0)
        m = self.minutes([(100, 100.5, 99.5, 100), (100, 101.0, 100, 100.8), (100.8, 102.0, 100.8, 101.9)])
        w = World(orders=[order()], daily={"ABC": daily}, minutes={"ABC": m}, measured={"ABC": 20.0})
        rep = run_evening(w.deps, D)
        self.assertEqual(len(rep["filled"]), 1)
        p = w.positions.find_one({"symbol": "ABC"})
        self.assertEqual(p["fill_model"], "minute_bar_measured_spread")
        self.assertAlmostEqual(p["entry_price"], 101.75)
        self.assertEqual(run_evening(w.deps, D)["filled"], [])                # re-run: nothing new

    def test_trigger_not_reached_expires(self):
        daily = with_bar(history(), "2026-11-12", 100.0, 101.0, 99.0, 100.5)
        w = World(orders=[order()], daily={"ABC": daily}, minutes={"ABC": self.minutes([(100, 101.0, 99.0, 100.5)])})
        rep = run_evening(w.deps, D)
        self.assertEqual((len(rep["expired"]), len(w.positions.rows)), (1, 0))

    def test_missing_daily_bar_waits_instead_of_guessing(self):
        w = World(orders=[order()], daily={"ABC": history()})
        rep = run_evening(w.deps, D)
        self.assertEqual((len(rep["waiting"]), w.events), (1, []))

    def test_minute_bar_outage_falls_back_to_the_daily_rule_and_says_so(self):
        daily = with_bar(history(), "2026-11-12", 100.0, 104.0, 99.0, 103.0)
        w = World(orders=[order()], daily={"ABC": daily})
        w.deps.minute_bars = lambda s, d: (_ for _ in ()).throw(RuntimeError("Upstox minute candles failed"))
        rep = run_evening(w.deps, D)
        self.assertEqual(w.positions.find_one({"symbol": "ABC"})["fill_model"], "daily_bar_replay_rule")
        self.assertIn("minute bars unavailable", rep["notes"][0])

    def test_day0_close_below_the_initial_stop_closes_the_position(self):
        pos = dict(symbol="ABC", signal_id="RESTING_ABC_20261112", entry_source="RESTING_AUTOFILL", status="ACTIVE", entry_price=102.0,
                   trailing_stop=95.0, entry_date=datetime(2026, 11, 12, 9, 17), quantity=20)
        daily = with_bar(history(), "2026-11-12", 100.0, 104.0, 90.0, 93.0)
        w = World(positions=[pos], daily={"ABC": daily})
        rep = run_evening(w.deps, D)
        self.assertEqual(len(rep["day0_stops"]), 1)
        p = w.positions.find_one({"symbol": "ABC"})
        self.assertEqual((p["status"], p["exit_price"]), ("CLOSED", 93.0))

    def test_unsettled_exit_is_priced_from_the_next_daily_open(self):
        pos = closed_pos(decision="2026-11-11", decision_px=96.0)
        daily = with_bar(history(), "2026-11-12", 95.0, 96.0, 93.0, 94.0)
        w = World(positions=[pos], daily={"ABC": daily})
        rep = run_evening(w.deps, D)
        self.assertEqual(len(rep["sold"]), 1)
        p = w.positions.find_one({"symbol": "ABC"})
        self.assertLess(p["exit_price"], 95.0)
        self.assertEqual(p["exit_fill_model"], "daily_open_tier_spread")

    def test_unsettled_exit_on_a_locked_down_day_defers(self):
        pos = closed_pos(decision="2026-11-11", decision_px=96.0)
        daily = with_bar(history(), "2026-11-12", 90.0, 90.0, 90.0, 90.0)
        w = World(positions=[pos], daily={"ABC": daily})
        rep = run_evening(w.deps, D)
        self.assertEqual((len(rep["deferred"]), w.positions.find_one({"symbol": "ABC"})["exit_deferred_days"]), (1, 1))


class TestReport(unittest.TestCase):
    def test_report_lines_escape_and_cover_every_outcome(self):
        from core.resting_runtime import format_exec_lines, format_morning_message
        rep = dict(filled=["A<B>: BUY 5 @ 10.00"], expired=["C: trigger not reached"], sold=["D: SELL @ 9.00"], deferred=["E: lower circuit"],
                   day0_stops=["F: stop"], pending=["G: later"], waiting=["H: data"], exit_waiting=[], notes=["n"], day="2026-11-12", quotes=3)
        text = "\n".join(format_exec_lines(rep, "t"))
        self.assertIn("A&lt;B&gt;", text)
        for tag in ("FORWARD FILLS", "DID NOT FILL", "EXITS SOLD", "EXITS DEFERRED", "DAY-0 STOP", "LEFT FOR THE EVENING", "WAITING FOR DATA", "NOTES"):
            self.assertIn(tag, text)
        self.assertEqual(format_exec_lines(dict(), "t"), [])
        self.assertIn("Nothing to execute", format_morning_message(dict(day="2026-11-12", quotes=0), NOW))


if __name__ == "__main__":
    unittest.main()
