"""A tiny SQLite JSON cache so repeat lookups do not hammer the MLB API.

Keys are strings; the client uses ``people:{person_id}:{sport_id}`` and
``lookup:{normalised name}``. Entries older than the TTL are treated as misses
and overwritten on the next successful fetch.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

from app.config import CACHE_PATH, CACHE_TTL_SECONDS

_SCHEMA = """
CREATE TABLE IF NOT EXISTS api_cache (
    key        TEXT PRIMARY KEY,
    payload    TEXT NOT NULL,
    fetched_at REAL NOT NULL
);
"""


class JSONCache:
    """Thread-safe key/value cache of raw API JSON."""

    def __init__(self, path: Path | str = CACHE_PATH, ttl_seconds: int = CACHE_TTL_SECONDS) -> None:
        self.path = Path(path)
        self.ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        if self.path.parent and str(self.path.parent) not in ("", "."):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(_SCHEMA)

    def get(self, key: str) -> Optional[Any]:
        """Return the cached payload, or ``None`` if missing or stale."""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT payload, fetched_at FROM api_cache WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return None
        payload, fetched_at = row
        if self.ttl_seconds >= 0 and (time.time() - fetched_at) > self.ttl_seconds:
            return None
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return None

    def set(self, key: str, value: Any) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO api_cache (key, payload, fetched_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET payload = excluded.payload, "
                "fetched_at = excluded.fetched_at",
                (key, json.dumps(value), time.time()),
            )

    def clear(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM api_cache")
