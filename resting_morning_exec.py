# -*- coding: utf-8 -*-
"""
resting_morning_exec.py -- the open-time half of the automatic forward test of policy P's resting orders (see core/resting_exec.py for the model).

  python resting_morning_exec.py              # ~09:16-09:25 IST: first quote after the open
  python resting_morning_exec.py --dry-run    # decide and print, write nothing, send nothing

For every order the system told you to place today (ORDER_GENERATED in the trade ledger) that is still PLACED:
  gap-up open at or above the trigger  -> FILL at the best ASK if within the limit, recorded in `positions` (entry guard + ledger) tagged RESTING_AUTOFILL;
  opened above the limit / ask above the limit / upper circuit with no sellers -> not filled (a ledger note);
  opened below the trigger -> left for the evening job (priced from the minute bars).
For every autofill position whose stop fired on the previous close (the 6:30 PM job closed it at that close): SELL at the best BID of the open-time
quote and replace the decision close with it (EXIT_SETTLED ledger event); no bid / lower circuit defers the sale to the next session.
Every quote is audited (logs, QUOTE_AUDIT) so the measured spread replaces the assumed tiers.  A quote or database failure exits non-zero (fail loud:
no guessed fill); the whole flow is idempotent, so the dispatcher and the cron backup can both start it.
Nothing here places a broker order: it records what the resting orders would have done against the real book.
"""

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="decide and print only: write nothing, send nothing")
    ap.add_argument("--capital", type=float, default=float(os.getenv("RESTING_ORDER_CAPITAL", "100000")))
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    from core import resting_runtime as rt
    from core.resting_jobs import run_morning
    from s3_paper_alerts import enable_exchange_holidays
    from utils import send_telegram_msg

    enable_exchange_holidays()
    now_ist = datetime.now(IST)
    deps = rt.build_deps("resting_morning_exec.py", os.getenv("UPSTOX_ACCESS_TOKEN"), a.capital, dry_run=a.dry_run, now_ist=now_ist)
    rep = run_morning(deps, now_ist)
    text = rt.format_morning_message(rep, now_ist)
    print(text)
    if rep.get("skipped") or a.dry_run:
        return
    acted = any(rep[k] for k in ("filled", "expired", "sold", "deferred"))
    if acted or rep["waiting"] or rep["exit_waiting"]:
        send_telegram_msg(text, disable_notification=not acted)
    from db import system_audits_col
    from datetime import datetime as _dt
    system_audits_col.update_one({"audit_id": f"resting_morning_{rep['day']}"},
                                 {"$set": dict(audit_type="RESTING_MORNING_EXEC", date=rep["day"], timestamp=_dt.now(timezone.utc), triggered_by="resting_morning_exec.py",
                                               n_filled=len(rep["filled"]), n_expired=len(rep["expired"]), n_sold=len(rep["sold"]), n_deferred=len(rep["deferred"]),
                                               n_pending=len(rep["pending"]), quotes=rep.get("quotes", 0), run_time_ist=now_ist.strftime("%H:%M:%S"))}, upsert=True)


if __name__ == "__main__":
    main()
