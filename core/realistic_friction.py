# -*- coding: utf-8 -*-
"""
core/realistic_friction.py -- research overlay: re-price replay trades with liquidity-tiered spread, impact, tick rounding, the stop-limit
buy limit, upper-circuit entry blocking and lower-circuit exit deferral.  Pure functions, no DB, no network, imports nothing from the scanners.

The spread / impact part is a PORT of Learning-Lab's `nse_quant_lab/cost_model.py` (tier table, tick floor, square-root impact, 5 bps floor,
8% cap).  Every number in it is an ASSUMPTION until the forward test measures real fills against the trigger / next open (the source says so
too).  Nothing here changes an entry or exit RULE, the honest backtest engine or any live scanner: it only re-prices the SAME trades.

Differences from the flat replay cost (`pnl_pct - 1.0`, entry slip +0.3% inside the entry price), all fixed here BEFORE seeing any result:
  1. entry slippage per leg = slippage_fraction(ADT of the prior 50 sessions, price, order value, prior 20-day sigma) instead of flat 0.3%;
  2. the fill must still be at or below the resting order's LIMIT (listing high +2.5%); a fill that the spread pushes above it does not happen;
  3. an entry bar that printed a single price at least 4.5% above the prior close (heuristic upper-circuit lock) cannot be bought;
  4. an exit bar that printed a single price at least 4.5% below the prior close (heuristic lower-circuit lock) cannot be sold: the exit moves to
     the next unlocked session's open (at most MAX_DEFER sessions, then the last price tried);
  5. exit slippage per leg with the same model at the exit session; prices are rounded to the NSE tick against the trader;
  6. statutory and broker charges on the ACTUAL whole-share traded value (core.execution_model.CostModel.fees, slippage fields unused).
Circuit bands are not in the candle data, so (3) and (4) are the same single-price heuristic the engine uses (core.execution_model).
"""

import math
from dataclasses import dataclass
from typing import List, Optional

import numpy as np
import pandas as pd

from core.execution_model import CostModel, is_locked_down, is_locked_up

# ---- ported from Learning-Lab cost_model.py (ASSUMED values, not measured) ----
SPREAD_TIERS_BPS = (
    (1.0e8, 8.0),    # >= Rs 10 Cr / day
    (3.0e7, 15.0),   # Rs 3 - 10 Cr
    (1.0e7, 25.0),   # Rs 1 - 3 Cr
    (5.0e6, 40.0),   # Rs 50 L - 1 Cr
    (2.5e6, 60.0),   # Rs 25 - 50 L
    (0.0, 90.0),     # below Rs 25 L
)
TICK = 0.05
IMPACT_COEF = 0.7
DEFAULT_SIGMA = 0.03
MIN_BPS = 5.0
MAX_FRACTION = 0.08

ADT_WINDOW = 50
SIGMA_WINDOW = 20
ADT_MIN_SESSIONS = 3        # the engine never enters before bar 3, so every entry has at least 3 prior sessions
SIGMA_MIN_RETURNS = 2
MAX_DEFER = 10
LOCK_BAND_PCT = 4.5


def spread_bps(adt: Optional[float]) -> float:
    if adt is None or not (adt == adt) or adt <= 0:
        return SPREAD_TIERS_BPS[-1][1]
    for floor, bps in SPREAD_TIERS_BPS:
        if adt >= floor:
            return bps
    return SPREAD_TIERS_BPS[-1][1]


def slippage_fraction(adt_prev: Optional[float], price: float, order_value: float, sigma_prev: Optional[float] = None, mult: float = 1.0) -> float:
    """Per-leg cost as a fraction of price (0.002 == 20 bps): tier spread or tick floor, plus square-root impact, floor 5 bps, cap 8%."""
    sp = spread_bps(adt_prev)
    tick_floor = (0.5 * TICK / price) * 1e4 if price > 0 else 0.0
    base_bps = max(sp, tick_floor)
    sig = sigma_prev if (sigma_prev is not None and sigma_prev == sigma_prev and sigma_prev > 0) else DEFAULT_SIGMA
    part = order_value / adt_prev if (adt_prev is not None and adt_prev == adt_prev and adt_prev > 0) else 1.0
    impact_bps = IMPACT_COEF * sig * math.sqrt(max(part, 0.0)) * 1e4
    total = max(MIN_BPS, (base_bps + impact_bps) * mult) / 1e4
    return min(total, MAX_FRACTION)


def tick_up(price: float) -> float:
    return math.ceil(round(price / TICK, 6)) * TICK


def tick_down(price: float) -> float:
    return math.floor(round(price / TICK, 6)) * TICK


# ---- point-in-time liquidity inputs (strictly BEFORE bar `idx`) ----
def adt_before(df: pd.DataFrame, idx: int) -> Optional[float]:
    """Median of CLOSE x VOLUME over up to ADT_WINDOW sessions before `idx` (needs ADT_MIN_SESSIONS), Rs; None when too short."""
    lo = max(0, idx - ADT_WINDOW)
    t = (df["CLOSE"].iloc[lo:idx].astype(float) * df["VOLUME"].iloc[lo:idx].astype(float))
    return float(t.median()) if len(t) >= ADT_MIN_SESSIONS else None


def sigma_before(df: pd.DataFrame, idx: int) -> Optional[float]:
    lo = max(0, idx - SIGMA_WINDOW - 1)
    r = df["CLOSE"].iloc[lo:idx].astype(float).pct_change().dropna()
    return float(r.std()) if len(r) >= SIGMA_MIN_RETURNS else None


@dataclass
class Repriced:
    taken: bool
    reason: str = ""                  # why not taken: UPPER_CIRCUIT | ABOVE_LIMIT | NO_SHARES
    entry_fill: float = 0.0
    exit_fill: float = 0.0
    shares: int = 0
    entry_slip_bps: float = 0.0
    exit_slip_bps: float = 0.0
    fee_pct: float = 0.0
    gross_pct: float = 0.0            # (exit_fill - entry_fill) / entry_fill, before charges
    net_pct: float = 0.0              # gross_pct - fee_pct
    exit_deferred: int = 0            # sessions the exit moved because of a lower-circuit lock


def _locked_up(df, k):
    r = df.iloc[k]
    prev = float(df["CLOSE"].iloc[k - 1]) if k > 0 else None
    return is_locked_up(float(r["OPEN"]), float(r["HIGH"]), float(r["LOW"]), float(r["CLOSE"]), prev, LOCK_BAND_PCT)


def _locked_down(df, k):
    r = df.iloc[k]
    prev = float(df["CLOSE"].iloc[k - 1]) if k > 0 else None
    return is_locked_down(float(r["OPEN"]), float(r["HIGH"]), float(r["LOW"]), float(r["CLOSE"]), prev, LOCK_BAND_PCT)


def reprice_trade(df: pd.DataFrame, trade: dict, ld_high: float, position_value: float, mult: float = 1.0,
                  cost: CostModel = CostModel(), base_slip_pct: float = 0.3, fill_cap_pct: float = 2.5) -> Repriced:
    """
    Re-price one replay trade (as returned by simulate_symbol_cross) under the rules in the module docstring.
    `trade` needs entry_idx, exit_idx (open trades: the last bar), entry_price (incl. the replay's flat slip), exit_price, open.
    """
    i = int(trade["entry_idx"])
    n = len(df)
    j = int(trade.get("exit_idx", n - 1)) if not trade.get("open") else n - 1
    if _locked_up(df, i):
        return Repriced(False, "UPPER_CIRCUIT")
    ref_entry = float(trade["entry_price"]) / (1.0 + base_slip_pct / 100.0)      # max(open, trigger): the fill before the replay's slip
    es = slippage_fraction(adt_before(df, i), ref_entry, position_value, sigma_before(df, i), mult)
    entry_fill = tick_up(ref_entry * (1.0 + es))
    limit = ld_high * (1.0 + fill_cap_pct / 100.0)
    if entry_fill > limit:
        return Repriced(False, "ABOVE_LIMIT")
    shares = int(position_value // entry_fill)
    if shares < 1:
        return Repriced(False, "NO_SHARES")

    # exit: the replay's exit price at session j; a lower-circuit locked session moves it to the next unlocked open
    k, deferred, ref_exit = j, 0, float(trade["exit_price"])
    while not trade.get("open") and _locked_down(df, k) and deferred < MAX_DEFER and k + 1 < n:
        k += 1
        deferred += 1
        ref_exit = float(df["OPEN"].iloc[k])
    xs = slippage_fraction(adt_before(df, k), ref_exit, shares * ref_exit, sigma_before(df, k), mult)
    exit_fill = max(tick_down(ref_exit * (1.0 - xs)), 0.0)

    buy_value, sell_value = shares * entry_fill, shares * exit_fill
    free = CostModel(brokerage_per_order=cost.brokerage_per_order, stt_pct=cost.stt_pct, stamp_duty_buy_pct=cost.stamp_duty_buy_pct,
                     exchange_txn_pct=cost.exchange_txn_pct, sebi_pct=cost.sebi_pct, gst_pct=cost.gst_pct, dp_charge_sell=cost.dp_charge_sell,
                     entry_slippage_pct=0.0, exit_slippage_pct=0.0)
    fee_pct = free.fees(buy_value, sell_value) / buy_value * 100.0
    gross = (exit_fill - entry_fill) / entry_fill * 100.0
    return Repriced(True, "", entry_fill, exit_fill, shares, es * 1e4, xs * 1e4, fee_pct, gross, gross - fee_pct, deferred)
