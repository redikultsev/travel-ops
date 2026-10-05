"""Rate limiter tuned to never get banned. It sits in the network client, so a source cannot forget it.

A bucket is `source@exit`: penalties belong to the address that earned them, so a new proxy starts clean and coming
back without it resumes the old history. State lives in SQLite and survives restarts, so CLI, MCP and agent share
one budget."""

from __future__ import annotations

import asyncio
import json
import random
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Awaitable, Callable


@dataclass(frozen=True)
class Rule:
    interval: float = 4.0  # seconds between requests to one bucket
    jitter: float = 1.5
    window: int = 40  # at most `window` requests…
    per: float = 900.0  # …in this many seconds
    penalty: float = 2.0  # interval multiplier after each block
    max_slow: float = 16.0
    quarantine: float = 1800.0  # seconds a bucket rests after a block


@dataclass
class State:
    last: float = 0.0
    history: list[float] = field(default_factory=list)
    slow: float = 1.0
    quarantined_until: float = 0.0


class Quarantined(Exception):
    def __init__(self, bucket: str, until: float) -> None:
        super().__init__(f"{bucket} is resting after a block")
        self.bucket, self.until = bucket, until


class Limiter:
    def __init__(
        self,
        path: Path | None,
        rules: dict[str, Rule] | None = None,
        default: Rule = Rule(),
        *,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rand: Callable[[], float] = random.random,
    ) -> None:
        self.rules, self.default = rules or {}, default
        self.clock, self.sleep, self.rand = clock, sleep, rand
        self.db = sqlite3.connect(path or ":memory:")
        self.db.execute("CREATE TABLE IF NOT EXISTS limiter (bucket TEXT PRIMARY KEY, state TEXT NOT NULL)")
        self.locks: dict[str, asyncio.Lock] = {}

    @staticmethod
    def bucket(source: str, exit_: str) -> str:
        return f"{source}@{exit_}" if exit_ else source

    def rule(self, bucket: str) -> Rule:
        return self.rules.get(bucket.split("@")[0], self.default)

    def state(self, bucket: str) -> State:
        row = self.db.execute("SELECT state FROM limiter WHERE bucket = ?", (bucket,)).fetchone()
        return State(**json.loads(row[0])) if row else State()

    def save(self, bucket: str, state: State) -> None:
        self.db.execute("INSERT OR REPLACE INTO limiter VALUES (?, ?)", (bucket, json.dumps(asdict(state))))
        self.db.commit()

    async def acquire(self, bucket: str) -> None:
        async with self.locks.setdefault(bucket, asyncio.Lock()):
            rule, st, now = self.rule(bucket), self.state(bucket), self.clock()
            if st.quarantined_until > now:
                raise Quarantined(bucket, st.quarantined_until)
            wait = 0.0
            if st.last:
                wait = st.last + rule.interval * st.slow + rule.jitter * self.rand() - now
            st.history = [t for t in st.history if t > now - rule.per]
            if len(st.history) >= rule.window:
                wait = max(wait, st.history[0] + rule.per - now)
            if wait > 0:
                await self.sleep(wait)
            st.last = self.clock()
            st.history.append(st.last)
            self.save(bucket, st)

    def blocked(self, bucket: str) -> None:
        rule, st = self.rule(bucket), self.state(bucket)
        st.slow = min(st.slow * rule.penalty, rule.max_slow)
        st.quarantined_until = self.clock() + rule.quarantine
        self.save(bucket, st)

    def succeeded(self, bucket: str) -> None:
        st = self.state(bucket)
        if st.slow > 1.0:
            st.slow = max(1.0, st.slow / 1.25)
            self.save(bucket, st)

    def reset(self, bucket: str) -> None:
        self.db.execute("DELETE FROM limiter WHERE bucket = ?", (bucket,))
        self.db.commit()

    def estimate(self, bucket: str, requests: int) -> float:
        rule, st = self.rule(bucket), self.state(bucket)
        return max(0, requests - 1) * (rule.interval * st.slow + rule.jitter / 2)

    def longest_wait(self, bucket: str, requests: int) -> float:
        """Upper bound of the spacing these requests can cost, for deadlines rather than for the user."""
        rule, st = self.rule(bucket), self.state(bucket)
        return requests * (rule.interval * st.slow + rule.jitter)

    def overview(self) -> list[tuple[str, float, float]]:
        """(bucket, current interval, quarantined until) — credentials never get here, only host:port."""
        rows = self.db.execute("SELECT bucket FROM limiter ORDER BY bucket").fetchall()
        return [(b, self.rule(b).interval * self.state(b).slow, self.state(b).quarantined_until) for (b,) in rows]
