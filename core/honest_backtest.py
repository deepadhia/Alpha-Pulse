# -*- coding: utf-8 -*-
"""
core/honest_backtest.py

A flag-driven per-symbol simulator used to measure how much of the backtest edge
survives when the backtest is forced to follow what the LIVE scanners actually do.

Every behavioural difference between `run_latest_rules_backtest.py` (the "legacy"
backtest) and the live scanners is one switch in `SimConfig`.  With all switches at
their legacy value this module reproduces the legacy backtest trade for trade
(verified by test_honest_backtest.py / run_honest_backtest.py --verify-legacy).
Flipping the switches one at a time gives an attribution "bridge" from the legacy
profit factor to the live-faithful one.

This module is research tooling: it never touches MongoDB, Telegram or the scanners'
state. Rule constants come from `Params`, whose defaults are asserted equal to the live
module constants in test_honest_backtest.py so they cannot silently drift.
"""

from dataclasses import dataclass, replace
from typing import List, Optional

import numpy as np
import pandas as pd

from core.execution_model import is_locked_down, is_locked_up


# --------------------------------------------------------------------------- config

@dataclass(frozen=True)
class Params:
    """Shared rule constants. Defaults mirror the live scanners (asserted in tests)."""
    min_volume: int = 150000
    min_vol_mult: float = 1.8
    max_extension_pct: float = 3.5
    max_age: int = 60
    cooldown_bars: int = 30
    min_close_loc: float = 0.50
    min_risk_reward: float = 1.25
    enforce_winner_cushion: bool = True
    shelf_min_age: int = 3
    shelf_max_age: int = 30
    shelf_min_turnover_cr: float = 10.0
    shelf_max_chase_pct: float = 5.0
    breakout_max_chase_pct: float = 3.5
    min_entry_price: float = 25.0
    dead_money_days_ipo: int = 14
    dead_money_runup_ipo: float = 4.0
    trail_start_pnl: float = 3.0
    min_trail_move_pct: float = 1.0
    ipo_trail_pct: float = 0.10
    winner_runup_pct: float = 15.0
    vol_exhaustion_ratio: float = 0.45
    min_leader_score: int = 5
    min_post_confirm_pct: float = 1.5
    turnover_floor_cr: float = 0.75
    circuit_day_threshold: int = 3
    enable_speed_gates: bool = True
    enable_vol_exit: bool = True
    enable_stagnant_guard: bool = True
    tier_b_enabled: bool = True
    tier_b_max_distance_pct: float = 20.0
    start_date: Optional[str] = None
    # --- research knobs (defaults reproduce live behaviour) ---
    stop_cap_pct: float = 12.0             # hard cap on initial risk
    stop_buffer: float = 0.97              # swing-low buffer
    swing_lookback: int = 15
    exit_overrides: tuple = ()             # ((ExitParams field, value), ...)
    breakeven_runup_pct: float = 0.0       # research: once peak run-up >= this, stop never below entry*(1+lock)
    breakeven_lock_pct: float = 0.0        # 0.0 / 0.0 = off (live behaviour)
    cat_stop_pct: float = 0.0              # research (exit_fill="eod_next_open"): ALSO hold a resting disaster stop this
                                           # far below the alert level; 0.0 = pure close-based alert stop (live behaviour)

    @staticmethod
    def from_live() -> "Params":
        """Bind to the live scanner constants (imports the scanners, so needs the env)."""
        import listing_day_breakout_scanner as lscan
        import streamlined_ipo_scanner as sscan
        return Params(
            min_vol_mult=lscan.MIN_VOLUME_MULTIPLIER,
            max_extension_pct=lscan.MAX_ENTRY_ABOVE_HIGH_PCT,
            max_age=lscan.MAX_DAYS_SINCE_LISTING_FOR_BREAKOUT,
            min_risk_reward=lscan.MIN_RISK_REWARD,
            shelf_min_age=lscan.LISTING_SHELF_MIN_AGE_DAYS,
            shelf_max_age=lscan.LISTING_SHELF_MAX_AGE_DAYS,
            shelf_min_turnover_cr=lscan.LISTING_SHELF_MIN_TURNOVER_CR,
            shelf_max_chase_pct=5.0,
            min_entry_price=sscan.MIN_ENTRY_PRICE_RS,
            dead_money_days_ipo=sscan.DEAD_MONEY_DAYS_IPO,
            dead_money_runup_ipo=sscan.DEAD_MONEY_RUNUP_IPO,
            min_trail_move_pct=sscan.MIN_TRAIL_MOVE_PCT,
            min_leader_score=lscan.LISTING_MIN_LEADER_SCORE,
            min_post_confirm_pct=lscan.LISTING_MIN_POST_CONFIRM_MOVE_PCT,
            circuit_day_threshold=sscan.CIRCUIT_DAY_THRESHOLD,
            tier_b_enabled=lscan.LISTING_TIER_B_ENABLED,
            tier_b_max_distance_pct=lscan.LISTING_TIER_B_MAX_DISTANCE_FROM_HIGH_PCT,
        )


@dataclass(frozen=True)
class SimConfig:
    """
    Fidelity switches. The value shown first is the LEGACY backtest behaviour,
    the value shown second is what the live system does (or the honest assumption).
    """
    shelf_offbyone: bool = True        # True: legacy drops one extra bar before shelf detection
    age_basis: str = "bars"            # "bars" | "calendar"   (live uses calendar days)
    entry_path: str = "legacy"         # "legacy" | "live"     (live gate stack, see _signal_live)
    shelf_turnover: str = "or"         # "or" | "and"          (live: volume spike AND >= Rs10 Cr)
    runup_basis: str = "intraday_high"  # "intraday_high" | "close"
    trail_basis: str = "initial_risk"  # "initial_risk" | "grade_10pct"
    gate_clock: str = "bars"           # "bars" | "live"       (14 calendar days OR 10 sessions)
    exit_fill: str = "stop_price"      # "stop_price" | "gap_aware" | "eod_next_open"
    entry_fill: str = "close"          # "close" | "next_open"
    lock_model: bool = False           # circuit-lock heuristic delays exits / blocks entries


LEGACY = SimConfig()
LIVE_FAITHFUL = SimConfig(
    shelf_offbyone=False, age_basis="calendar", entry_path="live", shelf_turnover="and",
    runup_basis="close", trail_basis="grade_10pct", gate_clock="live",
    exit_fill="gap_aware", entry_fill="next_open", lock_model=True,
)

# Order in which divergences are switched on for the attribution bridge.
BRIDGE_STEPS = [
    ("shelf_offbyone", False, "Shelf detector off-by-one fixed (use yesterday's bar)"),
    ("age_basis", "calendar", "IPO age measured in calendar days (live) not bar index"),
    ("entry_path", "live", "Live gate stack: fresh-cross not required, leader score, "
                          "+1.5% post-confirm, turnover/circuit/price floors, Tier B"),
    ("shelf_turnover", "and", "Shelf needs volume spike AND >= Rs10 Cr (live)"),
    ("runup_basis", "close", "Peak run-up / winner status from closes (live), not intraday highs"),
    ("trail_basis", "grade_10pct", "Stage-1 trail = 10% below price (live), not entry risk %"),
    ("gate_clock", "live", "Dead-money gates on 14 calendar days / 10 sessions (live)"),
    ("entry_fill", "next_open", "Entry fills at next open under the live limit-buy ceiling"),
    ("exit_fill", "gap_aware", "Stop exits fill at min(open, stop), not at the stop"),
    ("lock_model", True, "Lower/upper circuit lock delays exits and blocks entries"),
]


# ----------------------------------------------------------------------- helpers

def _naive(value) -> pd.Timestamp:
    """Timestamp without timezone, normalised to midnight (listing dates arrive tz-aware)."""
    ts = pd.Timestamp(value)
    if ts.tzinfo is not None:
        ts = ts.tz_localize(None)
    return ts.normalize()


def _leader_score(ext_pct, vol_ratio, rr, price, listing_high, listing_range_pct) -> int:
    """Copy of listing_day_breakout_scanner._leader_score (parity asserted in tests)."""
    score = 0
    if vol_ratio >= 2.0:
        score += 2
    elif vol_ratio >= 1.5:
        score += 1
    if price >= listing_high * 1.01:
        score += 2
    elif price >= listing_high:
        score += 1
    if listing_range_pct <= 18:
        score += 2
    elif listing_range_pct <= 28:
        score += 1
    if rr >= 1.8:
        score += 2
    elif rr >= 1.25:
        score += 1
    if ext_pct > 4.0:
        score -= 2
    elif ext_pct > 2.5:
        score -= 1
    return score


def _shelf_detector():
    import listing_day_breakout_scanner as lscan
    return lscan._detect_high_shelf_breakout


def _base_checker():
    import listing_day_breakout_scanner as lscan
    return lscan._evaluate_watchlist_perfect_base, lscan.LISTING_WATCHLIST_BASE_LOOKBACK


def _profit_target_rr(entry, stop, ld_high, ld_low, pivot):
    ld_range = ld_high - ld_low if (ld_low and ld_low > 0) else ld_high * 0.10
    ext_above_pivot = ((entry - pivot) / pivot * 100.0) if pivot > 0 else 0.0
    mult = 1.0 if ext_above_pivot <= 2.0 else (0.75 if ext_above_pivot <= 5.0 else 0.5)
    target = entry + max(ld_range * mult, entry * 0.20)
    risk = entry - stop
    return (target - entry) / risk if risk > 0 else 0.0


def _stop_from_history(df, i, entry, p=None):
    """15-day swing-low stop, 3% buffer, 12% cap, 8% fallback (same as live and legacy)."""
    lookback = p.swing_lookback if p is not None else 15
    buffer = p.stop_buffer if p is not None else 0.97
    cap = (1.0 - p.stop_cap_pct / 100.0) if p is not None else 0.88
    swing_bars = df.iloc[max(0, i - lookback):i]
    swing_low = float(swing_bars["LOW"].min()) if len(swing_bars) > 0 else float(df["LOW"].iat[i])
    struct_stop = swing_low * buffer
    stop = max(struct_stop, entry * cap)
    if stop >= entry or pd.isna(stop):
        stop = entry * 0.92
    return stop


# ------------------------------------------------------------------- entry signals

def _signal_legacy(df, i, ld_high, ld_low, p: Params, cfg: SimConfig, age):
    """Entry logic exactly as run_latest_rules_backtest.simulate_production_latest_system."""
    close = df["CLOSE"].iat[i]; high = df["HIGH"].iat[i]; low = df["LOW"].iat[i]
    vol = df["VOLUME"].iat[i]
    if age > p.max_age:
        return None
    if vol < p.min_volume:
        return None

    completed = df.iloc[:i]
    if len(completed) > 2:
        avg_vol = float(completed["VOLUME"].iloc[2:].tail(10).mean())
    else:
        avg_vol = float(completed["VOLUME"].mean())
    if avg_vol <= 0:
        avg_vol = vol
    vol_ratio = vol / avg_vol

    prev_high = df["HIGH"].iat[i - 1]
    prev_was_below = prev_high <= ld_high
    cand = None
    pivot = ld_high
    shelf_prng = None

    if high > ld_high and prev_was_below:
        ext_pct = (close - ld_high) / ld_high * 100.0
        if ext_pct <= p.max_extension_pct and vol_ratio >= p.min_vol_mult:
            cand = "LISTING_BREAKOUT"

    if cand is None and p.shelf_min_age <= age <= p.shelf_max_age:
        window = df.iloc[:i + 1] if not cfg.shelf_offbyone else completed
        ok, s_high, s_low, s_prng, _ = _shelf_detector()(
            df=window, current_price=close, current_high=high,
            listing_day_high=ld_high, days_since_listing=age)
        if ok:
            turnover = close * vol
            min_turn = p.shelf_min_turnover_cr * 1e7
            if cfg.shelf_turnover == "or":
                passed = turnover >= min_turn or vol_ratio >= 2.0 or (vol_ratio >= 1.5 and s_prng <= 12.0)
            else:
                passed = vol_ratio >= p.min_vol_mult and turnover >= min_turn
            if passed:
                cand = "SHELF_BREAKOUT"
                pivot = s_high
                shelf_prng = s_prng
    if cand is None:
        return None

    candle_range = high - low
    if candle_range <= 0 or (close - low) / candle_range < p.min_close_loc:
        return None
    if p.enforce_winner_cushion:
        ld_range = ld_high - ld_low if (ld_low and ld_low > 0) else ld_high * 0.10
        if (ld_range / ld_high * 100.0) < 5.0:
            return None

    stop = _stop_from_history(df, i, close, p)
    risk_pct = (close - stop) / close
    if risk_pct <= 0 or risk_pct > p.stop_cap_pct / 100.0:
        return None
    rr = _profit_target_rr(close, stop, ld_high, ld_low, pivot)
    if rr < p.min_risk_reward:
        return None
    return dict(type=cand, signal_price=close, stop=stop, risk_pct=risk_pct, pivot=pivot,
                vol_ratio=vol_ratio, ext_pct=(close - pivot) / pivot * 100.0, rr=rr,
                shelf_prng=shelf_prng, max_chase=p.shelf_max_chase_pct if cand == "SHELF_BREAKOUT" else p.breakout_max_chase_pct)


def _signal_live(df, i, ld_high, ld_low, p: Params, cfg: SimConfig, age):
    """
    End-of-day approximation of listing_day_breakout_scanner.check_listing_day_breakout.
    NOT modelled (needs intraday data): the 60-minute hold and the 2.5% pullback ejection.
    """
    if age > p.max_age:   # every live entry type (breakout 60d, tier B 60d, shelf 30d) needs age <= 60
        return None
    close = float(df["CLOSE"].iat[i]); high = float(df["HIGH"].iat[i]); low = float(df["LOW"].iat[i])
    vol = float(df["VOLUME"].iat[i])

    if close < p.min_entry_price:
        return None
    if vol < p.min_volume:
        return None

    upto = df.iloc[:i + 1]
    turnover_cr = float((upto["CLOSE"] * upto["VOLUME"]).tail(20).mean()) / 1e7
    if turnover_cr > 0 and turnover_cr < p.turnover_floor_cr:
        return None
    circuit_days = int(((upto["HIGH"] == upto["LOW"]) & (upto["VOLUME"] > 0)).tail(15).sum())
    if circuit_days >= p.circuit_day_threshold:
        return None

    completed = df.iloc[:i]
    if len(completed) > 2:
        avg_vol = float(completed["VOLUME"].iloc[2:].tail(10).mean())
    elif len(completed) == 2:
        avg_vol = float(completed["VOLUME"].iloc[1])
    else:
        avg_vol = vol
    if avg_vol <= 0:
        avg_vol = vol
    vol_ratio = vol / avg_vol
    volume_spike = vol >= avg_vol * p.min_vol_mult

    listing_range_pct = ((ld_high - ld_low) / ld_high * 100.0) if ld_high > 0 else 0.0
    sig = None

    if high > ld_high:
        entry = max(close, ld_high)
        ext = (entry - ld_high) / ld_high * 100.0
        if ext <= p.max_extension_pct:
            if age > p.max_age or not volume_spike:
                return None
            sig = dict(type="LISTING_BREAKOUT", pivot=ld_high, post_confirm=ext,
                       max_chase=p.breakout_max_chase_pct, shelf_prng=None)
        elif p.shelf_min_age <= age:
            ok, s_high, s_low, s_prng, _ = _shelf_detector()(
                df=upto, current_price=entry, current_high=high,
                listing_day_high=ld_high, days_since_listing=age)
            if not ok:
                return None
            if not volume_spike:
                return None
            if cfg.shelf_turnover == "and" and close * vol < p.shelf_min_turnover_cr * 1e7:
                return None
            if cfg.shelf_turnover == "or" and not (close * vol >= p.shelf_min_turnover_cr * 1e7
                                                   or vol_ratio >= 2.0
                                                   or (vol_ratio >= 1.5 and s_prng <= 12.0)):
                return None
            entry = max(entry, s_high)
            sig = dict(type="SHELF_BREAKOUT", pivot=s_high,
                       post_confirm=(high - s_high) / s_high * 100.0,
                       max_chase=p.shelf_max_chase_pct, shelf_prng=s_prng)
        else:
            return None
    elif high >= ld_high * 0.95:
        return None  # WATCHLIST only, never a trade
    elif p.tier_b_enabled:
        if age > p.max_age:
            return None
        dist_below = (ld_high - high) / ld_high * 100.0
        if dist_below > p.tier_b_max_distance_pct or not volume_spike:
            return None
        base_fn, lookback = _base_checker()
        ok_b, _, _ = base_fn(upto, lookback, float(ld_high), float(high), proximity_check=False)
        if not ok_b:
            return None
        tail_b = upto.tail(lookback)
        base_high = float(tail_b["HIGH"].iloc[:-1].max()) if len(tail_b) > 1 else float(tail_b["HIGH"].max())
        if high <= base_high:
            return None
        entry = close
        sig = dict(type="BASE_BREAKOUT", pivot=base_high,
                   post_confirm=(high - base_high) / base_high * 100.0,
                   max_chase=2.0, shelf_prng=None)
    else:
        return None

    # strict body gate
    candle_range = high - low
    if candle_range > 0 and (close - low) / candle_range < p.min_close_loc:
        return None

    stop = _stop_from_history(df, i, entry, p)
    risk_pct = (entry - stop) / entry
    if risk_pct <= 0:
        return None
    rr = _profit_target_rr(entry, stop, ld_high, ld_low, sig["pivot"])
    if rr < p.min_risk_reward:
        return None

    if sig["type"] == "LISTING_BREAKOUT":
        ext = sig["post_confirm"]
        if _leader_score(ext, vol_ratio, rr, close, ld_high, listing_range_pct) < p.min_leader_score:
            return None
        if listing_range_pct > 0 and listing_range_pct < 5.0:
            return None
    if sig["post_confirm"] < p.min_post_confirm_pct:
        return None
    if sig["type"] == "SHELF_BREAKOUT" and vol_ratio < 1.5:
        return None

    sig.update(signal_price=entry, stop=stop, risk_pct=risk_pct, vol_ratio=vol_ratio,
               ext_pct=(entry - sig["pivot"]) / sig["pivot"] * 100.0, rr=rr)
    return sig


# --------------------------------------------------------------------- simulation

@dataclass
class _Bars:
    dates: np.ndarray
    opens: np.ndarray
    highs: np.ndarray
    lows: np.ndarray
    closes: np.ndarray
    vols: np.ndarray

    @staticmethod
    def of(df: pd.DataFrame) -> "_Bars":
        return _Bars(
            dates=df["DATE"].values,
            opens=df["OPEN"].values if "OPEN" in df.columns else df["CLOSE"].values,
            highs=df["HIGH"].values, lows=df["LOW"].values,
            closes=df["CLOSE"].values, vols=df["VOLUME"].values,
        )


def _listing_context(df, listing_info):
    ld_high = listing_info.get("listing_day_high")
    ld_low = listing_info.get("listing_day_low")
    if not ld_high or ld_high <= 0:
        ld_high = float(df["HIGH"].iloc[:3].max())
        ld_low = float(df["LOW"].iloc[:3].min())
    _ld = listing_info.get("listing_date")
    list_date = _naive(_ld) if (_ld is not None and not pd.isna(_ld)) else _naive(df["DATE"].values[0])
    return float(ld_high), (float(ld_low) if ld_low else ld_low), list_date


def _open_trade(b: _Bars, i: int, sig: dict, cfg: SimConfig, age: int):
    """
    Turn a signal on bar i into an open-trade state according to the entry-fill model.
    Returns (trade_state, first_bar_to_manage, day0_bar) or None when the order would not fill.
    """
    n = len(b.closes)
    cur_date = pd.Timestamp(b.dates[i])
    stop = sig["stop"]
    if cfg.entry_fill == "close":
        t = dict(entry_idx=i, entry_date=cur_date, entry_price=sig["signal_price"], signal=sig,
                 signal_idx=i, signal_date=cur_date, trailing=stop, risk_pct=sig["risk_pct"],
                 max_runup=0.0, path=[], stop_initial=stop, age=age, missed=False)
        return t, i + 1, None

    if i + 1 >= n:
        return None
    o1, h1, l1, c1 = float(b.opens[i + 1]), float(b.highs[i + 1]), float(b.lows[i + 1]), float(b.closes[i + 1])
    limit_ceiling = sig["pivot"] * (1.0 + sig["max_chase"] / 100.0)
    limit_price = max(sig["signal_price"] * 1.02, limit_ceiling)
    blocked = (o1 > limit_price) or (o1 <= stop) or \
              (cfg.lock_model and is_locked_up(o1, h1, l1, c1, b.closes[i]))
    if blocked:
        return None
    t = dict(entry_idx=i + 1, entry_date=pd.Timestamp(b.dates[i + 1]), entry_price=o1, signal=sig,
             signal_idx=i, signal_date=cur_date, trailing=stop,
             risk_pct=(o1 - stop) / o1, max_runup=0.0, path=[], stop_initial=stop, age=age, missed=False)
    return t, i + 1, i + 1


def _uses_shared_exit_rules(cfg: SimConfig, p: Params) -> bool:
    """The live exit configuration is delegated to core.exit_rules (single source of truth)."""
    return (cfg.gate_clock == "live" and cfg.trail_basis == "grade_10pct" and cfg.runup_basis == "close"
            and p.enable_speed_gates and p.enable_vol_exit and p.enable_stagnant_guard)


def _exit_params(p: Params):
    from core.exit_rules import ExitParams
    kwargs = dict(
        winner_runup_pct=p.winner_runup_pct,
        dead_money_days_ipo=p.dead_money_days_ipo, dead_money_runup_ipo=p.dead_money_runup_ipo,
        vol_ratio_threshold=p.vol_exhaustion_ratio,
        listing_trail_start_pnl=p.trail_start_pnl, min_trail_move_pct=p.min_trail_move_pct,
    )
    kwargs.update(dict(p.exit_overrides))
    return ExitParams(**kwargs)


def _run_trade(b: _Bars, t: dict, j0: int, day0_bar, cfg: SimConfig, p: Params, symbol: str):
    """
    Manage one open trade from bar j0 until it exits.
    Returns (trade_dict, exit_bar_index), or (None, None) if the data ends with the trade still open.
    """
    n = len(b.closes)
    pending_exit = None
    shared = _uses_shared_exit_rules(cfg, p)
    ex_params = _exit_params(p) if shared else None
    j = j0
    while j < n:
        cur_date = pd.Timestamp(b.dates[j])
        o, h, l, c = b.opens[j], b.highs[j], b.lows[j], b.closes[j]
        prev_close = b.closes[j - 1]
        days_bars = j - t["entry_idx"]
        days_cal = int((cur_date - t["entry_date"]).days)

        # a previously triggered stop/exit executes at the first tradable bar
        if pending_exit is not None:
            if cfg.lock_model and is_locked_down(o, h, l, c, prev_close):
                t["path"].append((cur_date, float(c), t["trailing"]))
                j += 1
                continue
            return _make_trade(t, j, b.dates, float(o), pending_exit, symbol), j

        if day0_bar == j:
            # fill-day handling: only the protective stop can act on the fill day
            hit = (l <= t["trailing"]) if cfg.exit_fill != "eod_next_open" else (c <= t["trailing"])
            t["path"].append((cur_date, float(c), t["trailing"]))
            if cfg.exit_fill == "eod_next_open" and p.cat_stop_pct > 0.0:
                cat = t["trailing"] * (1.0 - p.cat_stop_pct / 100.0)
                if l <= cat:
                    return _make_trade(t, j, b.dates, min(float(o), cat), "Disaster Stop (resting)", symbol), j
            if hit:
                if cfg.exit_fill == "eod_next_open":
                    pending_exit = "Stop Loss (EOD alert)"
                else:
                    px = min(float(o), t["trailing"]) if cfg.exit_fill == "gap_aware" else t["trailing"]
                    return _make_trade(t, j, b.dates, px, "Trailing Stop Loss", symbol), j
            day0_bar = None
            j += 1
            continue

        pnl_close = (c - t["entry_price"]) / t["entry_price"] * 100.0
        if cfg.runup_basis == "intraday_high":
            t["max_runup"] = max(t["max_runup"], (h - t["entry_price"]) / t["entry_price"] * 100.0)
        else:
            t["max_runup"] = max(t["max_runup"], pnl_close)
        winner = t["max_runup"] >= p.winner_runup_pct

        exit_reason, exit_price, defer = None, c, False

        # 1. stop
        if cfg.exit_fill == "stop_price":
            if l <= t["trailing"]:
                exit_reason, exit_price = "Trailing Stop Loss", t["trailing"]
        elif cfg.exit_fill == "gap_aware":
            if l <= t["trailing"]:
                exit_reason = "Trailing Stop Loss"
                exit_price = min(float(o), t["trailing"])
                if cfg.lock_model and is_locked_down(o, h, l, c, prev_close):
                    defer = True
        else:  # eod_next_open
            cat = t["trailing"] * (1.0 - p.cat_stop_pct / 100.0) if p.cat_stop_pct > 0.0 else None
            if cat is not None and l <= cat:
                exit_reason, exit_price = "Disaster Stop (resting)", min(float(o), cat)
            elif c <= t["trailing"]:
                exit_reason, defer = "Stop Loss (EOD alert)", True

        decision = None
        if exit_reason is None and shared:
            # live rules: one definition, shared with production (core/exit_rules.py)
            from core.exit_rules import evaluate_position
            decision = evaluate_position(
                entry_price=t["entry_price"], old_trailing=t["trailing"], current_price=float(c),
                days_held=days_cal, trading_sessions=days_bars,
                prev_max_runup=t["max_runup"], prev_max_drawdown=0.0,
                grade_exit="LISTING_BREAKOUT", grade_trail="LISTING_BREAKOUT",
                volume_provider=lambda: b.vols[t["entry_idx"] + 1:j + 1],
                params=ex_params, check_stop=False)
            exit_reason = decision.exit_reason
        elif exit_reason is None and not winner:
            # 2. legacy-clock time / dead-money gates (only reachable in bridge configurations)
            if cfg.gate_clock == "bars":
                dh = days_bars
                vel_hit = days_bars >= 14
            else:
                dh = days_cal
                vel_hit = (days_cal >= 14 or days_bars >= 10)
            if p.enable_speed_gates and vel_hit and pnl_close <= 0.0:
                exit_reason = "Time Stop - 14-Day Velocity Gate"
            elif p.enable_speed_gates and dh >= p.dead_money_days_ipo and t["max_runup"] < p.dead_money_runup_ipo:
                exit_reason = "Time Stop - IPO Dead Money"
            elif (p.enable_vol_exit and dh >= 15 and -3.0 <= pnl_close < 5.0 and t["max_runup"] < 8.0):
                post = b.vols[t["entry_idx"] + 1:j + 1]
                if len(post) >= 16:
                    base = float(np.mean(post[:11])); recent = float(np.mean(post[-5:]))
                    if base >= 50000 and (recent / base) < p.vol_exhaustion_ratio:
                        exit_reason = "Volume Exhaustion Exit"
            if exit_reason is None and p.enable_stagnant_guard and dh >= 40 and pnl_close <= 0.0 and t["max_runup"] < 8.0:
                exit_reason = "Time Stop - 40d Stagnant Guard"
            if exit_reason is None and dh > 30 and c < t["entry_price"] * 0.95:
                exit_reason = "Time Stop -5% (30d)"
            if exit_reason is None and cfg.gate_clock == "live" and dh > 60 and c < t["entry_price"] * 0.92:
                exit_reason = "Time Stop -8% (60d)"

        t["path"].append((cur_date, float(c), t["trailing"]))

        if exit_reason:
            if defer:
                pending_exit = exit_reason
                j += 1
                continue
            return _make_trade(t, j, b.dates, float(exit_price), exit_reason, symbol), j

        # 3. trailing stop update (after the exit checks, applies from the next bar)
        if decision is not None:
            t["trailing"] = decision.new_trailing
        elif winner:
            peak_price = t["entry_price"] * (1.0 + t["max_runup"] / 100.0)
            cand_trail = max(t["entry_price"] * 1.05, peak_price * 0.80)
            if cfg.trail_basis == "initial_risk":
                t["trailing"] = max(t["trailing"], cand_trail)
            elif cand_trail > t["trailing"] and (cand_trail - t["trailing"]) >= t["entry_price"] * p.min_trail_move_pct / 100.0:
                t["trailing"] = cand_trail
        elif pnl_close >= p.trail_start_pnl:
            if cfg.trail_basis == "initial_risk":
                t["trailing"] = max(t["trailing"], c * (1.0 - t["risk_pct"]))
            else:
                cand_trail = c * (1.0 - p.ipo_trail_pct)
                if cand_trail > t["trailing"] and (cand_trail - t["trailing"]) >= t["entry_price"] * p.min_trail_move_pct / 100.0:
                    t["trailing"] = cand_trail
        if p.breakeven_runup_pct > 0.0 and t["max_runup"] >= p.breakeven_runup_pct:
            t["trailing"] = max(t["trailing"], t["entry_price"] * (1.0 + p.breakeven_lock_pct / 100.0))
        t["path"][-1] = (cur_date, float(c), t["trailing"])
        j += 1
    return None, None


def precompute_signals(df: pd.DataFrame, listing_info: dict, cfg: SimConfig, p: Params) -> dict:
    """{bar index: signal} for every bar that produces a signal (independent of position state)."""
    n = len(df)
    out = {}
    if n < 15:
        return out
    ld_high, ld_low, list_date = _listing_context(df, listing_info)
    for i in range(3, n):
        age = i if cfg.age_basis == "bars" else int((pd.Timestamp(df["DATE"].values[i]) - list_date).days)
        if age > p.max_age:
            break
        sig = (_signal_legacy if cfg.entry_path == "legacy" else _signal_live)(df, i, ld_high, ld_low, p, cfg, age)
        if sig is not None:
            out[i] = sig
    return out


def _make_open_trade(t: dict, b: _Bars, symbol: str) -> dict:
    """
    A position still open when the data ends, marked to the last close and flagged open=True.
    Right-censored trades must never be dropped: they are the current runners (e.g. KSHINTL +183%).
    """
    last = len(b.closes) - 1
    trade = _make_trade(t, last, b.dates, float(b.closes[last]), "OPEN", symbol)
    trade["open"] = True
    return trade


def simulate_symbol(df: pd.DataFrame, symbol: str, listing_info: dict,
                    cfg: SimConfig = LEGACY, p: Params = Params(), signals: Optional[dict] = None,
                    include_open: bool = False) -> List[dict]:
    """
    Simulate one symbol, taking every signal in turn (paper book). Returns trade dicts.
    `signals` (from precompute_signals) skips signal generation when only exit rules change.
    `include_open=True` appends a position still open at the end of the data, marked to market
    and flagged `open=True` (default False keeps the legacy-parity behaviour of dropping it).
    """
    trades: List[dict] = []
    n = len(df)
    if n < 15:
        return trades
    if listing_info.get("is_nse_addition") and not listing_info.get("bse_liquidity_passed", True):
        return trades

    ld_high, ld_low, list_date = _listing_context(df, listing_info)
    b = _Bars.of(df)
    start_filter = pd.to_datetime(p.start_date) if p.start_date else None

    def age_of(i):
        return i if cfg.age_basis == "bars" else int((pd.Timestamp(b.dates[i]) - list_date).days)

    last_exit_idx = -100
    i = 3
    while i < n:
        cur_date = pd.Timestamp(b.dates[i])
        if start_filter is not None and cur_date < start_filter:
            i += 1
            continue
        if (i - last_exit_idx) < p.cooldown_bars:
            i += 1
            continue
        age = age_of(i)
        if signals is not None:
            sig = signals.get(i)
        elif cfg.entry_path == "legacy":
            sig = _signal_legacy(df, i, ld_high, ld_low, p, cfg, age)
        else:
            sig = _signal_live(df, i, ld_high, ld_low, p, cfg, age)
        if sig is None:
            i += 1
            continue
        opened = _open_trade(b, i, sig, cfg, age)
        if opened is None:
            i += 1
            continue
        t, j0, day0 = opened
        trade, exit_idx = _run_trade(b, t, j0, day0, cfg, p, symbol)
        if trade is None:       # data ended with the position still open
            if include_open:
                trades.append(_make_open_trade(t, b, symbol))
            break
        trade["open"] = False
        trades.append(trade)
        last_exit_idx = exit_idx
        i = exit_idx + 1
    return trades


def simulate_events(df: pd.DataFrame, symbol: str, listing_info: dict,
                    cfg: SimConfig = LIVE_FAITHFUL, p: Params = Params(),
                    signals: Optional[dict] = None) -> List[dict]:
    """
    Research helper: evaluate EVERY bar that produces a signal (no one-trade-at-a-time limit,
    no cooldown) and return the live-exit outcome of each as an independent event.
    Used with loosened gates to study which features predict the outcome.
    """
    events: List[dict] = []
    n = len(df)
    if n < 15:
        return events
    if listing_info.get("is_nse_addition") and not listing_info.get("bse_liquidity_passed", True):
        return events
    ld_high, ld_low, list_date = _listing_context(df, listing_info)
    b = _Bars.of(df)
    for i in range(3, n):
        age = i if cfg.age_basis == "bars" else int((pd.Timestamp(b.dates[i]) - list_date).days)
        if age > p.max_age:
            break
        if signals is not None:
            sig = signals.get(i)
        elif cfg.entry_path == "legacy":
            sig = _signal_legacy(df, i, ld_high, ld_low, p, cfg, age)
        else:
            sig = _signal_live(df, i, ld_high, ld_low, p, cfg, age)
        if sig is None:
            continue
        opened = _open_trade(b, i, sig, cfg, age)
        if opened is None:
            continue
        t, j0, day0 = opened
        trade, _ = _run_trade(b, t, j0, day0, cfg, p, symbol)
        if trade is None:
            continue
        trade["signal_idx"] = i
        trade["ld_high"] = ld_high
        trade["ld_low"] = ld_low
        trade["list_date"] = list_date.strftime("%Y-%m-%d")
        events.append(trade)
    return events


def _make_trade(t, i, dates, exit_price, reason, symbol):
    e = t["entry_price"]
    pnl = (exit_price - e) / e * 100.0
    exit_date = pd.Timestamp(dates[i])
    sig = t["signal"]
    return {
        "symbol": symbol,
        "type": sig["type"],
        "setup_type": sig["type"],
        "signal_date": t["signal_date"].strftime("%Y-%m-%d"),
        "entry_date": t["entry_date"].strftime("%Y-%m-%d"),
        "exit_date": exit_date.strftime("%Y-%m-%d"),
        "year": t["entry_date"].year,
        "days_held": i - t["entry_idx"],
        "days_held_cal": int((exit_date - t["entry_date"]).days),
        "days_since_listing": t["age"],
        "entry_price": float(e),
        "signal_price": float(sig["signal_price"]),
        "stop_initial": float(t["stop_initial"]),
        "initial_risk_pct": float(t["risk_pct"] * 100.0),
        "exit_price": float(exit_price),
        "pnl_pct": float(pnl),
        "max_runup_pct": float(t["max_runup"]),
        "exit_reason": reason,
        "win": pnl > 0,
        "vol_ratio": float(sig.get("vol_ratio", 0.0)),
        "ext_pct": float(sig.get("ext_pct", 0.0)),
        "post_confirm": float(sig.get("post_confirm", 0.0)) if sig.get("post_confirm") is not None else 0.0,
        "shelf_prng": sig.get("shelf_prng"),
        "rr": float(sig.get("rr", 0.0)),
        "path": list(t["path"]),
    }


# ------------------------------------------------------- intraday-crossing entry model
#
# Why: the live scanner decides on the CURRENT price when it fires (entry = max(price, listing high),
# post-confirm and extension gates on that price) and buys during the signal day.  `_signal_live`
# above decides on the day's CLOSE and buys the NEXT open, which rejects strong breakout days
# (closes more than 3.5% over the pivot) and keeps the ones that faded.  This model fills at the
# crossing price instead.  Reference: LEARNINGS.md sections 7-8, research_intraday_proxy.py.
#
# Not modelled (needs intraday bars): the 60-minute hold and the 2.5% pullback ejection.
# End-of-day volume is >= volume at trigger time, so the volume gate is somewhat generous.

def cross_signal_at(df: pd.DataFrame, b: _Bars, i: int, ld_high: float, rng_pct: float, age: int,
                    p: Params, vol_gate: bool = True, body_gate: bool = False, slip_pct: float = 0.0,
                    fill_cap_pct: Optional[float] = None, causal: bool = False) -> Optional[dict]:
    """
    LISTING_BREAKOUT signal on bar i under intraday-crossing fills, or None.

      trigger = ld_high * (1 + min_post_confirm_pct)               (live: +1.5%)
      skip if the day opens above ld_high * (1 + cap)              (cap = fill_cap_pct or max_extension_pct)
      fill    = max(open, trigger) * (1 + slip)                    (slip models chasing after the hold)

    `fill_cap_pct` models a resting stop-limit order: gap-ups above the limit are NOT filled.
    No condition on the day's close is applied (body_gate=True adds the EOD body gate as a
    HINDSIGHT variant that is not achievable live).

    `causal=True` removes every use of same-day volume (floor, spike gate, leader-score volume term): the
    prior bar's volume stands in.  That is the information a pre-placed resting order actually has.  The
    default (False) uses end-of-day volume, which is what the live scanner approximates mid-day.
    """
    o, h, l, c, v = (float(b.opens[i]), float(b.highs[i]), float(b.lows[i]),
                     float(b.closes[i]), float(b.vols[i]))
    trig = ld_high * (1 + p.min_post_confirm_pct / 100.0)
    cap = p.max_extension_pct if fill_cap_pct is None else fill_cap_pct
    ceil = ld_high * (1 + cap / 100.0)
    if h < trig or o > ceil:
        return None
    entry = max(o, trig)
    if causal:
        v = float(b.vols[i - 1])      # prior bar: the only volume known before the order triggers
    if entry > ceil or entry < p.min_entry_price or v < p.min_volume:
        return None
    upto = df.iloc[:i]
    turn = float((upto["CLOSE"] * upto["VOLUME"]).tail(20).mean()) / 1e7 if len(upto) else 0.0
    circ = int(((upto["HIGH"] == upto["LOW"]) & (upto["VOLUME"] > 0)).tail(15).sum())
    if (turn > 0 and turn < p.turnover_floor_cr) or circ >= p.circuit_day_threshold:
        return None
    ref = df.iloc[:i - 1] if causal else upto
    if len(ref) > 2:
        avg_vol = float(ref["VOLUME"].iloc[2:].tail(10).mean())
    elif len(ref) == 2:
        avg_vol = float(ref["VOLUME"].iloc[1])
    else:
        avg_vol = v
    avg_vol = avg_vol if avg_vol > 0 else v
    vr = v / avg_vol
    if vol_gate and vr < p.min_vol_mult:
        return None
    if body_gate and not causal and h > l and (c - l) / (h - l) < p.min_close_loc:
        return None
    if 0 < rng_pct < 5.0:
        return None
    fill = entry * (1 + slip_pct / 100.0)
    stop = _stop_from_history(df, i, fill, p)
    risk = (fill - stop) / fill
    if risk <= 0:
        return None
    return dict(entry=entry, fill=fill, stop=stop, risk=risk, vr=vr, turn=turn, trig=trig,
                ext=(entry - ld_high) / ld_high * 100.0)


def cross_entry(df: pd.DataFrame, b: _Bars, i: int, ld_high: float, ld_low, rng_pct: float, age: int,
                p: Params, vol_gate: bool = True, body_gate: bool = False, slip_pct: float = 0.0,
                fill_cap_pct: Optional[float] = None, causal: bool = False) -> Optional[dict]:
    """Every live gate for a listing breakout on bar i under intraday-crossing fills (signal + R:R + leader score)."""
    s = cross_signal_at(df, b, i, ld_high, rng_pct, age, p, vol_gate, body_gate, slip_pct, fill_cap_pct, causal)
    if s is None:
        return None
    rr = _profit_target_rr(s["fill"], s["stop"], ld_high, ld_low, ld_high)
    if rr < p.min_risk_reward:
        return None
    if _leader_score(s["ext"], s["vr"], rr, s["entry"], ld_high, rng_pct) < p.min_leader_score:
        return None
    s["rr"] = rr
    return s


def shelf_cross_entry(df: pd.DataFrame, b: _Bars, i: int, ld_high: float, ld_low, age: int, p: Params,
                      vol_gate: bool = True, slip_pct: float = 0.0, fill_cap_pct: Optional[float] = None,
                      causal: bool = False) -> Optional[dict]:
    """
    SHELF_BREAKOUT (3-8 bar tight shelf above / near the listing high, days 3-30) under intraday-crossing fills.

    The shelf geometry comes from the live detector (`_detect_high_shelf_breakout`, called with a price that always
    qualifies so only the shelf itself is tested).  The trigger is the shelf high + the live post-confirm move (1.5%); the
    fill cap is relative to the shelf high (live chase limit 5%).  Gates that need today's volume (the live 1.8x spike and
    Rs 10 Cr day turnover) are replaced in causal mode by the prior bar's volume and the 20-day turnover floor.
    """
    if age < p.shelf_min_age or age > p.shelf_max_age or i < 4:
        return None
    window = df.iloc[:i + 1]
    ok, s_high, s_low, s_prng, _ = _shelf_detector()(
        df=window, current_price=1e12, current_high=1e12, listing_day_high=ld_high, days_since_listing=age,
        max_entry_above_pivot_pct=1e12)
    if not ok:
        return None
    o, h, l, c, v = (float(b.opens[i]), float(b.highs[i]), float(b.lows[i]), float(b.closes[i]), float(b.vols[i]))
    trig = s_high * (1 + p.min_post_confirm_pct / 100.0)
    cap = p.shelf_max_chase_pct if fill_cap_pct is None else fill_cap_pct
    ceil = s_high * (1 + cap / 100.0)
    if h < trig or o > ceil:
        return None
    entry = max(o, trig)
    v_eff = float(b.vols[i - 1]) if causal else v
    if entry > ceil or entry < p.min_entry_price or v_eff < p.min_volume:
        return None
    upto = df.iloc[:i]
    turn = float((upto["CLOSE"] * upto["VOLUME"]).tail(20).mean()) / 1e7 if len(upto) else 0.0
    circ = int(((upto["HIGH"] == upto["LOW"]) & (upto["VOLUME"] > 0)).tail(15).sum())
    if (turn > 0 and turn < p.turnover_floor_cr) or circ >= p.circuit_day_threshold:
        return None
    if not causal and c * v < p.shelf_min_turnover_cr * 1e7:
        return None
    ref = df.iloc[:i - 1] if causal else upto
    if len(ref) > 2:
        avg_vol = float(ref["VOLUME"].iloc[2:].tail(10).mean())
    elif len(ref) == 2:
        avg_vol = float(ref["VOLUME"].iloc[1])
    else:
        avg_vol = v_eff
    avg_vol = avg_vol if avg_vol > 0 else v_eff
    vr = v_eff / avg_vol
    if vol_gate and vr < p.min_vol_mult:
        return None
    if vol_gate and vr < 1.5:
        return None
    fill = entry * (1 + slip_pct / 100.0)
    stop = _stop_from_history(df, i, fill, p)
    risk = (fill - stop) / fill
    if risk <= 0:
        return None
    rr = _profit_target_rr(fill, stop, ld_high, ld_low, s_high)
    if rr < p.min_risk_reward:
        return None
    return dict(entry=entry, fill=fill, stop=stop, risk=risk, vr=vr, turn=turn, trig=trig, rr=rr, pivot=s_high,
                ext=(entry - s_high) / s_high * 100.0, setup_type="SHELF_BREAKOUT", shelf_prng=s_prng)


def preview_resting_entry(df: pd.DataFrame, listing_info: dict, today, p: Params = Params(),
                          slip_pct: float = 0.3, fill_cap_pct: float = 2.5, min_age: int = 5) -> Optional[dict]:
    """
    Pre-market eligibility of a resting stop-limit BUY for `today`, using ONLY information known before the
    open (df = completed sessions up to the previous close).  It runs the same gates as the backtest
    (`cross_entry`, causal mode) on a synthetic bar that prints exactly at the trigger, so the order sheet
    and the research engine cannot drift apart.

    Returns None when not eligible, else dict(trigger, limit, fill, stop, risk_pct, rr, age, ld_high,
    last_close, already_above_trigger).
    """
    n = len(df)
    if n < 15:
        return None
    if listing_info.get("is_nse_addition") and not listing_info.get("bse_liquidity_passed", True):
        return None
    ld_high, ld_low, list_date = _listing_context(df, listing_info)
    today = _naive(today)
    age = int((today - list_date).days)
    if age < min_age or age > p.max_age:
        return None
    last_close = float(df["CLOSE"].iat[-1])
    trig = ld_high * (1 + p.min_post_confirm_pct / 100.0)
    cap_price = ld_high * (1 + fill_cap_pct / 100.0)
    if last_close > cap_price:
        return None                      # already extended beyond the fill cap: a limit buy could not fill
    last_vol = float(df["VOLUME"].iat[-1])
    row = {c: df[c].iat[-1] for c in df.columns}
    row.update(DATE=today, OPEN=trig, HIGH=trig, LOW=trig, CLOSE=trig, VOLUME=last_vol)
    df2 = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    b = _Bars.of(df2)
    rng_pct = (ld_high - ld_low) / ld_high * 100.0 if ld_low else 0.0
    s = cross_entry(df2, b, len(df2) - 1, ld_high, ld_low, rng_pct, age, p, vol_gate=False, body_gate=False,
                    slip_pct=slip_pct, fill_cap_pct=fill_cap_pct, causal=True)
    if s is None:
        return None
    return dict(trigger=trig, limit=cap_price, fill=s["fill"], stop=s["stop"], risk_pct=s["risk"] * 100.0,
                rr=s["rr"], age=age, ld_high=ld_high, last_close=last_close,
                already_above_trigger=last_close >= trig)


def simulate_symbol_cross(df: pd.DataFrame, symbol: str, listing_info: dict, p: Params = Params(),
                          cfg: SimConfig = LIVE_FAITHFUL, vol_gate: bool = True, body_gate: bool = False,
                          slip_pct: float = 0.0, fill_cap_pct: Optional[float] = None, min_age: int = 0,
                          include_open: bool = True, signal_filter=None, cooldown: bool = True,
                          causal: bool = False, setup: str = "listing") -> List[dict]:
    """
    LISTING_BREAKOUT trades under intraday-crossing fills: one position at a time per symbol,
    `p.cooldown_bars` bars between a close and the next entry.  Trades still open at the end of
    the data are returned flagged `open=True` (marked to the last close) when include_open.

    `signal_filter(ctx) -> bool` is an optional extra entry rule, evaluated only on bars that already
    passed every live gate; ctx has symbol, i, age, date, vr, ext, fill, stop, risk, rr, df, ld_high.
    `setup="shelf"` trades SHELF_BREAKOUT instead of LISTING_BREAKOUT (see `shelf_cross_entry`).
    """
    out: List[dict] = []
    n = len(df)
    if n < 15:
        return out
    if listing_info.get("is_nse_addition") and not listing_info.get("bse_liquidity_passed", True):
        return out
    ld_high, ld_low, list_date = _listing_context(df, listing_info)
    b = _Bars.of(df)
    rng_pct = (ld_high - ld_low) / ld_high * 100.0 if ld_low else 0.0
    last_exit = -100
    i = 3
    while i < n:
        age = int((pd.Timestamp(b.dates[i]) - list_date).days)
        if age > p.max_age:
            break
        if (cooldown and (i - last_exit) < p.cooldown_bars) or age < min_age:
            i += 1
            continue
        if setup == "shelf":
            s = shelf_cross_entry(df, b, i, ld_high, ld_low, age, p, vol_gate, slip_pct, fill_cap_pct, causal)
        else:
            s = cross_entry(df, b, i, ld_high, ld_low, rng_pct, age, p, vol_gate, body_gate, slip_pct, fill_cap_pct, causal)
        if s is None:
            i += 1
            continue
        rr = s["rr"]
        pivot = s.get("pivot", ld_high)
        setup_type = s.get("setup_type", "LISTING_BREAKOUT")
        if signal_filter is not None and not signal_filter(dict(
                symbol=symbol, i=i, age=age, date=pd.Timestamp(b.dates[i]), vr=s["vr"], ext=s["ext"],
                fill=s["fill"], stop=s["stop"], risk=s["risk"], rr=rr, df=df, ld_high=ld_high,
                turn=s["turn"], rng_pct=rng_pct)):
            i += 1
            continue
        cur = pd.Timestamp(b.dates[i])
        sig = dict(type=setup_type, pivot=pivot, post_confirm=s["ext"],
                   max_chase=p.breakout_max_chase_pct, signal_price=s["fill"], vol_ratio=s["vr"],
                   ext_pct=s["ext"], rr=rr, shelf_prng=s.get("shelf_prng"))
        t = dict(entry_idx=i, entry_date=cur, entry_price=s["fill"], signal=sig, signal_idx=i,
                 signal_date=cur, trailing=s["stop"], risk_pct=s["risk"], max_runup=0.0, path=[],
                 stop_initial=s["stop"], age=age, missed=False)
        trade, ex = _run_trade(b, t, i, i, cfg, p, symbol)   # day0_bar=i: only the protective stop acts on fill day
        if trade is None:
            if include_open:
                tr = _make_open_trade(t, b, symbol)
                tr["entry_idx"] = i
                out.append(tr)
            break
        trade["open"] = False
        trade["entry_idx"] = i
        trade["exit_idx"] = ex
        out.append(trade)
        last_exit = ex
        i = ex + 1
    return out
