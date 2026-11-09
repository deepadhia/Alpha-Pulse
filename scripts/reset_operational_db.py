#!/usr/bin/env python3
"""
scripts/reset_operational_db.py

Safe archival and operational reset script for AlphaPulse:
1. Exports full JSON disaster-recovery backup to backups/<timestamp>/
2. Copies operational collections to dated MongoDB archive collections (*_archive_<date>)
3. Clears operational collections (positions, signals, signals_v2, signal_outcomes, signal_updates, strategy_evidence, pending_states, watchlist)
4. Preserves ALL critical infrastructure collections:
   - daily_candles_cache
   - instrument_keys
   - ipos
   - listing_data
   - market_holidays
   - trade_ledger
   - s3_paper_* collections
   - legacy archives & system logs
"""

import os
import sys
import argparse
from datetime import datetime
from dotenv import load_dotenv
from pymongo import MongoClient

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

load_dotenv(os.path.join(PROJECT_DIR, ".env"))

OPERATIONAL_COLLECTIONS = [
    "positions",
    "signals",
    "signals_v2",
    "signal_outcomes",
    "signal_updates",
    "strategy_evidence",
    "pending_states",
    "watchlist",
]

ARCHIVE_DATE_TAG = datetime.now().strftime("%Y%m%d")


def run_reset(dry_run=True):
    mongo_uri = os.getenv("MONGO_URI", "")
    if not mongo_uri:
        print("❌ Error: MONGO_URI not found in .env")
        sys.exit(1)

    client = MongoClient(mongo_uri)
    db = client["ipo_scanner_v2"]

    print("=" * 80)
    print("🧹 ALPHAPULSE OPERATIONAL DATABASE RESET & ARCHIVAL")
    print(f"Mode: {'DRY RUN (no changes applied)' if dry_run else 'LIVE EXECUTION'}")
    print(f"Archive Tag: {ARCHIVE_DATE_TAG}")
    print("=" * 80)

    # 1. Inspect current state
    print("\n[Step 1] Inspecting current operational collections:")
    counts = {}
    for coll_name in OPERATIONAL_COLLECTIONS:
        cnt = db[coll_name].count_documents({})
        counts[coll_name] = cnt
        print(f"  - {coll_name:25s}: {cnt:5d} documents")

    if dry_run:
        print("\n[DRY RUN] Would execute:")
        print("  1. Run mongodb_backup.py -> full JSON snapshot to backups/<timestamp>/")
        for coll_name in OPERATIONAL_COLLECTIONS:
            target_archive = f"{coll_name}_archive_{ARCHIVE_DATE_TAG}"
            print(f"  2. Archive {coll_name} ({counts[coll_name]} docs) -> {target_archive}")
            print(f"  3. Clear {coll_name} (delete_many({{}}))")
        print("\nPreserving untouched:")
        print("  - daily_candles_cache, instrument_keys, ipos, listing_data, market_holidays")
        print("  - trade_ledger (insert-only audit log)")
        print("  - s3_paper_orders, s3_paper_positions, s3_paper_runs")
        print("  - logs, system_audits, positions_legacy_archive, signals_legacy_archive")
        print("\nPass --confirm to execute live.")
        return

    # 2. Live: Run JSON backup first
    print("\n[Step 2] Executing disaster recovery JSON backup via mongodb_backup.py...")
    import mongodb_backup
    mongodb_backup.run_backup()

    # 3. Live: Server-side archive copy
    print("\n[Step 3] Archiving operational collections server-side in MongoDB...")
    for coll_name in OPERATIONAL_COLLECTIONS:
        cnt = counts[coll_name]
        target_archive = f"{coll_name}_archive_{ARCHIVE_DATE_TAG}"
        if cnt > 0:
            docs = list(db[coll_name].find({}))
            # Drop target archive if it exists from earlier run today, then insert
            db[target_archive].drop()
            db[target_archive].insert_many(docs)
            archived_cnt = db[target_archive].count_documents({})
            if archived_cnt != cnt:
                raise RuntimeError(
                    f"FATAL: Archive count mismatch for {coll_name}! "
                    f"Source: {cnt}, Archived: {archived_cnt}. Aborting before clear."
                )
            print(f"  ✅ Copied and verified {cnt} docs from {coll_name} -> {target_archive}")
        else:
            print(f"  ℹ️ {coll_name} is empty; skipped archiving")

    # 4. Live: Clear operational collections
    print("\n[Step 4] Clearing operational collections...")
    for coll_name in OPERATIONAL_COLLECTIONS:
        res = db[coll_name].delete_many({})
        print(f"  🧹 Cleared {coll_name}: {res.deleted_count} documents removed")

    # 5. Verification
    print("\n[Step 5] Verifying post-reset state:")
    all_clean = True
    for coll_name in OPERATIONAL_COLLECTIONS:
        remaining = db[coll_name].count_documents({})
        status = "OK (clean)" if remaining == 0 else f"WARN ({remaining} remaining)"
        if remaining != 0:
            all_clean = False
        print(f"  - {coll_name:25s}: {status}")

    print("\n" + "=" * 80)
    if all_clean:
        print("🎉 SUCCESS: Operational database successfully archived and cleared!")
        print("System is ready for clean forward testing.")
    else:
        print("⚠️ WARNING: Some collections still have documents.")
    print("=" * 80)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AlphaPulse Safe DB Reset")
    parser.add_argument("--confirm", action="store_true", help="Confirm live execution")
    args = parser.parse_args()

    run_reset(dry_run=not args.confirm)

