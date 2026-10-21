# -*- coding: utf-8 -*-
"""
core/portfolio_sim.py

Turns a per-symbol trade list (core.honest_backtest) into an account-level result:
slot caps, whole-share sizing, cash limits, per-order costs and a mark-to-market
equity curve.  Pure pandas/numpy, no DB or scanner imports.

Why this exists: the per-symbol backtest takes every signal and reports trade-level
percentages.  The NAV, 7-vs-10-slot and friction figures in the audit dossier have no
code behind them in this repository; this module is the reproducible replacement.

Slot policies
  as_coded    : mirrors listing_day_breakout_scanner.commit_trade_to_db.  Between the soft
                and hard cap a trade is admitted if tier A / score >= 4 / CORRECTION, which
                in practice is almost always, so the effective cap is the hard cap.
  documented  : mirrors the audit dossier: slots beyond the soft cap are only usable while
                EVERY open position already has its stop at or above entry (risk-free).
  flat        : a single cap of `hard_cap`.
"""

from dataclasses import dataclass, replace
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from core.execution_model import CostModel


@dataclass(frozen=True)
class PortfolioConfig:
    capital: float = 100000.0
    soft_cap: int = 5
    hard_cap: int = 7
    cap_policy: str = "as_coded"          # "as_coded" | "documented" | "flat"
    weight: Optional[float] = None         # fraction of NAV per position; default 1 / hard_cap
    priority: str = "vol_ratio"            # "vol_ratio" | "first" | "random"
    same_day_reuse: bool = False           # may a slot freed on day D be reused on day D?
    min_fill_fraction: float = 0.5         # skip if whole shares reach less than this of target
    risk_budget_pct: Optional[float] = None  # research: size = NAV * risk_budget / initial stop distance (None = flat weight)
    max_weight: float = 0.20               # cap on a risk-sized position (fraction of NAV)
    dd_half_pct: Optional[float] = None    # research: halve new position size once NAV is this far below its peak
    dd_halt_pct: Optional[float] = None    # research: stop opening positions at this drawdown (mirrors core.risk_guard)
    rotate_after_days: Optional[int] = None  # research: when a new signal cannot be funded (slots or cash), sell the worst
                                             # dead-money candidate (held >= this many calendar days, peak close run-up below
                                             # rotate_max_peak_pct, current PnL at or below rotate_max_pnl_pct) at the prior close
    rotate_max_peak_pct: float = 4.0
    rotate_max_pnl_pct: float = 2.0
    seed: int = 0
    cost: CostModel = CostModel()


def _path_lookup(path):
    """date -> (close, trailing) for one trade."""
    return {pd.Timestamp(d): (c, tr) for d, c, tr in path}


def _last_at_or_before(lookup, keys_sorted, date):
    """Return the entry for the latest date <= `date`, or None."""
    lo, hi, res = 0, len(keys_sorted) - 1, None
    while lo <= hi:
        mid = (lo + hi) // 2
        if keys_sorted[mid] <= date:
            res, lo = keys_sorted[mid], mid + 1
        else:
            hi = mid - 1
    return lookup[res] if res is not None else None


def run_portfolio(trades: List[dict], cfg: PortfolioConfig = PortfolioConfig()) -> dict:
    """
    Simulate an account taking a subset of `trades` (each from simulate_symbol).
    Returns {"summary": dict, "equity": DataFrame, "log": DataFrame}.
    """
    if not trades:
        return {"summary": {"trades_taken": 0}, "equity": pd.DataFrame(), "log": pd.DataFrame()}

    weight = cfg.weight if cfg.weight is not None else 1.0 / max(1, cfg.hard_cap)
    rng = np.random.default_rng(cfg.seed)

    book = []
    for k, tr in enumerate(trades):
        look = _path_lookup(tr["path"])
        book.append(dict(
            k=k, tr=tr, look=look, keys=sorted(look.keys()),
            entry=pd.Timestamp(tr["entry_date"]), exit=pd.Timestamp(tr["exit_date"]),
        ))
    book.sort(key=lambda b: (b["entry"], b["k"]))

    by_date: Dict[pd.Timestamp, list] = {}
    for b in book:
        by_date.setdefault(b["entry"], []).append(b)

    cash = float(cfg.capital)
    peak_nav = float(cfg.capital)
    open_pos: List[dict] = []
    closed_rows: List[dict] = []
    skipped = {"cap": 0, "too_expensive": 0, "cash": 0}
    log_rows: List[dict] = []

    def mark(date):
        total = 0.0
        for pos in open_pos:
            v = _last_at_or_before(pos["b"]["look"], pos["b"]["keys"], date)
            px = v[0] if v is not None else pos["fill"]
            total += pos["shares"] * px
        return total

    def settle_until(date, inclusive):
        nonlocal cash
        still = []
        for pos in open_pos:
            ex = pos["b"]["exit"]
            if ex < date or (inclusive and ex == date):
                tr = pos["b"]["tr"]
                sell_px = cfg.cost.slipped_exit(tr["exit_price"])
                proceeds = pos["shares"] * sell_px
                buy_value = pos["shares"] * pos["fill"]
                fees = cfg.cost.fees(buy_value, proceeds)
                cash += proceeds - fees
                net = proceeds - fees - buy_value
                closed_rows.append(dict(
                    symbol=tr["symbol"], type=tr["type"], entry_date=tr["entry_date"],
                    exit_date=tr["exit_date"], shares=pos["shares"], buy_value=buy_value,
                    net_pnl=net, net_pct=net / buy_value * 100.0 if buy_value else 0.0,
                    gross_pct=tr["pnl_pct"], exit_reason=tr["exit_reason"], year=tr["year"],
                ))
            else:
                still.append(pos)
        open_pos[:] = still

    rotated: List[dict] = []

    def rotate_one(date):
        """Sell the worst dead-money candidate at the prior close to fund a new signal. Returns True if one was sold."""
        nonlocal cash
        prev = date - pd.Timedelta(days=1)
        best = None
        for pos in open_pos:
            bk = pos["b"]
            tr = bk["tr"]
            if (date - bk["entry"]).days < cfg.rotate_after_days:
                continue
            v = _last_at_or_before(bk["look"], bk["keys"], prev)
            if v is None:
                continue
            px, entry_px = v[0], tr["entry_price"]
            peak = max(c for d_, (c, _t) in bk["look"].items() if d_ <= prev) / entry_px - 1.0
            pnl = px / entry_px - 1.0
            if peak * 100.0 >= cfg.rotate_max_peak_pct or pnl * 100.0 > cfg.rotate_max_pnl_pct:
                continue
            if best is None or pnl < best[0]:
                best = (pnl, pos, px)
        if best is None:
            return False
        pnl, pos, px = best
        tr = pos["b"]["tr"]
        proceeds = pos["shares"] * cfg.cost.slipped_exit(px)
        buy_value = pos["shares"] * pos["fill"]
        fees = cfg.cost.fees(buy_value, proceeds)
        cash += proceeds - fees
        net = proceeds - fees - buy_value
        closed_rows.append(dict(
            symbol=tr["symbol"], type=tr["type"], entry_date=tr["entry_date"], exit_date=str(date.date()),
            shares=pos["shares"], buy_value=buy_value, net_pnl=net, net_pct=net / buy_value * 100.0 if buy_value else 0.0,
            gross_pct=(px / tr["entry_price"] - 1.0) * 100.0, exit_reason="Rotated (dead money)", year=tr["year"]))
        rotated.append(dict(symbol=tr["symbol"], date=date, would_have_ended=tr["pnl_pct"], sold_at_pct=(px / tr["entry_price"] - 1.0) * 100.0,
                            original_exit=tr["exit_reason"]))
        pos["b"]["exit"] = date
        open_pos.remove(pos)
        return True

    for date in sorted(by_date.keys()):
        settle_until(date, inclusive=cfg.same_day_reuse)
        todays = by_date[date]
        if cfg.priority == "vol_ratio":
            todays = sorted(todays, key=lambda b: -b["tr"].get("vol_ratio", 0.0))
        elif cfg.priority == "random":
            todays = list(todays)
            rng.shuffle(todays)

        for b in todays:
            reason = None
            for attempt in (0, 1):
                n_open = len(open_pos)
                if cfg.cap_policy == "flat":
                    allowed = n_open < cfg.hard_cap
                elif cfg.cap_policy == "documented":
                    if n_open < cfg.soft_cap:
                        allowed = True
                    elif n_open < cfg.hard_cap:
                        allowed = all(
                            (lambda v: v is not None and v[1] >= pos["b"]["tr"]["entry_price"])(
                                _last_at_or_before(pos["b"]["look"], pos["b"]["keys"], date - pd.Timedelta(days=1)))
                            for pos in open_pos)
                    else:
                        allowed = False
                else:  # as_coded
                    allowed = n_open < cfg.hard_cap
                if not allowed:
                    reason = "cap"
                else:
                    nav = cash + mark(date - pd.Timedelta(days=1))
                    fill = cfg.cost.slipped_entry(b["tr"]["entry_price"])
                    peak_nav = max(peak_nav, nav)
                    dd_now = (1.0 - nav / peak_nav) * 100.0 if peak_nav > 0 else 0.0
                    if cfg.dd_halt_pct is not None and dd_now >= cfg.dd_halt_pct:
                        reason = "dd_halt"
                        break
                    w_i = weight
                    if cfg.dd_half_pct is not None and dd_now >= cfg.dd_half_pct:
                        w_i *= 0.5
                    if cfg.risk_budget_pct is not None:
                        risk_frac = max(b["tr"].get("initial_risk_pct", 0.0) / 100.0, 0.02)
                        w_i = min(cfg.max_weight, cfg.risk_budget_pct / 100.0 / risk_frac)
                    target_value = nav * w_i
                    shares = int(min(target_value, cash) // fill)
                    if shares < 1:
                        reason = "cash" if cash < fill else "too_expensive"
                    elif shares * fill < cfg.min_fill_fraction * target_value:
                        reason = "too_expensive"
                    else:
                        reason = None
                if reason is None:
                    break
                if attempt == 0 and cfg.rotate_after_days is not None and rotate_one(date):
                    reason = None
                    continue
                break
            if reason is not None:
                skipped[reason] = skipped.get(reason, 0) + 1
                continue
            cash -= shares * fill
            open_pos.append(dict(b=b, shares=shares, fill=fill))
            log_rows.append(dict(date=date, symbol=b["tr"]["symbol"], shares=shares,
                                 value=shares * fill, nav_before=nav, open_after=len(open_pos)))

    last_exit = max(b["exit"] for b in book)
    settle_until(last_exit + pd.Timedelta(days=1), inclusive=True)

    # ---- mark-to-market equity curve over every trading date seen in any path
    taken = {r["symbol"] + "|" + r["entry_date"] for r in closed_rows}
    dates = sorted({d for b in book for d in b["keys"]})
    pos_for_curve = [b for b in book if b["tr"]["symbol"] + "|" + b["tr"]["entry_date"] in taken]
    shares_map = {}
    for r in closed_rows:
        shares_map[r["symbol"] + "|" + r["entry_date"]] = r["shares"]

    cash_events = []
    for r in closed_rows:
        cash_events.append((pd.Timestamp(r["exit_date"]), r["net_pnl"] + r["buy_value"]))
    equity_rows = []
    if closed_rows:
        first = min(pd.Timestamp(r["entry_date"]) for r in closed_rows)
        buys = sorted([(pd.Timestamp(r["entry_date"]), -r["buy_value"]) for r in closed_rows])
        sells = sorted(cash_events)
        for d in [x for x in dates if x >= first]:
            c = cfg.capital + sum(v for dd, v in buys if dd <= d) + sum(v for dd, v in sells if dd < d)
            pos_val = 0.0
            for b in pos_for_curve:
                if b["entry"] <= d <= b["exit"]:
                    v = _last_at_or_before(b["look"], b["keys"], d)
                    # entry day has no path price yet: value the position at its fill price
                    px = v[0] if v is not None else b["tr"]["entry_price"]
                    pos_val += shares_map[b["tr"]["symbol"] + "|" + b["tr"]["entry_date"]] * px
            equity_rows.append((d, c + pos_val))
    equity = pd.DataFrame(equity_rows, columns=["date", "nav"]).set_index("date") if equity_rows else pd.DataFrame()

    closed = pd.DataFrame(closed_rows)
    summary = {"trades_taken": len(closed), "skipped": skipped, "signals": len(book), "rotated": rotated}
    if len(closed):
        wins = closed[closed["net_pnl"] > 0]["net_pnl"].sum()
        losses = -closed[closed["net_pnl"] < 0]["net_pnl"].sum()
        final_nav = cfg.capital + closed["net_pnl"].sum()
        years = max(1e-9, (last_exit - min(b["entry"] for b in book)).days / 365.25)
        summary.update(
            final_nav=final_nav,
            total_return_pct=(final_nav / cfg.capital - 1.0) * 100.0,
            cagr_pct=((final_nav / cfg.capital) ** (1.0 / years) - 1.0) * 100.0 if final_nav > 0 else -100.0,
            win_rate_pct=float((closed["net_pnl"] > 0).mean() * 100.0),
            profit_factor=float(wins / losses) if losses > 0 else float("inf"),
            avg_net_pct=float(closed["net_pct"].mean()),
            worst_trade_pct=float(closed["net_pct"].min()),
            best_trade_pct=float(closed["net_pct"].max()),
            top3_share_of_profit_pct=float(
                closed.nlargest(3, "net_pnl")["net_pnl"].sum() / closed["net_pnl"].sum() * 100.0
            ) if closed["net_pnl"].sum() > 0 else float("nan"),
            years=years,
        )
        if len(equity):
            roll_max = equity["nav"].cummax()
            dd = (equity["nav"] / roll_max - 1.0) * 100.0
            summary["max_drawdown_pct"] = float(dd.min())
            by_year = {}
            for y, g in equity.groupby(equity.index.year):
                by_year[int(y)] = float((g["nav"].iloc[-1] / g["nav"].iloc[0] - 1.0) * 100.0)
            summary["by_year_pct"] = by_year
    return {"summary": summary, "equity": equity, "log": closed}
