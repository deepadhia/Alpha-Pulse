# -*- coding: utf-8 -*-
"""
s3_paper_alerts.py -- the daily alert of the PAPER_ONLY S3 book (LEARNINGS 20.4).  Separate from policy P: its own message prefix, optional
the same Telegram bot and chat as the rest of the system (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID), its own exit logic (X1: close-based stop
alert and trailing ratchet only) and its own storage (s3_paper_* collections).  Nothing here touches policy P's positions, signals or scanners.

  python s3_paper_alerts.py                  # evening run: reads open S3 paper positions, prints/sends the alert, stores tomorrow's orders
  python s3_paper_alerts.py --dry-run        # compute and print, send nothing, write nothing
  python s3_paper_alerts.py --capital 100000 # paper capital for the 10 x 10% sizing (default env S3_PAPER_CAPITAL or 100000)

What it sends (one message a day, silent unless something needs action):
  1. STOP ALERTS: open S3 paper positions whose last close is at or below the X1 stop (mark the exit at the next open, then
     `python s3_paper.py exit SYMBOL PRICE`).
  2. Trailing levels of every open position, with open P&L and peak run-up.
  3. Orders for the NEXT session: stop-limit BUY at highest-since-listing +1.5% (limit +2.5%) for IPOs aged 61-400 days with 20-session mean
     turnover <= Rs 25 Cr.  They are stored in s3_paper_orders; after a paper fill run `python s3_paper.py fill SYMBOL PRICE`.
  4. A data-freshness line: the candles must hold the last completed session, otherwise the message says the data is stale.  The shared candle
     cache only holds sessions up to the previous day at 18:50 IST, so this job first fetches the missing completed bars for the held positions and
     the names near their pivot from Upstox, in memory only (core/s3_refresh.py; needs UPSTOX_ACCESS_TOKEN).  If that fails the message says STALE.
Every run writes one heartbeat to s3_paper_runs, so "did the job run, with which data" can be audited (s3_scorecard.py prints it).
"""

import argparse
import html
import os
from datetime import datetime, timedelta, timezone

import pandas as pd

PREFIX = "S3 PAPER"


_is_holiday = None      # optional predicate(Timestamp) -> bool, set by enable_exchange_holidays(); None = weekends only (pure, used by the tests)


def enable_exchange_holidays() -> bool:
    """
    Make next_session / previous_session skip NSE trading holidays (utils.get_dynamic_nse_holidays: memory, Mongo, Upstox, static fallback).
    Before 2026-11-06 only weekends were skipped, so the evening before a holiday (Mon 2026-11-09, Tue 11-10 is closed) produced orders and a
    freshness check for a day with no session: the orders would never fill, no candle ever arrives for that date, and the data was flagged STALE.
    Returns False (weekends only) when the holiday source cannot be loaded.
    """
    global _is_holiday
    try:
        from utils import get_dynamic_nse_holidays
        get_dynamic_nse_holidays(datetime.now().year)           # fail now, not in the middle of a date loop
        _is_holiday = lambda d: pd.Timestamp(d).strftime("%Y-%m-%d") in get_dynamic_nse_holidays(pd.Timestamp(d).year)
        return True
    except Exception:
        _is_holiday = None
        return False


def _closed(d: pd.Timestamp) -> bool:
    if d.weekday() >= 5:
        return True
    try:
        return bool(_is_holiday and _is_holiday(d))
    except Exception:
        return False


def next_session(today: pd.Timestamp) -> pd.Timestamp:
    """Next trading day after `today`: skips weekends and, after enable_exchange_holidays(), NSE holidays."""
    d = today + pd.Timedelta(days=1)
    for _ in range(10):
        if not _closed(d):
            break
        d += pd.Timedelta(days=1)
    return d


def previous_session(day: pd.Timestamp) -> pd.Timestamp:
    d = day - pd.Timedelta(days=1)
    for _ in range(10):
        if not _closed(d):
            break
        d -= pd.Timedelta(days=1)
    return d


def build_message(rows: list, entries: list, order_day: pd.Timestamp, data_asof, warnings=None, settled: dict = None) -> dict:
    """
    Pure message builder.  Returns dict(text, n_alerts, n_orders, n_open, stale, actionable).  `rows` as order_sheet.s3_position_rows,
    `entries` as core.order_sheet.RestingEntry.  Text is plain with minimal HTML (Telegram HTML mode), all dynamic text escaped.
    """
    e = html.escape
    alerts = [r for r in rows if not r.get("error") and r.get("status") == "OPEN" and r.get("alert")]
    exited = [r for r in rows if not r.get("error") and r.get("status") == "EXITED"]
    errors = [r for r in rows if r.get("error") and not r.get("pending")]
    pending = [r for r in rows if r.get("pending")]
    open_rows = [r for r in rows if not r.get("error") and r.get("status") == "OPEN"]
    expected = previous_session(order_day)
    stale = data_asof is None or pd.Timestamp(data_asof) < expected

    lines = [
        f"📋 <b>{PREFIX}</b>  (paper only, not P)",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📅 <b>Target Session:</b> {order_day.date()}  •  <b>Data:</b> {pd.Timestamp(data_asof).date() if data_asof is not None else 'n/a'}"
        + ("  ⚠️ <b>STALE: expected " + str(expected.date()) + "; do not trust today's orders</b>" if stale else "  (✅ Fresh)"),
        "ℹ️ <i>Automated Stage-2 (61–400d) paper test. No real capital allocated.</i>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]

    settled = settled or {}
    if settled.get("filled"):
        lines += ["", "🟢 <b>PAPER FILLS RECORDED</b> (auto: the resting order would have filled):"]
        for x in settled["filled"]:
            lines.append(f"  • {e(x)}")

    if settled.get("expired"):
        lines += ["", "⏳ <b>ORDERS THAT DID NOT FILL</b> (expired at market close):"]
        for x in settled["expired"]:
            lines.append(f"  • {e(x)}")

    if alerts:
        lines += ["", "🛑 <b>STOP ALERT: mark the exit at the next open</b>"]
        for r in alerts:
            pnl_str = f"{r['pnl_pct']:+.1f}%"
            lines.append(
                f"  • <b>{e(r['symbol'])}</b>: Close ₹{r['last_close']:.2f} at/below alert ₹{r['trailing']:.2f} ({pnl_str})\n"
                f"    Action: Sell at tomorrow's open -> <code>python s3_paper.py exit {e(r['symbol'])} PRICE</code>"
            )

    if exited:
        lines += ["", "🚪 <b>ENGINE SAYS EXITED (record it):</b>"]
        for r in exited:
            pnl_str = f"{r['pnl_pct']:+.1f}%"
            lines.append(f"  • <b>{e(r['symbol'])}</b>: {str(r['exit_date'])[:10]} @ ₹{r['exit_price']:.2f} ({e(r['exit_reason'])}) {pnl_str}")

    if open_rows:
        lines += ["", "📈 <b>OPEN POSITIONS (alert level = trailing stop)</b>"]
        for r in open_rows:
            pnl_color = "🟢" if r.get('pnl_pct', 0) >= 0 else "🔴"
            pnl_str = f"{r.get('pnl_pct', 0):+.1f}%"
            peak_str = f"{r.get('max_runup', 0):+.1f}%"
            lines.append(
                f"  • <b>{e(r['symbol'])}</b>: Close ₹{r['last_close']:.2f} · {pnl_color} <b>{pnl_str}</b> (Peak: {peak_str}) · Trailing Stop: ₹{r['trailing']:.2f}"
            )

    if pending:
        lines += ["", "⏳ <b>FILLED TODAY, WAITING FOR THE CANDLE CACHE:</b> " + ", ".join(e(r["symbol"]) for r in pending)]

    if errors:
        lines += ["", "⚠️ <b>CANNOT EVALUATE:</b>"]
        for r in errors:
            lines.append(f"  • <b>{e(r['symbol'])}</b>: {e(r['error'])}")

    if entries:
        lines += ["", "🎯 <b>RESTING BUY ORDERS</b> (stop-limit, DAY)"]
        for x in entries:
            lines.append(
                f"  • <b>{e(x.symbol)}</b>: BUY {x.quantity} qty · Trigger: ₹{x.trigger:.2f} · Limit: ₹{x.limit:.2f}\n"
                f"    Initial Stop: ₹{x.stop_alert:.2f} (Risk: {x.risk_pct_of_capital:.2f}% of capital)"
            )
    else:
        lines += ["", "💤 <i>No S3 orders for the next session.</i>"]

    for w in (warnings or []):
        lines.append(f"  ! {e(w)}")

    lines += ["", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]

    actionable = bool(alerts or exited or entries or errors or stale or settled.get("filled"))
    return dict(text="\n".join(lines), n_alerts=len(alerts), n_orders=len(entries), n_open=len(open_rows), stale=stale, actionable=actionable)


def rf_report(res: dict) -> dict:
    """The refresh report of a previous s3_collect result (reused when the collect is repeated on frozen candles)."""
    return res.get("refresh") or dict(selected=set(), fetched=0, updated=0, failed={}, note=None)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--capital", type=float, default=float(os.getenv("S3_PAPER_CAPITAL", "100000")))
    ap.add_argument("--dry-run", action="store_true", help="print only: send nothing, store nothing")
    ap.add_argument("--today", default=None, help="override the run date (YYYY-MM-DD), for testing")
    ap.add_argument("--force", action="store_true", help="run even if today's alert was already sent with fresh data")
    ap.add_argument("--no-autofill", action="store_true", help="do not record automatic paper fills / expiries")
    a = ap.parse_args(argv)

    import order_sheet as osh
    from core import s3_store
    from core.order_sheet import SheetConfig
    from db import s3_paper_orders_col, s3_paper_positions_col, s3_paper_runs_col
    from utils import send_telegram_msg

    from core import s3_refresh
    from db import get_instrument_key_mapping

    if not enable_exchange_holidays():
        print("WARNING: NSE holiday list unavailable, only weekends are skipped when picking the order day")
    run_day = pd.Timestamp(a.today) if a.today else pd.Timestamp(datetime.now().date())
    order_day = next_session(run_day)
    cfg = SheetConfig(capital=a.capital)
    now_ist = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=5, minutes=30))).replace(tzinfo=None)
    max_date = s3_refresh.last_complete_date(now_ist, run_day)

    def refresh(candles, listing_syms, held):
        # The shared candle cache holds sessions up to D-1 at 18:50 IST on day D; fetch the missing completed bars in memory (nothing is written).
        return s3_refresh.refresh_candles(candles, listing_syms, held, max_date, os.getenv("UPSTOX_ACCESS_TOKEN"), get_instrument_key_mapping(),
                                          is_holiday=_is_holiday)

    if _closed(run_day) and not a.force:
        print(f"{run_day.date()} is a weekend or an exchange holiday: no session to report (use --force to run anyway)")
        return
    if not a.dry_run and not a.force and s3_store.run_already_sent(s3_paper_runs_col, run_day):
        # the job is started by both the GitHub schedule and the Cloudflare dispatcher; the second start must not repeat the alert
        print(f"S3 alert for {run_day.date()} was already sent with fresh data; nothing to do (use --force to run again)")
        return

    res = osh.s3_collect(order_day, cfg, refresh=refresh)
    if res is None:
        raise SystemExit("MongoDB is not connected")
    rf = res.get("refresh") or {}
    print(f"[s3_refresh] max_date {max_date.date()}: {len(rf.get('selected', ()))} names near pivot or held, {rf.get('fetched', 0)} fetched, "
          f"{rf.get('updated', 0)} updated, {len(rf.get('failed', {}))} failed")
    # Settle the stored orders against the sessions that have a completed candle: record paper fills / expiries (core/s3_autofill.py).
    settled = None
    if not a.no_autofill:
        from core import s3_autofill
        if not a.dry_run:
            s3_store.ensure_indexes(s3_paper_orders_col, s3_paper_positions_col, s3_paper_runs_col)
        settled = s3_autofill.autofill(s3_paper_orders_col, s3_paper_positions_col, res["candles"], run_day, apply=not a.dry_run)
        print(f"[s3_autofill] filled {len(settled['filled'])}, expired {len(settled['expired'])}, waiting {len(settled['waiting'])}, skipped {len(settled['skipped'])}"
              + (" (dry run: nothing written)" if a.dry_run else ""))
        if not a.dry_run and (settled["filled"] or settled["expired"] or settled["skipped"]):
            # positions changed: recompute rows, open count and the new orders (a held name must not get a new order); candles are reused, no refetch
            frozen = res["candles"]
            res = osh.s3_collect(order_day, cfg, refresh=lambda c, l, h: ({**c, **frozen}, rf_report(res)))
            rf = res.get("refresh") or rf
    msg = build_message(res["rows"], res["entries"], order_day, res["data_asof"], res["warnings"], settled)
    print(msg["text"])
    if a.dry_run:
        return
    s3_store.ensure_indexes(s3_paper_orders_col, s3_paper_positions_col, s3_paper_runs_col)
    if res["entries"] and not msg["stale"]:
        s3_store.record_orders(s3_paper_orders_col, order_day, osh.s3_order_docs(res["entries"], res["cands"]))
    sent = send_telegram_msg(msg["text"], disable_notification=not msg["actionable"])
    s3_store.record_run(s3_paper_runs_col, run_day, order_day=order_day.to_pydatetime(), data_asof=(res["data_asof"].to_pydatetime() if res["data_asof"] is not None else None),
                        n_open=msg["n_open"], n_alerts=msg["n_alerts"], n_orders=msg["n_orders"], stale=msg["stale"], sent=bool(sent), capital=a.capital,
                        n_refreshed=rf.get("updated", 0), n_refresh_failed=len(rf.get("failed", {})))


if __name__ == "__main__":
    main()
