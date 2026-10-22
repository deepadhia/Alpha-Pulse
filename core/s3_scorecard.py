# -*- coding: utf-8 -*-
"""
core/s3_scorecard.py -- the forward proof test of the PAPER_ONLY S3 book.  Pure functions on documents read from MongoDB (s3_paper_*), so they are
unit-tested without a database.  The criteria are FIXED HERE, before the first paper trade exists, so the verdict cannot be tuned afterwards:

  Net return per trade = pnl_pct - COST_PCT (1.0% round trip, the research assumption).  Open positions are marked to market for the "incl. open" view
  only; the verdict uses CLOSED trades.
  INSUFFICIENT   fewer than MIN_CLOSED (40) closed trades: no verdict.  (Replay standard deviation is about 17% per trade, so 40 trades resolve about 5pp.)
  GOOD           at least 40 closed AND mean net 90% bootstrap lower bound > 0 AND closed profit factor >= 1.3 AND mean net without the best trade > 0.
  BAD            at least 40 closed AND (mean net <= 0 OR mean net 90% upper bound < +2% (the replay said +8%, so a CI that excludes even +2% contradicts it)).
  CONTINUE       anything else with at least 40 closed: keep paper trading; re-evaluate at 60 and 80 closed trades.
  OPERATIONS     the daily alert job must have a heartbeat (s3_paper_runs) for at least 90% of the last 10 business days, and every filled order must carry
                 a fill price; reported separately from the algorithm verdict.
"""

from datetime import datetime, timezone
from typing import List, Optional

import numpy as np
import pandas as pd

COST_PCT = 1.0
MIN_CLOSED = 40
REPLAY_MEAN_NET = 8.0          # LEARNINGS 20.4: replay of the paper rule, N=179, +7.86%


def profit_factor(x) -> float:
    x = np.asarray(x, float)
    g, l = x[x > 0].sum(), -x[x < 0].sum()
    return float("inf") if l == 0 and g > 0 else (g / l if l > 0 else float("nan"))


def boot_mean_ci(x, n: int = 4000, seed: int = 0, level: float = 90.0):
    x = np.asarray(x, float)
    if len(x) < 2:
        return float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    m = x[rng.integers(0, len(x), (n, len(x)))].mean(1)
    a = (100 - level) / 2
    return float(np.percentile(m, a)), float(np.percentile(m, 100 - a))


def evaluate(closed: List[dict], open_pnls: Optional[List[float]] = None) -> dict:
    """closed: position documents with pnl_pct (CLOSED).  open_pnls: marked-to-market pnl_pct of open positions."""
    net = np.array([float(d["pnl_pct"]) - COST_PCT for d in closed if d.get("pnl_pct") is not None], float)
    n = len(net)
    out = dict(n_closed=n, n_open=len(open_pnls or []), verdict="INSUFFICIENT", reason=f"{n} of {MIN_CLOSED} closed trades needed")
    if n == 0:
        return out
    lo, hi = boot_mean_ci(net)
    best = int(np.argmax(net))
    ex1 = float(np.delete(net, best).mean()) if n > 1 else float("nan")
    allv = np.concatenate([net, np.asarray(open_pnls or [], float) - COST_PCT]) if open_pnls else net
    out.update(mean=float(net.mean()), median=float(np.median(net)), win=float((net > 0).mean() * 100), ci_lo=lo, ci_hi=hi, pf_closed=profit_factor(net),
               pf_incl_open=profit_factor(allv), mean_ex_best=ex1, best=float(net.max()), worst=float(net.min()), replay_mean=REPLAY_MEAN_NET)
    if n < MIN_CLOSED:
        return out
    if out["mean"] <= 0 or hi < 2.0:
        out.update(verdict="BAD", reason=f"mean net {out['mean']:+.2f}% (CI90 upper {hi:+.1f}%) contradicts the replay's +{REPLAY_MEAN_NET:.0f}%")
    elif lo > 0 and out["pf_closed"] >= 1.3 and ex1 > 0:
        out.update(verdict="GOOD", reason=f"mean net {out['mean']:+.2f}% CI90 [{lo:+.1f}, {hi:+.1f}], closed PF {out['pf_closed']:.2f}, ex-best {ex1:+.2f}%")
    else:
        out.update(verdict="CONTINUE", reason=f"mean net {out['mean']:+.2f}% CI90 [{lo:+.1f}, {hi:+.1f}], closed PF {out['pf_closed']:.2f}, ex-best {ex1:+.2f}%: not yet conclusive")
    return out


def fill_stats(orders: List[dict]) -> dict:
    """Order -> fill conversion and the fill price versus the trigger (the replay assumed trigger + 0.3%)."""
    placed = len(orders)
    filled = [o for o in orders if o.get("status") == "FILLED"]
    slips = [(float(o["fill_price"]) / float(o["trigger"]) - 1.0) * 100.0 for o in filled if o.get("fill_price") and o.get("trigger")]
    return dict(placed=placed, filled=len(filled), fill_rate=(100.0 * len(filled) / placed if placed else float("nan")),
                mean_slip_vs_trigger=(float(np.mean(slips)) if slips else float("nan")), missing_fill_price=sum(1 for o in filled if not o.get("fill_price")))


def business_days(end: pd.Timestamp, n: int) -> List[pd.Timestamp]:
    out, d = [], pd.Timestamp(end).normalize()
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= pd.Timedelta(days=1)
    return out


def operations(runs: List[dict], today: pd.Timestamp, window: int = 10) -> dict:
    have = {pd.Timestamp(r["run_date"]).tz_localize(None).normalize() if pd.Timestamp(r["run_date"]).tzinfo else pd.Timestamp(r["run_date"]).normalize() for r in runs}
    days = business_days(today, window)
    hit = [d for d in days if d in have]
    stale = sum(1 for r in runs if r.get("stale"))
    return dict(window=window, days_with_run=len(hit), coverage=100.0 * len(hit) / window, missing=[str(d.date()) for d in days if d not in have],
                stale_runs=stale, ok=len(hit) >= 0.9 * window)
