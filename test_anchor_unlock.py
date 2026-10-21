# -*- coding: utf-8 -*-
"""Tests for the event-study helpers in research_anchor_unlock.py.  Synthetic data only; nothing touches MongoDB."""
import os
os.environ.setdefault("DISABLE_TELEGRAM", "1")
os.environ.setdefault("TESTING", "1")

import unittest

import numpy as np
import pandas as pd

import research_anchor_unlock as ru


def mk_sym(n=120, growth=1.01, price=100.0, volume=1e6, name="SYN"):
    dates = pd.date_range("2025-01-01", periods=n, freq="D")
    close = price * growth ** np.arange(n)
    df = pd.DataFrame(dict(DATE=dates, OPEN=close, HIGH=close, LOW=close, CLOSE=close, VOLUME=volume))
    return ru.Sym(name, df, dict(listing_date=dates[0])), dates


def flat_market(dates):
    return pd.Series(0.0, index=dates).pipe(np.log1p).cumsum()


class TestWindow(unittest.TestCase):
    def test_abnormal_return_is_the_stock_return_minus_the_market(self):
        s, dates = mk_sym()
        w = ru.window(s, 28, flat_market(dates))
        self.assertEqual(w["e"], 28)                                           # first session on or after listing + 28 days
        self.assertAlmostEqual(w["ar"], 1.01 ** 10 - 1.0, places=9)           # close[e+5] / close[e-5] - 1 with a flat market
        up = pd.Series(0.001, index=dates)
        w2 = ru.window(s, 28, np.log1p(up).cumsum())
        self.assertAlmostEqual(w2["ar"], (1.01 ** 10 - 1.0) - (1.001 ** 10 - 1.0), places=9)

    def test_liquidity_gates_and_data_edges(self):
        s, dates = mk_sym(price=15.0)
        self.assertIsNone(ru.window(s, 28, flat_market(dates)))              # below the Rs 25 floor
        s, dates = mk_sym(volume=1e3)
        self.assertIsNone(ru.window(s, 28, flat_market(dates)))              # turnover below Rs 0.75 Cr
        s, dates = mk_sym(n=40)
        self.assertIsNone(ru.window(s, 38, flat_market(dates)))              # e + 5 beyond the data
        s, dates = mk_sym()
        self.assertIsNone(ru.window(s, 2, flat_market(dates)))               # fewer than 5 sessions before the window

    def test_event_window_uses_only_the_event_and_placebo_dates(self):
        s, dates = mk_sym()
        base = ru.window(s, 28, flat_market(dates))["ar"]
        s.c[40:] = s.c[40:] * 3.0                                            # changes far after the window (b = 33)
        self.assertAlmostEqual(ru.window(s, 28, flat_market(dates))["ar"], base, places=12)


class TestMarket(unittest.TestCase):
    def test_market_needs_20_symbols_and_winsorises(self):
        syms = [mk_sym(name=f"S{i}")[0] for i in range(19)]
        m = ru.build_market(syms)
        self.assertTrue((m == 0.0).all())                                    # fewer than 20 symbols a day: no market return
        syms = [mk_sym(name=f"S{i}")[0] for i in range(20)]
        syms[0].c[60] = syms[0].c[59] * 5.0                                   # +400% bar
        m = ru.build_market(syms)
        day = syms[0].d[60]
        self.assertAlmostEqual(m.loc[day], (19 * 0.01 + 0.20) / 20, places=9) # clipped to +20% before averaging

    def test_car_path_shape(self):
        s, dates = mk_sym()
        ret = pd.Series(0.0, index=dates)
        out = ru.car_path(s, 40, ret)
        self.assertIsNotNone(out)
        car, vol = out
        self.assertAlmostEqual(car[0], 10 * 1.0, places=6)                    # ten sessions of +1% minus a zero market, in percent
        self.assertAlmostEqual(vol[0], 1.0, places=9)
        self.assertIsNone(ru.car_path(s, 5, ret))
        self.assertIsNone(ru.car_path(s, 115, ret))


if __name__ == "__main__":
    unittest.main()
