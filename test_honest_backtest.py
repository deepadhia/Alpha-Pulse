# -*- coding: utf-8 -*-
"""Tests for core.execution_model, core.honest_backtest and core.portfolio_sim."""
import os
os.environ["DISABLE_TELEGRAM"] = "1"
os.environ["TESTING"] = "1"

import sys
import unittest
from dataclasses import replace

import numpy as np
import pandas as pd

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core.execution_model import (CostModel, ZERO_COST, is_locked_down, is_locked_up,
                                   round_trip_cost_pct)
from core.honest_backtest import LEGACY, LIVE_FAITHFUL, Params, SimConfig, simulate_symbol, _leader_score
from core.portfolio_sim import PortfolioConfig, run_portfolio


# ------------------------------------------------------------------ synthetic data

def make_df(rows, start="2024-01-01"):
    """rows: list of (open, high, low, close, volume). Business-day dates."""
    dates = pd.bdate_range(start=start, periods=len(rows))
    df = pd.DataFrame(rows, columns=["OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"])
    df.insert(0, "DATE", dates)
    return df


CRASH = [(80, 81, 79, 80, 900000)]     # gaps through any stop so the trade is recorded


LISTING = {"listing_day_high": 102.0, "listing_day_low": 95.0, "listing_date": "2024-01-01"}


def base_rows():
    """Bars 0..13 flat around 100, then a clean breakout bar at index 14 (close 103.8)."""
    rows = [(100, 101, 99, 100, 300000) for _ in range(14)]
    rows.append((101, 104, 100.5, 103.8, 750000))      # bar 14: breakout, vol 2.5x
    return rows


class TestExecutionModel(unittest.TestCase):
    def test_zero_cost_is_zero(self):
        self.assertEqual(ZERO_COST.fees(14000, 15000), 0.0)
        self.assertEqual(round_trip_cost_pct(ZERO_COST, 14000), 0.0)

    def test_small_account_cost_is_material(self):
        # Rs ~14k position: fixed charges + STT + slippage should be roughly 1.0-1.6% round trip
        pct = round_trip_cost_pct(CostModel(), 14000.0)
        self.assertGreater(pct, 0.9)
        self.assertLess(pct, 1.8)

    def test_fixed_costs_hurt_small_positions_more(self):
        c = CostModel()
        self.assertGreater(round_trip_cost_pct(c, 5000.0), round_trip_cost_pct(c, 50000.0))

    def test_lock_heuristics(self):
        self.assertTrue(is_locked_down(90, 90, 90, 90, 100))
        self.assertFalse(is_locked_down(95, 97, 90, 92, 100))
        self.assertTrue(is_locked_up(110, 110, 110, 110, 100))
        self.assertFalse(is_locked_up(101, 104, 100, 103, 100))


class TestParamsMatchLive(unittest.TestCase):
    """Defaults in Params must equal the live constants, or the backtest silently drifts."""

    def test_defaults_equal_live(self):
        live = Params.from_live()
        self.assertEqual(Params(), live)

    def test_leader_score_matches_live(self):
        import listing_day_breakout_scanner as lscan
        rng = np.random.default_rng(7)
        for _ in range(300):
            args = (float(rng.uniform(-1, 8)), float(rng.uniform(0.5, 4)), float(rng.uniform(0.5, 3)),
                    float(rng.uniform(90, 120)), 100.0, float(rng.uniform(2, 40)))
            self.assertEqual(_leader_score(*args), lscan._leader_score(*args))


class TestEntryAndExitMechanics(unittest.TestCase):
    def _run(self, tail_rows, cfg, p=Params()):
        df = make_df(base_rows() + tail_rows)
        return simulate_symbol(df, "TEST", dict(LISTING), cfg, p)

    def test_entry_happens_at_breakout_close(self):
        trades = self._run([(104, 105, 103, 104, 300000)] * 3 + CRASH, LEGACY)
        self.assertEqual(len(trades), 1)
        self.assertAlmostEqual(trades[0]["entry_price"], 103.8)
        self.assertEqual(trades[0]["type"], "LISTING_BREAKOUT")

    def test_gap_down_stop_fill_models(self):
        gap = [(90, 92, 89, 91, 800000)]
        legacy = self._run(gap, LEGACY)[0]
        honest = self._run(gap, replace(LEGACY, exit_fill="gap_aware"))[0]
        self.assertAlmostEqual(legacy["exit_price"], legacy["stop_initial"])   # fills at the stop (unrealistic)
        self.assertAlmostEqual(honest["exit_price"], 90.0)                      # fills at the open
        self.assertLess(honest["pnl_pct"], legacy["pnl_pct"])

    def test_eod_alert_mode_ignores_intraday_touch_and_sells_next_open(self):
        # bar 10 dips below the stop intraday but closes above it -> no exit under the EOD-alert model
        touch = [(100, 101, 95.0, 100.5, 300000)] + [(100, 101, 99, 100, 300000)] * 3
        trades = self._run(touch, replace(LEGACY, exit_fill="eod_next_open"))
        self.assertTrue(all(t["exit_reason"] != "Stop Loss (EOD alert)" for t in trades))
        # bar 10 closes below the stop, bar 11 opens lower -> exit at bar 11 open
        t = self._run([(97, 98, 94, 95, 400000), (94, 95, 92, 93, 400000)],
                      replace(LEGACY, exit_fill="eod_next_open"))[0]
        self.assertEqual(t["exit_reason"], "Stop Loss (EOD alert)")
        self.assertAlmostEqual(t["exit_price"], 94.0)

    def test_locked_down_exit_is_deferred(self):
        locked = [(88, 88, 88, 88, 100000), (85, 86, 84, 85, 500000)]
        unlocked_model = self._run(locked, replace(LEGACY, exit_fill="gap_aware"))[0]
        locked_model = self._run(locked, replace(LEGACY, exit_fill="gap_aware", lock_model=True))[0]
        self.assertAlmostEqual(unlocked_model["exit_price"], 88.0)
        self.assertAlmostEqual(locked_model["exit_price"], 85.0)   # could not sell into the lock

    def test_velocity_gate_clock(self):
        flat = [(103.5, 104, 103, 103.6, 300000)] * 20           # flat, peak < 4%, pnl <= 0
        bars = self._run(flat, LEGACY)[0]
        live = self._run(flat, replace(LEGACY, gate_clock="live"))[0]
        self.assertEqual(bars["days_held"], 14)       # 14 bars
        self.assertEqual(live["days_held"], 10)       # 10 sessions / 14 calendar days comes first

    def test_stage1_trail_basis(self):
        # +10.8% close (below the 15% winner threshold) -> Stage-1 trail applies
        rise = [(104, 116, 104, 115, 400000)] + [(115, 116, 114, 115, 400000)] * 2
        crash = [(95, 96, 94, 95, 900000)]
        t_initial = self._run(rise + crash, LEGACY)[0]
        t_grade = self._run(rise + crash, replace(LEGACY, trail_basis="grade_10pct"))[0]
        # legacy trails by the entry risk % (~7.5%); live trails a flat 10% below price
        self.assertGreater(t_initial["exit_price"], t_grade["exit_price"])
        self.assertAlmostEqual(t_grade["exit_price"], 115 * 0.90, places=4)

    def test_trail_requires_minimum_improvement_in_live_mode(self):
        # candidate stop (107 * 0.9 = 96.3) is < 1% of entry above the initial stop -> not applied
        rise = [(104, 108, 104, 107, 400000)] * 2
        t = self._run(rise + [(95, 96, 94, 95, 900000)], replace(LEGACY, trail_basis="grade_10pct"))[0]
        self.assertAlmostEqual(t["exit_price"], t["stop_initial"], places=4)

    def test_next_open_fill_respects_limit_and_gap(self):
        # next-day open far above the limit ceiling -> signal is missed
        miss = self._run([(120, 125, 119, 124, 500000)] * 3, replace(LEGACY, entry_fill="next_open"))
        self.assertEqual(len(miss), 0)
        # normal next open -> fills at that open
        ok = self._run([(104.5, 106, 104, 105, 400000)] * 3 + CRASH, replace(LEGACY, entry_fill="next_open"))
        self.assertEqual(len(ok), 1)
        self.assertAlmostEqual(ok[0]["entry_price"], 104.5)

    def test_live_entry_path_requires_post_confirm_move(self):
        weak = base_rows()[:-1] + [(101, 103.0, 100.5, 102.6, 750000)]   # +0.6% above listing high
        df = make_df(weak + [(103, 104, 102, 103, 300000)] * 3 + CRASH)
        self.assertEqual(len(simulate_symbol(df, "T", dict(LISTING), replace(LEGACY, entry_path="live"))), 0)
        self.assertEqual(len(simulate_symbol(df, "T", dict(LISTING), LEGACY)), 1)   # legacy takes it


class TestLegacyParityOffline(unittest.TestCase):
    """New engine with legacy flags == run_latest_rules_backtest on seeded synthetic IPOs."""

    @staticmethod
    def synth(seed, n=260):
        rng = np.random.default_rng(seed)
        price = 100.0
        rows = []
        for k in range(n):
            ret = rng.normal(0.004, 0.035)
            o = price
            c = max(5.0, price * (1 + ret))
            h = max(o, c) * (1 + abs(rng.normal(0, 0.012)))
            l = min(o, c) * (1 - abs(rng.normal(0, 0.012)))
            v = float(np.exp(rng.normal(12.2, 0.5)))
            if rng.random() < 0.25:
                v *= rng.uniform(2.0, 6.0)
            rows.append((o, h, l, c, v))
            price = c
        return make_df(rows, start="2023-01-02")

    def test_parity(self):
        import run_latest_rules_backtest as legacy
        import listing_day_breakout_scanner as lscan
        p = Params.from_live()
        cfg = {
            "enable_vol_exit": True, "enable_stagnant_guard": True, "enable_speed_gates": True,
            "trail_pnl_ipo": 3.0, "vol_ratio": 0.45, "start_date": None, "min_volume": 150000,
            "min_vol_mult": lscan.MIN_VOLUME_MULTIPLIER, "min_close_loc": 0.50,
            "max_extension_pct": lscan.MAX_ENTRY_ABOVE_HIGH_PCT,
            "max_age": lscan.MAX_DAYS_SINCE_LISTING_FOR_BREAKOUT, "cooldown_days": 30,
            "min_risk_reward": lscan.MIN_RISK_REWARD, "enforce_winner_cushion": True,
        }
        total = 0
        for seed in range(120):
            df = self.synth(seed)
            li = {"listing_day_high": float(df["HIGH"].iat[0]), "listing_day_low": float(df["LOW"].iat[0]),
                  "listing_date": df["DATE"].iat[0]}
            old = legacy.simulate_production_latest_system(df, f"S{seed}", li, cfg)
            new = simulate_symbol(df, f"S{seed}", li, LEGACY, p)
            key = lambda t: (t["entry_date"], t["exit_date"], round(t["pnl_pct"], 9))
            self.assertEqual(sorted(map(key, old)), sorted(map(key, new)), f"seed {seed}")
            total += len(old)
        self.assertGreater(total, 15, "synthetic data produced too few trades to be a meaningful parity test")


class TestPortfolioSim(unittest.TestCase):
    @staticmethod
    def trade(sym, entry, exit_, e_px=100.0, x_px=110.0, vol=2.0, trailing=95.0):
        days = pd.bdate_range(entry, exit_)
        path = [(d, e_px + (x_px - e_px) * k / max(1, len(days) - 1), trailing) for k, d in enumerate(days)]
        return dict(symbol=sym, type="LISTING_BREAKOUT", entry_date=entry, exit_date=exit_,
                    entry_price=e_px, exit_price=x_px, pnl_pct=(x_px / e_px - 1) * 100, exit_reason="x",
                    year=int(entry[:4]), vol_ratio=vol, path=path)

    def test_slot_cap_and_whole_shares(self):
        trades = [self.trade(f"S{k}", "2024-03-01", "2024-03-20", e_px=100 + k) for k in range(10)]
        r = run_portfolio(trades, PortfolioConfig(capital=100000, soft_cap=5, hard_cap=7, cost=ZERO_COST))
        self.assertEqual(r["summary"]["trades_taken"], 7)
        self.assertEqual(r["summary"]["skipped"]["cap"], 3)
        self.assertTrue((r["log"]["shares"] == r["log"]["shares"].astype(int)).all())

    def test_too_expensive_stock_is_skipped(self):
        # target ~ Rs 14.3k, price Rs 20,000 -> zero shares
        r = run_portfolio([self.trade("BIG", "2024-03-01", "2024-03-10", e_px=20000, x_px=21000)],
                          PortfolioConfig(capital=100000, cost=ZERO_COST))
        self.assertEqual(r["summary"]["trades_taken"], 0)

    def test_documented_policy_blocks_risk_bearing_overflow(self):
        # 5 positions with stops below entry -> the 6th/7th slot must be refused under 'documented'
        early = [self.trade(f"E{k}", "2024-03-01", "2024-04-30", trailing=95.0) for k in range(5)]
        late = [self.trade("L1", "2024-03-15", "2024-04-30"), self.trade("L2", "2024-03-15", "2024-04-30")]
        doc = run_portfolio(early + late, PortfolioConfig(cap_policy="documented", cost=ZERO_COST))
        coded = run_portfolio(early + late, PortfolioConfig(cap_policy="as_coded", cost=ZERO_COST))
        self.assertEqual(doc["summary"]["trades_taken"], 5)
        self.assertEqual(coded["summary"]["trades_taken"], 7)

    def test_documented_policy_allows_overflow_when_book_is_risk_free(self):
        early = [self.trade(f"E{k}", "2024-03-01", "2024-04-30", trailing=101.0) for k in range(5)]
        late = [self.trade("L1", "2024-03-15", "2024-04-30")]
        doc = run_portfolio(early + late, PortfolioConfig(cap_policy="documented", cost=ZERO_COST))
        self.assertEqual(doc["summary"]["trades_taken"], 6)

    def test_equity_curve_does_not_drop_on_entry_day(self):
        # regression: positions had no mark on their entry day and were valued at zero,
        # producing phantom drawdowns of the size of the whole position
        trades = [self.trade("A", "2024-03-04", "2024-03-20", e_px=100, x_px=100),
                  self.trade("B", "2024-03-11", "2024-03-20", e_px=100, x_px=100)]
        r = run_portfolio(trades, PortfolioConfig(cost=ZERO_COST))
        nav = r["equity"]["nav"]
        self.assertGreater(nav.min(), 99000.0)
        self.assertGreater(r["summary"]["max_drawdown_pct"], -1.0)

    def test_costs_reduce_return(self):
        trades = [self.trade("A", "2024-03-01", "2024-03-20")]
        free = run_portfolio(trades, PortfolioConfig(cost=ZERO_COST))["summary"]["final_nav"]
        paid = run_portfolio(trades, PortfolioConfig(cost=CostModel()))["summary"]["final_nav"]
        self.assertLess(paid, free)


if __name__ == "__main__":
    unittest.main()
