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
from core.forward_cohorts import split_cohorts
from core.trade_guard import to_date
from core.versioning import NATIVE_FORWARD_CUTOFF, NATIVE_FORWARD_VERSIONS


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-09-29")
    ap.add_argument("--friction", type=float, default=1.0, help="round-trip cost assumption in percent")
    ap.add_argument("--pf-target", type=float, default=2.0, help="profit-factor target for the monitor (reporting only)")
    args = ap.parse_args(argv)

    from db import positions_col
    if positions_col is None:
        raise SystemExit("MongoDB is not connected")

    if to_date(args.since) is None:
        ap.error("--since must be YYYY-MM-DD")
    groups, excluded = split_cohorts(positions_col.find(
        {"_backfilled": {"$ne": True}, "version": {"$in": list(NATIVE_FORWARD_VERSIONS)}}, {"_id": 0}), args.since)
    cfg = GateConfig(friction_pct=args.friction)
    print("Separate cohorts; no pooled LIVE headline. Pending sales/recoveries are excluded from performance.")
    for title, group in sorted(groups.items()):
        pnls = [d["pnl_pct"] for d in group["closed"] if d.get("pnl_pct") is not None]
        print(format_scorecard(scorecard(pnls, cfg), title))
        open_pnls = [d["pnl_pct"] for d in group["open"] if d.get("pnl_pct") is not None]
        print(format_pf_report(pf_report(pnls, open_pnls, cfg, target=args.pf_target), "PROFIT FACTOR MONITOR"))
        print(f"Unsettled sales/recoveries excluded: {len(group['pending'])}\n")
    if not groups:
        print("No eligible native forward records.")
    print(f"Other ineligible records excluded: {excluded}")


if __name__ == "__main__":
    main()
