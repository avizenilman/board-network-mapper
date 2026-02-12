"""SQLite cache and storage layer."""

import sqlite3
import json
import time
import os
from pathlib import Path
from typing import Optional

DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "board_mapper.db")
DEFAULT_TTL = 30 * 24 * 3600  # 30 days in seconds


class Database:
    def __init__(self, db_path: str = DEFAULT_DB_PATH, ttl: int = DEFAULT_TTL):
        self.db_path = db_path
        self.ttl = ttl
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS cache (
                source TEXT NOT NULL,
                query TEXT NOT NULL,
                query_type TEXT NOT NULL,
                data TEXT NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY (source, query, query_type)
            );

            CREATE TABLE IF NOT EXISTS search_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query_name TEXT NOT NULL,
                timestamp REAL NOT NULL,
                result_count INTEGER DEFAULT 0,
                mode TEXT DEFAULT 'quick',
                sources_used TEXT DEFAULT '[]',
                sources_failed TEXT DEFAULT '[]'
            );

            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                query_description TEXT,
                csv_data TEXT NOT NULL,
                row_count INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS validations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ein TEXT NOT NULL,
                person_name TEXT NOT NULL,
                status TEXT NOT NULL,
                on_990 INTEGER DEFAULT 0,
                on_website INTEGER DEFAULT 0,
                validated_at REAL NOT NULL,
                UNIQUE(ein, person_name)
            );

            CREATE TABLE IF NOT EXISTS exclusions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                first_name TEXT,
                last_name TEXT,
                full_name TEXT,
                added_at REAL NOT NULL
            );
        """)
        self.conn.commit()

    def cache_get(self, source: str, query: str, query_type: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT data, created_at FROM cache WHERE source=? AND query=? AND query_type=?",
            (source, query.lower().strip(), query_type)
        ).fetchone()
        if row is None:
            return None
        age = time.time() - row["created_at"]
        if age > self.ttl:
            self.conn.execute(
                "DELETE FROM cache WHERE source=? AND query=? AND query_type=?",
                (source, query.lower().strip(), query_type)
            )
            self.conn.commit()
            return None
        return json.loads(row["data"])

    def cache_set(self, source: str, query: str, query_type: str, data: dict):
        self.conn.execute(
            """INSERT OR REPLACE INTO cache (source, query, query_type, data, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (source, query.lower().strip(), query_type, json.dumps(data), time.time())
        )
        self.conn.commit()

    def cache_age(self, source: str, query: str, query_type: str) -> Optional[float]:
        """Return age in seconds of a cache entry, or None if not cached."""
        row = self.conn.execute(
            "SELECT created_at FROM cache WHERE source=? AND query=? AND query_type=?",
            (source, query.lower().strip(), query_type)
        ).fetchone()
        if row is None:
            return None
        return time.time() - row["created_at"]

    def log_search(self, query_name: str, result_count: int, mode: str = "quick",
                   sources_used: list[str] = None, sources_failed: list[str] = None):
        self.conn.execute(
            """INSERT INTO search_log (query_name, timestamp, result_count, mode, sources_used, sources_failed)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (query_name, time.time(), result_count, mode,
             json.dumps(sources_used or []), json.dumps(sources_failed or []))
        )
        self.conn.commit()

    def get_search_history(self, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            """SELECT query_name, timestamp, result_count, mode, sources_used, sources_failed
               FROM search_log ORDER BY timestamp DESC LIMIT ?""",
            (limit,)
        ).fetchall()
        return [
            {
                "query_name": r["query_name"],
                "timestamp": r["timestamp"],
                "result_count": r["result_count"],
                "mode": r["mode"],
                "sources_used": json.loads(r["sources_used"]),
                "sources_failed": json.loads(r["sources_failed"]),
            }
            for r in rows
        ]

    def save_snapshot(self, csv_data: str, query_description: str = "", row_count: int = 0):
        self.conn.execute(
            "INSERT INTO snapshots (timestamp, query_description, csv_data, row_count) VALUES (?, ?, ?, ?)",
            (time.time(), query_description, csv_data, row_count)
        )
        self.conn.commit()

    def get_snapshots(self, limit: int = 20) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, timestamp, query_description, row_count FROM snapshots ORDER BY timestamp DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_snapshot_data(self, snapshot_id: int) -> Optional[str]:
        row = self.conn.execute("SELECT csv_data FROM snapshots WHERE id=?", (snapshot_id,)).fetchone()
        return row["csv_data"] if row else None

    def save_validation(self, ein: str, person_name: str, status: str,
                        on_990: bool, on_website: bool):
        self.conn.execute(
            """INSERT OR REPLACE INTO validations (ein, person_name, status, on_990, on_website, validated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (ein, person_name, status, int(on_990), int(on_website), time.time())
        )
        self.conn.commit()

    def get_validations(self, ein: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT person_name, status, on_990, on_website, validated_at FROM validations WHERE ein=?",
            (ein,)
        ).fetchall()
        return [dict(r) for r in rows]

    def load_exclusions(self, names: list[dict]):
        """Load exclusion list. Each dict has first_name, last_name, or full_name."""
        for n in names:
            self.conn.execute(
                "INSERT INTO exclusions (first_name, last_name, full_name, added_at) VALUES (?, ?, ?, ?)",
                (n.get("first_name", ""), n.get("last_name", ""),
                 n.get("full_name", ""), time.time())
            )
        self.conn.commit()

    def clear_exclusions(self):
        self.conn.execute("DELETE FROM exclusions")
        self.conn.commit()

    def is_excluded(self, name: str) -> bool:
        name_lower = name.lower().strip()
        row = self.conn.execute(
            "SELECT 1 FROM exclusions WHERE LOWER(full_name)=? OR (LOWER(first_name) || ' ' || LOWER(last_name))=?",
            (name_lower, name_lower)
        ).fetchone()
        return row is not None

    def get_excluded_names(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT COALESCE(NULLIF(full_name,''), first_name || ' ' || last_name) as name FROM exclusions"
        ).fetchall()
        return [r["name"] for r in rows]

    def close(self):
        self.conn.close()
