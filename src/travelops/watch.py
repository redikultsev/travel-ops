"""Price watching: a saved search that runs again on its own and says when the price falls.

A watch is the arguments of a search tool, plus the filters of its refine tool, plus a rule for when to speak. It
runs only where `travelops watch run` is scheduled, one search at a time, through the same tools an agent calls,
so the limits, the memory and the reports are the same. A watch is compared with the price it last told: the
first check, or the last alert. A slow slide of a few percent a day therefore still adds up to an alert, and a
price that climbs and falls back to where it was says nothing new."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import inspect
import json
import os
import sqlite3
import time
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

from .kinds import KINDS
from .views import _comparable

MIN_EVERY_HOURS = 3.0  # a watch is a background errand: it must not spend a source's patience
MAX_ACTIVE = 20
# Set by the watch itself, not by whoever adds it.
RESERVED = ("refresh", "confirm", "limit", "currency")


@dataclass
class Watch:
    id: str
    kind: str
    arguments: dict
    filters: dict
    currency: str
    below: float | None
    drop_percent: float
    every_hours: float
    until: str
    created: float
    last_run: float | None = None
    told: float | None = None  # the price the human last heard: the first check, or the last alert
    active: bool = True

    def label(self) -> str:
        return KINDS[self.kind].label(self.arguments)


def judge(told: float | None, best: float | None, below: float | None, drop_percent: float) -> str | None:
    """Why this price is worth telling, or None."""
    if best is None:
        return None
    if below is not None and best <= below and (told is None or told > below):
        return f"at or under {below:g}"
    if told is not None and best <= told * (1 - drop_percent / 100):
        return f"{(1 - best / told) * 100:.0f}% below the {told:g} last told"
    return None


def offers_of(card: dict) -> list[dict]:
    """The priced offers of a card, cheapest first, whatever its kind."""
    if "groups" in card:
        return [f for g in card["groups"] for f in g["fares"]]
    return card["fares"] if "fares" in card else card["rates"]


def cheapest(card: dict | None, currency: str) -> float | None:
    if card is None:
        return None
    offers = offers_of(card)
    amounts = [a for a in (_comparable(o, currency) for o in offers) if a is not None]
    return min(amounts) if amounts else None


class Watches:
    def __init__(self, path: Path | None, clock: Callable[[], float] = time.time) -> None:
        self.clock = clock
        self.db = sqlite3.connect(path or ":memory:")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS watches (id TEXT PRIMARY KEY, body TEXT NOT NULL, active INTEGER NOT NULL)"
        )
        self.db.execute("CREATE TABLE IF NOT EXISTS checks (watch TEXT, at REAL, best REAL, search_id TEXT, note TEXT)")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS alerts (id INTEGER PRIMARY KEY, at REAL, body TEXT, delivered INTEGER DEFAULT 0)"
        )

    def _save(self, watch: Watch) -> None:
        body = {k: v for k, v in watch.__dict__.items() if k != "active"}
        self.db.execute(
            "INSERT OR REPLACE INTO watches VALUES (?, ?, ?)", (watch.id, json.dumps(body), int(watch.active))
        )
        self.db.commit()

    def add(self, kind, arguments, filters, currency, below=None, drop_percent=5.0, every_hours=6.0) -> Watch:
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {', '.join(KINDS)}")
        if every_hours < MIN_EVERY_HOURS:
            raise ValueError(f"every_hours must be at least {MIN_EVERY_HOURS:g}: a watch must not hammer the sources")
        if not 1 <= drop_percent <= 90:
            raise ValueError("drop_percent must be from 1 to 90")
        if below is not None and below <= 0:
            raise ValueError("below must be a positive price in the watch currency")
        if len(self.list()) >= MAX_ACTIVE:
            raise ValueError(f"at most {MAX_ACTIVE} watches run at once; stop one first")
        until = arguments[KINDS[kind].last_day]
        at = self.clock()
        ident = "w" + base64.b32encode(hashlib.sha256(f"{kind}{arguments}{at}".encode()).digest()).decode()
        watch = Watch(
            ident.lower()[:8], kind, arguments, filters, currency, below, drop_percent, every_hours, until, at
        )
        self._save(watch)
        return watch

    def get(self, ident: str) -> Watch | None:
        row = self.db.execute("SELECT body, active FROM watches WHERE id = ?", (ident.strip().lower(),)).fetchone()
        return Watch(**json.loads(row[0]), active=bool(row[1])) if row else None

    def list(self, ended: bool = False) -> list[Watch]:
        rows = self.db.execute(
            "SELECT body, active FROM watches" + ("" if ended else " WHERE active = 1") + " ORDER BY rowid"
        ).fetchall()
        return [Watch(**json.loads(body), active=bool(active)) for body, active in rows]

    def stop(self, ident: str) -> Watch:
        watch = self.get(ident)
        if watch is None:
            raise ValueError(f"no watch {ident!r}")
        watch.active = False
        self._save(watch)
        return watch

    def due(self) -> list[Watch]:
        now, today = self.clock(), datetime.fromtimestamp(self.clock(), timezone.utc).date()
        due = []
        for watch in self.list():
            if date.fromisoformat(watch.until) <= today:
                self.stop(watch.id)  # the trip has started: there is nothing left to buy
            elif watch.last_run is None or now - watch.last_run >= watch.every_hours * 3600:
                due.append(watch)
        return due

    def record(self, watch: Watch, best: float | None, search_id: str | None, note: str = "") -> str | None:
        """Keep one check and say why it is worth telling, if it is."""
        why = judge(watch.told, best, watch.below, watch.drop_percent)
        if best is not None and (watch.told is None or why):
            watch.told = best
        watch.last_run = self.clock()
        self._save(watch)
        self.db.execute("INSERT INTO checks VALUES (?, ?, ?, ?, ?)", (watch.id, watch.last_run, best, search_id, note))
        self.db.commit()
        return why

    def alert(self, alert: dict) -> None:
        self.db.execute("INSERT INTO alerts (at, body) VALUES (?, ?)", (self.clock(), json.dumps(alert)))
        self.db.commit()

    def pending(self, take: bool = True) -> list[dict]:
        """Alerts nobody has collected yet, oldest first. `take` marks them collected: whoever asked now tells."""
        rows = self.db.execute("SELECT id, body FROM alerts WHERE delivered = 0 ORDER BY id").fetchall()
        if take and rows:
            self.db.execute(f"UPDATE alerts SET delivered = 1 WHERE id <= {rows[-1][0]} AND delivered = 0")
            self.db.commit()
        return [json.loads(body) for _, body in rows]

    def history(self, ident: str, last: int = 10) -> list[dict]:
        rows = self.db.execute(
            "SELECT at, best, search_id, note FROM checks WHERE watch = ? ORDER BY at DESC LIMIT ?", (ident, last)
        ).fetchall()
        return [
            {
                "at": datetime.fromtimestamp(at, timezone.utc).isoformat(timespec="seconds"),
                "best": best,
                "search_id": search_id,
                "note": note,
            }
            for at, best, search_id, note in rows
        ]


def watch_json(watch: Watch, watches: Watches) -> dict:
    return {
        "watch_id": watch.id,
        "what": watch.label(),
        "kind": watch.kind,
        "arguments": watch.arguments,
        "filters": watch.filters,
        "currency": watch.currency,
        "below": watch.below,
        "drop_percent": watch.drop_percent,
        "every_hours": watch.every_hours,
        "until": watch.until,
        "active": watch.active,
        "price_last_told": watch.told,
        "checks": watches.history(watch.id),
    }


def checked_arguments(tool, given: dict, what: str, partial: bool = False) -> dict:
    """The arguments a tool would accept, without calling it. `partial`: the watch adds the rest."""
    if not isinstance(given, dict):
        raise ValueError(f"{what} must be an object of the tool's arguments")
    reserved = sorted(set(given) & set(RESERVED))
    if reserved:
        raise ValueError(f"{what} cannot set {', '.join(reserved)}: the watch sets them")
    try:
        signature = inspect.signature(tool)
        (signature.bind_partial if partial else signature.bind)(**given)
    except TypeError as exc:
        raise ValueError(f"{what}: {exc}") from exc
    return dict(given)


async def check(tools, watches: Watches, watch: Watch) -> tuple[dict | None, str]:
    """Search again for one watch. Returns the alert, if the price is worth telling, and a line for the log."""
    kind = KINDS[watch.kind]
    search, refine = getattr(tools, kind.tool), getattr(tools, kind.refine_tool)
    try:
        result = await search(**watch.arguments, currency=watch.currency, refresh=True, confirm=True, limit=1)
        if watch.filters:
            result = await refine(result["search_id"], limit=1, **watch.filters)
    except Exception as exc:  # one watch failing must not stop the others
        watches.record(watch, None, None, f"failed: {exc}")
        return None, f"{watch.label()}: the search failed ({exc})"
    card = result["cards"][0] if result["cards"] else None
    best = cheapest(card, watch.currency)
    problems = ", ".join(p["source"] for p in result["report"]["problems"])
    note = f"no answer from {problems}" if problems else ""
    previous = watch.told
    why = watches.record(watch, best, result["search_id"], note)
    if best is None:
        return None, f"{watch.label()}: nothing matches now" + (f" ({note})" if note else "")
    offer = offers_of(card)[0]
    line = f"{watch.label()}: {best:g} {watch.currency}" + (
        f" (last told {previous:g})" if previous is not None else ""
    )
    if not why:
        return None, line
    alert = {
        "watch_id": watch.id,
        "what": watch.label(),
        "price": best,
        "currency": watch.currency,
        "last_told": previous,
        "why": why,
        "seller": offer.get("seller") or card.get("stay", {}).get("source"),
        "link": (offer.get("link") or {}).get("url"),
        "search_id": result["search_id"],
        "seen_at": result.get("searched_at"),
        "sources_without_answer": problems or None,
    }
    return alert, f"{line} — {why}"


async def notify(alert: dict, line: str) -> list[str]:
    """Push an alert, besides keeping it for `watch_alerts`, where the person running the watches pointed it.
    `TRAVELOPS_NOTIFY_URL` gets the line as a plain POST (an ntfy topic works as is); `TRAVELOPS_NOTIFY_COMMAND`
    gets the alert as JSON on standard input, for an assistant that tells the human in its own words."""
    failures = []
    url = os.environ.get("TRAVELOPS_NOTIFY_URL")
    if url:

        def post():
            request = urllib.request.Request(url, data=line.encode(), headers={"Title": "travel-ops"})
            with urllib.request.urlopen(request, timeout=20) as response:
                response.read()

        try:
            await asyncio.to_thread(post)
        except OSError as exc:
            failures.append(f"notify url: {exc}")
    command = os.environ.get("TRAVELOPS_NOTIFY_COMMAND")
    if command:
        process = await asyncio.create_subprocess_shell(command, stdin=asyncio.subprocess.PIPE)
        await process.communicate(json.dumps(alert, ensure_ascii=False).encode())
        if process.returncode:
            failures.append(f"notify command exited with {process.returncode}")
    return failures


async def run_due(tools, watches: Watches, say: Callable[[str], None] = print) -> int:
    """Check every watch that is due, one after another. Alerts are kept until collected with `watch_alerts`, and
    pushed if a hook is set. Returns how many alerts there were."""
    alerts = 0
    for watch in watches.due():
        alert, line = await check(tools, watches, watch)
        say(line)
        if alert:
            alerts += 1
            watches.alert(alert)
            for failure in await notify(alert, line):
                say(failure)
    return alerts
