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


async def test_a_route_that_fails_costs_only_that_route():
    class OneRouteHangs(Good):
        name = "hangs"

        async def fetch(self, q, ctx):
            if q.destinations == ("TGD",):
                raise TimeoutError("a request got no answer in 30 s")
            assert len(q.origins) == len(q.destinations) == 1, "a source is asked one route at a time"
            return [b"x"]

    ctx = Context(net=None, browser=None, now=lambda: NOW)
    two = FlightQuery(("BEG",), ("MOW", "TGD"), date(2026, 11, 15))
    result = await search_flights(two, [OneRouteHangs()], ctx, Rates("EUR", {}, "d"), "EUR")
    report = result.reports[0]
    assert report.status is Status.OK and report.offers == 1
    assert "failed for BEG-TGD: a request got no answer in 30 s" in report.notes
    assert "BEG-MOW" not in report.notes, "a route that answered with no note is not a note"


async def test_a_route_with_nothing_says_so():
    from travelops.search import run_source

    class Nothing(Good):
        def parse(self, raws, query, seen_at):
            return Parsed([], [])

    ctx = Context(net=None, browser=None, now=lambda: NOW)
    _, report = await run_source(Nothing(), FlightQuery(("BEG",), ("VRL",), date(2026, 11, 20)), ctx, 5, "BEG-VRL")
    assert report.status is Status.EMPTY and report.notes == ["BEG-VRL: no offers"]


def test_a_rest_that_ends_another_day_names_the_day():
    import time

    from travelops.search import until

    assert len(until(time.time() + 60)) in (5, 16)
    assert until(time.time() + 3 * 86400).count("-") == 2


async def test_a_note_that_narrows_the_answer_names_its_route():
    from travelops.core.flights import FlightQuery as Query
    from travelops.search import run_source
    from travelops.sources.base import Parsed

    class Source:
        name = "any"

        async def fetch(self, query, ctx):
            return [b""]

        def parse(self, raws, query, seen_at):
            return Parsed(
                [object()],
                ["requested cabin: economy", "26 one-way halves were left out", "BEG-TIV: results truncated: 30 of 62"],
            )

    class Ctx:
        net = None

        def now(self):
            return None

    _, report = await run_source(Source(), Query(("BEG",), ("TIV",), date(2026, 10, 22)), Ctx(), 5, "BEG-TIV")
    assert report.notes == [
        "requested cabin: economy",
        "BEG-TIV: 26 one-way halves were left out",
        "BEG-TIV: results truncated: 30 of 62",
    ]


async def test_runs_of_one_source_count_their_own_requests(tmp_path):
    from travelops.net.client import Net, Response
    from travelops.net.limiter import Limiter, Rule
    from travelops.sources.base import Parsed

    net = Net(tmp_path, limiter=Limiter(None, default=Rule(interval=0, jitter=0)))

    async def send(method, url, **kw):
        await asyncio.sleep(0.01)
        return Response(200, b"{}")

    net._send = send

    class Source:
        name = "any"

        def max_requests(self, query):
            return 3

        async def fetch(self, query, ctx):
            for _ in range(3 if query.destinations == ("TIV",) else 1):
                await ctx.net.request(self.name, "GET", "https://any.example/" + query.destinations[0])
            return [b""]

        def parse(self, raws, query, seen_at):
            return Parsed([], [])

    query = FlightQuery(("BEG",), ("TIV", "TGD"), date(2026, 10, 22))
    result = await search_flights(query, [Source()], Context(net, None, lambda: NOW), Rates("EUR", {}, "d"), "EUR")
    assert result.reports[0].requests == 4 == net.counts["any"], "three and one, not each run counting both"


async def test_a_route_note_of_a_shifted_date_gains_the_date():
    from travelops.core.flights import FlightQuery as Query
    from travelops.search import run_source
    from travelops.sources.base import Parsed

    class Source:
        name = "any"

        async def fetch(self, query, ctx):
            return [b""]

        def parse(self, raws, query, seen_at):
            return Parsed([object()], ["BEG-TIV: results truncated: 30 of 62", "26 halves were left out"])

    class Ctx:
        net = None

        def now(self):
            return None

    _, report = await run_source(
        Source(), Query(("BEG",), ("TIV",), date(2026, 10, 21)), Ctx(), 5, "2026-10-21 BEG-TIV"
    )
    assert report.notes == [
        "2026-10-21: BEG-TIV: results truncated: 30 of 62",
        "2026-10-21 BEG-TIV: 26 halves were left out",
    ]


async def test_offers_to_another_city_than_asked_are_left_out_and_said():
    from travelops.core.flights import FlightQuery as Query
    from travelops.search import on_the_route, run_source
    from travelops.sources.base import Parsed

    def offer(*legs):
        segments = [
            Segment("TK", f"TK{i}", a, b, at_airport("2026-11-12T08:00", a), at_airport("2026-11-12T10:00", b))
            for i, (a, b) in enumerate(legs)
        ]
        fare = Fare(Money(100, "EUR"), "s", "tutu", "economy", Baggage(), None, NOW)
        return FlightOffer(Itinerary(tuple(segments)), fare)

    good, stray, same_city = (
        offer(("BEG", "IST"), ("IST", "LIS")),
        offer(("BEG", "IST"), ("IST", "LED")),
        offer(("BEG", "SAW")),
    )

    class Source:
        name = "tutu"

        async def fetch(self, query, ctx):
            return [b""]

        def parse(self, raws, query, seen_at):
            return Parsed([good, stray, same_city], [])

    class Ctx:
        net = None

        def now(self):
            return None

    query = Query(("BEG",), ("LIS", "IST"), date(2026, 11, 12))
    found, report = await run_source(Source(), query, Ctx(), 5, screen=on_the_route)
    assert found == [good, same_city], "Sabiha Gokcen serves Istanbul; Pulkovo does not serve Lisbon"
    assert report.offers == 2 and "1 offers fly elsewhere than asked (LED)" in report.notes[0]
