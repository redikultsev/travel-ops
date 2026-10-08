"""What each source did during a search. Shown to the user on every answer: nothing is hidden silently."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum


class Status(StrEnum):
    OK = "ok"
    EMPTY = "empty"  # asked, answered, nothing for these dates
    BLOCKED = "blocked"
    TIMEOUT = "timeout"
    UNPARSED = "unparsed"  # answered in a shape we do not understand
    FAILED = "failed"  # our bug or an unexpected error
    NOT_CONFIGURED = "not_configured"


@dataclass
class SourceReport:
    source: str
    status: Status
    reason: str = ""
    notes: list[str] = field(default_factory=list)
    offers: int = 0
    requests: int = 0
    seconds: float = 0.0


def combine(reports: list[SourceReport]) -> SourceReport:
    """One line per source when it ran for several dates. A failed date keeps its label: "2026-11-16: reason"."""
    good = [r for r in reports if r.status in (Status.OK, Status.EMPTY)]
    failed = [r for r in reports if r not in good]
    found = any(r.status is Status.OK for r in reports)
    # Nothing found where a run failed is that failure, not an empty answer: the failed run might have had them.
    base = good[0] if good and (found or not failed) else failed[0]
    status = Status.OK if found else base.status
    out = SourceReport(
        base.source,
        status,
        base.reason,
        offers=sum(r.offers for r in reports),
        requests=sum(r.requests for r in reports),
        seconds=max(r.seconds for r in reports),
    )
    for r in reports:
        if r in good or len(reports) == 1:
            new = r.notes
        else:
            new = [f"failed for {', '.join(r.notes) or 'one run'}: {r.reason}"]
        out.notes += [n for n in new if n not in out.notes]
    return out


# Notes that say the answer is narrower than the question. The rest describe how a source was read.
LIMITS = (
    "failed for",
    "truncated",
    "partial results",
    "left out",
    "cap reached",
    "first ssr page",
    "first page",
    "one advertiser",
    "several tickets",
    "fly elsewhere",
    "band is missing",
    "inventory is not complete",
    "source selection",
    "no validated ticket",
    "summary fares only",
    "understood as",
    "refused the query",
    "not searched",
    "short of",
)


def narrows(note: str) -> bool:
    return any(word in note.lower() for word in LIMITS)


def brief(reports: list[dict]) -> dict:
    """What must reach the human about the sources, short enough to say in a chat: who answered, who did not and
    why, and where the answer is narrower than the question. The full reports stay in `sources`. A limit that
    repeats for every date or route is said once, with how many runs it touched."""
    out: dict = {"ok": [], "empty": [], "problems": [], "limits": []}
    for report in reports:
        if report["status"] == Status.OK:
            out["ok"].append(report["source"])
        elif report["status"] == Status.EMPTY:
            out["empty"].append(report["source"])
        else:
            out["problems"].append({k: report[k] for k in ("source", "status", "reason")})
        alike: dict[str, list[str]] = {}
        for note in report["notes"]:
            if narrows(note):
                # Same words, other numbers, dates or routes: one kind of limit.
                kind = re.sub(r"\b[A-Z]{3}-[A-Z]{3}\b", "R", note)
                alike.setdefault(re.sub(r"\d+", "#", kind), []).append(note)
        for notes in alike.values():
            more = f" (and {len(notes) - 1} more dates or routes like it)" if len(notes) > 1 else ""
            out["limits"].append(f"{report['source']}: {notes[0]}{more}")
    return out
