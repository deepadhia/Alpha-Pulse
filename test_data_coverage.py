# -*- coding: utf-8 -*-
"""Tests for core.data_coverage and check_data_coverage (synthetic data, no database)."""
import os
os.environ["DISABLE_TELEGRAM"] = "1"
os.environ["TESTING"] = "1"

import pickle
import sys
import tempfile
import unittest

import pandas as pd

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core.data_coverage import (MISSING, OK, STALE, TOO_YOUNG, TRUNCATED, cache_as_of, coverage_report)
import check_data_coverage

AS_OF = pd.Timestamp("2026-10-16")          # a Friday


def candles(start, end):
    dates = pd.bdate_range(start=start, end=end)
    return pd.DataFrame({"DATE": dates, "OPEN": 10.0, "HIGH": 11.0, "LOW": 9.0, "CLOSE": 10.5, "VOLUME": 1e6})


def listing(date_str):
    return {"listing_date": pd.Timestamp(date_str, tz="UTC")}


def fixture():
    listings = {
        "GOOD": listing("2026-09-01"),        # full history to as-of
        "NOCANDLES": listing("2026-09-01"),   # absent from candle_map, old enough
        "BABY": listing("2026-10-12"),        # 5 sessions old, absent: exempt
        "STALE": listing("2026-09-01"),       # stops 8 sessions early
        "TRUNC": listing("2026-09-01"),       # history starts 4 weeks after listing
        "OLD": listing("2026-05-01"),         # outside the 60-day window: ignored
        "NODATE": {},                          # no listing date: ignored
    }
    cmap = {
        "GOOD": candles("2026-09-01", "2026-10-16"),
        "STALE": candles("2026-09-01", "2026-10-06"),
        "TRUNC": candles("2026-09-29", "2026-10-16"),
        "OLD": candles("2026-05-01", "2026-10-16"),
    }
    return listings, cmap


class TestCoverageReport(unittest.TestCase):
    def test_statuses(self):
        listings, cmap = fixture()
        rep = coverage_report(listings, cmap, as_of=AS_OF)
        st = {r["symbol"]: r["status"] for r in rep["rows"]}
        self.assertEqual(st, {"GOOD": OK, "NOCANDLES": MISSING, "BABY": TOO_YOUNG, "STALE": STALE, "TRUNC": TRUNCATED})
        self.assertEqual(rep["in_scope"], 5)
        self.assertEqual(rep["evaluable"], 4)               # TOO_YOUNG is exempt
        self.assertAlmostEqual(rep["coverage"], 0.25)

    def test_as_of_defaults_to_newest_candle(self):
        listings, cmap = fixture()
        self.assertEqual(cache_as_of(cmap), AS_OF)
        self.assertEqual(coverage_report(listings, cmap)["as_of"], AS_OF.date())

    def test_stale_tolerance(self):
        listings = {"X": listing("2026-09-01")}
        cmap = {"X": candles("2026-09-01", "2026-10-14")}   # 2 sessions behind: still OK
        self.assertEqual(coverage_report(listings, cmap, as_of=AS_OF)["rows"][0]["status"], OK)
        cmap = {"X": candles("2026-09-01", "2026-10-13")}   # 3 sessions behind: stale
        self.assertEqual(coverage_report(listings, cmap, as_of=AS_OF)["rows"][0]["status"], STALE)

    def test_placeholder_date_cluster_is_flagged_not_scored(self):
        listings = {f"S{i}": listing("2026-09-01") for i in range(12)}
        cmap = {s: candles("2026-09-01", "2026-10-16") for s in listings}
        rep = coverage_report(listings, cmap, as_of=AS_OF)
        self.assertEqual(rep["suspect_dates"], {pd.Timestamp("2026-09-01").date(): 12})
        self.assertEqual(rep["coverage"], 1.0)
        listings, cmap = fixture()
        self.assertEqual(coverage_report(listings, cmap, as_of=AS_OF)["suspect_dates"], {})

    def test_empty_scope_is_full_coverage(self):
        rep = coverage_report({"OLD": listing("2026-01-01")}, {"OLD": candles("2026-01-01", "2026-10-16")}, as_of=AS_OF)
        self.assertEqual(rep["in_scope"], 0)
        self.assertEqual(rep["coverage"], 1.0)


class TestCli(unittest.TestCase):
    def _run(self, listings, cmap, threshold):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "cache.pkl")
            with open(path, "wb") as fh:
                pickle.dump((listings, cmap), fh)
            return check_data_coverage.main(["--cache", path, "--threshold", str(threshold)])

    def test_exit_nonzero_below_threshold(self):
        listings, cmap = fixture()
        self.assertEqual(self._run(listings, cmap, 0.90), 1)

    def test_exit_zero_at_or_above_threshold(self):
        listings, cmap = fixture()
        self.assertEqual(self._run(listings, cmap, 0.25), 0)
        good = {k: v for k, v in listings.items() if k in ("GOOD", "BABY")}
        self.assertEqual(self._run(good, cmap, 0.90), 0)


if __name__ == "__main__":
    unittest.main()
