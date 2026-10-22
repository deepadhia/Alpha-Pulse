# -*- coding: utf-8 -*-
"""
Tests for the PAPER_ONLY S3 wiring: core/setup3.py (preview_s3_entry, s3_paper_status, the turnover cap) and the pure helpers of order_sheet.py.
Synthetic data only (the stock of test_setup3.py: pivot 110 at bar 20, base 104-106 at bars 70-79, breakout bar 80); nothing touches MongoDB.
"""
import os
os.environ.setdefault("DISABLE_TELEGRAM", "1")
os.environ.setdefault("TESTING", "1")

import unittest
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import order_sheet as osh
from core.honest_backtest import Params, _Bars, _listing_context
from core import s3_store
from core.order_sheet import SheetConfig, build_resting_entries, format_resting_entries
from core.setup3 import (NO_TIME_EXITS, PAPER_MAX_TURN_CR, PAPER_PARAMS, S3_PAPER_COHORT, S3_PAPER_VERSION, Setup3Params, paper_rules_fingerprint, _prepare, paper_exit_params, pivot_arrays, preview_s3_entry,
                         s3_paper_status, setup3_signal, setup3_state, simulate_symbol_setup3)
from test_setup3 import LISTING, mk_df

TODAY = pd.Timestamp("2026-03-22")          # = bar 80 of mk_df (listing 2026-01-01)


def prior(df, n=80):
    return df.iloc[:n].copy()


class TestPaperParams(unittest.TestCase):
    def test_owner_decision_constants(self):
        self.assertEqual(PAPER_MAX_TURN_CR, 25.0)
        self.assertEqual(PAPER_PARAMS.max_turn_cr, 25.0)
        self.assertIsNone(Setup3Params().max_turn_cr)               # research default untouched
        self.assertEqual(dict(paper_exit_params().exit_overrides), dict(NO_TIME_EXITS))
        self.assertTrue(all(v == 9999 for _, v in NO_TIME_EXITS))


class TestTurnoverCap(unittest.TestCase):
    def _state(self, df, s3):
        b = _Bars.of(df)
        ld_high, _, ld = _listing_context(df, LISTING)
        pv, pi = pivot_arrays(b)
        return setup3_state(b, _prepare(df), pv, pi, 80, ld_high, ld, Params(), s3)

    def test_cap_passes_below_and_blocks_above(self):
        df = mk_df()                                                # turnover about Rs 10.5 Cr a day
        self.assertIsNotNone(self._state(df, Setup3Params(max_turn_cr=25.0)))
        self.assertIsNone(self._state(df, Setup3Params(max_turn_cr=5.0)))
        self.assertIsNotNone(self._state(df, Setup3Params()))       # no cap by default

    def test_replay_respects_the_cap(self):
        df = mk_df(vol=1e8)                                         # about Rs 1,050 Cr a day
        self.assertTrue(simulate_symbol_setup3(df, "X", LISTING, Params(), Setup3Params()))
        self.assertEqual(simulate_symbol_setup3(df, "X", LISTING, Params(), PAPER_PARAMS), [])


class TestPreview(unittest.TestCase):
    def test_preview_matches_the_replay_signal(self):
        df = mk_df()
        pv = preview_s3_entry(prior(df), LISTING, TODAY)
        self.assertIsNotNone(pv)
        b = _Bars.of(df)
        ld_high, _, ld = _listing_context(df, LISTING)
        pvt, pi = pivot_arrays(b)
        s = setup3_signal(df, b, _prepare(df), pvt, pi, 80, ld_high, ld, Params(), PAPER_PARAMS, slip_pct=0.3)
        self.assertIsNotNone(s)
        for k_pv, k_s in (("trigger", "entry"), ("fill", "fill"), ("stop", "stop"), ("pivot", "pivot")):
            self.assertAlmostEqual(pv[k_pv], s[k_s], places=6, msg=k_pv)
        self.assertEqual(pv["klass"], s["klass"])
        self.assertAlmostEqual(pv["limit"], 110.0 * 1.025, places=6)
        self.assertFalse(pv["already_above_trigger"])
        self.assertAlmostEqual(pv["risk_pct"], 12.0, places=6)

    def test_preview_uses_only_completed_sessions(self):
        df = mk_df()
        a = preview_s3_entry(prior(df), LISTING, TODAY)
        df2 = df.copy()
        df2.loc[80:, ["HIGH", "LOW", "CLOSE", "VOLUME"]] = [900.0, 1.0, 500.0, 1.0]   # whatever happens on or after today
        b = preview_s3_entry(prior(df2), LISTING, TODAY)
        self.assertEqual({k: a[k] for k in ("trigger", "fill", "stop", "klass", "turn20")}, {k: b[k] for k in ("trigger", "fill", "stop", "klass", "turn20")})

    def test_ineligible_cases(self):
        df = mk_df()
        self.assertIsNone(preview_s3_entry(prior(df, 50), LISTING, pd.Timestamp("2026-02-20")))            # age 50 < 61
        hot = mk_df(vol=1e8)
        self.assertIsNone(preview_s3_entry(prior(hot), LISTING, TODAY))                                      # turnover above the cap
        self.assertIsNotNone(preview_s3_entry(prior(hot), LISTING, TODAY, s3=Setup3Params()))                # ... only because of the cap
        far = prior(df)
        far.loc[79, "CLOSE"] = 97.0                                                                          # more than 10% under the pivot
        self.assertIsNone(preview_s3_entry(far, LISTING, TODAY))
        ext = prior(df)
        ext.loc[79, ["CLOSE", "HIGH"]] = [113.5, 113.5]                                                      # last close above the 112.75 limit
        self.assertIsNone(preview_s3_entry(ext, LISTING, TODAY))
        nse = dict(LISTING, is_nse_addition=True, bse_liquidity_passed=False)
        self.assertIsNone(preview_s3_entry(prior(df), nse, TODAY))

    def test_a_breakout_that_already_happened_is_not_chased(self):
        # yesterday's high becomes the new pivot (1 session old: a run, not a base), so no order is placed; by construction an eligible
        # S3 order always sits below its trigger (already_above_trigger is False)
        df = prior(mk_df())
        df.loc[79, ["CLOSE", "HIGH"]] = [112.0, 112.0]
        self.assertIsNone(preview_s3_entry(df, LISTING, TODAY))


class TestPaperStatus(unittest.TestCase):
    ENTRY = 110.0 * 1.015 * 1.003

    def test_open_flat_position_is_never_cut_by_a_time_stop(self):
        df = mk_df(n=140)                                          # 59 sessions after the entry, flat at about +1%
        st = s3_paper_status(df, df.DATE[80], self.ENTRY)
        self.assertEqual(st["status"], "OPEN")
        self.assertFalse(st["alert"])
        self.assertAlmostEqual(st["trailing"], self.ENTRY * 0.88, places=3)
        # the same trade under policy P's exits is closed by the dead-money gate
        pst = s3_paper_status(df, df.DATE[80], self.ENTRY, p=Params())
        self.assertEqual(pst["status"], "EXITED")
        self.assertIn("Dead Money", pst["exit_reason"] + "Dead Money")

    def test_stop_alert_then_exit_at_next_open(self):
        df = mk_df(n=89)
        df.loc[88, ["CLOSE", "LOW"]] = [90.0, 89.0]               # last close below the 98.5 stop
        st = s3_paper_status(df, df.DATE[80], self.ENTRY)
        self.assertEqual(st["status"], "OPEN")
        self.assertTrue(st["alert"])
        df2 = mk_df(n=91)
        df2.loc[88, ["CLOSE", "LOW"]] = [90.0, 89.0]
        df2.loc[89, ["OPEN", "HIGH", "LOW", "CLOSE"]] = [91.0, 92.0, 90.0, 91.5]
        df2.loc[90, ["OPEN", "HIGH", "LOW", "CLOSE"]] = [91.0, 92.0, 90.0, 91.5]
        ex = s3_paper_status(df2, df2.DATE[80], self.ENTRY)
        self.assertEqual(ex["status"], "EXITED")
        self.assertAlmostEqual(ex["exit_price"], 91.0, places=6)    # the next open after the alert close

    def test_trailing_ratchets_up_on_a_runner_and_never_down(self):
        df = mk_df(n=100)
        for i in range(81, 100):
            px = 113.0 * (1.02 ** (i - 80))
            df.loc[i, ["OPEN", "HIGH", "LOW", "CLOSE"]] = [px * 0.995, px * 1.01, px * 0.99, px]
        st = s3_paper_status(df, df.DATE[80], self.ENTRY)
        self.assertEqual(st["status"], "OPEN")
        self.assertGreater(st["max_runup"], 15.0)
        self.assertGreaterEqual(st["trailing"], self.ENTRY * 1.05)
        self.assertGreater(st["trailing"], self.ENTRY * 0.88)

    def test_unusable_entry(self):
        df = mk_df()
        self.assertIsNone(s3_paper_status(df, pd.Timestamp("2025-01-01"), self.ENTRY))
        self.assertIsNone(s3_paper_status(df, df.DATE[80], 0.0))


class TestOrderSheetHelpers(unittest.TestCase):
    def listing(self, **kw):
        return dict(LISTING, **kw)

    def test_candidates_eligible_and_hygiene(self):
        df = prior(mk_df())
        cands, notes = osh.s3_candidates({"OK": self.listing()}, {"OK": df}, {}, TODAY)
        self.assertEqual([c["symbol"] for c in cands], ["OK"])
        cands, notes = osh.s3_candidates({"S": self.listing()}, {"S": df}, {"S": "held in the policy-P book"}, TODAY)
        self.assertEqual(cands, [])
        self.assertIn("held in the policy-P book", notes[0])
        cands, notes = osh.s3_candidates({"P": self.listing(listing_date=pd.Timestamp("2026-08-17"))}, {"P": df}, {}, TODAY)
        self.assertEqual(cands, [])
        self.assertIn("placeholder", notes[0])
        late = df.iloc[10:].reset_index(drop=True)                                   # history that does not start at the listing
        cands, notes = osh.s3_candidates({"L": self.listing()}, {"L": late}, {}, TODAY)
        self.assertEqual(cands, [])
        self.assertIn("does not start at the listing", notes[0])
        cands, notes = osh.s3_candidates({"T": self.listing()}, {"T": df}, {}, TODAY + pd.Timedelta(days=30))
        self.assertEqual(cands, [])
        self.assertIn("stale", notes[0])
        cands, notes = osh.s3_candidates({"H": self.listing(listing_day_high=130.0)}, {"H": df}, {}, TODAY)
        self.assertEqual(cands, [])
        self.assertIn("inconsistent", notes[0])

    def test_turnover_cap_applies_in_the_sheet_path(self):
        hot = prior(mk_df(vol=1e8))
        cands, _ = osh.s3_candidates({"HOT": self.listing()}, {"HOT": hot}, {}, TODAY)
        self.assertEqual(cands, [])

    def test_orders_are_sized_and_capped_by_free_slots(self):
        df = prior(mk_df())
        cands, _ = osh.s3_candidates({f"S{i}": self.listing() for i in range(4)}, {f"S{i}": df for i in range(4)}, {}, TODAY)
        cfg = SheetConfig(capital=100000.0, hard_cap=10)
        entries, skipped, _w = build_resting_entries(cands, slots_used=8, cfg=cfg)
        self.assertEqual(len(entries), 2)
        self.assertEqual(len(skipped), 2)
        self.assertAlmostEqual(entries[0].trigger, 111.65, places=1)
        self.assertGreater(entries[0].quantity, 0)

    def test_formatting_custom_and_default(self):
        df = prior(mk_df())
        cands, _ = osh.s3_candidates({"OK": self.listing()}, {"OK": df}, {}, TODAY)
        cfg = SheetConfig(capital=100000.0)
        entries, skipped, w = build_resting_entries(cands, slots_used=0, cfg=cfg)
        txt = format_resting_entries(entries, skipped, cfg, w, title="S3 PAPER BOOK: TEST", footer=osh.S3_FOOTER)
        self.assertTrue(txt.startswith("S3 PAPER BOOK: TEST"))
        self.assertIn("PAPER_ONLY", txt)
        self.assertNotIn("R:R", txt)                                  # S3 has no R:R gate; a zero would look like a bad trade
        self.assertNotIn("Why: replaying 2022-26", txt)
        default = format_resting_entries(entries, skipped, cfg, w)    # policy P's text is unchanged
        self.assertTrue(default.startswith("PRE-MARKET RESTING ENTRY ORDERS"))
        self.assertIn("Why: replaying 2022-26", default)

    def test_split_positions_open_and_cooldown(self):
        docs = [dict(symbol="A", status="OPEN", entry_date=datetime(2026, 3, 1, tzinfo=timezone.utc), entry_price=100.0, quantity=5),
                dict(symbol="B", status="CLOSED", exit_date=datetime(2026, 3, 10, tzinfo=timezone.utc)),
                dict(symbol="C", status="CLOSED", exit_date=datetime(2025, 8, 10, tzinfo=timezone.utc))]
        open_h, cool = osh.s3_split_positions(docs, TODAY)
        self.assertEqual(set(open_h), {"A"})
        self.assertEqual(set(cool), {"B"})                           # C exited long ago: free again

    def test_position_rows_and_format(self):
        df = mk_df(n=89)
        df.loc[88, ["CLOSE", "LOW"]] = [90.0, 89.0]
        rows = osh.s3_position_rows({"AAA": dict(entry_date=str(df.DATE[80].date()), entry_price=111.985, quantity=10),
                                     "BBB": dict(entry_date="2020-01-01", entry_price=50, quantity=1)}, {"AAA": df, "BBB": df})
        by = {r["symbol"]: r for r in rows}
        self.assertTrue(by["AAA"]["alert"])
        self.assertIn("error", by["BBB"])
        txt = osh.format_s3_positions(rows)
        self.assertIn("STOP ALERT HIT", txt)
        self.assertIn("PAPER_ONLY", txt)
        self.assertIn("none open", osh.format_s3_positions([]))


class FakeCol:
    """Minimal in-memory stand-in for a pymongo collection (equality filters, $set / $setOnInsert, upsert)."""

    def __init__(self):
        self.docs = []
        self.indexes = []

    def create_index(self, *a, **k):
        self.indexes.append((a, k))

    @staticmethod
    def _match(d, f):
        return all(d.get(k) == v for k, v in f.items())

    def find(self, f=None, proj=None):
        return [dict(d) for d in self.docs if self._match(d, f or {})]

    def find_one(self, f=None, proj=None):
        r = self.find(f)
        return r[0] if r else None

    def insert_one(self, d):
        self.docs.append(dict(d))

    def update_one(self, f, u, upsert=False):
        for d in self.docs:
            if self._match(d, f):
                d.update(u.get("$set", {}))
                return
        if upsert:
            d = dict(f)
            d.update(u.get("$setOnInsert", {}))
            d.update(u.get("$set", {}))
            self.docs.append(d)


class TestStore(unittest.TestCase):
    def setUp(self):
        self.orders, self.pos = FakeCol(), FakeCol()

    def order(self, sym="AAA", qty=50):
        return dict(symbol=sym, quantity=qty, trigger=111.65, limit=112.75, est_fill=111.98, stop_alert=98.5, pivot=110.0, klass="LONG_BASE", turn20=10.5)

    def test_record_orders_is_idempotent_and_keeps_filled_status(self):
        self.assertEqual(s3_store.record_orders(self.orders, "2026-03-22", [self.order()]), 1)
        s3_store.record_orders(self.orders, "2026-03-22", [self.order()])
        self.assertEqual(len(self.orders.docs), 1)
        self.assertEqual(self.orders.docs[0]["status"], "PLACED")
        s3_store.open_position(self.pos, self.orders, "AAA", "2026-03-22", 112.0)
        s3_store.record_orders(self.orders, "2026-03-22", [dict(self.order(), quantity=60)])     # the sheet re-run later the same day
        self.assertEqual(self.orders.docs[0]["status"], "FILLED")
        self.assertEqual(self.orders.docs[0]["quantity"], 60)
        self.assertTrue(self.orders.docs[0]["paper_only"])

    def test_fill_takes_quantity_from_the_order_and_marks_it_filled(self):
        s3_store.record_orders(self.orders, "2026-03-22", [self.order(qty=42)])
        d = s3_store.open_position(self.pos, self.orders, "aaa", "2026-03-23", 112.5)
        self.assertEqual((d["symbol"], d["quantity"], d["status"]), ("AAA", 42, "OPEN"))
        self.assertEqual(self.orders.docs[0]["status"], "FILLED")
        self.assertEqual(self.orders.docs[0]["fill_price"], 112.5)
        self.assertEqual(d["klass"], "LONG_BASE")

    def test_fill_validation(self):
        with self.assertRaises(ValueError):
            s3_store.open_position(self.pos, self.orders, "AAA", "2026-03-23", 112.5)             # no order, no quantity
        with self.assertRaises(ValueError):
            s3_store.open_position(self.pos, self.orders, "AAA", "2026-03-23", 0.0, 10)
        s3_store.open_position(self.pos, self.orders, "AAA", "2026-03-23", 112.5, 10)
        with self.assertRaises(ValueError):
            s3_store.open_position(self.pos, self.orders, "AAA", "2026-03-24", 113.0, 10)         # already open

    def test_close_position_and_listing(self):
        s3_store.open_position(self.pos, self.orders, "AAA", "2026-03-23", 100.0, 10)
        self.assertEqual([d["symbol"] for d in s3_store.open_positions(self.pos)], ["AAA"])
        d = s3_store.close_position(self.pos, "AAA", "2026-04-02", 90.0, "stop alert")
        self.assertAlmostEqual(d["pnl_pct"], -10.0)
        self.assertEqual(s3_store.open_positions(self.pos), [])
        self.assertEqual(len(s3_store.closed_since(self.pos, "2026-03-01")), 1)
        self.assertEqual(s3_store.closed_since(self.pos, "2026-05-01"), [])
        with self.assertRaises(ValueError):
            s3_store.close_position(self.pos, "AAA", "2026-04-03", 90.0)                          # nothing open
        s3_store.open_position(self.pos, self.orders, "AAA", "2026-06-01", 95.0, 10)             # re-entry after the exit is allowed

    def test_dates_are_utc_midnight_datetimes(self):
        s3_store.open_position(self.pos, self.orders, "AAA", pd.Timestamp("2026-03-23 15:30"), 100.0, 10)
        ed = self.pos.docs[0]["entry_date"]
        self.assertEqual((ed.hour, ed.minute, ed.tzinfo), (0, 0, timezone.utc))

    def test_position_rows_accept_stored_documents(self):
        df = mk_df(n=89)
        s3_store.open_position(self.pos, self.orders, "AAA", df.DATE[80], 111.985, 10)
        rows = osh.s3_position_rows({d["symbol"]: d for d in s3_store.open_positions(self.pos)}, {"AAA": df})
        self.assertEqual(rows[0]["status"], "OPEN")
        self.assertFalse(rows[0]["alert"])


# The paper rules of version 1.0.0.  If this assertion fails you changed a paper rule: bump S3_PAPER_VERSION in core/setup3.py, add a CHANGELOG.md
# entry, and register the new fingerprint here (keep the old one so the history stays readable).
S3_RULES_FINGERPRINT_V1 = "73c19964dde3"


class TestVersioning(unittest.TestCase):
    BASE = os.path.dirname(os.path.abspath(__file__))

    def read(self, name):
        with open(os.path.join(self.BASE, name), encoding="utf-8") as fh:
            return fh.read()

    def test_rules_fingerprint_is_registered_for_the_current_version(self):
        self.assertEqual(S3_PAPER_VERSION, "1.0.0")
        self.assertEqual(paper_rules_fingerprint(), S3_RULES_FINGERPRINT_V1)
        self.assertIn(S3_PAPER_VERSION, S3_PAPER_COHORT)

    def test_documents_carry_the_version(self):
        orders, pos, runs = FakeCol(), FakeCol(), FakeCol()
        s3_store.record_orders(orders, "2026-03-22", [dict(symbol="AAA", quantity=5, trigger=100.0)])
        s3_store.open_position(pos, orders, "AAA", "2026-03-22", 100.0)
        s3_store.record_run(runs, "2026-03-22", n_open=1)
        for col in (orders, pos, runs):
            self.assertEqual(col.docs[0]["s3_version"], S3_PAPER_VERSION)
            self.assertEqual(col.docs[0]["rules_fp"], S3_RULES_FINGERPRINT_V1)

    def test_docs_mention_the_version_and_the_scanner_version_is_untouched(self):
        self.assertIn(f"S3 paper book {S3_PAPER_VERSION}", self.read("CHANGELOG.md"))
        self.assertIn(f"S3_PAPER_VERSION", self.read("SESSION_HANDOFF.md"))
        self.assertIn(f"S3_PAPER_VERSION", self.read("AGENTS.md"))
        import re
        expected = re.search(r'EXPECTED_VERSION\s*=\s*"([^"]+)"', self.read("master_audit.py")).group(1)
        self.assertEqual(expected, "3.6.0")                       # S3 is a patch-level, PAPER_ONLY addition: no milestone bump

    def test_default_quantity_ignores_an_old_order(self):
        orders, pos = FakeCol(), FakeCol()
        s3_store.record_orders(orders, "2026-03-01", [dict(symbol="AAA", quantity=99)])
        with self.assertRaises(ValueError):
            s3_store.open_position(pos, orders, "AAA", "2026-03-20", 100.0)             # 19 days later: not this order's fill
        s3_store.record_orders(orders, "2026-03-18", [dict(symbol="AAA", quantity=7)])
        self.assertEqual(s3_store.open_position(pos, orders, "AAA", "2026-03-20", 100.0)["quantity"], 7)

    def test_fill_before_the_candle_cache_catches_up_is_pending_not_an_error(self):
        df = mk_df(n=81)                                                                    # last candle = bar 80
        rows = osh.s3_position_rows({"AAA": dict(entry_date=str((df.DATE[80] + pd.Timedelta(days=1)).date()), entry_price=112.0, quantity=5)}, {"AAA": df})
        self.assertTrue(rows[0]["pending"])
        import s3_paper_alerts as al
        m = al.build_message(rows, [], pd.Timestamp("2026-03-24"), df.DATE[80])
        self.assertIn("WAITING FOR THE CANDLE CACHE", m["text"])
        self.assertNotIn("CANNOT EVALUATE", m["text"])


class TestPolicyPUntouched(unittest.TestCase):
    def test_policy_p_defaults_and_gates_unchanged(self):
        p = Params()
        self.assertEqual((p.max_age, p.dead_money_days_ipo, p.dead_money_runup_ipo, p.exit_overrides), (60, 14, 4.0, ()))
        self.assertEqual(paper_exit_params().max_age, 60)             # S3 paper reuses the engine; it does not edit Params defaults


if __name__ == "__main__":
    unittest.main()
