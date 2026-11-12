"""Pure forward-cohort isolation. Operational status never proves a broker fill."""
from collections import defaultdict

from core.trade_guard import to_date
from core.versioning import NATIVE_FORWARD_CUTOFF, NATIVE_FORWARD_VERSIONS


def split_cohorts(docs, since=NATIVE_FORWARD_CUTOFF):
    cutoff = max(to_date(since), to_date(NATIVE_FORWARD_CUTOFF), to_date("2026-07-05"))
    groups = defaultdict(lambda: dict(closed=[], open=[], pending=[]))
    excluded = 0
    for doc in docs:
        entered = to_date(doc.get("entry_date"))
        version = doc.get("version")
        if doc.get("_backfilled") or version not in NATIVE_FORWARD_VERSIONS or entered is None or entered < cutoff:
            excluded += 1
            continue
        status = doc.get("status")
        automatic = doc.get("entry_source") == "RESTING_AUTOFILL"
        if automatic:
            source = f"PAPER FORWARD / P RESTING {doc.get('resting_version') or 'unversioned'}"
            setup = "LISTING_BREAKOUT"
        else:
            source = "PAPER SCANNER" if status in ("PAPER_ONLY", "PAPER_CLOSED") else "SCANNER RECORDS (broker fills unverified)"
            setup = "SHELF_BREAKOUT" if str(doc.get("signal_id") or "").startswith("SHELF_") else doc.get("grade", "UNKNOWN")
        key = f"{version} | {source} | {setup}"
        if status in ("CLOSED", "PAPER_CLOSED"):
            bucket = "pending" if automatic and doc.get("exit_settled") is not True else "closed"
        elif status in ("ACTIVE", "PAPER_ONLY"):
            bucket = "pending" if doc.get("recovery_pending") else "open"
        else:
            excluded += 1
            continue
        groups[key][bucket].append(doc)
    return dict(groups), excluded
