import asyncio
from datetime import date, datetime, timezone

from travelops.core.flights import Baggage, Fare, FlightOffer, FlightQuery, Itinerary, Segment, at_airport
from travelops.core.money import Money, Rates
from travelops.core.report import Status
from travelops.net.client import Blocked
from travelops.search import estimate_flights, search_flights
from travelops.sources.base import Context, ParseError, Parsed

NOW = datetime(2026, 10, 4, tzinfo=timezone.utc)
Q = FlightQuery(("BEG",), ("SVO",), date(2026, 11, 15))


def an_offer(source):
    seg = Segment(
        "JU", "JU130", "BEG", "SVO", at_airport("2026-11-15T10:00", "BEG"), at_airport("2026-11-15T14:00", "SVO")
    )
    return FlightOffer(Itinerary((seg,)), Fare(Money(300, "EUR"), source, source, "economy", Baggage(), None, NOW))


class Good:
    name = "good"

    def max_requests(self, q):
        return 2

    async def fetch(self, q, ctx):
        return [b"x"]

    def parse(self, raws, q, seen_at):
        return Parsed([an_offer("good")], ["economy only"])


class Banned(Good):
    name = "banned"

    async def fetch(self, q, ctx):
        raise Blocked("anti-bot did not let us in")


class Slow(Good):
    name = "slow"

    async def fetch(self, q, ctx):
        await asyncio.sleep(5)


class Broken(Good):
    name = "broken"

    def parse(self, raws, q, seen_at):
        raise ParseError("no 'tickets' field")


class Empty(Good):
    name = "empty"

    def parse(self, raws, q, seen_at):
        return Parsed()


async def test_failures_are_isolated_and_named(tmp_path):
    ctx = Context(net=None, browser=None, now=lambda: NOW)
    result = await search_flights(
        Q, [Good(), Banned(), Slow(), Broken(), Empty()], ctx, Rates("EUR", {}, "d"), "EUR", timeout=0.05
    )
    status = {r.source: r.status for r in result.reports}
    assert status == {
        "good": Status.OK,
        "banned": Status.BLOCKED,
        "slow": Status.TIMEOUT,
        "broken": Status.UNPARSED,
        "empty": Status.EMPTY,
    }
    assert len(result.cards) == 1
    assert "economy only" in next(r for r in result.reports if r.source == "good").notes
    assert "anti-bot" in next(r for r in result.reports if r.source == "banned").reason


async def test_flex_runs_every_date():
    seen = []

    class Recording(Good):
        async def fetch(self, q, ctx):
            seen.append(q.depart)
            return [b"x"]

    ctx = Context(net=None, browser=None, now=lambda: NOW)
    q = FlightQuery(("BEG",), ("SVO",), date(2026, 11, 15), flex_days=1)
    await search_flights(q, [Recording()], ctx, Rates("EUR", {}, "d"), "EUR")
    assert sorted(seen) == [date(2026, 11, 14), date(2026, 11, 15), date(2026, 11, 16)]


def test_estimate_uses_the_slowest_source():
    class FakeLimiter:
        def estimate(self, bucket, n):
            return n * 10.0

    assert estimate_flights(Q, [Good()], FakeLimiter(), exit_="") == 20.0


async def test_request_spacing_does_not_eat_the_answer_deadline():
    from collections import Counter
    from types import SimpleNamespace
    from travelops.net.limiter import Limiter, Rule
    from travelops.search import deadline

    limiter = Limiter(None, {"good": Rule(interval=30, jitter=10)})
    ctx = Context(net=SimpleNamespace(limiter=limiter, exit="", counts=Counter()), browser=None, now=lambda: NOW)
    assert deadline(Good(), Q, ctx, 120) == 120 + 2 * 40
    assert deadline(Good(), Q, ctx, 120, runs=3) == 120 + 6 * 40
    limiter.blocked("good")
    assert deadline(Good(), Q, ctx, 120) == 120 + 2 * 70, "a slowed bucket waits longer"
