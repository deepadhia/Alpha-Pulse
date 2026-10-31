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


def next_session(today: pd.Timestamp) -> pd.Timestamp:
    """Next weekday after `today` (exchange holidays are not modelled: a holiday only makes the orders one day early, they are DAY orders)."""
    d = today + pd.Timedelta(days=1)
    while d.weekday() >= 5:
        d += pd.Timedelta(days=1)
    return d


def previous_session(day: pd.Timestamp) -> pd.Timestamp:
    d = day - pd.Timedelta(days=1)
    while d.weekday() >= 5:
        d -= pd.Timedelta(days=1)
    return d


def build_message(rows: list, entries: list, order_day: pd.Timestamp, data_asof, warnings=None) -> dict:
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
    lines = [f"<b>{PREFIX}</b>  (paper only, not P)  orders for {order_day.date()}"]
    lines.append(f"data as of {pd.Timestamp(data_asof).date() if data_asof is not None else 'n/a'}"
                 + ("  <b>STALE: expected " + str(expected.date()) + "; do not trust today's orders</b>" if stale else ""))
    if alerts:
        lines += ["", "<b>STOP ALERT: mark the exit at the next open</b>"]
        for r in alerts:
            lines.append(f"  {e(r['symbol'])}: close {r['last_close']:.2f} at/below alert {r['trailing']:.2f} ({r['pnl_pct']:+.1f}%)  -> python s3_paper.py exit {e(r['symbol'])} PRICE")
    if exited:
        lines += ["", "ENGINE SAYS EXITED (record it):"]
        for r in exited:
            lines.append(f"  {e(r['symbol'])}: {str(r['exit_date'])[:10]} @ {r['exit_price']:.2f} ({e(r['exit_reason'])}) {r['pnl_pct']:+.1f}%")
    if open_rows:
        lines += ["", "OPEN POSITIONS (alert level = trailing stop)"]
        for r in open_rows:
            lines.append(f"  {e(r['symbol'])}: close {r['last_close']:.2f} ({r['pnl_pct']:+.1f}%, peak {r['max_runup']:+.1f}%), alert level {r['trailing']:.2f}")
    if pending:
        lines += ["", "FILLED TODAY, WAITING FOR THE CANDLE CACHE: " + ", ".join(e(r["symbol"]) for r in pending)]
    if errors:
        lines += ["", "CANNOT EVALUATE:"] + [f"  {e(r['symbol'])}: {e(r['error'])}" for r in errors]
    lines += ["", "<b>RESTING BUY ORDERS</b> (stop-limit, DAY)" if entries else "No S3 orders for the next session."]
    for x in entries:
        lines.append(f"  {e(x.symbol)}: BUY {x.quantity} trigger {x.trigger:.2f} limit {x.limit:.2f} (stop alert {x.stop_alert:.2f}, risk {x.risk_pct_of_capital:.2f}% of capital)")
    for w in (warnings or []):
        lines.append(f"  ! {e(w)}")
    actionable = bool(alerts or exited or entries or errors or stale)
    return dict(text="\n".join(lines), n_alerts=len(alerts), n_orders=len(entries), n_open=len(open_rows), stale=stale, actionable=actionable)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--capital", type=float, default=float(os.getenv("S3_PAPER_CAPITAL", "100000")))
    ap.add_argument("--dry-run", action="store_true", help="print only: send nothing, store nothing")
    ap.add_argument("--today", default=None, help="override the run date (YYYY-MM-DD), for testing")
    a = ap.parse_args(argv)

    import order_sheet as osh
    from core import s3_store
    from core.order_sheet import SheetConfig
    from db import s3_paper_orders_col, s3_paper_positions_col, s3_paper_runs_col
    from utils import send_telegram_msg

    from core import s3_refresh
    from db import get_instrument_key_mapping

    run_day = pd.Timestamp(a.today) if a.today else pd.Timestamp(datetime.now().date())
    order_day = next_session(run_day)
    cfg = SheetConfig(capital=a.capital)
    now_ist = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=5, minutes=30))).replace(tzinfo=None)
    max_date = s3_refresh.last_complete_date(now_ist, run_day)

    def refresh(candles, listing_syms, held):
        # The shared candle cache holds sessions up to D-1 at 18:50 IST on day D; fetch the missing completed bars in memory (nothing is written).
        return s3_refresh.refresh_candles(candles, listing_syms, held, max_date, os.getenv("UPSTOX_ACCESS_TOKEN"), get_instrument_key_mapping())

    res = osh.s3_collect(order_day, cfg, refresh=refresh)
    if res is None:
        raise SystemExit("MongoDB is not connected")
    rf = res.get("refresh") or {}
    print(f"[s3_refresh] max_date {max_date.date()}: {len(rf.get('selected', ()))} names near pivot or held, {rf.get('fetched', 0)} fetched, "
          f"{rf.get('updated', 0)} updated, {len(rf.get('failed', {}))} failed")
    msg = build_message(res["rows"], res["entries"], order_day, res["data_asof"], res["warnings"])
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
