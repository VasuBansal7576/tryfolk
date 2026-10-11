"""SQLite transactions are the budget, idempotency and durable queue boundary."""

from contextlib import contextmanager
from pathlib import Path
import json
import sqlite3


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS quotes(id TEXT PRIMARY KEY, payload TEXT NOT NULL, consumed INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, payload TEXT NOT NULL, token TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS populations(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS reservations(id TEXT PRIMARY KEY, reserved INTEGER NOT NULL, spent INTEGER DEFAULT 0,
                released INTEGER DEFAULT 0, after_started INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS idempotency(key TEXT PRIMARY KEY, request TEXT NOT NULL, response TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, run_id TEXT NOT NULL, kind TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued', updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS findings(id TEXT PRIMARY KEY, run_id TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS repairs(id TEXT PRIMARY KEY, run_id TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                persona_id TEXT, kind TEXT NOT NULL, created_at TEXT NOT NULL, data TEXT NOT NULL, trace_url TEXT);
            CREATE TABLE IF NOT EXISTS owner_sessions(id TEXT PRIMARY KEY, csrf TEXT NOT NULL, expires_at TEXT NOT NULL);
            """)
        path.chmod(0o600)

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def load(db, table, identifier):
        row = db.execute(f"SELECT payload FROM {table} WHERE id=?", (identifier,)).fetchone()
        return json.loads(row[0]) if row else None

    @staticmethod
    def save(db, table, data):
        db.execute(f"UPDATE {table} SET payload=? WHERE id=?", (json.dumps(data), data["id"]))
