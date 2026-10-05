"""One search: every source × every date runs concurrently and in isolation. A failed source never fails the
search; it gets a status and a reason in words. Merging happens once, over everything that came back."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from .core.flights import FlightQuery
from .core.money import Rates
from .core.report import SourceReport, Status, combine
from .core.stays import StayQuery
from .merge.flights import FlightCard, merge_flights
from .merge.stays import StayCard, list_stays
from .net.browser import BrowserUnavailable
from .net.client import Blocked
from .net.limiter import Limiter, Quarantined
from .sources.base import Context, NotConfigured, ParseError

log = logging.getLogger("travelops.search")


@dataclass
class FlightSearch:
    query: FlightQuery
    currency: str
    cards: list[FlightCard]
    reports: list[SourceReport]


@dataclass
class StaySearch:
    query: StayQuery
    currency: str
    cards: list[StayCard]
    reports: list[SourceReport]


async def run_source(source, query, ctx: Context, timeout: float, label: str = "") -> tuple[list, SourceReport]:
    started = time.monotonic()
    before = ctx.net.counts[source.name] if ctx.net else 0

    def report(status: Status, reason: str = "", notes: list[str] | None = None, offers: int = 0) -> SourceReport:
        used = (ctx.net.counts[source.name] - before) if ctx.net else 0
        return SourceReport(
            source.name,
            status,
            reason,
            notes or ([label] if label else []),
            offers,
            used,
            round(time.monotonic() - started, 1),
        )

    try:
        raws = await asyncio.wait_for(source.fetch(query, ctx), timeout)
        parsed = source.parse(raws, query, ctx.now())
    except Quarantined as exc:
        return [], report(
            Status.BLOCKED, f"resting after a block until {time.strftime('%H:%M', time.localtime(exc.until))}"
        )
    except Blocked as exc:
        return [], report(Status.BLOCKED, str(exc))
    except (TimeoutError, asyncio.TimeoutError):
        return [], report(Status.TIMEOUT, f"no answer in {timeout:.0f} s")
    except ParseError as exc:
        return [], report(Status.UNPARSED, f"answer not understood: {exc}")
    except (NotConfigured, BrowserUnavailable) as exc:
        return [], report(Status.NOT_CONFIGURED, str(exc))
    except Exception as exc:  # our bug: keep the search alive, name it
        log.exception("%s failed", source.name)
        return [], report(Status.FAILED, f"{type(exc).__name__}: {exc}")
    status = Status.OK if parsed.offers else Status.EMPTY
    return parsed.offers, report(status, notes=parsed.notes, offers=len(parsed.offers))


def deadline(source, query, ctx: Context, timeout: float, runs: int = 1) -> float:
    """`timeout` is what the site may take to answer. Our own request spacing is added on top: every run of a
    source waits in one queue, so without this a search over several dates would time out on its own politeness."""
    if not ctx.net:
        return timeout
    spacing = ctx.net.limiter.longest_wait(Limiter.bucket(source.name, ctx.net.exit), source.max_requests(query) * runs)
    return timeout + spacing


async def search_flights(
    query: FlightQuery, sources: list, ctx: Context, rates: Rates, currency: str, timeout: float = 120
) -> FlightSearch:
    pairs = query.date_pairs()
    jobs = [(s, query.on(d, r), d.isoformat() if len(pairs) > 1 else "") for s in sources for d, r in pairs]
    results = await asyncio.gather(
        *(run_source(s, q, ctx, deadline(s, q, ctx, timeout, len(pairs)), label) for s, q, label in jobs)
    )
    offers = [o for found, _ in results for o in found]
    reports = [combine([rep for (s, _, _), (_, rep) in zip(jobs, results) if s is src]) for src in sources]
    return FlightSearch(query, currency, merge_flights(offers, rates, currency), reports)


async def search_stays(
    query: StayQuery, sources: list, ctx: Context, rates: Rates, currency: str, timeout: float = 120
) -> StaySearch:
    results = await asyncio.gather(*(run_source(s, query, ctx, deadline(s, query, ctx, timeout)) for s in sources))
    offers = [o for found, _ in results for o in found]
    return StaySearch(query, currency, list_stays(offers, rates, currency), [rep for _, rep in results])


def estimate_flights(query: FlightQuery, sources: list, limiter, exit_: str) -> float:
    """Sources run in parallel, requests within a source wait for each other: the slowest source decides."""
    dates = len(query.date_pairs())
    return max(
        (limiter.estimate(Limiter.bucket(s.name, exit_), s.max_requests(query) * dates) for s in sources), default=0.0
    )


def estimate_stays(query: StayQuery, sources: list, limiter, exit_: str) -> float:
    return max((limiter.estimate(Limiter.bucket(s.name, exit_), s.max_requests(query)) for s in sources), default=0.0)
