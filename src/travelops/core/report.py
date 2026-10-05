"""What each source did during a search. Shown to the user on every answer: nothing is hidden silently."""

from __future__ import annotations

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
    base = good[0] if good else reports[0]
    status = Status.OK if any(r.status is Status.OK for r in reports) else base.status
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
            new = [f"{', '.join(r.notes) or 'one run'}: {r.reason}"]
        out.notes += [n for n in new if n not in out.notes]
    return out
