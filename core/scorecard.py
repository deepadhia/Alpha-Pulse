# -*- coding: utf-8 -*-
"""
core/scorecard.py

Statistics and the pre-registered stop / continue / scale rules for the NATIVE FORWARD cohort
(entry on or after 2026-09-29, not backfilled).  Pure functions.

Why pre-registered: the live-faithful book makes ~19 trades a year with a standard deviation of
~12.5% per trade.  Confirming a true +2% per-trade edge at (one-sided 10%, 80% power) needs about
177 trades, roughly 9.5 years at that pace.  A few lucky or unlucky trades therefore say very
little, and the decision rules must be fixed BEFORE the results come in:

  INSUFFICIENT_DATA   fewer than 20 closed trades: no decision
  STOP                >= 30 trades and the 80% upper confidence bound of the mean net return is below 0:
                      the data say the strategy loses money; go back to research
  SCALE_ELIGIBLE      >= 100 trades, the 90% lower bound of the mean is above 0, and the mean without the
                      single best trade is still positive: evidence of a real edge
  CONTINUE            everything else: stay at minimum size / paper, keep collecting trades

"net" means after costs: pnl_pct minus the round-trip friction assumption.
"""

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

import numpy as np

INSUFFICIENT = "INSUFFICIENT_DATA"
STOP = "STOP"
CONTINUE = "CONTINUE"
SCALE = "SCALE_ELIGIBLE"


@dataclass(frozen=True)
class GateConfig:
    min_trades_any_decision: int = 20
    min_trades_stop: int = 30
    stop_confidence_z: float = 0.84          # 80% one-sided
    min_trades_scale: int = 100
    scale_confidence_z: float = 1.2816       # 90% one-sided
    friction_pct: float = 1.0


@dataclass
class Scorecard:
    n: int = 0
    mean: float = 0.0
    median: float = 0.0
    sd: float = 0.0
    se: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    mean_ex_best: float = 0.0
    best: float = 0.0
    worst: float = 0.0
    upper80: float = 0.0
    lower90: float = 0.0
    status: str = INSUFFICIENT
    reasons: List[str] = field(default_factory=list)


def _pf(x: np.ndarray) -> float:
    w, l = x[x > 0].sum(), -x[x < 0].sum()
    return float(w / l) if l > 0 else float("inf")


def scorecard(pnl_pcts: Sequence[float], cfg: GateConfig = GateConfig()) -> Scorecard:
    """`pnl_pcts`: gross per-trade percentage returns (price only). Costs are deducted here."""
    sc = Scorecard()
    x = np.asarray([float(v) for v in pnl_pcts if v is not None and not math.isnan(float(v))]) - cfg.friction_pct
    sc.n = len(x)
    if sc.n == 0:
        sc.reasons.append("no closed native trades yet")
        return sc
    sc.mean = float(x.mean())
    sc.median = float(np.median(x))
    sc.sd = float(x.std(ddof=1)) if sc.n > 1 else 0.0
    sc.se = sc.sd / math.sqrt(sc.n) if sc.n > 1 else float("inf")
    sc.win_rate = float((x > 0).mean() * 100.0)
    sc.profit_factor = _pf(x)
    sc.best, sc.worst = float(x.max()), float(x.min())
    sc.mean_ex_best = float(np.delete(x, int(np.argmax(x))).mean()) if sc.n > 1 else float("nan")
    sc.upper80 = sc.mean + cfg.stop_confidence_z * sc.se
    sc.lower90 = sc.mean - cfg.scale_confidence_z * sc.se

    if sc.n < cfg.min_trades_any_decision:
        sc.status = INSUFFICIENT
        sc.reasons.append(f"{sc.n} closed trades < {cfg.min_trades_any_decision} needed for any decision")
    elif sc.n >= cfg.min_trades_stop and sc.upper80 < 0:
        sc.status = STOP
        sc.reasons.append(f"80% upper bound of mean net return is {sc.upper80:+.2f}% < 0 over {sc.n} trades: "
                          f"the data say this loses money")
    elif sc.n >= cfg.min_trades_scale and sc.lower90 > 0 and sc.mean_ex_best > 0:
        sc.status = SCALE
        sc.reasons.append(f"90% lower bound {sc.lower90:+.2f}% > 0 and mean without the best trade "
                          f"{sc.mean_ex_best:+.2f}% > 0 over {sc.n} trades")
    else:
        sc.status = CONTINUE
        need = []
        if sc.n < cfg.min_trades_scale:
            need.append(f"{cfg.min_trades_scale - sc.n} more trades for a scale decision")
        if sc.lower90 <= 0:
            need.append(f"90% lower bound is {sc.lower90:+.2f}% (needs > 0)")
        if sc.mean_ex_best <= 0:
            need.append(f"mean without best trade is {sc.mean_ex_best:+.2f}% (needs > 0)")
        sc.reasons.append("; ".join(need) if need else "keep collecting")
    return sc


def format_scorecard(sc: Scorecard, title: str = "NATIVE FORWARD COHORT") -> str:
    if sc.n == 0:
        return f"{title}: no closed trades yet."
    return "\n".join([
        f"{title}  (net of costs)",
        f"  closed trades        : {sc.n}",
        f"  mean / median        : {sc.mean:+.2f}% / {sc.median:+.2f}%   (SD {sc.sd:.1f}%, SE {sc.se:.2f}%)",
        f"  win rate / PF        : {sc.win_rate:.1f}% / {sc.profit_factor:.2f}",
        f"  best / worst         : {sc.best:+.1f}% / {sc.worst:+.1f}%   (mean without best: {sc.mean_ex_best:+.2f}%)",
        f"  80% upper / 90% lower: {sc.upper80:+.2f}% / {sc.lower90:+.2f}%",
        f"  DECISION             : {sc.status}",
        *[f"     - {r}" for r in sc.reasons],
    ])


# --------------------------------------------------------------------------- profit-factor monitor
#
# Profit factor is a right-tail statistic for this strategy: in the 2022-26 replay the closed outliers (+25% or more,
# about 7% of closed trades) earned +1,035pp while the other trades netted -489pp, so PF 2.0 needs about 26% more outlier
# profit than the replay had and PF falls to 1.0 with half of it (reports/2026-10-14/).  This monitor reports the PF the way
# that makes the dependence visible; it never changes the STOP / CONTINUE / SCALE decision above.

@dataclass
class PfReport:
    target: float = 2.0
    n_closed: int = 0
    n_open: int = 0
    pf_closed: float = 0.0           # closed trades only
    pf_incl_open: float = 0.0        # closed trades + open trades marked to market
    pf_closed_ex_top3: float = 0.0   # closed trades without the three best
    lo90: float = 0.0                # 90% bootstrap interval of pf_incl_open
    hi90: float = 0.0
    prob_ge_target: float = 0.0      # bootstrap probability that pf_incl_open >= target
    outlier_count: int = 0           # closed trades at or above outlier_pct gross
    outlier_share_of_profit: float = 0.0
    notes: List[str] = field(default_factory=list)


def pf_report(closed_pcts: Sequence[float], open_pcts: Sequence[float] = (), cfg: GateConfig = GateConfig(),
              target: float = 2.0, outlier_pct: float = 25.0, n_boot: int = 2000, seed: int = 0) -> PfReport:
    """Profit factor three ways with a bootstrap interval.  Inputs are gross per-trade percentages; costs are deducted here.
    The bootstrap resamples trades (symbols are not available here), so the interval is a little narrower than a symbol-cluster one."""
    def clean(v):
        return np.asarray([float(a) for a in v if a is not None and not math.isnan(float(a))], dtype=float)

    rep = PfReport(target=target)
    c, o = clean(closed_pcts), clean(open_pcts)
    rep.n_closed, rep.n_open = len(c), len(o)
    if rep.n_closed == 0:
        rep.notes.append("no closed trades yet")
        return rep
    cn, on = c - cfg.friction_pct, o - cfg.friction_pct
    allx = np.concatenate([cn, on])
    rep.pf_closed = _pf(cn)
    rep.pf_incl_open = _pf(allx)
    rep.pf_closed_ex_top3 = _pf(np.sort(cn)[:-3]) if len(cn) > 3 else float("nan")
    gross_profit = cn[cn > 0].sum()
    big = c >= outlier_pct
    rep.outlier_count = int(big.sum())
    rep.outlier_share_of_profit = float(cn[big].sum() / gross_profit) if gross_profit > 0 else 0.0
    rng = np.random.default_rng(seed)
    boots = np.array([_pf(rng.choice(allx, len(allx))) for _ in range(n_boot)])
    boots = boots[np.isfinite(boots)] if np.isfinite(boots).any() else boots
    rep.lo90, rep.hi90 = float(np.percentile(boots, 5)), float(np.percentile(boots, 95))
    rep.prob_ge_target = float(np.mean(boots >= target))
    if rep.n_closed < 30:
        rep.notes.append(f"only {rep.n_closed} closed trades: the interval is very wide and tells little (the replay needed 100+)")
    if rep.outlier_count < 5:
        rep.notes.append(f"only {rep.outlier_count} closed outliers (>= {outlier_pct:.0f}%): PF is dominated by chance until more arrive")
    if rep.pf_incl_open >= target > rep.pf_closed:
        rep.notes.append("the target is met only with open runners marked to market; closed trades alone are below it")
    return rep


def format_pf_report(r: PfReport, title: str = "PROFIT FACTOR MONITOR") -> str:
    if r.n_closed == 0:
        return f"{title}: no closed trades yet."
    lines = [
        f"{title}  (net of costs; target {r.target:.1f})",
        f"  PF closed only            : {r.pf_closed:.2f}   ({r.n_closed} closed trades)",
        f"  PF incl. {r.n_open} open at MTM    : {r.pf_incl_open:.2f}   90% interval [{r.lo90:.2f}, {r.hi90:.2f}], P(PF >= {r.target:.1f}) = {r.prob_ge_target:.0%}",
        f"  PF closed without top 3   : {r.pf_closed_ex_top3:.2f}",
        f"  closed outliers (>= 25%)  : {r.outlier_count} trades = {r.outlier_share_of_profit:.0%} of gross profit",
    ]
    lines += [f"     - {n}" for n in r.notes]
    return "\n".join(lines)
