"""Persistent monotonic IDs, safe across app restarts and concurrent instances."""
from pathlib import Path
import sqlite3


class ShotIdAllocator:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect(self.path,timeout=10) as db:
            db.execute("CREATE TABLE IF NOT EXISTS sequence (key INTEGER PRIMARY KEY CHECK(key=1), next_id INTEGER NOT NULL)")
            db.execute("INSERT OR IGNORE INTO sequence VALUES (1,1)")

    def allocate(self, minimum=1):
        with sqlite3.connect(self.path,timeout=10) as db:
            db.execute("BEGIN IMMEDIATE")
            value = max(minimum, db.execute("SELECT next_id FROM sequence WHERE key=1").fetchone()[0])
            db.execute("UPDATE sequence SET next_id=? WHERE key=1",(value+1,))
            return value

    def reserve(self, next_id):
        with sqlite3.connect(self.path,timeout=10) as db:
            db.execute("UPDATE sequence SET next_id=MAX(next_id,?) WHERE key=1",(next_id,))
