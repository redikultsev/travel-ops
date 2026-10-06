"""What a search found, kept so that a follow-up question costs no request. A stored result is the whole answer
before any filter or shortlist: asking for more cards, other times or a stricter bar is another view of it, not
another search. Every view says when the prices were seen."""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import time
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

REUSE_SECONDS = 30 * 60  # the same question within this time is answered from memory
KEEP_SECONDS = 7 * 86400
DETAILS_SECONDS = 30 * 86400


@dataclass
class Stored:
    id: str
    kind: str
    at: float
    result: dict
    fresh: bool = False  # true when this call ran the search

    def stamp(self, view: dict, now: float) -> dict:
        """Put the identity and the age of the search on a view of it."""
        age = max(0, int((now - self.at) // 60))
        view["search_id"] = self.id
        view["searched_at"] = datetime.fromtimestamp(self.at, timezone.utc).isoformat(timespec="seconds")
        view["age_minutes"] = age
        view["from_memory"] = not self.fresh
        if now - self.at > REUSE_SECONDS:
            view["stale"] = f"prices were seen {age} minutes ago; search again before recommending a booking"
        return view


class Results:
    def __init__(self, path: Path | None, clock: Callable[[], float] = time.time) -> None:
        self.clock = clock
        self.db = sqlite3.connect(path or ":memory:")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS results (id TEXT PRIMARY KEY, key TEXT, kind TEXT, at REAL, body BLOB)"
        )
        self.db.execute("CREATE INDEX IF NOT EXISTS results_key ON results (key, at)")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS details (source TEXT, source_id TEXT, at REAL, body BLOB,"
            " PRIMARY KEY (source, source_id))"
        )

    @staticmethod
    def key(kind: str, query: dict, sources: list[str], currency: str) -> str:
        """Two parts: what was asked, and of whom. A recording is found by the first part alone, so it does not
        go stale when a source is added."""
        asked = hashlib.sha256(json.dumps([kind, query, currency], sort_keys=True, default=str).encode()).hexdigest()
        whom = hashlib.sha256(json.dumps(sorted(sources)).encode()).hexdigest()[:16]
        return f"{asked}:{whom}"

    def put(self, kind: str, key: str, result: dict) -> Stored:
        at = self.clock()
        digest = hashlib.sha256(f"{key}{at}".encode()).digest()
        # Short enough to pass around in a chat; the kind is readable in the first letter.
        ident = kind[0] + base64.b32encode(digest).decode().lower()[:7]
        self.db.execute("DELETE FROM results WHERE at < ?", (at - KEEP_SECONDS,))
        self.db.execute(
            "INSERT OR REPLACE INTO results VALUES (?, ?, ?, ?, ?)",
            (ident, key, kind, at, zlib.compress(json.dumps(result).encode())),
        )
        self.db.commit()
        return Stored(ident, kind, at, json.loads(json.dumps(result)), fresh=True)

    def load(self, ident: str, key: str, kind: str, at: float, result: dict) -> None:
        """Put back a search that was recorded elsewhere."""
        self.db.execute(
            "INSERT OR REPLACE INTO results VALUES (?, ?, ?, ?, ?)",
            (ident, key, kind, at, zlib.compress(json.dumps(result).encode())),
        )
        self.db.commit()

    def put_details(self, source: str, source_id: str, details: dict) -> dict:
        details = dict(
            details, seen_at=datetime.fromtimestamp(self.clock(), timezone.utc).isoformat(timespec="seconds")
        )
        self.db.execute(
            "INSERT OR REPLACE INTO details VALUES (?, ?, ?, ?)",
            (source, source_id, self.clock(), zlib.compress(json.dumps(details).encode())),
        )
        self.db.commit()
        return details

    def details(self, source: str, source_id: str, max_age: float | None = DETAILS_SECONDS) -> dict | None:
        """What a property page said. It changes slowly, unlike a price, so it is kept for a month."""
        row = self.db.execute(
            "SELECT at, body FROM details WHERE source = ? AND source_id = ?", (source, source_id)
        ).fetchone()
        if row is None or (max_age is not None and self.clock() - row[0] > max_age):
            return None
        return json.loads(zlib.decompress(row[1]))

    def _row(self, row) -> Stored | None:
        return Stored(row[0], row[1], row[2], json.loads(zlib.decompress(row[3]))) if row else None

    def recent(self, key: str, max_age: float | None = REUSE_SECONDS, any_sources: bool = False) -> Stored | None:
        """The latest answer to the same question, if it is young enough. `None` as the age takes any;
        `any_sources` takes an answer whichever sources gave it."""
        row = self.db.execute(
            "SELECT id, kind, at, body FROM results WHERE key LIKE ? ORDER BY at DESC LIMIT 1",
            (key.split(":")[0] + ":%" if any_sources else key,),
        ).fetchone()
        if row is None or (max_age is not None and self.clock() - row[2] > max_age):
            return None
        return self._row(row)

    def get(self, ident: str) -> Stored | None:
        return self._row(
            self.db.execute("SELECT id, kind, at, body FROM results WHERE id = ?", (ident.strip().lower(),)).fetchone()
        )
