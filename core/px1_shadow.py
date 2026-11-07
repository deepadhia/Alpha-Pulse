# -*- coding: utf-8 -*-
"""
core/px1_shadow.py -- PAPER_ONLY forward test of "policy P with the time-based exits switched off" (X1), added 2026-11-07.

WHAT IS TESTED (one hypothesis, nothing else): on IDENTICAL entries, does P's exit set minus its time-based exits (dead-money 14 d /
10-session velocity gate, stagnant, volume exhaustion, long-underwater; the stop and the trailing ratchet stay) earn more per trade than
P's live exits?  Replay evidence (LEARNINGS 22.1, tier REPLAY): paired gain +3.17pp per trade pooled over 2006-2026 (CI90 +1.55..+4.98),
+1.23pp (CI -0.14..+2.79) on the 2006-2022 holdout, +4.76pp on 2022-26; it missed the pre-registered holdout bar, so it is NOT live and
this module exists to collect forward evidence without touching any live rule.

HOW: a deterministic forward replay, not a stored book.  For every listing, the live-rules engine (core.honest_backtest.simulate_symbol_cross,
resting-order entries, trigger +0.3% slip, cap +2.5%, no volume gate, eod_next_open exits: exactly the replay of LEARNINGS 12/22) produces the
entries; each entry is then re-run with X1 exits (`rerun_exits_for_entry`, the same procedure as research_alpha_lab.rerun_exits, parity checked in
the tests).  Only entries on or after PX1_START_DATE count, so nothing in the sample was seen when the rule was written.  The result depends only on the
candle cache, so it can be recomputed at any time and no new collection or live code path is needed.

WHAT IT CANNOT TELL: it uses the replay's idealised entry (a real fill may differ: the live resting-order test measures that), it ignores slot
competition (longer holds occupy slots; the account effect is only measured in the replay, LEARNINGS 22.1), and the candle cache of an IPO older
than 60 days is only refreshed by `refresh_stale_caches.py`, so the CLI flags stale held names.

VERDICT RULES (FIXED HERE before the first paired trade exists; never edit them after seeing forward data -- bump PX1_SHADOW_VERSION instead):
  A PAIR is an entry on/after PX1_START_DATE whose LIVE-exit leg has CLOSED.  The X1 leg may still be open; it is then marked to market (dropping
  open X1 legs would bias against X1, which holds longer).  gain = X1 pnl% - live pnl% (costs cancel).
  INSUFFICIENT  fewer than MIN_PAIRS (40) pairs: no verdict.
  SUPPORTED     >= 40 pairs AND mean gain 90% bootstrap lower bound > 0 AND mean gain without the single largest gain > 0.  SUPPORTED is
                a candidate for a live proposal (needs an owner decision, a milestone version bump and the AGENTS rule 19 note), never an automatic change.
  REJECTED      >= 40 pairs AND (mean gain <= 0 OR 90% upper bound < 0).
  CONTINUE      anything else with >= 40 pairs; re-evaluate at 60 and 80 pairs.
"""

import hashlib
from dataclasses import replace
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from core.honest_backtest import (LIVE_FAITHFUL, Params, _Bars, _listing_context, _make_open_trade, _run_trade, _stop_from_history,
                                  simulate_symbol_cross)
from core.setup3 import NO_TIME_EXITS

PX1_SHADOW_VERSION = "1.0.0"
PX1_START_DATE = "2026-11-09"       # first session whose entries count (candles in the research cache ended 2026-11-05)
COST_PCT = 1.0
MIN_PAIRS = 40
ENTRY_SLIP_PCT = 0.3
FILL_CAP_PCT = 2.5
MIN_AGE_DAYS = 5
CFG = replace(LIVE_FAITHFUL, exit_fill="eod_next_open")


def live_params() -> Params:
    return Params()


def x1_params() -> Params:
    """P's live parameters with every time-based exit disabled (the S3 exit set)."""
    return replace(live_params(), exit_overrides=NO_TIME_EXITS)


def shadow_rules_fingerprint() -> str:
    """Short stable hash of every constant that defines the shadow rules."""
    spec = (PX1_SHADOW_VERSION, PX1_START_DATE, COST_PCT, MIN_PAIRS, ENTRY_SLIP_PCT, FILL_CAP_PCT, MIN_AGE_DAYS, tuple(NO_TIME_EXITS),
            CFG.exit_fill, CFG.entry_fill)
    return hashlib.sha1(repr(spec).encode("utf-8")).hexdigest()[:12]


# --------------------------------------------------------------------------------------------- engine
def live_entries(df: pd.DataFrame, symbol: str, listing_info: dict) -> List[dict]:
    """Policy-P entries (with their LIVE-exit outcome) for one symbol, exactly as in the replay."""
    return simulate_symbol_cross(df, symbol, listing_info, live_params(), cfg=CFG, vol_gate=False, slip_pct=ENTRY_SLIP_PCT,
                                 fill_cap_pct=FILL_CAP_PCT, min_age=MIN_AGE_DAYS, include_open=True, causal=True)


def rerun_exits_for_entry(df: pd.DataFrame, listing_info: dict, trade: dict, p: Params) -> Optional[dict]:
    """Same entry, exits of `p`.  Mirrors research_alpha_lab.rerun_exits (asserted equal in test_px1_shadow.py and in the parity run)."""
    b = _Bars.of(df)
    i = int(trade["entry_idx"])
    fill = float(trade["entry_price"])
    stop = _stop_from_history(df, i, fill, p)
    risk = (fill - stop) / fill
    if risk <= 0:
        return None
    ld_high = _listing_context(df, listing_info)[0]
    cur = pd.Timestamp(b.dates[i])
    sig = dict(type="LISTING_BREAKOUT", pivot=float(ld_high), post_confirm=float(trade["ext_pct"]), signal_price=fill,
               vol_ratio=float(trade["vol_ratio"]), ext_pct=float(trade["ext_pct"]), rr=float(trade["rr"]))
    t = dict(entry_idx=i, entry_date=cur, entry_price=fill, signal=sig, signal_idx=i, signal_date=cur, trailing=stop, risk_pct=risk,
             max_runup=0.0, path=[], stop_initial=stop, age=int(trade["days_since_listing"]), missed=False)
    out, _ex = _run_trade(b, t, i, i, CFG, p, trade["symbol"])
    if out is None:
        out = _make_open_trade(t, b, trade["symbol"])
    return out


def forward_pairs(candle_map: Dict[str, pd.DataFrame], listing_map: Dict[str, dict], start_date: str = PX1_START_DATE) -> pd.DataFrame:
    """One row per entry on/after `start_date` whose LIVE leg is closed: live and X1 outcomes on identical entries."""
    start = pd.Timestamp(start_date)
    p1 = x1_params()
    rows = []
    for sym, df in candle_map.items():
        li = listing_map.get(sym, {})
        for t in live_entries(df, sym, li):
            if pd.Timestamp(t["entry_date"]) < start:
                continue
            x = rerun_exits_for_entry(df, li, t, p1)
            if x is None:
                continue
            rows.append(dict(
                symbol=sym, entry_date=pd.Timestamp(t["entry_date"]), entry_price=float(t["entry_price"]),
                live_open=bool(t["open"]), live_pnl=float(t["pnl_pct"]), live_exit_date=str(t["exit_date"])[:10], live_exit_reason=str(t["exit_reason"]),
                live_days=int(t["days_held"]),
                x1_open=bool(x.get("exit_reason") == "OPEN" or x.get("open", False)), x1_pnl=float(x["pnl_pct"]),
                x1_exit_date=str(x["exit_date"])[:10], x1_exit_reason=str(x["exit_reason"]), x1_days=int(x["days_held"]),
                last_candle=str(pd.Timestamp(df["DATE"].iloc[-1]).date())))
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------------------------- verdict
def profit_factor(x) -> float:
    x = np.asarray(x, float)
    g, l = x[x > 0].sum(), -x[x < 0].sum()
    return float("inf") if l == 0 and g > 0 else (g / l if l > 0 else float("nan"))


def boot_mean_ci(x, groups=None, n: int = 4000, seed: int = 0, level: float = 90.0):
    x = np.asarray(x, float)
    if len(x) < 2:
        return float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    if groups is None:
        m = x[rng.integers(0, len(x), (n, len(x)))].mean(1)
    else:
        g = pd.factorize(np.asarray(groups))[0]
        k = g.max() + 1
        sums, cnt = np.bincount(g, weights=x, minlength=k), np.bincount(g, minlength=k).astype(float)
        idx = rng.integers(0, k, (n, k))
        m = sums[idx].sum(1) / cnt[idx].sum(1)
    a = (100 - level) / 2
    return float(np.percentile(m, a)), float(np.percentile(m, 100 - a))


def evaluate(pairs: pd.DataFrame) -> dict:
    """Apply the fixed verdict rules to forward pairs (as returned by forward_pairs)."""
    d = pairs[~pairs.live_open] if len(pairs) else pairs
    n = len(d)
    out = dict(n_pairs=n, n_x1_open=int(d.x1_open.sum()) if n else 0, verdict="INSUFFICIENT", reason=f"{n} of {MIN_PAIRS} pairs needed",
               version=PX1_SHADOW_VERSION, fingerprint=shadow_rules_fingerprint())
    if n == 0:
        return out
    gain = (d.x1_pnl - d.live_pnl).values.astype(float)
    lo, hi = boot_mean_ci(gain, d.symbol.values)
    ex1 = float(np.delete(gain, int(np.argmax(gain))).mean()) if n > 1 else float("nan")
    live_net, x1_net = d.live_pnl.values - COST_PCT, d.x1_pnl.values - COST_PCT
    out.update(mean_gain=float(gain.mean()), ci_lo=lo, ci_hi=hi, mean_gain_ex_largest=ex1, share_better=float((gain > 1e-9).mean() * 100),
               share_worse=float((gain < -1e-9).mean() * 100), live_mean_net=float(live_net.mean()), x1_mean_net=float(x1_net.mean()),
               live_pf=profit_factor(live_net), x1_pf=profit_factor(x1_net),
               x1_pf_closed=profit_factor(d.x1_pnl[~d.x1_open].values - COST_PCT) if (~d.x1_open).any() else float("nan"),
               live_mean_days=float(d.live_days.mean()), x1_mean_days=float(d.x1_days.mean()))
    if n < MIN_PAIRS:
        return out
    if out["mean_gain"] <= 0 or hi < 0:
        out.update(verdict="REJECTED", reason=f"mean gain {out['mean_gain']:+.2f}pp (CI90 [{lo:+.2f}, {hi:+.2f}]): X1 does not beat the live exits forward")
    elif lo > 0 and ex1 > 0:
        out.update(verdict="SUPPORTED", reason=f"mean gain {out['mean_gain']:+.2f}pp CI90 [{lo:+.2f}, {hi:+.2f}], ex-largest {ex1:+.2f}pp: a candidate for an owner decision, not a live change")
    else:
        out.update(verdict="CONTINUE", reason=f"mean gain {out['mean_gain']:+.2f}pp CI90 [{lo:+.2f}, {hi:+.2f}], ex-largest {ex1:+.2f}pp: not yet conclusive")
    return out


def stale_symbols(pairs: pd.DataFrame, asof: pd.Timestamp, max_lag_days: int = 4) -> List[str]:
    """Symbols whose X1 leg is still open but whose candle cache stopped growing (the scanners do not refetch IPOs older than 60 days)."""
    if not len(pairs):
        return []
    held = pairs[pairs.x1_open | pairs.live_open]
    return sorted(held[pd.to_datetime(held.last_candle) < (pd.Timestamp(asof) - pd.Timedelta(days=max_lag_days))].symbol.unique())
