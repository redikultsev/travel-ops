"""What a source is: `fetch` goes to the network through the context, `parse` turns raw bodies into offers.
`parse` never touches the network, so parsers are tested and repaired on recorded answers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Generic, Protocol, TypeVar

from ..net.browser import BrowserSessions
from ..net.client import Net


class ParseError(Exception):
    """The answer is not in the shape we know. The raw body stays in the cache for repair."""


class NotConfigured(Exception):
    pass


class SourceFault(Exception):
    """The site answered with its own server error. Not a block and not our bug."""


@dataclass
class Context:
    net: Net
    browser: BrowserSessions
    now: Callable[[], datetime]


@dataclass
class Coverage:
    """Whether everything a site has under a ceiling was read: its own count against the listings read. A source
    that gives no count, or a search with no ceiling, cannot be complete; it says what it read."""

    read: int
    counted: int | None = None  # what the site says it has for the search, under the ceiling
    ceiling_eur: float | None = None  # for the whole stay; None: none was asked
    complete: bool = False


@dataclass
class Parsed:
    offers: list = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # e.g. "results truncated: 120 of 300"
    coverage: Coverage | None = None


Query = TypeVar("Query", contravariant=True)


class Source(Protocol, Generic[Query]):
    """A site or server that answers the queries of one kind: a FlightQuery, a StayQuery or a GroundQuery. A new
    instance serves one search, and every date and route of that search is a run of it.

    `max_requests` is the most one run can send: deadlines are made from it. A source whose runs usually send
    fewer, because a handshake is shared or a second page is rare, also says `typical_requests(query)`, and the
    wait told before a search is made from that. Without it the ceiling is what is told."""

    name: str

    def max_requests(self, query: Query) -> int: ...
    async def fetch(self, query: Query, ctx: Context) -> list[bytes]: ...
    def parse(self, raws: list[bytes], query: Query, seen_at: datetime) -> Parsed: ...
