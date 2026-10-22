# -*- coding: utf-8 -*-
"""
core/setup3.py

RESEARCH ONLY (2026-10-22).  A third, separate entry setup, "S3: late breakout of the post-listing high", for the slow IPO leaders that
policy P (age <= 60 days) cannot hold: RUBICON, CORONA, SUDEEPPHRM, MOLBIO-type names whose big move started 3-9 months after listing, after a
long base under the highest price printed since listing.  Pre-registration and the adoption bar: research_setup3.py (docstring).

Differences from core/setup2.py: (1) the pivot is the highest HIGH of ALL completed sessions since listing (so it is at or above the listing
high), not the last 10 sessions; (2) the stock must be within 10% below that pivot, not more than 10% below the listing high; (3) age
window 61-400 calendar days.  Everything else (liquidity gates, resting stop-limit entry, stop, exits, cooldown, costs) is setup2's.

Information rule (AGENTS.md rule 21): everything is decided from bars up to the PRIOR close; bar i supplies only the open and the high.
Nothing here is imported by a scanner, changes a default, or touches MongoDB.
"""

from dataclasses import dataclass
from typing import List, Optional

import numpy as np
import pandas as pd

from core.honest_backtest import (Params, _Bars, _listing_context, _make_open_trade, _naive, _run_trade, _stop_from_history)
from core.setup2 import EOD, _prepare, _Pre, clean_universe  # noqa: F401  (re-exported for the research script)

# X1 exits (LEARNINGS 20): the protective stop and the trailing ratchet only; every time-based exit is switched off.
NO_TIME_EXITS = (("dead_money_days_ipo", 9999), ("dead_money_days_consol", 9999), ("velocity_days", 9999), ("velocity_sessions", 9999),
                 ("stagnant_days", 9999), ("vol_min_days_ipo", 9999), ("vol_min_days_consol", 9999), ("dead_money_days_other", 9999),
                 ("long_underwater_days", 9999))
PAPER_MAX_TURN_CR = 25.0          # owner decision 2026-10-22: PAPER_ONLY S3 trades only names with 20-session mean turnover <= Rs 25 Cr


@dataclass(frozen=True)
class Setup3Params:
    min_age_days: int = 61               # policy P owns ages <= 60
    max_age_days: int = 400
    pivot_min_sessions_old: int = 5      # the pivot session index <= i - (this + 1): a base, not a run
    long_base_sessions: int = 40         # LONG base when the pivot is at least this many sessions old
    proximity_frac: float = 0.10         # prior close at or above pivot x (1 - this): the order-sheet feasibility rule
    trigger_pct: float = 1.5
    cap_pct: float = 2.5
    max_turn_cr: Optional[float] = None  # None = research default (no cap); PAPER_PARAMS caps the 20-session mean turnover (Rs crore)


PAPER_PARAMS = Setup3Params(max_turn_cr=PAPER_MAX_TURN_CR)

# Version of the PAPER_ONLY S3 book's RULES (independent of SCANNER_VERSION, which versions policy P and the live scanners).  Every stored order,
# position and heartbeat carries it, and the scorecard judges one version only, so a rule change can never blend into an old forward sample.
# Change any paper rule (turnover cap, trigger, limit, age window, pivot age, proximity, X1 exits, slippage assumption) => bump this string, record
# the change in CHANGELOG.md, and update S3_RULES_FINGERPRINT_V1 in test_s3_paper.py (the test fails until you do).
S3_PAPER_VERSION = "1.0.0"
S3_PAPER_COHORT = ("1.0.0",)


def paper_rules_fingerprint() -> str:
    """Short stable hash of every constant that defines the paper rules."""
    import hashlib
    p = PAPER_PARAMS
    spec = (p.min_age_days, p.max_age_days, p.pivot_min_sessions_old, p.long_base_sessions, p.proximity_frac, p.trigger_pct, p.cap_pct, p.max_turn_cr,
            tuple(NO_TIME_EXITS))
    return hashlib.sha1(repr(spec).encode("utf-8")).hexdigest()[:12]


def paper_exit_params() -> Params:
    """Params carrying the X1 exits used by the paper book (a fresh object so callers cannot mutate a shared one)."""
    return Params(exit_overrides=NO_TIME_EXITS)


def pivot_arrays(b: _Bars):
    """pivot[i] = highest HIGH over sessions 0..i-1; pidx[i] = last session index that printed it (-1 when i == 0)."""
    n = len(b.highs)
    pivot = np.full(n, np.nan)
    pidx = np.full(n, -1, dtype=int)
    best, bi = -np.inf, -1
    for i in range(n):
        pivot[i], pidx[i] = best, bi
        if b.highs[i] >= best:
            best, bi = float(b.highs[i]), i
    return pivot, pidx


def setup3_state(b: _Bars, pre: _Pre, pv: np.ndarray, pi: np.ndarray, i: int, ld_high: float, list_date: pd.Timestamp,
                 p: Params = Params(), s3: Setup3Params = Setup3Params()) -> Optional[dict]:
    """Pre-open eligibility of a resting order for bar i, from bars up to i-1 only (bar i contributes only its date)."""
    if i < 12:
        return None
    age = int((pd.Timestamp(b.dates[i]) - list_date).days)
    if age < s3.min_age_days or age > s3.max_age_days:
        return None
    pivot = float(pv[i])
    if not np.isfinite(pivot) or pivot <= 0:
        return None
    sess_old = i - int(pi[i])
    if sess_old < s3.pivot_min_sessions_old + 1:
        return None
    prev_close = float(b.closes[i - 1])
    if prev_close < pivot * (1.0 - s3.proximity_frac):
        return None
    if float(b.vols[i - 1]) < p.min_volume:
        return None
    turn, circ = float(pre.turn[i]), float(pre.circ[i])
    if (turn > 0 and turn < p.turnover_floor_cr) or circ >= p.circuit_day_threshold:
        return None
    if s3.max_turn_cr is not None and turn > s3.max_turn_cr:
        return None
    klass = "LONG_BASE" if sess_old >= s3.long_base_sessions else "SHORT_BASE"
    return dict(pivot=pivot, trig=pivot * (1.0 + s3.trigger_pct / 100.0), ceil=pivot * (1.0 + s3.cap_pct / 100.0), age=age,
                klass=klass, prev_close=prev_close, sess_old=sess_old, dist_lh=prev_close / ld_high - 1.0,
                pivot_vs_lh=pivot / ld_high - 1.0, prox=prev_close / pivot - 1.0, turn=turn)


def setup3_signal(df, b, pre, pv, pi, i, ld_high, list_date, p=Params(), s3=Setup3Params(), slip_pct=0.3,
                  state: Optional[dict] = None) -> Optional[dict]:
    st = state if state is not None else setup3_state(b, pre, pv, pi, i, ld_high, list_date, p, s3)
    if st is None:
        return None
    o, h = float(b.opens[i]), float(b.highs[i])
    if h < st["trig"] or o > st["ceil"]:
        return None
    entry = max(o, st["trig"])
    if entry > st["ceil"] or entry < p.min_entry_price:
        return None
    fill = entry * (1.0 + slip_pct / 100.0)
    stop = _stop_from_history(df, i, fill, p)
    risk = (fill - stop) / fill
    if risk <= 0:
        return None
    return dict(st, entry=entry, fill=fill, stop=stop, risk=risk, ext=(entry - st["pivot"]) / st["pivot"] * 100.0)


def _signal_dict(s: dict) -> dict:
    return dict(type="S3_" + s["klass"], pivot=s["pivot"], post_confirm=s["ext"], max_chase=2.5, signal_price=s["fill"],
                vol_ratio=0.0, ext_pct=s["ext"], rr=0.0, shelf_prng=None)


def _decorate(trade: dict, s: dict, i: int, ex: Optional[int]) -> dict:
    trade["entry_idx"] = i
    trade["exit_idx"] = ex
    trade["klass"] = s["klass"]
    trade["pivot"] = float(s["pivot"])
    trade["sess_old"] = int(s["sess_old"])
    trade["dist_lh_pct"] = float(s["dist_lh"] * 100.0)
    trade["pivot_vs_lh_pct"] = float(s["pivot_vs_lh"] * 100.0)
    trade["age_signal"] = int(s["age"])
    return trade


def simulate_symbol_setup3(df: pd.DataFrame, symbol: str, listing_info: dict, p: Params = Params(),
                           s3: Setup3Params = Setup3Params(), cfg=EOD, slip_pct: float = 0.3,
                           include_open: bool = True) -> List[dict]:
    """S3 trades for one symbol: one position at a time, `p.cooldown_bars` bars between an exit and the next entry."""
    out: List[dict] = []
    n = len(df)
    if n < 15:
        return out
    if listing_info.get("is_nse_addition") and not listing_info.get("bse_liquidity_passed", True):
        return out
    ld_high, ld_low, list_date = _listing_context(df, listing_info)
    b = _Bars.of(df)
    pre = _prepare(df)
    pv, pi = pivot_arrays(b)
    last_exit = -100
    i = 12
    while i < n:
        if (i - last_exit) < p.cooldown_bars:
            i += 1
            continue
        s = setup3_signal(df, b, pre, pv, pi, i, ld_high, list_date, p, s3, slip_pct)
        if s is None:
            i += 1
            continue
        cur = pd.Timestamp(b.dates[i])
        t = dict(entry_idx=i, entry_date=cur, entry_price=s["fill"], signal=_signal_dict(s), signal_idx=i, signal_date=cur,
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


def order_days(df: pd.DataFrame, listing_info: dict, p: Params = Params(), s3: Setup3Params = Setup3Params()) -> List[pd.Timestamp]:
    """Dates on which a resting order would have been placed pre-market (state passes and the trigger price is tradable)."""
    ld_high, ld_low, list_date = _listing_context(df, listing_info)
    b = _Bars.of(df)
    pre = _prepare(df)
    pv, pi = pivot_arrays(b)
    days = []
    for i in range(12, len(df)):
        st = setup3_state(b, pre, pv, pi, i, ld_high, list_date, p, s3)
        if st is not None and st["trig"] >= p.min_entry_price:
            days.append(pd.Timestamp(b.dates[i]))
    return days


def control_indices(df: pd.DataFrame, listing_info: dict, p: Params = Params(), s3: Setup3Params = Setup3Params()) -> List[int]:
    """Bars where the S3 STATE holds (and the open is tradable) irrespective of any breakout."""
    ld_high, ld_low, list_date = _listing_context(df, listing_info)
    b = _Bars.of(df)
    pre = _prepare(df)
    pv, pi = pivot_arrays(b)
    out = []
    for i in range(12, len(df) - 1):
        st = setup3_state(b, pre, pv, pi, i, ld_high, list_date, p, s3)
        if st is not None and float(b.opens[i]) >= p.min_entry_price:
            out.append(i)
    return out


def simulate_control_entry(df: pd.DataFrame, symbol: str, listing_info: dict, i: int, p: Params = Params(),
                           s3: Setup3Params = Setup3Params(), cfg=EOD, slip_pct: float = 0.3,
                           include_open: bool = True) -> Optional[dict]:
    """One control trade: buy bar i's OPEN (plus slippage) with the same stop and exits as S3, no trigger required."""
    ld_high, ld_low, list_date = _listing_context(df, listing_info)
    b = _Bars.of(df)
    pre = _prepare(df)
    pv, pi = pivot_arrays(b)
    st = setup3_state(b, pre, pv, pi, i, ld_high, list_date, p, s3)
    if st is None:
        return None
    fill = float(b.opens[i]) * (1.0 + slip_pct / 100.0)
    stop = _stop_from_history(df, i, fill, p)
    risk = (fill - stop) / fill
    if risk <= 0:
        return None
    s = dict(st, fill=fill, stop=stop, risk=risk, ext=0.0)
    cur = pd.Timestamp(b.dates[i])
    t = dict(entry_idx=i, entry_date=cur, entry_price=fill, signal=_signal_dict(s), signal_idx=i, signal_date=cur,
             trailing=stop, risk_pct=risk, max_runup=0.0, path=[], stop_initial=stop, age=st["age"], missed=False)
    trade, ex = _run_trade(b, t, i, i, cfg, p, symbol)
    if trade is None:
        if not include_open:
            return None
        return _decorate(_make_open_trade(t, b, symbol), s, i, None)
    trade["open"] = False
    return _decorate(trade, s, i, ex)


# ------------------------------------------------------------------------------------------ paper book (order sheet)

def preview_s3_entry(df: pd.DataFrame, listing_info: dict, today, p: Params = None, s3: Setup3Params = PAPER_PARAMS,
                     slip_pct: float = 0.3) -> Optional[dict]:
    """
    Pre-market eligibility of an S3 resting stop-limit BUY for `today`, from completed sessions only (df ends at the previous close).
    Runs the research gates (`setup3_state` / `setup3_signal`) on a synthetic bar that prints exactly at the trigger, so the order sheet and
    the replay cannot drift apart.  Returns None when not eligible, else dict(trigger, limit, fill, stop, risk_pct, rr, age, ld_high,
    last_close, already_above_trigger, pivot, klass, sess_old, turn20).
    """
    p = p if p is not None else paper_exit_params()
    n = len(df)
    if n < 15:
        return None
    if listing_info.get("is_nse_addition") and not listing_info.get("bse_liquidity_passed", True):
        return None
    ld_high, _ld_low, list_date = _listing_context(df, listing_info)
    today = _naive(today)
    pivot = float(df["HIGH"].astype(float).max())
    trig = pivot * (1.0 + s3.trigger_pct / 100.0)
    ceil = pivot * (1.0 + s3.cap_pct / 100.0)
    last_close = float(df["CLOSE"].iat[-1])
    if last_close > ceil:
        return None                                    # already beyond the limit: a limit buy could not fill
    row = {c: df[c].iat[-1] for c in df.columns}
    row.update(DATE=today, OPEN=trig, HIGH=trig, LOW=trig, CLOSE=trig, VOLUME=float(df["VOLUME"].iat[-1]))
    df2 = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    i = len(df2) - 1
    b = _Bars.of(df2)
    pre = _prepare(df2)
    pv, pi = pivot_arrays(b)
    st = setup3_state(b, pre, pv, pi, i, ld_high, list_date, p, s3)
    if st is None:
        return None
    s = setup3_signal(df2, b, pre, pv, pi, i, ld_high, list_date, p, s3, slip_pct, state=st)
    if s is None:
        return None
    return dict(trigger=trig, limit=ceil, fill=s["fill"], stop=s["stop"], risk_pct=s["risk"] * 100.0, rr=0.0, age=s["age"], ld_high=ld_high,
                last_close=last_close, already_above_trigger=last_close >= trig, pivot=s["pivot"], klass=s["klass"], sess_old=s["sess_old"],
                turn20=s["turn"])


def s3_paper_status(df: pd.DataFrame, entry_date, entry_price: float, p: Params = None, symbol: str = "") -> Optional[dict]:
    """
    Where a PAPER_ONLY S3 position stands after the last completed session, managed by the same engine as the replay (X1 exits, close-based
    stop alert, sale at the next open).  `entry_price` is the fill YOU recorded.  Returns None when the entry date is not in `df`, else
    dict(status "OPEN" | "EXITED", trailing, alert (True when the last close is at or below the stop: sell at the next open), last_close,
    pnl_pct, max_runup, days_held, exit_date, exit_price, exit_reason).
    """
    p = p if p is not None else paper_exit_params()
    ed = pd.Timestamp(entry_date).tz_localize(None).normalize() if pd.Timestamp(entry_date).tzinfo else pd.Timestamp(entry_date).normalize()
    dates = pd.to_datetime(df["DATE"])
    dates = dates.dt.tz_localize(None) if dates.dt.tz is not None else dates
    hit = np.where(dates.dt.normalize().values == np.datetime64(ed))[0]
    if len(hit) == 0 or entry_price <= 0:
        return None
    i = int(hit[0])
    b = _Bars.of(df)
    stop = _stop_from_history(df, i, entry_price, p)
    risk = (entry_price - stop) / entry_price
    sig = dict(type="S3_PAPER", pivot=entry_price, post_confirm=0.0, max_chase=2.5, signal_price=entry_price, vol_ratio=0.0, ext_pct=0.0, rr=0.0,
               shelf_prng=None)
    cur = pd.Timestamp(b.dates[i])
    t = dict(entry_idx=i, entry_date=cur, entry_price=float(entry_price), signal=sig, signal_idx=i, signal_date=cur, trailing=stop, risk_pct=risk,
             max_runup=0.0, path=[], stop_initial=stop, age=0, missed=False)
    trade, ex = _run_trade(b, t, i, i, EOD, p, symbol)
    last_close = float(b.closes[-1])
    if trade is not None:
        return dict(status="EXITED", trailing=float(t["trailing"]), alert=False, last_close=last_close, pnl_pct=float(trade["pnl_pct"]),
                    max_runup=float(trade.get("max_runup_pct", t["max_runup"])), days_held=int(trade["days_held_cal"]), exit_date=trade["exit_date"],
                    exit_price=float(trade["exit_price"]), exit_reason=str(trade["exit_reason"]), stop_initial=float(stop))
    return dict(status="OPEN", trailing=float(t["trailing"]), alert=bool(last_close <= t["trailing"]), last_close=last_close,
                pnl_pct=(last_close / entry_price - 1.0) * 100.0, max_runup=float(t["max_runup"]), days_held=int((pd.Timestamp(b.dates[-1]) - cur).days),
                exit_date=None, exit_price=None, exit_reason=None, stop_initial=float(stop))
