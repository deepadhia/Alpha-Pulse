# -*- coding: utf-8 -*-
"""
core/versioning.py

Single definition of which stored `version` tags belong to the native forward production cohort (AGENTS.md section 2).

The six literal version touchpoints (master_audit.EXPECTED_VERSION, the three scanners, db.py, the README badge) stay plain strings because
master_audit.py checks them by regex.  Analytics that isolate the native forward cohort must NOT hard-code a single version string, or a milestone
bump would silently empty the scorecard.  v3.6.0 (hardened execution release) changed capital allocation and tooling, not the v3.5.0 entry rules,
so rows tagged 3.5.0 and 3.6.0 that were entered natively on or after the cutoff form one forward cohort.  Report the two tags separately when
comparing slot-cap regimes (5/7 slots under 3.5.0, 10 x 10% under 3.6.0).

Pure constants and one function: no DB, no network.
"""

NATIVE_FORWARD_CUTOFF = "2026-09-29"
NATIVE_FORWARD_VERSIONS = ("3.5.0", "3.6.0")


def is_native_forward(version, backfilled, entry_date_str) -> bool:
    """True for a natively generated (not backfilled) row of a forward-cohort version entered on or after the cutoff."""
    return bool(version in NATIVE_FORWARD_VERSIONS and not backfilled and str(entry_date_str or "") >= NATIVE_FORWARD_CUTOFF)
