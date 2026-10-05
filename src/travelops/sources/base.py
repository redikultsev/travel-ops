"""What a source is: `fetch` goes to the network through the context, `parse` turns raw bodies into offers.
`parse` never touches the network, so parsers are tested and repaired on recorded answers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Protocol

from ..core.flights import FlightQuery
from ..core.stays import StayQuery
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
class Parsed:
    offers: list = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # e.g. "results truncated: 120 of 300"


class FlightSource(Protocol):
    name: str

    def max_requests(self, query: FlightQuery) -> int: ...
    async def fetch(self, query: FlightQuery, ctx: Context) -> list[bytes]: ...
    def parse(self, raws: list[bytes], query: FlightQuery, seen_at: datetime) -> Parsed: ...


class StaySource(Protocol):
    name: str

    def max_requests(self, query: StayQuery) -> int: ...
    async def fetch(self, query: StayQuery, ctx: Context) -> list[bytes]: ...
    def parse(self, raws: list[bytes], query: StayQuery, seen_at: datetime) -> Parsed: ...
