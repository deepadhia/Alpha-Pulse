# -*- coding: utf-8 -*-
"""
core/execution_model.py

Realistic execution assumptions for backtests and for sizing the live order sheet.
Pure functions / dataclasses only: no DB, no network, no scanner imports.

All cost defaults are ASSUMPTIONS for NSE/BSE delivery trades and MUST be checked
against your broker contract note before you rely on the absolute numbers.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class CostModel:
    """Delivery-trade charges for a small retail account (rupees / fractions)."""

    brokerage_per_order: float = 20.0        # flat per executed order (verify with broker)
    stt_pct: float = 0.100                   # % of turnover, both sides for delivery
    stamp_duty_buy_pct: float = 0.015        # % of buy value
    exchange_txn_pct: float = 0.00297        # % of turnover, both sides (NSE)
    sebi_pct: float = 0.0001                 # % of turnover, both sides
    gst_pct: float = 18.0                    # % on brokerage + exchange + SEBI
    dp_charge_sell: float = 13.5             # per sell, before GST (depository charge)
    entry_slippage_pct: float = 0.30         # adverse % vs reference price on entry
    exit_slippage_pct: float = 0.30          # adverse % vs reference price on exit

    def fees(self, buy_value: float, sell_value: float) -> float:
        """Statutory + broker charges for one round trip, excluding slippage."""
        turnover = buy_value + sell_value
        brokerage = 2.0 * self.brokerage_per_order
        stt = turnover * self.stt_pct / 100.0
        stamp = buy_value * self.stamp_duty_buy_pct / 100.0
        exch = turnover * self.exchange_txn_pct / 100.0
        sebi = turnover * self.sebi_pct / 100.0
        gst = (brokerage + exch + sebi) * self.gst_pct / 100.0
        dp = self.dp_charge_sell * (1.0 + self.gst_pct / 100.0)
        return brokerage + stt + stamp + exch + sebi + gst + dp

    def slipped_entry(self, price: float) -> float:
        return price * (1.0 + self.entry_slippage_pct / 100.0)

    def slipped_exit(self, price: float) -> float:
        return price * (1.0 - self.exit_slippage_pct / 100.0)


ZERO_COST = CostModel(
    brokerage_per_order=0.0, stt_pct=0.0, stamp_duty_buy_pct=0.0, exchange_txn_pct=0.0,
    sebi_pct=0.0, gst_pct=0.0, dp_charge_sell=0.0, entry_slippage_pct=0.0, exit_slippage_pct=0.0,
)


def round_trip_cost_pct(cost: CostModel, position_value: float, gross_return_pct: float = 0.0) -> float:
    """Total round-trip cost as a % of the position value (fees + slippage)."""
    if position_value <= 0:
        return 0.0
    buy_value = position_value
    sell_value = position_value * (1.0 + gross_return_pct / 100.0)
    fee = cost.fees(buy_value, sell_value)
    slip = (cost.entry_slippage_pct + cost.exit_slippage_pct) / 100.0 * position_value
    return (fee + slip) / position_value * 100.0


def is_locked_down(open_, high, low, close, prev_close, band_pct: float = 4.5) -> bool:
    """
    Heuristic for a lower-circuit lock: the bar printed a single price (high == low)
    that is at least `band_pct` below the previous close. Locked bars cannot be exited.
    This is an approximation; exchange circuit bands are not in the candle data.
    """
    if prev_close is None or prev_close <= 0:
        return False
    return bool(high == low and close <= prev_close * (1.0 - band_pct / 100.0))


def is_locked_up(open_, high, low, close, prev_close, band_pct: float = 4.5) -> bool:
    """Heuristic upper-circuit lock: single price at least `band_pct` above prev close."""
    if prev_close is None or prev_close <= 0:
        return False
    return bool(high == low and close >= prev_close * (1.0 + band_pct / 100.0))
