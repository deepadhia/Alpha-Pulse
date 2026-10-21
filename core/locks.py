# -*- coding: utf-8 -*-
"""
core/locks.py

A tiny MongoDB mutex used to make "count active positions -> decide -> insert position"
atomic across overlapping scanner processes (listing scan, hourly scan, GitHub Actions
re-runs).  Without it two scans can both read 6 active positions, both pass the cap check
and open a 7th and 8th ACTIVE trade.

Mechanism: an upsert on a fixed _id whose filter only matches an EXPIRED lock.
  * no lock document        -> upsert inserts it            -> acquired
  * expired lock document   -> filter matches, update sets  -> acquired
  * live lock document      -> filter misses, upsert tries to insert the same _id
                               -> DuplicateKeyError         -> busy
MongoDB guarantees the unique _id, so exactly one caller wins.  Locks carry a TTL so a crashed
scanner cannot wedge the book.

Fail-safe policy for callers: if the lock cannot be acquired, open the trade as PAPER_ONLY
(paper positions do not consume capital slots) instead of risking an uncapped ACTIVE trade.
"""

import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

try:
    from pymongo.errors import DuplicateKeyError
except Exception:                                    # pragma: no cover - pymongo is a hard dep in prod
    class DuplicateKeyError(Exception):
        pass


def _now():
    return datetime.now(timezone.utc)


def try_acquire(collection, name: str, owner: str, ttl_s: float = 30.0, now=None) -> bool:
    now = now or _now()
    try:
        collection.update_one(
            {"_id": name, "expires_at": {"$lt": now}},
            {"$set": {"owner": owner, "expires_at": now + timedelta(seconds=ttl_s)}},
            upsert=True,
        )
        return True
    except DuplicateKeyError:
        return False


def release(collection, name: str, owner: str) -> None:
    try:
        collection.delete_one({"_id": name, "owner": owner})
    except Exception:
        pass   # TTL will expire it


@contextmanager
def portfolio_commit_lock(db, name: str = "portfolio_commit", ttl_s: float = 30.0,
                          wait_s: float = 10.0, poll_s: float = 0.25, sleep=time.sleep, clock=_now):
    """
    Context manager yielding True when the lock is held, False when it could not be acquired
    within `wait_s` (or when no database is available).  Callers MUST treat False as
    "do not open ACTIVE capital".
    """
    if db is None:
        yield False
        return
    coll = db["locks"]
    owner = uuid.uuid4().hex
    deadline = clock() + timedelta(seconds=wait_s)
    held = False
    while True:
        try:
            held = try_acquire(coll, name, owner, ttl_s, clock())
        except Exception:
            held = False
            break
        if held or clock() >= deadline:
            break
        sleep(poll_s)
    try:
        yield held
    finally:
        if held:
            release(coll, name, owner)


def acquire_blocking(db, name: str = "portfolio_commit", ttl_s: float = 30.0, wait_s: float = 10.0,
                     poll_s: float = 0.25, sleep=time.sleep, clock=_now):
    """
    Non-context-manager form for call sites that cannot be re-indented.
    Returns an owner token when the lock is held, else None.  Pair with release_owner().
    """
    if db is None:
        return None
    coll = db["locks"]
    owner = uuid.uuid4().hex
    deadline = clock() + timedelta(seconds=wait_s)
    while True:
        try:
            if try_acquire(coll, name, owner, ttl_s, clock()):
                return owner
        except Exception:
            return None
        if clock() >= deadline:
            return None
        sleep(poll_s)


def release_owner(db, owner, name: str = "portfolio_commit") -> None:
    if db is not None and owner:
        release(db["locks"], name, owner)
