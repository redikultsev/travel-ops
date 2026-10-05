"""Cache of raw responses. Raw, not parsed: a broken parser is fixed on saved answers without any request.
Cached prices are never shown as fresh — the search always fetches live; the cache serves repeats and repairs."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass
class Cached:
    status: int
    body: bytes
    url: str
    at: float


class RawCache:
    def __init__(self, path: Path | None, clock: Callable[[], float] = time.time) -> None:
        self.clock = clock
        self.db = sqlite3.connect(path or ":memory:")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS raw (key TEXT PRIMARY KEY, at REAL, source TEXT, url TEXT,"
            " status INTEGER, body BLOB)"
        )

    @staticmethod
    def key(method: str, url: str, params: object, body: object) -> str:
        blob = json.dumps([method.upper(), url, params, body], sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()

    def put(self, key: str, source: str, url: str, status: int, body: bytes) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO raw VALUES (?, ?, ?, ?, ?, ?)", (key, self.clock(), source, url, status, body)
        )
        self.db.commit()

    def get(self, key: str, max_age: float) -> Cached | None:
        row = self.db.execute("SELECT status, body, url, at FROM raw WHERE key = ?", (key,)).fetchone()
        if row is None or self.clock() - row[3] > max_age:
            return None
        return Cached(*row)

    def latest(self, source: str, limit: int) -> list[Cached]:
        rows = self.db.execute(
            "SELECT status, body, url, at FROM raw WHERE source = ? ORDER BY at DESC, rowid DESC LIMIT ?",
            (source, limit),
        ).fetchall()
        return [Cached(*r) for r in rows]
