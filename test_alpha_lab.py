# -*- coding: utf-8 -*-
"""
Tests for the 2026-10-14 research/execution additions:
  * core.honest_backtest: intraday-crossing entry model, causal mode, open-trade emission, disaster stop,
    pre-market preview (must agree with the backtest gates)
  * core.portfolio_sim: risk-sized and drawdown-throttled sizing (defaults unchanged)
  * core.order_sheet: close-alert stop mode, resting entry orders

Synthetic data only. The shelf tests import the scanner module for its shelf detector (as test_commit_policy.py does);
nothing is written to MongoDB.
"""

import os
os.environ.setdefault("DISABLE_TELEGRAM", "1")     # the shelf tests import the scanner module for its shelf detector
os.environ.setdefault("TESTING", "1")

import unittest
from dataclasses import replace

import numpy as np
import pandas as pd

from core.honest_backtest import (LIVE_FAITHFUL, Params, _Bars, cross_signal_at, preview_resting_entry,
                                   simulate_symbol_cross)
from core.order_sheet import (SheetConfig, build_resting_entries, build_sheet, format_resting_entries,
                               format_sheet)
from core.portfolio_sim import PortfolioConfig, run_portfolio
from core.risk_guard import GuardStatus

LD_HIGH, LD_LOW = 100.0, 90.0
EOD = replace(LIVE_FAITHFUL, exit_fill="eod_next_open")


def mk_df(n=40, breakout=20, rising_after=True):
    """Listing day high 100; flat 90-96 base; breakout bar at `breakout` (open 99, high 103); then +1%/day."""
    dates = pd.date_range("2026-01-01", periods=n, freq="D")
    rows = []
    prev_close = 98.0
    for i, d in enumerate(dates):
        if i == 0:
            o, h, l, c, v = 95.0, LD_HIGH, LD_LOW, 98.0, 3e6
        elif i < breakout:
            o, h, l, c, v = 93.0, 96.0, 90.0, 94.0, 2e6
        elif i == breakout:
            o, h, l, c, v = 99.0, 103.0, 98.0, 102.5, 6e6
        else:
            o = prev_close
            c = prev_close * (1.01 if rising_after else 1.0)
            h, l, v = c * 1.005, o * 0.995, 3e6
        rows.append(dict(DATE=d, OPEN=o, HIGH=h, LOW=l, CLOSE=c, VOLUME=v))
        prev_close = c
    return pd.DataFrame(rows)


LISTING = dict(listing_day_high=LD_HIGH, listing_day_low=LD_LOW, listing_date=pd.Timestamp("2026-01-01"))


class TestCrossEntry(unittest.TestCase):
    def setUp(self):
        self.p = Params()
        self.df = mk_df()
        self.b = _Bars.of(self.df)

    def sig(self, i=20, **kw):
        return cross_signal_at(self.df, self.b, i, LD_HIGH, 10.0, i, self.p, **kw)

    def test_fills_at_trigger_when_open_is_below_it(self):
        s = self.sig()
        self.assertIsNotNone(s)
        self.assertAlmostEqual(s["entry"], 101.5, places=6)          # listing high + 1.5%
        self.assertAlmostEqual(s["fill"], 101.5, places=6)

    def test_slip_is_applied_to_the_fill_only(self):
        s = self.sig(slip_pct=1.7)
        self.assertAlmostEqual(s["entry"], 101.5, places=6)
        self.assertAlmostEqual(s["fill"], 101.5 * 1.017, places=6)

    def test_gap_up_fills_at_the_open_inside_the_cap_and_not_beyond(self):
        self.df.loc[20, ["OPEN", "HIGH"]] = [102.8, 104.0]
        self.b = _Bars.of(self.df)
        self.assertAlmostEqual(self.sig()["entry"], 102.8, places=6)         # default cap = +3.5%
        self.assertIsNone(self.sig(fill_cap_pct=2.5))                         # resting limit at +2.5%: no fill
        self.df.loc[20, "OPEN"] = 104.0
        self.b = _Bars.of(self.df)
        self.assertIsNone(self.sig())                                         # open above +3.5%: skipped

    def test_no_trigger_no_signal(self):
        self.df.loc[20, "HIGH"] = 101.0                                       # never reaches +1.5%
        self.b = _Bars.of(self.df)
        self.assertIsNone(self.sig())

    def test_close_is_never_used(self):
        base = self.sig()
        self.df.loc[20, "CLOSE"] = 98.5                                       # faded to the low of the day
        self.b = _Bars.of(self.df)
        faded = self.sig()
        self.assertIsNotNone(faded)
        self.assertEqual(base["fill"], faded["fill"])

    def test_causal_mode_ignores_same_day_volume(self):
        a = self.sig(causal=True, vol_gate=False)
        self.df.loc[20, "VOLUME"] = 10.0                                      # today's volume is unknowable pre-trigger
        self.b = _Bars.of(self.df)
        b = self.sig(causal=True, vol_gate=False)
        self.assertIsNotNone(a)
        self.assertEqual(a["fill"], b["fill"])
        self.assertEqual(a["vr"], b["vr"])
        self.assertIsNone(self.sig(causal=False))                              # the end-of-day model would reject it

    def test_causal_mode_uses_the_prior_session_volume(self):
        self.df.loc[19, "VOLUME"] = 10.0
        self.b = _Bars.of(self.df)
        self.assertIsNone(self.sig(causal=True, vol_gate=False))              # prior bar below the 150k floor


class TestSimulateCross(unittest.TestCase):
    def test_open_trade_is_kept_and_flagged(self):
        df = mk_df()
        out = simulate_symbol_cross(df, "X", LISTING, Params(), min_age=5, include_open=True)
        self.assertEqual(len(out), 1)
        t = out[0]
        self.assertTrue(t["open"])
        self.assertEqual(t["exit_reason"], "OPEN")
        self.assertGreater(t["pnl_pct"], 0.0)                                  # marked to the last close
        self.assertEqual(t["entry_idx"], 20)
        self.assertEqual(simulate_symbol_cross(df, "X", LISTING, Params(), min_age=5, include_open=False), [])

    def test_min_age_blocks_early_breakouts(self):
        df = mk_df(breakout=3)
        self.assertEqual(simulate_symbol_cross(df, "X", LISTING, Params(), min_age=5), [])

    def test_disaster_stop_fires_intraday_only_when_enabled(self):
        df = mk_df(n=24, rising_after=False)
        df.loc[21] = dict(DATE=df.DATE[21], OPEN=101.0, HIGH=101.0, LOW=60.0, CLOSE=95.0, VOLUME=3e6)
        base = simulate_symbol_cross(df, "X", LISTING, Params(), cfg=EOD, min_age=5, include_open=True)[0]
        self.assertNotEqual(base["exit_reason"], "Disaster Stop (resting)")    # close stayed above the stop
        cat = simulate_symbol_cross(df, "X", LISTING, replace(Params(), cat_stop_pct=8.0), cfg=EOD,
                                    min_age=5, include_open=True)[0]
        self.assertEqual(cat["exit_reason"], "Disaster Stop (resting)")
        self.assertLess(cat["exit_price"], base["entry_price"] * 0.85)

    def test_close_alert_exits_next_open_not_at_the_stop(self):
        df = mk_df(n=24, rising_after=False)
        df.loc[21] = dict(DATE=df.DATE[21], OPEN=100.0, HIGH=100.0, LOW=80.0, CLOSE=85.0, VOLUME=3e6)   # closes below the stop
        df.loc[22] = dict(DATE=df.DATE[22], OPEN=83.0, HIGH=84.0, LOW=82.0, CLOSE=83.5, VOLUME=3e6)
        t = simulate_symbol_cross(df, "X", LISTING, Params(), cfg=EOD, min_age=5, include_open=True)[0]
        self.assertEqual(t["exit_price"], 83.0)                                 # next open, not the stop level
        r = simulate_symbol_cross(df, "X", LISTING, Params(), cfg=LIVE_FAITHFUL, min_age=5, include_open=True)[0]
        self.assertGreater(r["exit_price"], 83.0)                               # an intraday stop sold higher here


class TestPreviewParity(unittest.TestCase):
    """The pre-market sheet and the research engine must apply the same gates (single source of truth)."""

    def test_preview_matches_backtest_entry(self):
        df = mk_df()
        today = df.DATE[20]
        pv = preview_resting_entry(df.iloc[:20], LISTING, today, Params())
        self.assertIsNotNone(pv)
        sim = simulate_symbol_cross(df, "X", LISTING, Params(), causal=True, vol_gate=False, slip_pct=0.3,
                                    fill_cap_pct=2.5, min_age=5)[0]
        self.assertAlmostEqual(pv["fill"], sim["entry_price"], places=6)
        self.assertAlmostEqual(pv["stop"], sim["stop_initial"], places=6)
        self.assertAlmostEqual(pv["trigger"], 101.5, places=6)
        self.assertAlmostEqual(pv["limit"], 102.5, places=6)

    def test_preview_rejections(self):
        df = mk_df()
        self.assertIsNone(preview_resting_entry(df.iloc[:3], LISTING, df.DATE[3], Params()))        # too little history
        self.assertIsNone(preview_resting_entry(df.iloc[:20], LISTING, df.DATE[3] if False else pd.Timestamp("2026-01-04"),
                                                Params()))                                           # age 3 < 5
        thin = df.copy()
        thin["VOLUME"] = 1000.0
        self.assertIsNone(preview_resting_entry(thin.iloc[:20], LISTING, df.DATE[20], Params()))     # below the volume floor
        ext = df.copy()
        ext.loc[19, "CLOSE"] = 110.0                                                                 # already above the cap
        self.assertIsNone(preview_resting_entry(ext.iloc[:20], LISTING, df.DATE[20], Params()))
        old = preview_resting_entry(df.iloc[:20], LISTING, pd.Timestamp("2026-04-01"), Params())
        self.assertIsNone(old)                                                                       # older than 60 days


def fake_trade(sym, entry, exit_, risk_pct, exit_px):
    dates = list(pd.date_range(entry, exit_))
    path = [(d, 100.0 if d != dates[-1] else exit_px, 90.0) for d in dates]
    return dict(symbol=sym, type="LISTING_BREAKOUT", entry_date=entry, exit_date=exit_, entry_price=100.0,
                exit_price=exit_px, year=2026, exit_reason="x", pnl_pct=exit_px - 100.0, path=path,
                initial_risk_pct=risk_pct, vol_ratio=1.0)


class TestPortfolioSizing(unittest.TestCase):
    def test_defaults_are_unchanged(self):
        trs = [fake_trade("A", "2026-01-05", "2026-01-12", 10.0, 110.0), fake_trade("B", "2026-01-05", "2026-01-12", 5.0, 90.0)]
        base = run_portfolio(trs, PortfolioConfig(capital=1e6))["summary"]
        explicit = run_portfolio(trs, PortfolioConfig(capital=1e6, risk_budget_pct=None, dd_half_pct=None,
                                                      dd_halt_pct=None))["summary"]
        self.assertEqual(base["final_nav"], explicit["final_nav"])

    def test_risk_sizing_gives_wide_stops_smaller_positions(self):
        trs = [fake_trade("WIDE", "2026-01-05", "2026-01-12", 12.0, 100.0), fake_trade("TIGHT", "2026-01-06", "2026-01-12", 6.0, 100.0)]
        log = run_portfolio(trs, PortfolioConfig(capital=1e6, risk_budget_pct=1.0, max_weight=0.5))["log"].set_index("symbol")
        self.assertLess(log.loc["WIDE", "buy_value"], log.loc["TIGHT", "buy_value"])
        self.assertAlmostEqual(log.loc["TIGHT", "buy_value"] / log.loc["WIDE", "buy_value"], 2.0, delta=0.05)

    def test_drawdown_halving_and_halt(self):
        # a first big loser pushes NAV down ~20% (100% weight), the second entry is then throttled
        lose = fake_trade("L", "2026-01-05", "2026-01-08", 10.0, 80.0)
        nxt = fake_trade("N", "2026-01-12", "2026-01-20", 10.0, 100.0)
        full = run_portfolio([lose, nxt], PortfolioConfig(capital=1e6, hard_cap=1, soft_cap=1, weight=1.0, cap_policy="flat"))
        half = run_portfolio([lose, nxt], PortfolioConfig(capital=1e6, hard_cap=1, soft_cap=1, weight=1.0, cap_policy="flat",
                                                          dd_half_pct=10.0))
        halt = run_portfolio([lose, nxt], PortfolioConfig(capital=1e6, hard_cap=1, soft_cap=1, weight=1.0, cap_policy="flat",
                                                          dd_halt_pct=10.0))
        v = lambda r: r["log"].set_index("symbol").loc["N", "buy_value"]
        self.assertAlmostEqual(v(half) / v(full), 0.5, delta=0.05)
        self.assertEqual(halt["summary"]["skipped"].get("dd_halt"), 1)
        self.assertNotIn("N", set(halt["log"].symbol))


def mk_shelf_df(n=20, breakout=8):
    """Listing high 100; gap up to a 105-112 shelf (bars 2-7); breakout bar through the shelf high at `breakout`."""
    dates = pd.date_range("2026-01-01", periods=n, freq="D")
    rows = [dict(DATE=dates[0], OPEN=95.0, HIGH=100.0, LOW=90.0, CLOSE=98.0, VOLUME=3e6),
            dict(DATE=dates[1], OPEN=106.0, HIGH=111.0, LOW=104.0, CLOSE=110.0, VOLUME=4e6),
            dict(DATE=dates[2], OPEN=110.0, HIGH=112.0, LOW=107.0, CLOSE=109.0, VOLUME=3e6)]
    for i in range(3, breakout):
        rows.append(dict(DATE=dates[i], OPEN=109.0, HIGH=111.0, LOW=107.0, CLOSE=110.0, VOLUME=2e6))
    rows.append(dict(DATE=dates[breakout], OPEN=110.0, HIGH=115.0, LOW=109.0, CLOSE=114.0, VOLUME=5e6))
    prev = 114.0
    for i in range(breakout + 1, n):
        c = prev * 1.01
        rows.append(dict(DATE=dates[i], OPEN=prev, HIGH=c * 1.005, LOW=prev * 0.995, CLOSE=c, VOLUME=3e6))
        prev = c
    return pd.DataFrame(rows)


class TestShelfCross(unittest.TestCase):
    def setUp(self):
        self.df = mk_shelf_df()
        self.p = Params()

    def run_shelf(self, df=None, **kw):
        kw.setdefault("min_age", 5)
        kw.setdefault("causal", True)
        kw.setdefault("vol_gate", False)
        return simulate_symbol_cross(self.df if df is None else df, "SHF", LISTING, self.p, cfg=EOD, setup="shelf", **kw)

    def test_enters_at_the_shelf_trigger_not_the_close(self):
        t = self.run_shelf()[0]
        self.assertEqual(t["type"], "SHELF_BREAKOUT")
        self.assertEqual(t["entry_idx"], 8)
        self.assertAlmostEqual(t["entry_price"], 111.0 * 1.015, places=6)          # shelf high 111 + 1.5%
        self.assertTrue(t["open"])

    def test_listing_setup_ignores_a_shelf_above_the_listing_high(self):
        out = simulate_symbol_cross(self.df, "SHF", LISTING, self.p, cfg=EOD, min_age=5, causal=True, vol_gate=False)
        # the same bars are far above the listing-high trigger: no listing-breakout entry may be created from the shelf
        self.assertTrue(all(t["type"] == "LISTING_BREAKOUT" for t in out))
        self.assertTrue(all(t["entry_idx"] != 8 for t in out))

    def test_fill_cap_relative_to_the_shelf_high(self):
        df = self.df.copy()
        df.loc[8, ["OPEN", "HIGH"]] = [116.0, 117.0]                                # opens 4.5% over the shelf high
        self.assertEqual(self.run_shelf(df, fill_cap_pct=2.5), [])
        t = self.run_shelf(df, fill_cap_pct=5.0)[0]
        self.assertAlmostEqual(t["entry_price"], 116.0, places=6)

    def test_causal_mode_ignores_same_day_volume(self):
        a = self.run_shelf()[0]
        df = self.df.copy()
        df.loc[8, "VOLUME"] = 10.0
        b = self.run_shelf(df)[0]
        self.assertEqual(a["entry_price"], b["entry_price"])
        self.assertEqual(self.run_shelf(df, causal=False, vol_gate=True), [])      # the end-of-day model would reject it

    def test_age_window(self):
        self.assertEqual(self.run_shelf(min_age=9), [])                             # breakout is at age 8


def dead_then_runner_trades():
    """A: flat for 20 days then a time stop. B: runner. C: a fresh signal on day 10 while the 2-slot book is full."""
    def path(start, end, f):
        ds = list(pd.date_range(start, end))
        return [(d, f(i), 90.0) for i, d in enumerate(ds)], ds
    pa, da = path("2026-03-02", "2026-03-22", lambda i: 100.5)
    pb, db_ = path("2026-03-03", "2026-04-10", lambda i: 100.0 + 3.0 * i)
    pc, dc = path("2026-03-12", "2026-03-25", lambda i: 100.0 + 2.0 * i)
    def mk(sym, ds, p, entry_price, exit_price, reason):
        return dict(symbol=sym, type="LISTING_BREAKOUT", entry_date=str(ds[0].date()), exit_date=str(ds[-1].date()),
                    entry_price=entry_price, exit_price=exit_price, year=2026, exit_reason=reason,
                    pnl_pct=(exit_price / entry_price - 1) * 100, path=p, initial_risk_pct=10.0, vol_ratio=1.0)
    return [mk("A", da, pa, 100.0, 90.0, "Time Stop - IPO Dead Money"), mk("B", db_, pb, 100.0, 200.0, "Stop Loss"),
            mk("C", dc, pc, 100.0, 126.0, "Stop Loss")]


class TestRotation(unittest.TestCase):
    CFG = dict(capital=1e6, soft_cap=2, hard_cap=2, cap_policy="flat")

    def test_default_off_skips_the_signal_and_keeps_everything_else(self):
        r = run_portfolio(dead_then_runner_trades(), PortfolioConfig(**self.CFG))
        self.assertEqual(set(r["log"].symbol), {"A", "B"})
        self.assertEqual(r["summary"]["skipped"]["cap"], 1)
        self.assertEqual(r["summary"]["rotated"], [])

    def test_rotation_sells_the_dead_position_to_fund_the_new_signal(self):
        r = run_portfolio(dead_then_runner_trades(), PortfolioConfig(**self.CFG, rotate_after_days=7))
        self.assertEqual(set(r["log"].symbol), {"A", "B", "C"})
        self.assertEqual([x["symbol"] for x in r["summary"]["rotated"]], ["A"])     # B has a peak far above 4%
        row = r["log"].set_index("symbol")
        self.assertEqual(row.loc["A", "exit_reason"], "Rotated (dead money)")
        self.assertEqual(r["summary"]["rotated"][0]["original_exit"], "Time Stop - IPO Dead Money")

    def test_young_positions_are_not_rotated(self):
        r = run_portfolio(dead_then_runner_trades(), PortfolioConfig(**self.CFG, rotate_after_days=30))
        self.assertEqual(r["summary"]["rotated"], [])
        self.assertEqual(set(r["log"].symbol), {"A", "B"})


class TestOrderSheetModes(unittest.TestCase):
    POS = [dict(symbol="AAA", quantity=100, entry_price=100.0, trailing_stop=95.03, current_price=110.0)]

    def test_resting_mode_is_the_unchanged_default(self):
        res = build_sheet(self.POS, [], SheetConfig(capital=100000))
        self.assertIsNone(res.stops[0].alert_level)
        self.assertAlmostEqual(res.stops[0].trigger, 95.0)

    def test_close_alert_mode(self):
        cfg = SheetConfig(capital=100000, stop_mode="close_alert", disaster_buffer_pct=8.0)
        res = build_sheet(self.POS, [], cfg)
        st = res.stops[0]
        self.assertAlmostEqual(st.alert_level, 95.0)                              # decision level, rounded down to the tick
        self.assertLess(st.trigger, 95.03 * 0.92 + 0.06)                          # disaster stop 8% below
        text = format_sheet(res, cfg)
        self.assertIn("NEXT OPEN", text)
        self.assertIn("OPTIONAL DISASTER STOPS", text)

    def test_alert_warning_when_price_is_at_the_level(self):
        cfg = SheetConfig(capital=100000, stop_mode="close_alert")
        pos = [dict(symbol="AAA", quantity=10, entry_price=100.0, trailing_stop=95.0, current_price=94.0)]
        self.assertTrue(build_sheet(pos, [], cfg).warnings)


def cand(sym, trig=101.5, limit=102.5, last_close=95.0, above=False, price=101.8, stop=89.6):
    return dict(symbol=sym, trigger=trig, limit=limit, fill=price, stop=stop, risk_pct=12.0, rr=1.67, age=20,
                last_close=last_close, already_above_trigger=above)


class TestRestingEntries(unittest.TestCase):
    def test_orders_are_ranked_and_capped_at_free_slots(self):
        cands = [cand("FAR", last_close=88.0), cand("NEAR", last_close=100.0), cand("ABOVE", last_close=102.0, above=True)]
        entries, skipped, _ = build_resting_entries(cands, slots_used=8, cfg=SheetConfig(capital=1e6, hard_cap=10))
        self.assertEqual([e.symbol for e in entries], ["ABOVE", "NEAR"])
        self.assertEqual(len(skipped), 1)
        self.assertIn("FAR", skipped[0])

    def test_levels_and_sizing(self):
        entries, _, _ = build_resting_entries([cand("X")], 0, SheetConfig(capital=1e6, hard_cap=10))
        e = entries[0]
        self.assertGreaterEqual(e.limit, e.trigger)
        self.assertAlmostEqual(e.est_value, 1e5, delta=1500.0)                    # about 10% of capital (sized at the limit)
        self.assertLessEqual(e.quantity * e.limit, 1e5 + 1e-6)
        self.assertGreater(e.risk_rupees, 0.0)
        self.assertIn("X", format_resting_entries(entries, [], SheetConfig(capital=1e6)))

    def test_halt_blocks_all_orders(self):
        g = GuardStatus(level="HALT", halt_new_entries=True, reasons=["drawdown 21%"])
        entries, skipped, _ = build_resting_entries([cand("X")], 0, SheetConfig(capital=1e6), guard=g)
        self.assertEqual(entries, [])
        self.assertIn("halted", skipped[0])

    def test_unaffordable_names_are_skipped(self):
        entries, skipped, _ = build_resting_entries([cand("BIG", trig=25000.0, limit=25500.0, last_close=24000.0)], 0,
                                                    SheetConfig(capital=100000))
        self.assertEqual(entries, [])
        self.assertTrue(skipped)


if __name__ == "__main__":
    unittest.main()
