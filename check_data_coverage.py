# -*- coding: utf-8 -*-
"""
check_data_coverage.py -- report recent IPOs whose candles are missing, truncated or stale in the research cache.

  .venv\\Scripts\\python.exe check_data_coverage.py --cache data.pkl
  .venv\\Scripts\\python.exe check_data_coverage.py --cache data.pkl --threshold 0.95 --as-of today

Exit code 1 when coverage (OK / evaluable listings aged <= --max-age-days) is below --threshold, else 0.
Reads only the pickle; never touches MongoDB.  Run it before trusting any replay of the newest cohort.
"""

import argparse
import pickle
import sys

import pandas as pd

from core.data_coverage import (DEFAULT_MAX_AGE_DAYS, DEFAULT_MIN_CANDLES, DEFAULT_STALE_SESSIONS,
                                coverage_report, format_report)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="data.pkl", help="pickle of (listing_map, candle_map)")
    ap.add_argument("--threshold", type=float, default=0.90, help="minimum coverage fraction (default 0.90)")
    ap.add_argument("--max-age-days", type=int, default=DEFAULT_MAX_AGE_DAYS)
    ap.add_argument("--stale-sessions", type=int, default=DEFAULT_STALE_SESSIONS)
    ap.add_argument("--min-candles", type=int, default=DEFAULT_MIN_CANDLES)
    ap.add_argument("--as-of", default=None,
                    help="YYYY-MM-DD or 'today'; default is the newest candle date in the cache")
    ap.add_argument("--show-ok", action="store_true")
    args = ap.parse_args(argv)

    with open(args.cache, "rb") as fh:
        listing_map, candle_map = pickle.load(fh)
    as_of = pd.Timestamp.today().normalize() if args.as_of == "today" else args.as_of
    rep = coverage_report(listing_map, candle_map, as_of=as_of, max_age_days=args.max_age_days,
                          stale_sessions=args.stale_sessions, min_candles=args.min_candles)
    print(format_report(rep, threshold=args.threshold, show_ok=args.show_ok))
    if rep["coverage"] < args.threshold:
        print("COVERAGE FAIL")
        return 1
    print("COVERAGE PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
