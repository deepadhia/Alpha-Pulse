# -*- coding: utf-8 -*-
"""
paper_scorecard.py -- scorecard and pre-registered decision for the NATIVE FORWARD cohort (read-only).

  python paper_scorecard.py                 # live (CLOSED) and paper (PAPER_CLOSED) cohorts
  python paper_scorecard.py --since 2026-09-29

Native forward = entry_date >= 2026-09-29 and _backfilled != True (AGENTS.md cohort isolation rule).
Decision rules live in core/scorecard.py and are fixed in advance (see its docstring).
"""

import argparse
from datetime import datetime

from core.scorecard import GateConfig, format_pf_report, format_scorecard, pf_report, scorecard


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-09-29")
    ap.add_argument("--friction", type=float, default=1.0, help="round-trip cost assumption in percent")
    ap.add_argument("--pf-target", type=float, default=2.0, help="profit-factor target for the monitor (reporting only)")
    args = ap.parse_args(argv)

    from db import positions_col
    if positions_col is None:
        raise SystemExit("MongoDB is not connected")

    cohorts = {"LIVE (CLOSED)": ["CLOSED"], "PAPER (PAPER_CLOSED)": ["PAPER_CLOSED"],
               "ALL NATIVE (live + paper)": ["CLOSED", "PAPER_CLOSED"]}
    cfg = GateConfig(friction_pct=args.friction)
    for title, statuses in cohorts.items():
        since_dt = datetime.strptime(args.since, "%Y-%m-%d")
        docs = list(positions_col.find({
            "status": {"$in": statuses},
            "_backfilled": {"$ne": True},
            # entry_date is stored as a datetime by the listing scanner and as a string elsewhere;
            # BSON only compares like types, so match either form
            "$or": [{"entry_date": {"$gte": since_dt}}, {"entry_date": {"$gte": args.since}}],
        }, {"_id": 0, "pnl_pct": 1, "symbol": 1}))
        pnls = [d.get("pnl_pct") for d in docs if d.get("pnl_pct") is not None]
        print(format_scorecard(scorecard(pnls, cfg), title))
        open_docs = list(positions_col.find({
            "status": {"$in": ["ACTIVE"] if statuses == ["CLOSED"] else (["PAPER_ONLY"] if statuses == ["PAPER_CLOSED"] else ["ACTIVE", "PAPER_ONLY"])},
            "_backfilled": {"$ne": True},
            "$or": [{"entry_date": {"$gte": since_dt}}, {"entry_date": {"$gte": args.since}}],
        }, {"_id": 0, "pnl_pct": 1, "symbol": 1}))
        open_pnls = [d.get("pnl_pct") for d in open_docs if d.get("pnl_pct") is not None]
        print(format_pf_report(pf_report(pnls, open_pnls, cfg, target=args.pf_target), "PROFIT FACTOR MONITOR"))
        print()


if __name__ == "__main__":
    main()
