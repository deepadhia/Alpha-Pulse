# -*- coding: utf-8 -*-
"""
resting_order_alerts.py -- the AUTOMATIC evening alert of policy P's resting entry orders (AGENTS rules 13 and 21).

Before this job the pre-market orders only existed when the owner ran `python order_sheet.py --capital N --log` by hand, so on a day it was
not run nothing was sent and the forward test of the validated entry path never started.  This job does the same computation on a schedule:

  python resting_order_alerts.py              # evening run: builds tomorrow's orders, sends ONE Telegram message, records them in the trade ledger
  python resting_order_alerts.py --dry-run    # compute and print only: send nothing, write nothing
  python resting_order_alerts.py --capital 100000   # account value for the 10 x 10% sizing (default env RESTING_ORDER_CAPITAL or 100000)

What it does (nothing about the rules changes; it calls the same functions as order_sheet.py):
  1. Order day = the next NSE session after the run date (weekends and exchange holidays skipped).
  2. Eligible IPOs: order_sheet.resting_candidates(order_day, Params(), refresh=True), i.e. core.honest_backtest.preview_resting_entry on completed
     sessions only (the shared candle cache lags one session; the names near their listing high are refreshed from Upstox in memory).
  3. Sizing: core.order_sheet.build_resting_entries (10 slots of 10 percent, whole shares), slots already used = ACTIVE / PAPER_ONLY positions.
  4. Freshness: data older than the last completed session by 2 or more sessions WITHHOLDS the orders (same rule as order_sheet.py); 1 session
     is reported as a note.  A stale or withheld run is sent as a loud warning and does not count as sent, so a later start retries.
  5. Records each order as an ORDER_GENERATED event in the append-only trade ledger (source resting_order_alerts.py) so fills can be reconciled
     (`python trade_ledger.py fill SYMBOL PRICE`), and writes one heartbeat per order day to system_audits (audit_type RESTING_ORDERS_ALERT).

Not done by this job: placing orders at the broker (you place the stop-limit buys), recording fills (you run trade_ledger.py fill), S3 (own job).
Evidence tier: a run of this job is PLUMBING for the forward test.  It says nothing about edge (AGENTS rule 11).
"""

import argparse
import html
import os
from datetime import datetime, timedelta, timezone

import pandas as pd

PREFIX = "P RESTING ORDERS"
AUDIT_TYPE = "RESTING_ORDERS_ALERT"
SOURCE = "resting_order_alerts.py"


def session_lag(data_asof, expected, is_trading_day) -> int:
    """Completed sessions between the median candle date (data_asof) and the last completed session (expected); 99 when unknown."""
    if data_asof is None:
        return 99
    return max(0, sum(1 for d in pd.date_range(pd.Timestamp(data_asof) + pd.Timedelta(days=1), pd.Timestamp(expected))
                      if is_trading_day(d.date())))


def build_message(entries: list, skipped: list, order_day: pd.Timestamp, data_asof, expected: pd.Timestamp, lag: int,
                  warnings=None, n_held: int = 0, capital: float = 0.0, refresh_note: str = None, extra_lines: list = None) -> dict:
    """
    Pure message builder (Telegram HTML, all dynamic text escaped).  Returns dict(text, n_orders, withheld, actionable).
    withheld = the data is 2 or more sessions old: no order is listed and the run is not recorded as sent.
    """
    e = html.escape
    withheld = lag >= 2
    asof_s = str(pd.Timestamp(data_asof).date()) if data_asof is not None else "n/a"
    lines = [
        f"🎯 <b>{PREFIX}</b>  (policy P, validated entry path)",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📅 <b>Order session:</b> {order_day.date()}  •  <b>Data:</b> {asof_s}"
        + (f"  ⚠️ <b>STALE: expected {expected.date()}</b>" if withheld else ("  (1 session behind, see note)" if lag == 1 else "  (✅ Fresh)")),
        f"💼 Capital Rs {capital:,.0f}  •  Slots used {n_held}",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    lines += list(extra_lines or [])           # today's automatic forward-test fills / exits / expiries (core.resting_runtime.format_exec_lines)
    if withheld:
        lines += ["", f"🚫 <b>ORDERS WITHHELD</b>: the candle data is {('many' if lag >= 99 else lag)} session(s) older than {expected.date()}. "
                      f"Orders built on stale data can name the wrong levels. Nothing was recorded; the job will retry."]
    elif entries:
        lines += ["", "🛒 <b>PLACE BEFORE THE OPEN</b> (stop-limit BUY, validity DAY):"]
        for x in entries:
            lines.append(
                f"  • <b>{e(x.symbol)}</b>: BUY {x.quantity} qty · Trigger ₹{x.trigger:.2f} · Limit ₹{x.limit:.2f}\n"
                f"    Initial stop alert ₹{x.stop_alert:.2f} (risk {x.risk_pct_of_capital:.2f}% of capital) · age {x.age}d · R:R {x.rr:.2f}"
            )
        lines += ["", "ℹ️ <i>Fills and exits are recorded automatically from the real book (open-time bid/ask quotes, 1-minute bars). Use trade_ledger.py fill / sell only "
                      "to record a REAL broker fill or sale that differs. Pass mark: fills within +0.5% of the trigger.</i>"]
    else:
        lines += ["", "💤 <i>No eligible resting orders for the next session.</i>"]
    if skipped and not withheld:
        lines += ["", "⏭️ <b>Skipped:</b> " + "; ".join(e(str(s)) for s in skipped[:6])]
    if lag == 1 and not withheld:
        lines += ["", f"ℹ️ <i>Data is as of {asof_s}, one session behind {expected.date()} (the shared cache lags by design; names near their listing high "
                      f"were refreshed). Trigger levels do not change, but eligibility ignores {expected.date()}'s bar for the other names.</i>"]
    if refresh_note:
        lines.append(f"ℹ️ <i>{e(refresh_note)}</i>")
    for w in (warnings or []):
        lines.append(f"  ! {e(str(w))}")
    lines += ["", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]
    return dict(text="\n".join(lines), n_orders=0 if withheld else len(entries), withheld=withheld,
                actionable=bool(withheld or entries))


def already_sent(audits_col, order_day: pd.Timestamp) -> bool:
    """True when a FRESH (not stale, not withheld) alert for this order day was already sent: the second start must not repeat it."""
    try:
        doc = audits_col.find_one({"audit_id": f"resting_orders_{order_day.date()}"})
    except Exception:
        return False
    return bool(doc and doc.get("sent") and not doc.get("withheld"))


def ledger_has_order(ledger_col, symbol: str, order_day: pd.Timestamp) -> bool:
    """
    True when this job already recorded an ORDER_GENERATED event for `symbol` and `order_day`.  The ledger is insert-only (AGENTS rule 32), so a retry
    after a failed Telegram send must not append the same order again.  A lookup that fails counts as "not recorded" only when the collection is
    missing; any other error is raised so the run fails loudly instead of risking a duplicate.
    """
    if ledger_col is None:
        return False
    return ledger_col.find_one({"event_type": "ORDER_GENERATED", "source": SOURCE, "symbol": symbol,
                                "payload.order_date": str(order_day.date())}) is not None


def record_run(audits_col, order_day: pd.Timestamp, **fields) -> None:
    doc = dict(audit_type=AUDIT_TYPE, date=str(order_day.date()), timestamp=datetime.now(timezone.utc), triggered_by=SOURCE, **fields)
    audits_col.update_one({"audit_id": f"resting_orders_{order_day.date()}"}, {"$set": doc}, upsert=True)


def evening_execution(run_day: pd.Timestamp, capital: float, dry_run: bool) -> dict:
    """
    The automatic forward test, evening half (core/resting_jobs.run_evening): price the orders that did not fill at the open from the day's 1-minute
    bars, check the fill day's close against the initial stop, settle exits the open-time job could not.  Candles of the names involved are refreshed in
    memory so the run day's bar is there.
    """
    import s3_paper_alerts as S3A
    from core import resting_runtime as rt, s3_refresh
    from core.resting_jobs import run_evening
    from db import get_instrument_key_mapping
    token = os.getenv("UPSTOX_ACCESS_TOKEN")
    now_ist = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=5, minutes=30)))
    deps0 = rt.build_deps(SOURCE, token, capital, dry_run=dry_run, now_ist=now_ist)
    needed = rt.symbols_needed(deps0, run_day.date())
    candles = {s: df for s in needed for df in [rt.daily_from_cache(s)] if df is not None}
    if candles:
        candles, _frep = s3_refresh.refresh_candles(candles, set(), needed, s3_refresh.last_complete_date(now_ist, run_day), token,
                                                     get_instrument_key_mapping(), is_holiday=S3A._is_holiday)
    deps = rt.build_deps(SOURCE, token, capital, dry_run=dry_run, now_ist=now_ist, candles=candles)
    return run_evening(deps, run_day.date())


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--capital", type=float, default=float(os.getenv("RESTING_ORDER_CAPITAL", "100000")))
    ap.add_argument("--dry-run", action="store_true", help="print only: send nothing, write nothing")
    ap.add_argument("--today", default=None, help="override the run date (YYYY-MM-DD), for testing")
    ap.add_argument("--force", action="store_true", help="run even if a fresh alert for the order day was already sent")
    a = ap.parse_args(argv)
    try:
        import sys
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")     # emoji in the message must not crash a Windows console run
    except Exception:
        pass

    import order_sheet as osh
    from core.honest_backtest import Params
    from core.order_sheet import SheetConfig, build_resting_entries
    from core.trade_guard import default_is_trading_day, previous_trading_day
    from s3_paper_alerts import enable_exchange_holidays, next_session, _closed
    from db import positions_col, system_audits_col, trade_ledger_col, SCANNER_VERSION
    from utils import send_telegram_msg

    if positions_col is None or system_audits_col is None:
        raise SystemExit("MongoDB is not connected")
    if not enable_exchange_holidays():
        print("WARNING: NSE holiday list unavailable, only weekends are skipped when picking the order day")
    run_day = pd.Timestamp(a.today) if a.today else pd.Timestamp(datetime.now().date())
    if _closed(run_day) and not a.force:
        print(f"{run_day.date()} is a weekend or an exchange holiday: no session to report (use --force to run anyway)")
        return
    order_day = next_session(run_day)
    if not a.dry_run and not a.force and already_sent(system_audits_col, order_day):
        print(f"Resting-order alert for {order_day.date()} was already sent with fresh data; nothing to do (use --force to run again)")
        return

    # automatic forward test first: fills and exits settled today change which names are held, so they must precede tomorrow's order list
    from core import resting_runtime as rt
    exec_exc, exec_lines = None, []
    try:
        exec_rep = evening_execution(run_day, a.capital, a.dry_run)
        exec_lines = rt.format_exec_lines(exec_rep, "after the close")
    except Exception as e:
        exec_exc = e
        exec_lines = ["", "⚠️ <b>Forward-test settlement FAILED</b>: " + html.escape(f"{type(e).__name__}: {e}") + " (orders below are still listed; the run will fail)"]
    cfg = SheetConfig(capital=a.capital)
    held = {d["symbol"] for d in positions_col.find({"status": {"$in": ["ACTIVE", "PAPER_ONLY"]}}, {"_id": 0, "symbol": 1})}
    cands, notes, data_asof, rep = osh.resting_candidates(order_day, Params(), with_asof=True, refresh=True)
    expected = pd.Timestamp(previous_trading_day(order_day.date()))
    lag = session_lag(data_asof, expected, default_is_trading_day)
    refresh_note = None
    if rep is not None:
        refresh_note = (f"candle refresh: {len(rep['selected'])} name(s) near their listing high, {rep['fetched']} fetched, "
                        f"{rep['updated']} updated, {len(rep['failed'])} failed" + (f"; {rep['note']}" if rep.get("note") else ""))
        print(f"[{refresh_note}]")
    entries, skipped, warnings = ([], [], []) if lag >= 2 else build_resting_entries(cands, slots_used=len(held), cfg=cfg)
    msg = build_message(entries, skipped, order_day, data_asof, expected, lag, warnings, len(held), a.capital, refresh_note, extra_lines=exec_lines)
    print(msg["text"])
    if a.dry_run:
        if exec_exc is not None:
            raise exec_exc
        return

    ledger_error = None
    if entries and not msg["withheld"]:
        asof_s = str(pd.Timestamp(data_asof).date()) if data_asof is not None else None
        try:
            from core.ledger import append as ledger_append, ensure_indexes as ledger_indexes
            if trade_ledger_col is not None:
                ledger_indexes(trade_ledger_col)
            n_new = 0
            for x in entries:
                if ledger_has_order(trade_ledger_col, x.symbol, order_day):
                    continue                    # a retry (e.g. after a failed Telegram send) must not duplicate the order in the insert-only ledger
                ev = ledger_append(trade_ledger_col, "ORDER_GENERATED", x.symbol,
                                   dict(order_date=str(order_day.date()), data_asof=asof_s, capital=a.capital, **x.__dict__),
                                   SOURCE, code_version=SCANNER_VERSION)
                if ev is None:
                    raise RuntimeError(f"ledger append returned nothing for {x.symbol}")
                n_new += 1
            print(f"recorded {n_new} new ORDER_GENERATED event(s) in the trade ledger ({len(entries) - n_new} already recorded)")
        except Exception as led_err:
            ledger_error = f"{type(led_err).__name__}: {led_err}"
            print(f"(ledger not fully written: {ledger_error})")
            # the orders still go out (they are what you act on), but the gap must be visible, not silent
            msg["text"] += "\n⚠️ <b>Ledger not fully written</b> (" + html.escape(ledger_error) + "); a fill cannot be reconciled until this is fixed."

    sent = send_telegram_msg(msg["text"], disable_notification=not msg["actionable"])
    record_run(system_audits_col, order_day, sent=bool(sent), withheld=msg["withheld"], n_orders=msg["n_orders"], n_held=len(held),
               capital=a.capital, data_asof=(pd.Timestamp(data_asof).to_pydatetime() if data_asof is not None else None), lag=lag,
               ledger_ok=ledger_error is None, ledger_error=ledger_error,
               exec_ok=exec_exc is None, exec_error=(f"{type(exec_exc).__name__}: {exec_exc}" if exec_exc is not None else None))
    if exec_exc is not None:
        raise exec_exc                          # fail the workflow run so the failure is visible; the alert above was still sent


if __name__ == "__main__":
    main()
