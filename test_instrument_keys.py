# -*- coding: utf-8 -*-
"""Tests for core/instrument_keys.py (probe, repair planning) and the listing-scanner flags / would_have_triggered helper.  No network, no database."""
import os
os.environ.setdefault("DISABLE_TELEGRAM", "1")
os.environ.setdefault("TESTING", "1")

import subprocess
import sys
import unittest
from datetime import date

from core import instrument_keys as ik

ROOT = os.path.dirname(os.path.abspath(__file__))


class R:
    def __init__(self, code, text=""):
        self.status_code, self.text = code, text


INVALID = R(400, '{"errors":[{"errorCode":"UDAPI100011","message":"Invalid Instrument key"}]}')


def getter(map_):
    """http_get fake: map_ {instrument key substring: response}; unknown -> invalid."""
    def get(url, headers=None, timeout=None):
        for k, v in map_.items():
            if k in url:
                return v
        return INVALID
    return get


class TestProbe(unittest.TestCase):
    def test_ok_invalid_error(self):
        self.assertEqual(ik.probe_key("NSE_EQ|A", "t", getter({"NSE_EQ|A": R(200)}), date(2026, 10, 31)), "ok")
        self.assertEqual(ik.probe_key("NSE_EQ|B", "t", getter({}), date(2026, 10, 31)), "invalid")
        self.assertEqual(ik.probe_key("NSE_EQ|C", "t", getter({"NSE_EQ|C": R(401)}), date(2026, 10, 31)), "error:http 401")

    def test_400_without_the_invalid_code_is_not_called_invalid(self):
        self.assertEqual(ik.probe_key("NSE_EQ|D", "t", getter({"NSE_EQ|D": R(400, "bad dates")}), date(2026, 10, 31)), "error:http 400")

    def test_429_retried_once(self):
        calls = []

        def get(url, headers=None, timeout=None):
            calls.append(1)
            return R(429) if len(calls) == 1 else R(200)
        self.assertEqual(ik.probe_key("NSE_EQ|E", "t", get, date(2026, 10, 31), sleep=lambda s: None), "ok")
        self.assertEqual(len(calls), 2)

    def test_network_exception_is_an_error_not_invalid(self):
        def get(url, headers=None, timeout=None):
            raise ConnectionError("down")
        self.assertEqual(ik.probe_key("NSE_EQ|F", "t", get, date(2026, 10, 31)), "error:ConnectionError")


class TestPlan(unittest.TestCase):
    NSE = {"AZADIND": dict(isin="INE566M01025", name="x"), "SAME": dict(isin="INE000A01001", name="x"), "GOOD": dict(isin="INE111A01001", name="x"),
           "NEWBAD": dict(isin="INE222A01002", name="x")}

    def plan(self, sym, stored_key, http):
        return ik.plan_repair(sym, dict(instrument_key=stored_key, isin=stored_key.split("|")[-1]), "t", self.NSE, http, sleep=lambda s: None)

    def test_ok_key_untouched(self):
        self.assertEqual(self.plan("GOOD", "NSE_EQ|INE111A01001", getter({"INE111A01001": R(200)}))["status"], "ok")

    def test_repair_when_nse_has_new_isin_that_upstox_accepts(self):
        r = self.plan("AZADIND", "NSE_EQ|INE566M01017", getter({"INE566M01025": R(200)}))
        self.assertEqual((r["status"], r["new_key"], r["new_isin"]), ("repair", "NSE_EQ|INE566M01025", "INE566M01025"))

    def test_same_isin_is_unresolved(self):
        self.assertEqual(self.plan("SAME", "NSE_EQ|INE000A01001", getter({}))["status"], "unresolved_same_isin")

    def test_not_in_nse_is_unresolved(self):
        self.assertEqual(self.plan("GONE", "NSE_EQ|INE999A01001", getter({}))["status"], "unresolved_not_in_nse")

    def test_new_key_also_rejected_is_unresolved(self):
        self.assertEqual(self.plan("NEWBAD", "NSE_EQ|INE222A01001", getter({}))["status"], "unresolved_new_invalid")

    def test_undecidable_probe_is_an_error_never_a_repair(self):
        r = self.plan("AZADIND", "NSE_EQ|INE566M01017", getter({"INE566M01017": R(500)}))
        self.assertEqual(r["status"], "error")
        self.assertIsNone(r["new_key"])


class TestListingScannerFlags(unittest.TestCase):
    """Run in a subprocess: importing the scanner reads the environment at import time."""

    def run_py(self, code, **env):
        e = dict(os.environ, DISABLE_TELEGRAM="1", TESTING="1", **env)
        out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=e, capture_output=True, text=True, timeout=120)
        return [l for l in out.stdout.splitlines() if l.startswith("RESULT")]

    def test_defaults_keep_current_behaviour(self):
        r = self.run_py("import listing_day_breakout_scanner as m; print('RESULT', m.LISTING_INTRADAY_VOLUME_FLOOR_ENABLED, m.LISTING_INTRADAY_VOLUME_SPIKE_GATE_ENABLED)")
        self.assertEqual(r, ["RESULT True True"])

    def test_env_zero_switches_each_gate_off(self):
        r = self.run_py("import listing_day_breakout_scanner as m; print('RESULT', m.LISTING_INTRADAY_VOLUME_FLOOR_ENABLED, m.LISTING_INTRADAY_VOLUME_SPIKE_GATE_ENABLED)",
                        LISTING_INTRADAY_VOLUME_FLOOR_ENABLED="0")
        self.assertEqual(r, ["RESULT False True"])
        r = self.run_py("import listing_day_breakout_scanner as m; print('RESULT', m.LISTING_INTRADAY_VOLUME_FLOOR_ENABLED, m.LISTING_INTRADAY_VOLUME_SPIKE_GATE_ENABLED)",
                        LISTING_INTRADAY_VOLUME_FLOOR_ENABLED="0", LISTING_INTRADAY_VOLUME_SPIKE_GATE_ENABLED="0")
        self.assertEqual(r, ["RESULT False False"])

    def test_would_have_triggered_v2(self):
        code = ("import listing_day_breakout_scanner as m; f = m._would_have_triggered; "
                "print('RESULT', f(103, 100, 30), f(103, 100, 400), f(110, 100, 30), f(99, 100, 30), f(103, 100, 0), f(103, 100, None), f(0, 100, 30), f(103.5, 100, 60))")
        # in window and within the 3.5% cap: True; too old, over-extended, below the high, listing day, unknown age, no price: False; exactly the cap and day 60: True
        self.assertEqual(self.run_py(code), ["RESULT True False False False False False False True"])


if __name__ == "__main__":
    unittest.main()
