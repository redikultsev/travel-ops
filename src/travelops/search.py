"""One search: every source × every date runs concurrently and in isolation. A failed source never fails the
search; it gets a status and a reason in words. Merging happens once, over everything that came back."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from dataclasses import dataclass, replace

from .core.flights import FlightQuery, on_route
from .core.ground import GroundOffer, GroundQuery
from .core.money import Rates
from .core.report import SourceReport, Status, combine, narrows
from .core.stays import StayQuery
from .merge.flights import FlightCard, merge_flights
from .merge.stays import StayCard, list_stays
from .net.browser import BrowserUnavailable
from .net.client import Blocked
from .net.limiter import Limiter, Quarantined
from .net.tally import RUN
from .sources.base import Context, NotConfigured, ParseError, SourceFault

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


@dataclass
class GroundSearch:
    query: GroundQuery
    currency: str
    offers: list[GroundOffer]
    reports: list[SourceReport]


async def run_source(source, query, ctx: Context, timeout: float, label: str = "") -> tuple[list, SourceReport]:
    """One source, one date, one route. Runs as a task of its own: `gather` gives each run a context of
    its own, so the tally it sets here is not seen by its neighbours."""
    started = time.monotonic()
    # This run's own requests: runs of one source overlap, so the source's total would count the neighbours too.
    mine: Counter = Counter()
    RUN.set(mine)

    def report(status: Status, reason: str = "", notes: list[str] | None = None, offers: int = 0) -> SourceReport:
        return SourceReport(
            source.name,
            status,
            reason,
            notes or ([label] if label else []),
            offers,
            mine[source.name],
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
    except (TimeoutError, asyncio.TimeoutError) as exc:
        # A single request that hung says so itself; an empty message is the deadline of the whole run.
        return [], report(Status.TIMEOUT, str(exc) or f"no answer in {timeout:.0f} s")
    except ParseError as exc:
        return [], report(Status.UNPARSED, f"answer not understood: {exc}")
    except (NotConfigured, BrowserUnavailable) as exc:
        return [], report(Status.NOT_CONFIGURED, str(exc))
    except SourceFault as exc:
        return [], report(Status.FAILED, str(exc))
    except Exception as exc:  # our bug: keep the search alive, name it
        log.exception("%s failed", source.name)
        return [], report(Status.FAILED, f"{type(exc).__name__}: {exc}")
    offers, notes = parsed.offers, list(parsed.notes)
    if isinstance(query, FlightQuery):
        kept = [
            o for o in offers if not hasattr(o, "itinerary") or on_route(o.itinerary, query.origins, query.destinations)
        ]
        if len(kept) < len(offers):
            strays = {o.itinerary.outbound[-1].destination for o in offers if o not in kept}
            notes.append(
                f"{len(offers) - len(kept)} offers fly elsewhere than asked ({', '.join(sorted(strays))}) "
                "and were left out: the source took the place for another"
            )
        offers = kept
    status = Status.OK if offers else Status.EMPTY

    # A note that narrows the answer says which date and route it is about; the others read the same for all.
    def labelled(note: str) -> str:
        if not label or not narrows(note):
            return note
        missing = [part for part in label.split(" ") if not note.startswith(part) and f" {part}" not in note[:40]]
        return f"{' '.join(missing)}: {note}" if missing else note

    notes = [labelled(note) for note in notes]
    return offers, report(status, notes=notes, offers=len(offers))


def deadline(source, query, ctx: Context, timeout: float, runs: int = 1) -> float:
    """`timeout` is what the site may take to answer. Our own request spacing is added on top: every run of a
    source waits in one queue, so without this a search over several dates would time out on its own politeness."""
    if not ctx.net:
        return timeout
    spacing = ctx.net.limiter.longest_wait(Limiter.bucket(source.name, ctx.net.exit), source.max_requests(query) * runs)
    return timeout + spacing


async def search_flights(
    query: FlightQuery, sources: list, ctx: Context, rates: Rates, currency: str, timeout: float = 120, sharing: int = 1
) -> FlightSearch:
    """Every date and every route is its own run of a source: one that fails costs that date or route only.
    `sharing` is how many searches run at once over the same sources: they wait in the same queues, so each
    gets that much more time before it is called a timeout."""
    dates = query.date_pairs()
    routes = [(o, d) for o in query.origins for d in query.destinations]
    jobs = []
    for source in sources:
        for depart, back in dates:
            for origin, destination in routes:
                label = " ".join(
                    part
                    for part in (
                        depart.isoformat() if len(dates) > 1 else "",
                        f"{origin}-{destination}" if len(routes) > 1 else "",
                    )
                    if part
                )
                jobs.append(
                    (source, replace(query.on(depart, back), origins=(origin,), destinations=(destination,)), label)
                )
    runs = len(dates) * len(routes) * sharing
    results = await asyncio.gather(
        *(run_source(s, q, ctx, deadline(s, q, ctx, timeout, runs), label) for s, q, label in jobs)
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


async def search_ground(
    query: GroundQuery, sources: list, ctx: Context, rates: Rates, currency: str, timeout: float = 120
) -> GroundSearch:
    """Each source answers for every mode it carries. Offers are not merged across sources: two sources name one
    station in two languages, and a bus at the same minute may be another company's."""
    results = await asyncio.gather(*(run_source(s, query, ctx, deadline(s, query, ctx, timeout)) for s in sources))
    offers = [o for found, _ in results for o in found]
    return GroundSearch(query, currency, offers, [rep for _, rep in results])


def estimate_ground(query: GroundQuery, sources: list, limiter, exit_: str) -> float:
    return max(
        (limiter.estimate(Limiter.bucket(s.name, exit_), expected_requests(s, query)) for s in sources), default=0.0
    )


def expected_requests(source, query) -> int:
    """What a search usually costs. `max_requests` is the ceiling, kept for deadlines: telling the user the ceiling
    makes a two-minute search look like six and asks for a confirmation nobody needs."""
    return getattr(source, "typical_requests", source.max_requests)(query)


def estimate_flights(query: FlightQuery, sources: list, limiter, exit_: str) -> float:
    """Sources run in parallel, requests within a source wait for each other: the slowest source decides."""
    runs = len(query.date_pairs()) * len(query.origins) * len(query.destinations)
    one = replace(query, origins=query.origins[:1], destinations=query.destinations[:1])
    return max(
        (limiter.estimate(Limiter.bucket(s.name, exit_), expected_requests(s, one) * runs) for s in sources),
        default=0.0,
    )


def estimate_stays(query: StayQuery, sources: list, limiter, exit_: str) -> float:
    return max(
        (limiter.estimate(Limiter.bucket(s.name, exit_), expected_requests(s, query)) for s in sources), default=0.0
    )
