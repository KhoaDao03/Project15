"""Shared rolling retention for completed research segments, outside trading threads."""

import fcntl
import sqlite3
import time
from contextlib import closing
from pathlib import Path

MAX_COMPLETED_FILES = 10_000
RECONCILE_SECONDS = 3600


def retain_completed(root, completed=(), *, limit=MAX_COMPLETED_FILES):
    """Register rotations and remove oldest excess files; retry a busy lock later.

    The first pass builds the index. Hourly reconciliation repairs interrupted
    registrations or external file changes; ordinary passes never enumerate files.
    File deletion precedes SQL removal so a failed deletion remains retryable.
    """
    root = Path(root)
    with (root / ".retention.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        with closing(sqlite3.connect(root / "retention.sqlite", timeout=0)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS segments(path TEXT PRIMARY KEY, modified REAL NOT NULL)")
            db.execute("CREATE INDEX IF NOT EXISTS segments_oldest ON segments(modified,path)")
            db.execute("CREATE TABLE IF NOT EXISTS reconciliation(id INTEGER PRIMARY KEY, completed REAL)")
            previous = db.execute("SELECT completed FROM reconciliation WHERE id=1").fetchone()
            now = time.monotonic()
            reconcile = previous is None or not 0 <= now - previous[0] < RECONCILE_SECONDS
            if reconcile:
                paths = root.glob("*/*/events-*.jsonl.gz")
                db.execute("DELETE FROM segments")
            else:
                paths = completed
            for path in paths:
                path = Path(path)
                relative = path.relative_to(root)
                if (
                    len(relative.parts) != 3
                    or not path.name.startswith("events-")
                    or not path.name.endswith(".jsonl.gz")
                    or not path.resolve().is_relative_to(root.resolve())
                ):
                    raise ValueError("Not a completed research segment: " + str(path))
                try:
                    modified = path.stat().st_mtime
                except FileNotFoundError:
                    continue
                db.execute("INSERT OR REPLACE INTO segments VALUES (?,?)", (str(relative), modified))
            excess = max(0, db.execute("SELECT count(*) FROM segments").fetchone()[0] - limit)
            for (relative,) in db.execute(
                "SELECT path FROM segments ORDER BY modified,path LIMIT ?", (excess,)
            ).fetchall():
                path = root / relative
                if not path.resolve().is_relative_to(root.resolve()):
                    raise ValueError("Research segment moved outside capture directory")
                path.unlink(missing_ok=True)
                db.execute("DELETE FROM segments WHERE path=?", (relative,))
            if reconcile:
                db.execute("INSERT OR REPLACE INTO reconciliation VALUES (1,?)", (time.monotonic(),))
        return True
