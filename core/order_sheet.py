# -*- coding: utf-8 -*-
"""
core/order_sheet.py

Turns scanner state into concrete orders for MANUAL execution with resting stops.

Today the system only sends alerts, so a stop is "a message you must act on".  That means a
gap-down is sold at the next open at best, and the "12% max loss" is not a ceiling.  The order
sheet closes that gap: for every position it produces the trigger/limit pair for a broker GTT /
stop-limit order, and for every new signal the whole-share quantity for the account size.

Pure functions (no DB, no network) so they are fully testable.  `order_sheet.py` at the repo
root is the thin CLI that feeds it from MongoDB (read-only).
"""

import math
from dataclasses import dataclass, field
from typing import List, Optional

from core.portfolio_caps import DEFAULT_HARD_ACTIVE_POSITIONS, DEFAULT_MAX_ACTIVE_POSITIONS

TICK = 0.05


def round_tick(price: float, tick: float = TICK, direction: str = "nearest") -> float:
    if price is None or price <= 0:
        return 0.0
    q = price / tick
    if direction == "down":
        q = math.floor(q + 1e-9)
    elif direction == "up":
        q = math.ceil(q - 1e-9)
    else:
        q = round(q)
    return round(q * tick, 2)


@dataclass(frozen=True)
class SheetConfig:
    capital: float = 100000.0              # current total account value (cash + positions)
    hard_cap: int = DEFAULT_HARD_ACTIVE_POSITIONS     # core/portfolio_caps.py: 10 slots, weight 1 / hard_cap = 10%
    soft_cap: int = DEFAULT_MAX_ACTIVE_POSITIONS
    weight: Optional[float] = None         # fraction of capital per position; default 1 / hard_cap
    min_fill_fraction: float = 0.5         # skip if whole shares reach less than this of the target
    stop_limit_buffer_pct: float = 2.0     # sell limit sits this far below the trigger so small gaps still fill
    entry_limit_buffer_pct: float = 0.0    # extra room above the signal's own limit_buy_price
    max_total_open_risk_pct: float = 12.0  # warn if sum of per-position risk exceeds this % of capital
    use_tier_size: bool = False            # scale weight by the signal's position_size_pct / 100
    stop_mode: str = "resting"             # "resting" (GTT at the trailing level) | "close_alert" (see below)
    disaster_buffer_pct: float = 8.0       # close_alert mode: optional resting stop this far below the alert level


@dataclass
class StopOrder:
    symbol: str
    quantity: int
    trigger: float
    limit: float
    note: str = ""
    alert_level: Optional[float] = None    # close_alert mode: sell at the next open if the CLOSE is at/below this


@dataclass
class EntryOrder:
    symbol: str
    quantity: int
    limit_buy: float
    est_value: float
    stop_trigger: float
    stop_limit: float
    risk_rupees: float
    risk_pct_of_capital: float
    note: str = ""


@dataclass
class RestingEntry:
    symbol: str
    quantity: int
    trigger: float            # stop-limit BUY trigger (listing high + 1.5%)
    limit: float              # stop-limit BUY limit  (listing high + 2.5%): gap-ups above it do not fill
    est_fill: float
    est_value: float
    stop_alert: float         # initial stop: decision level on the close once filled
    risk_rupees: float
    risk_pct_of_capital: float
    age: int
    rr: float
    note: str = ""


@dataclass
class SheetResult:
    stops: List[StopOrder] = field(default_factory=list)
    entries: List[EntryOrder] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    open_risk_rupees: float = 0.0
    open_risk_pct: float = 0.0


def stop_order_levels(trigger: float, buffer_pct: float):
    """(trigger, limit) for a sell stop-limit: trigger rounded down, limit `buffer_pct` below it."""
    trig = round_tick(trigger, direction="down")
    lim = round_tick(trig * (1.0 - buffer_pct / 100.0), direction="down")
    return trig, lim


def size_shares(capital: float, price: float, weight: float, available_cash: Optional[float] = None,
                min_fill_fraction: float = 0.5):
    """Whole shares for a target weight. Returns (shares, reason_if_zero)."""
    if price <= 0:
        return 0, "invalid price"
    target = capital * weight
    budget = min(target, available_cash) if available_cash is not None else target
    shares = int(budget // price)
    if shares < 1:
        return 0, f"one share costs Rs {price:,.2f} > budget Rs {budget:,.0f}"
    if shares * price < min_fill_fraction * target:
        return 0, (f"whole shares reach only Rs {shares * price:,.0f} of the Rs {target:,.0f} target "
                   f"(price too high for this account size)")
    return shares, ""


def build_sheet(positions: List[dict], signals: List[dict], cfg: SheetConfig = SheetConfig(),
                guard=None) -> SheetResult:
    """
    positions: open ACTIVE positions: symbol, quantity (shares held), entry_price, trailing_stop, current_price.
               `quantity` is YOUR actual share count from the broker; the scanner does not know it.
    signals  : new ACTIVE signals to enter: symbol, entry_price, limit_buy_price, stop_loss, position_size_pct (opt).
    """
    res = SheetResult()
    weight = cfg.weight if cfg.weight is not None else 1.0 / max(1, cfg.hard_cap)

    invested = 0.0
    for p in positions:
        qty = int(p.get("quantity") or 0)
        trailing = float(p["trailing_stop"])
        cur = float(p.get("current_price") or p.get("entry_price") or 0.0)
        note = ""
        if cfg.stop_mode == "close_alert":
            alert = round_tick(trailing, direction="down")
            trig, lim = stop_order_levels(trailing * (1.0 - cfg.disaster_buffer_pct / 100.0), cfg.stop_limit_buffer_pct)
            if cur and alert >= cur:
                note = "LAST PRICE AT/BELOW THE ALERT LEVEL: if it closes there, sell at the next open"
                res.warnings.append(f"{p['symbol']}: alert level {alert} is at or above last price {cur}")
            if qty <= 0:
                note = (note + " | " if note else "") + "enter your held quantity"
            res.stops.append(StopOrder(p["symbol"], qty, trig, lim, note, alert_level=alert))
            risk_level = alert
        else:
            trig, lim = stop_order_levels(trailing, cfg.stop_limit_buffer_pct)
            if cur and trig >= cur:
                note = "TRIGGER AT/ABOVE LAST PRICE: broker will reject a GTT sell; exit manually now"
                res.warnings.append(f"{p['symbol']}: stop {trig} is at or above last price {cur}")
            if qty <= 0:
                note = (note + " | " if note else "") + "enter your held quantity"
            res.stops.append(StopOrder(p["symbol"], qty, trig, lim, note))
            risk_level = trig
        if qty > 0 and cur:
            invested += qty * cur
            res.open_risk_rupees += qty * max(0.0, cur - risk_level)

    slots_used = len(positions)
    if guard is not None and guard.level != "OK":
        res.warnings.append(f"RISK GUARD {guard.level}: " + "; ".join(guard.reasons))
    for s in signals:
        sym = s["symbol"]
        if guard is not None and guard.halt_new_entries:
            res.skipped.append(f"{sym}: new entries halted by the risk guard ({'; '.join(guard.reasons)})")
            continue
        if slots_used >= cfg.hard_cap:
            res.skipped.append(f"{sym}: book full ({slots_used}/{cfg.hard_cap} slots)")
            continue
        w = weight * (float(s.get("position_size_pct", 100)) / 100.0 if cfg.use_tier_size else 1.0)
        entry = float(s.get("limit_buy_price") or s["entry_price"]) * (1.0 + cfg.entry_limit_buffer_pct / 100.0)
        limit_buy = round_tick(entry, direction="up")
        shares, why = size_shares(cfg.capital, limit_buy, w, available_cash=max(0.0, cfg.capital - invested),
                                  min_fill_fraction=cfg.min_fill_fraction)
        if shares < 1:
            res.skipped.append(f"{sym}: {why}")
            continue
        trig, lim = stop_order_levels(float(s["stop_loss"]), cfg.stop_limit_buffer_pct)
        risk = shares * max(0.0, limit_buy - trig)
        res.entries.append(EntryOrder(
            symbol=sym, quantity=shares, limit_buy=limit_buy, est_value=shares * limit_buy,
            stop_trigger=trig, stop_limit=lim, risk_rupees=risk,
            risk_pct_of_capital=risk / cfg.capital * 100.0 if cfg.capital else 0.0,
            note="place the stop GTT the moment the buy fills"))
        invested += shares * limit_buy
        res.open_risk_rupees += risk
        slots_used += 1

    res.open_risk_pct = res.open_risk_rupees / cfg.capital * 100.0 if cfg.capital else 0.0
    if res.open_risk_pct > cfg.max_total_open_risk_pct:
        res.warnings.append(
            f"Total open risk {res.open_risk_pct:.1f}% of capital exceeds {cfg.max_total_open_risk_pct:.0f}% "
            f"if every stop fills at its trigger; gaps make it worse")
    if invested > cfg.capital * 1.0001:
        res.warnings.append(f"Invested Rs {invested:,.0f} exceeds capital Rs {cfg.capital:,.0f}")
    return res


def format_sheet(res: SheetResult, cfg: SheetConfig = SheetConfig()) -> str:
    lines = [f"ORDER SHEET  (capital Rs {cfg.capital:,.0f}, per-slot weight "
             f"{(cfg.weight if cfg.weight is not None else 1.0 / cfg.hard_cap) * 100:.1f}%)", ""]
    if cfg.stop_mode == "close_alert":
        lines.append("STOP ALERT LEVELS (decide on the CLOSE; if the close is at/below the level, sell at the NEXT OPEN):")
        if res.stops:
            for o in res.stops:
                lines.append(f"  {o.symbol:<12} qty {o.quantity:>5}  alert level {o.alert_level:>9.2f}"
                             + (f"   <- {o.note}" if o.note else ""))
        else:
            lines.append("  (no open positions)")
        lines.append("")
        lines.append(f"OPTIONAL DISASTER STOPS (GTT sell, {cfg.disaster_buffer_pct:.0f}% below the alert level; insurance only,")
        lines.append("  it will NOT fill if the stock gaps below its limit or is locked at the lower circuit):")
        for o in res.stops:
            lines.append(f"  {o.symbol:<12} qty {o.quantity:>5}  trigger {o.trigger:>9.2f}  limit {o.limit:>9.2f}")
    else:
        lines.append("RESTING STOPS (place / modify GTT sell, stop-limit):")
        if res.stops:
            for o in res.stops:
                lines.append(f"  {o.symbol:<12} qty {o.quantity:>5}  trigger {o.trigger:>9.2f}  limit {o.limit:>9.2f}"
                             + (f"   <- {o.note}" if o.note else ""))
        else:
            lines.append("  (no open positions)")
    lines.append("")
    lines.append("NEW ENTRIES (limit buy, then immediately the stop below):")
    if res.entries:
        for e in res.entries:
            lines.append(f"  {e.symbol:<12} BUY {e.quantity:>5} @ limit {e.limit_buy:>9.2f}  (~Rs {e.est_value:,.0f})"
                         f"  stop trigger {e.stop_trigger:.2f} / limit {e.stop_limit:.2f}"
                         f"  risk Rs {e.risk_rupees:,.0f} ({e.risk_pct_of_capital:.2f}% of capital)")
    else:
        lines.append("  (none)")
    if res.skipped:
        lines.append("")
        lines.append("SKIPPED:")
        lines += [f"  {x}" for x in res.skipped]
    lines.append("")
    lines.append(f"Open risk if every stop fills at its trigger: Rs {res.open_risk_rupees:,.0f} "
                 f"({res.open_risk_pct:.1f}% of capital)")
    if res.warnings:
        lines.append("")
        lines.append("WARNINGS:")
        lines += [f"  ! {w}" for w in res.warnings]
    lines.append("")
    if cfg.stop_mode == "close_alert":
        lines.append("Why close-based: in the 2022-26 replay an intraday stop was hit by wicks that recovered by the close; the "
                     "close-based alert earned about 2pp more per trade (paired, 5 of 5 years). The cost is a fatter left tail "
                     "(worst replayed loss about -17% vs -14%), which position size, not the stop, must absorb.")
    else:
        lines.append("Reminder: a stop-limit does not fill if the stock opens BELOW the limit price. "
                     "Check the open on gap days and sell manually if needed.")
    return "\n".join(lines)


def build_resting_entries(candidates: List[dict], slots_used: int, cfg: SheetConfig = SheetConfig(),
                          guard=None, available_cash: Optional[float] = None):
    """
    Pre-market stop-limit BUY orders for the day (execution policy, see LEARNINGS.md section 12).

    candidates: dicts from core.honest_backtest.preview_resting_entry plus a `symbol`
                (trigger, limit, fill, stop, risk_pct, rr, age, last_close, already_above_trigger).
    Orders are ranked by how close the last close is to the trigger (names already above the trigger fill at the
    open, so they come first) and capped at the free slots: broker buy orders are cash-funded, so more orders than
    free slots could not all be placed anyway.  Returns (entries, skipped, warnings).
    """
    entries: List[RestingEntry] = []
    skipped: List[str] = []
    warnings: List[str] = []
    if guard is not None and guard.halt_new_entries:
        return entries, [f"all: new entries halted by the risk guard ({'; '.join(guard.reasons)})"], warnings
    weight = cfg.weight if cfg.weight is not None else 1.0 / max(1, cfg.hard_cap)
    free = max(0, cfg.hard_cap - slots_used)
    cash = cfg.capital if available_cash is None else available_cash
    ranked = sorted(candidates, key=lambda c: (not c.get("already_above_trigger", False),
                                                abs(c["trigger"] / c["last_close"] - 1.0) if c.get("last_close") else 9.9))
    for c in ranked:
        sym = c["symbol"]
        if len(entries) >= free:
            skipped.append(f"{sym}: no free slot ({slots_used}/{cfg.hard_cap} used + {len(entries)} orders)")
            continue
        limit = round_tick(float(c["limit"]), direction="down")
        trig = round_tick(float(c["trigger"]), direction="up")
        if limit < trig:
            limit = trig
        shares, why = size_shares(cfg.capital, limit, weight, available_cash=max(0.0, cash),
                                  min_fill_fraction=cfg.min_fill_fraction)
        if shares < 1:
            skipped.append(f"{sym}: {why}")
            continue
        est_fill = float(c.get("fill") or trig)
        stop = round_tick(float(c["stop"]), direction="down")
        risk = shares * max(0.0, est_fill - stop)
        note = "already above the trigger: fills at the open if the open is within the limit" if c.get("already_above_trigger") else ""
        entries.append(RestingEntry(sym, shares, trig, limit, est_fill, shares * est_fill, stop, risk,
                                    risk / cfg.capital * 100.0 if cfg.capital else 0.0, int(c.get("age", 0)),
                                    float(c.get("rr", 0.0)), note))
        cash -= shares * limit
    return entries, skipped, warnings


def format_resting_entries(entries: List[RestingEntry], skipped: List[str], cfg: SheetConfig = SheetConfig(),
                           warnings: Optional[List[str]] = None, title: Optional[str] = None,
                           footer: Optional[List[str]] = None) -> str:
    """`title` / `footer` replace the policy-P header and explanation (used by the PAPER_ONLY S3 section); defaults keep P's text byte for byte."""
    lines = [title or "PRE-MARKET RESTING ENTRY ORDERS  (stop-limit BUY, DAY validity; cancel what is unfilled before the close)", ""]
    if entries:
        for e in entries:
            lines.append(f"  {e.symbol:<12} BUY {e.quantity:>5}  trigger {e.trigger:>9.2f}  limit {e.limit:>9.2f}  "
                         f"(~Rs {e.est_value:,.0f}, age {e.age}d{f', R:R {e.rr:.2f}' if e.rr else ''})  initial stop alert {e.stop_alert:.2f}  "
                         f"risk ~Rs {e.risk_rupees:,.0f} ({e.risk_pct_of_capital:.2f}% of capital)"
                         + (f"  <- {e.note}" if e.note else ""))
    else:
        lines.append("  (no eligible names)")
    if skipped:
        lines.append("")
        lines.append("  not placed:")
        lines += [f"    {x}" for x in skipped]
    lines.append("")
    if footer is not None:
        lines += footer
        if warnings:
            lines += [f"  ! {w}" for w in warnings]
        return "\n".join(lines)
    lines.append("  Why: replaying 2022-26, buying at the trigger with a resting order beat buying after the alert (about +3.2% "
                 "fills over the listing high) by about 1.3-1.9pp net per trade, paired, positive in every year. The edge "
                 "is unproven in absolute terms (see LEARNINGS.md section 12). Record every fill price and time.")
    lines.append("  Eligibility uses only prior-session data (liquidity, prior-day volume, stop, R:R, leader score); there "
                 "is deliberately no same-day volume gate because a pre-placed order cannot know it.")
    if warnings:
        lines += [f"  ! {w}" for w in warnings]
    return "\n".join(lines)
