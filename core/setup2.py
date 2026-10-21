# -*- coding: utf-8 -*-
"""
core/setup2.py

RESEARCH ONLY (2026-10-21).  A second, separate entry setup, "S2: pivot breakout BELOW the listing high", for the explosive post-listing
moves that policy P (listing-high breakout) can never hold.  Pre-registration and the adoption bar: research_setup2.py (docstring).

It reuses the honest engine as is (core.honest_backtest `_Bars`, `_run_trade`, `_make_trade`, `_make_open_trade`, `_stop_from_history`,
`_listing_context`) and therefore the same exits (core.exit_rules), close-based stops sold at the next open, stop cap and gates as policy P.
Nothing here is imported by a scanner, changes a default, or touches MongoDB.

Information rule (AGENTS.md rule 21): everything is decided from bars up to the PRIOR close; bar i supplies only the open and the high, which is
what a resting stop-limit order sees.  Same-day volume, same-day close and the forming candle are never read (tests assert this).
"""

import re
from dataclasses import dataclass, replace
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from core.honest_backtest import (LIVE_FAITHFUL, Params, _Bars, _listing_context, _make_open_trade, _run_trade,
                                   _stop_from_history)

EOD = replace(LIVE_FAITHFUL, exit_fill="eod_next_open")
PLACEHOLDER_LISTING_DATES = (pd.Timestamp("2026-08-17"), pd.Timestamp("2026-08-18"))
DEAD_MONEY_RE = re.compile(r"Dead|Velocity|Volume Exhaustion|Stagnant|Time Stop")


@dataclass(frozen=True)
class Setup2Params:
    pivot_window: int = 10               # pivot = highest HIGH of the last N completed sessions
    pivot_min_sessions_old: int = 5      # the pivot session is at least this many completed sessions before bar i (a base, not a run)
    min_age_days: int = 15
    max_age_days: int = 200
    young_max_age_days: int = 60         # H3 (young) up to this age; H1/H2 above it
    below_lh_frac: float = 0.10          # prior close at or below listing high x (1 - this)
    proximity_frac: float = 0.10         # prior close at or above pivot x (1 - this): the order-sheet feasibility rule
    deep_dd: float = 0.30                # H2 (recovery) when DD60 >= this
    dd_window: int = 60
    trigger_pct: float = 1.5             # resting trigger above the pivot (same as policy P, listing high +1.5%)
    cap_pct: float = 2.5                 # resting limit above the pivot (same as policy P, +2.5%)


@dataclass
class _Pre:
    """Rolling quantities that depend only on bars strictly before bar i."""
    turn: np.ndarray                     # mean(close x volume) of the last 20 bars before i, in Rs crore
    circ: np.ndarray                     # circuit-like days among the last 15 bars before i


def _prepare(df: pd.DataFrame) -> _Pre:
    cv = (df["CLOSE"] * df["VOLUME"]).astype(float)
    turn = (cv.rolling(20, min_periods=1).mean().shift(1) / 1e7).values
    circ = (((df["HIGH"] == df["LOW"]) & (df["VOLUME"] > 0)).astype(float).rolling(15, min_periods=1).sum().shift(1)).values
    return _Pre(turn=turn, circ=circ)


# ------------------------------------------------------------------------------------------ universe hygiene

def clean_universe(listing_map: dict, candle_map: dict, min_listing: str = "2022-05-01"):
    """
    Symbols whose listing record is consistent with their candles.  Returns ({symbol: (df, listing_info)}, {symbol: reason}).
    Excludes: no record, placeholder listing date (2026-08-17/18), listing before `min_listing`, first candle more than 5 days from the
    listing date, listing-day high not within 2% of the first candle high, fewer than 40 candles.
    """
    keep, dropped = {}, {}
    lo = pd.Timestamp(min_listing)
    for sym, c in candle_map.items():
        r = listing_map.get(sym)
        if r is None:
            dropped[sym] = "no listing record"
            continue
        if len(c) < 40:
            dropped[sym] = "fewer than 40 candles"
            continue
        ld = pd.Timestamp(r["listing_date"])
        ld = (ld.tz_localize(None) if ld.tzinfo else ld).normalize()
        if ld in PLACEHOLDER_LISTING_DATES:
            dropped[sym] = "placeholder listing date"
            continue
        if ld < lo:
            dropped[sym] = "listed before study window"
            continue
        d = c.copy()
        d["DATE"] = pd.to_datetime(d["DATE"])
        if d["DATE"].dt.tz is not None:
            d["DATE"] = d["DATE"].dt.tz_localize(None)
        d = d.sort_values("DATE").reset_index(drop=True)
        if abs((d["DATE"].iloc[0] - ld).days) > 5:
            dropped[sym] = "first candle far from listing date"
            continue
        lh = r.get("listing_day_high")
        if not lh or lh <= 0 or not (0.98 <= float(lh) / float(d["HIGH"].iloc[0]) <= 1.02):
            dropped[sym] = "listing high inconsistent with first candle"
            continue
        keep[sym] = (d, r)
    return keep, dropped


# ------------------------------------------------------------------------------------------ the setup

def classify(age: int, dd60: float, s2: Setup2Params = Setup2Params()) -> str:
    if age <= s2.young_max_age_days:
        return "H3_BASE_YOUNG"
    return "H2_RECOVERY" if dd60 >= s2.deep_dd else "H1_BASE_OLD"


def setup2_state(b: _Bars, pre: _Pre, i: int, ld_high: float, list_date: pd.Timestamp, p: Params = Params(),
                 s2: Setup2Params = Setup2Params()) -> Optional[dict]:
    """
    Pre-open eligibility of a resting order for bar i, from bars up to i-1 only (bar i contributes only its date).
    Returns dict(pivot, trig, ceil, age, dd60, klass, prev_close, dist_lh, prox, turn) or None.
    """
    w = s2.pivot_window
    if i < w + 1:
        return None
    age = int((pd.Timestamp(b.dates[i]) - list_date).days)
    if age < s2.min_age_days or age > s2.max_age_days:
        return None
    prev_close = float(b.closes[i - 1])
    if prev_close > ld_high * (1.0 - s2.below_lh_frac):
        return None
    window = b.highs[i - w:i]
    pivot = float(window.max())
    last_k = w - 1 - int(np.argmax(window[::-1]))                    # most recent session that printed the pivot
    if last_k > w - 1 - s2.pivot_min_sessions_old:
        return None                                                  # pivot set too recently: a run, not a base
    if pivot <= 0 or prev_close < pivot * (1.0 - s2.proximity_frac):
        return None
    if float(b.vols[i - 1]) < p.min_volume:                          # PRIOR bar volume, the only volume known pre-open
        return None
    turn, circ = float(pre.turn[i]), float(pre.circ[i])
    if (turn > 0 and turn < p.turnover_floor_cr) or circ >= p.circuit_day_threshold:
        return None
    lo_i = max(0, i - s2.dd_window)
    peak60 = float(b.highs[lo_i:i].max())
    dd60 = 1.0 - prev_close / peak60 if peak60 > 0 else 0.0
    return dict(pivot=pivot, trig=pivot * (1.0 + s2.trigger_pct / 100.0), ceil=pivot * (1.0 + s2.cap_pct / 100.0), age=age,
                dd60=dd60, klass=classify(age, dd60, s2), prev_close=prev_close, dist_lh=prev_close / ld_high - 1.0,
                prox=prev_close / pivot - 1.0, turn=turn)


def setup2_signal(df: pd.DataFrame, b: _Bars, pre: _Pre, i: int, ld_high: float, list_date: pd.Timestamp,
                  p: Params = Params(), s2: Setup2Params = Setup2Params(), slip_pct: float = 0.3,
                  fill_cap_pct: Optional[float] = None, state: Optional[dict] = None) -> Optional[dict]:
    """The resting stop-limit order of S2 fills on bar i, or None.  Uses bar i's open and high only."""
    st = state if state is not None else setup2_state(b, pre, i, ld_high, list_date, p, s2)
    if st is None:
        return None
    o, h = float(b.opens[i]), float(b.highs[i])
    ceil = st["ceil"] if fill_cap_pct is None else st["pivot"] * (1.0 + fill_cap_pct / 100.0)
    if h < st["trig"] or o > ceil:
        return None
    entry = max(o, st["trig"])
    if entry > ceil or entry < p.min_entry_price:
        return None
    fill = entry * (1.0 + slip_pct / 100.0)
    stop = _stop_from_history(df, i, fill, p)
    risk = (fill - stop) / fill
    if risk <= 0:
        return None
    return dict(st, entry=entry, fill=fill, stop=stop, risk=risk, ext=(entry - st["pivot"]) / st["pivot"] * 100.0)


def _signal_dict(s: dict, ld_high: float, vol_ratio: float = 0.0) -> dict:
    return dict(type="S2_" + s["klass"], pivot=s["pivot"], post_confirm=s["ext"], max_chase=2.5, signal_price=s["fill"],
                vol_ratio=vol_ratio, ext_pct=s["ext"], rr=0.0, shelf_prng=None)


def _decorate(trade: dict, s: dict, i: int, ex: Optional[int]) -> dict:
    trade["entry_idx"] = i
    trade["exit_idx"] = ex
    trade["klass"] = s["klass"]
    trade["dd60"] = float(s["dd60"])
    trade["pivot"] = float(s["pivot"])
    trade["dist_lh_pct"] = float(s["dist_lh"] * 100.0)
    trade["age_signal"] = int(s["age"])
    return trade


def simulate_symbol_setup2(df: pd.DataFrame, symbol: str, listing_info: dict, p: Params = Params(),
                           s2: Setup2Params = Setup2Params(), cfg=EOD, slip_pct: float = 0.3,
                           fill_cap_pct: Optional[float] = None, include_open: bool = True,
                           cooldown: bool = True) -> List[dict]:
    """
    S2 trades for one symbol: one position at a time, `p.cooldown_bars` bars between an exit and the next entry.  Trades still open at the
    end of the data are returned flagged open=True, marked to the last close.  `df` must be sorted by DATE with timezone-naive dates.
    """
    out: List[dict] = []
    n = len(df)
    if n < 15:
        return out
    if listing_info.get("is_nse_addition") and not listing_info.get("bse_liquidity_passed", True):
        return out
    ld_high, ld_low, list_date = _listing_context(df, listing_info)
    b = _Bars.of(df)
    pre = _prepare(df)
    last_exit = -100
    i = s2.pivot_window + 1
    while i < n:
        if cooldown and (i - last_exit) < p.cooldown_bars:
            i += 1
            continue
        s = setup2_signal(df, b, pre, i, ld_high, list_date, p, s2, slip_pct, fill_cap_pct)
        if s is None:
            i += 1
            continue
        cur = pd.Timestamp(b.dates[i])
        sig = _signal_dict(s, ld_high)
        t = dict(entry_idx=i, entry_date=cur, entry_price=s["fill"], signal=sig, signal_idx=i, signal_date=cur,
                 trailing=s["stop"], risk_pct=s["risk"], max_runup=0.0, path=[], stop_initial=s["stop"], age=s["age"],
                 missed=False)
        trade, ex = _run_trade(b, t, i, i, cfg, p, symbol)       # day0_bar=i: only the protective stop acts on the fill day
        if trade is None:
            if include_open:
                out.append(_decorate(_make_open_trade(t, b, symbol), s, i, None))
            break
        trade["open"] = False
        out.append(_decorate(trade, s, i, ex))
        last_exit = ex
        i = ex + 1
    return out


def order_days(df: pd.DataFrame, listing_info: dict, p: Params = Params(), s2: Setup2Params = Setup2Params()) -> List[pd.Timestamp]:
    """Dates on which a resting order would have been placed pre-market (state passes and the trigger price is tradable)."""
    ld_high, ld_low, list_date = _listing_context(df, listing_info)
    b = _Bars.of(df)
    pre = _prepare(df)
    days = []
    for i in range(s2.pivot_window + 1, len(df)):
        st = setup2_state(b, pre, i, ld_high, list_date, p, s2)
        if st is not None and st["trig"] >= p.min_entry_price:
            days.append(pd.Timestamp(b.dates[i]))
    return days


# ------------------------------------------------------------------------------------------ control

def control_indices(df: pd.DataFrame, listing_info: dict, p: Params = Params(), s2: Setup2Params = Setup2Params()) -> List[int]:
    """Bars where the S2 STATE holds (and the open is tradable at or above the price floor) irrespective of any breakout."""
    ld_high, ld_low, list_date = _listing_context(df, listing_info)
    b = _Bars.of(df)
    pre = _prepare(df)
    out = []
    for i in range(s2.pivot_window + 1, len(df) - 1):
        st = setup2_state(b, pre, i, ld_high, list_date, p, s2)
        if st is not None and float(b.opens[i]) >= p.min_entry_price:
            out.append(i)
    return out


def simulate_control_entry(df: pd.DataFrame, symbol: str, listing_info: dict, i: int, p: Params = Params(),
                           s2: Setup2Params = Setup2Params(), cfg=EOD, slip_pct: float = 0.3,
                           include_open: bool = True) -> Optional[dict]:
    """One control trade: buy bar i's OPEN (plus slippage) with the same stop and exits as S2, no trigger required."""
    ld_high, ld_low, list_date = _listing_context(df, listing_info)
    b = _Bars.of(df)
    pre = _prepare(df)
    st = setup2_state(b, pre, i, ld_high, list_date, p, s2)
    if st is None:
        return None
    fill = float(b.opens[i]) * (1.0 + slip_pct / 100.0)
    stop = _stop_from_history(df, i, fill, p)
    risk = (fill - stop) / fill
    if risk <= 0:
        return None
    s = dict(st, fill=fill, stop=stop, risk=risk, ext=0.0)
    cur = pd.Timestamp(b.dates[i])
    t = dict(entry_idx=i, entry_date=cur, entry_price=fill, signal=_signal_dict(s, ld_high), signal_idx=i, signal_date=cur,
             trailing=stop, risk_pct=risk, max_runup=0.0, path=[], stop_initial=stop, age=st["age"], missed=False)
    trade, ex = _run_trade(b, t, i, i, cfg, p, symbol)
    if trade is None:
        if not include_open:
            return None
        tr = _make_open_trade(t, b, symbol)
        return _decorate(tr, s, i, None)
    trade["open"] = False
    return _decorate(trade, s, i, ex)
