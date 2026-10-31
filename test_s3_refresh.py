# -*- coding: utf-8 -*-
"""Tests for core/s3_refresh.py (in-memory candle refresh of the S3 evening alert).  No database, no network: HTTP is a fake."""
import os
os.environ.setdefault("DISABLE_TELEGRAM", "1")
os.environ.setdefault("TESTING", "1")

import unittest
from datetime import datetime

import pandas as pd

from core import s3_refresh as rf


def frame(last="2026-10-29", n=4, close=100.0, high=None):
    d = pd.bdate_range(end=last, periods=n)
    return pd.DataFrame(dict(DATE=d, OPEN=close, HIGH=high or close * 1.02, LOW=close * 0.98, CLOSE=close, VOLUME=1000.0, LTP=close))


def raw_bar(date, o=100.0, h=102.0, l=98.0, c=101.0, v=5000):
    return [f"{date}T00:00:00+05:30", o, h, l, c, v, 0]


class FakeResp:
    def __init__(self, code=200, candles=None):
        self.status_code, self._c = code, candles or []

    def json(self):
        return {"status": "success", "data": {"candles": self._c}}


class TestLastComplete(unittest.TestCase):
    def test_after_close_today_counts(self):
        self.assertEqual(rf.last_complete_date(datetime(2026, 10, 30, 18, 50)), pd.Timestamp("2026-10-30"))

    def test_before_close_yesterday(self):
        self.assertEqual(rf.last_complete_date(datetime(2026, 10, 30, 11, 0)), pd.Timestamp("2026-10-29"))

    def test_never_beyond_run_day(self):
        self.assertEqual(rf.last_complete_date(datetime(2026, 10, 31, 22, 0), run_day="2026-10-30"), pd.Timestamp("2026-10-30"))


class TestParse(unittest.TestCase):
    def test_parses_sorts_and_drops_bad_rows(self):
        df = rf.parse_upstox_candles([raw_bar("2026-10-30"), raw_bar("2026-10-29"), ["bad"], None])
        self.assertEqual(list(df["DATE"]), [pd.Timestamp("2026-10-29"), pd.Timestamp("2026-10-30")])
        self.assertTrue(df["DATE"].dt.tz is None)

    def test_empty(self):
        self.assertTrue(rf.parse_upstox_candles([]).empty)


class TestMerge(unittest.TestCase):
    def test_appends_only_newer_completed_bars(self):
        df = frame()
        fresh = rf.parse_upstox_candles([raw_bar("2026-10-29", c=100.0), raw_bar("2026-10-30"), raw_bar("2026-10-31")])
        out, st = rf.merge_fresh(df, fresh, "2026-10-30")
        self.assertEqual(st, "ok")
        self.assertEqual(pd.Timestamp(out["DATE"].iloc[-1]), pd.Timestamp("2026-10-30"))
        self.assertEqual(len(out), len(df) + 1)
        self.assertEqual(float(out["LTP"].iloc[-1]), float(out["CLOSE"].iloc[-1]))

    def test_today_forming_bar_is_never_merged(self):
        out, st = rf.merge_fresh(frame(), rf.parse_upstox_candles([raw_bar("2026-10-29", c=100.0), raw_bar("2026-10-30")]), "2026-10-29")
        self.assertEqual(st, "nothing_new")
        self.assertEqual(len(out), 4)

    def test_adjusted_history_is_not_spliced(self):
        fresh = rf.parse_upstox_candles([raw_bar("2026-10-29", c=50.0), raw_bar("2026-10-30", c=50.0)])   # split: overlap close differs by 50%
        out, st = rf.merge_fresh(frame(), fresh, "2026-10-30")
        self.assertEqual(st, "overlap_mismatch")
        self.assertEqual(len(out), 4)

    def test_invalid_bar_rejected(self):
        fresh = rf.parse_upstox_candles([raw_bar("2026-10-29", c=100.0), raw_bar("2026-10-30", h=90.0, l=98.0)])
        _, st = rf.merge_fresh(frame(), fresh, "2026-10-30")
        self.assertEqual(st, "invalid_bar")


class TestSelect(unittest.TestCase):
    def test_held_always_and_near_pivot_only(self):
        near = frame(close=100.0, high=104.0)
        far = frame(close=50.0, high=104.0)
        sel = rf.select_symbols(dict(NEAR=near, FAR=far, HELD=far), ["NEAR", "FAR", "MISSING"], ["HELD"])
        self.assertEqual(sel, {"NEAR", "HELD"})


class TestRefresh(unittest.TestCase):
    def run_refresh(self, token="tok", responses=None, mapping=None, candles=None):
        calls = []
        responses = responses if responses is not None else [FakeResp(200, [raw_bar("2026-10-29", c=100.0), raw_bar("2026-10-30")])]
        it = iter(responses)

        def get(url, headers=None, timeout=None):
            calls.append((url, headers))
            return next(it)

        candles = candles or dict(AAA=frame())
        out, rep = rf.refresh_candles(candles, ["AAA"], [], "2026-10-30", token, mapping if mapping is not None else dict(AAA="NSE_EQ|X"),
                                      http_get=get, sleep=lambda s: None)
        return out, rep, calls

    def test_happy_path_updates_and_does_not_mutate_input(self):
        src = dict(AAA=frame())
        out, rep, calls = self.run_refresh(candles=src)
        self.assertEqual(rep["updated"], 1)
        self.assertEqual(pd.Timestamp(out["AAA"]["DATE"].iloc[-1]), pd.Timestamp("2026-10-30"))
        self.assertEqual(pd.Timestamp(src["AAA"]["DATE"].iloc[-1]), pd.Timestamp("2026-10-29"))
        self.assertIn("/day/2026-10-30/2026-10-29", calls[0][0])
        self.assertEqual(rf.describe(rep), [])

    def test_no_token_leaves_data_and_says_so(self):
        out, rep, calls = self.run_refresh(token=None)
        self.assertEqual(calls, [])
        self.assertEqual(pd.Timestamp(out["AAA"]["DATE"].iloc[-1]), pd.Timestamp("2026-10-29"))
        self.assertIn("NOT refreshed", rf.describe(rep)[0])

    def test_http_error_is_reported_not_raised(self):
        out, rep, _ = self.run_refresh(responses=[FakeResp(401)])
        self.assertEqual(rep["failed"], {"AAA": "http 401"})
        self.assertEqual(len(out["AAA"]), 4)
        self.assertTrue(rf.describe(rep))

    def test_429_retried_once(self):
        _, rep, calls = self.run_refresh(responses=[FakeResp(429), FakeResp(200, [raw_bar("2026-10-29", c=100.0), raw_bar("2026-10-30")])])
        self.assertEqual(len(calls), 2)
        self.assertEqual(rep["updated"], 1)

    def test_missing_instrument_key(self):
        _, rep, calls = self.run_refresh(mapping={})
        self.assertEqual(rep["failed"], {"AAA": "no instrument key"})
        self.assertEqual(calls, [])

    def test_already_current_symbol_is_not_fetched(self):
        _, rep, calls = self.run_refresh(candles=dict(AAA=frame(last="2026-10-30")))
        self.assertEqual(calls, [])
        self.assertEqual(rep["fetched"], 0)


class TestCollectFreshness(unittest.TestCase):
    """order_sheet.s3_collect judges data_asof on held + near-pivot names after a refresh (database replaced by fakes)."""

    def test_median_over_relevant_names_and_held_cap(self):
        import sys
        import types
        import order_sheet as osh

        today = pd.Timestamp("2026-11-02")
        ld = pd.Timestamp("2026-04-01")

        class Col:
            def __init__(self, docs):
                self.docs = docs

            def find(self, q=None, proj=None):
                if q and "symbol" in q and "$in" in q["symbol"]:
                    return [d for d in self.docs if d["symbol"] in q["symbol"]["$in"]]
                return list(self.docs)

        def cdoc(sym, last):
            d = frame(last=last, n=60, close=100.0)
            d["DATE"] = pd.bdate_range(end=last, periods=60)
            return dict(symbol=sym, candles=[dict(DATE=r.DATE.strftime("%Y-%m-%d"), OPEN=r.OPEN, HIGH=r.HIGH, LOW=r.LOW, CLOSE=r.CLOSE, VOLUME=r.VOLUME)
                                             for r in d.itertuples()])

        listings = [dict(symbol=s, listing_date=ld, listing_day_high=102.0) for s in ("AAA", "BBB", "CCC")]
        candle_docs = [cdoc("AAA", "2026-10-29"), cdoc("BBB", "2026-10-29"), cdoc("CCC", "2026-10-29")]
        fake_db = types.SimpleNamespace(daily_candles_col=Col(candle_docs), listing_data_col=Col(listings), positions_col=Col([]),
                                        s3_paper_orders_col=Col([]), s3_paper_positions_col=Col([]))
        saved = sys.modules.get("db")
        sys.modules["db"] = fake_db
        try:
            def refresh(candles, lsyms, held):
                out = dict(candles)
                for s in ("AAA", "BBB"):                                   # two of three refreshed; CCC is not near its pivot so not "relevant"
                    d = out[s].copy()
                    add = d.iloc[[-1]].copy()
                    add["DATE"] = pd.Timestamp("2026-10-30")
                    out[s] = pd.concat([d, add], ignore_index=True)
                return out, dict(selected={"AAA", "BBB"}, fetched=2, updated=2, failed={}, note=None)

            res = osh.s3_collect(today, osh.SheetConfig(capital=100000.0), refresh=refresh)
            self.assertEqual(res["data_asof"], pd.Timestamp("2026-10-30"))
            res_plain = osh.s3_collect(today, osh.SheetConfig(capital=100000.0))
            self.assertEqual(res_plain["data_asof"], pd.Timestamp("2026-10-29"))
        finally:
            if saved is not None:
                sys.modules["db"] = saved
            else:
                del sys.modules["db"]


if __name__ == "__main__":
    unittest.main()
