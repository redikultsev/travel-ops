from datetime import date, datetime, timezone
import pytest
from travelops.core.flights import FlightQuery
from travelops.net.client import Response
from travelops.sources.base import Context, NotConfigured
from travelops.sources.flights import kupibilet
from travelops.sources.flights.kupibilet import Source

QUERY = FlightQuery(("BEG",), ("MOW",), date(2026, 11, 14))
NOW = datetime(2026, 10, 5, tzinfo=timezone.utc)


async def test_requested_cabin_and_return_are_sent_once():
    class Net:
        calls = []

        async def request(self, *args, **kw):
            self.calls.append((args, kw))
            return Response(200, b'{"variants":[],"flights":{}}')

    net = Net()
    q = FlightQuery(QUERY.origins, QUERY.destinations, QUERY.depart, date(2026, 11, 16), adults=2, cabin="business")
    await Source().fetch(q, Context(net, None, lambda: NOW))
    assert len(net.calls) == Source().max_requests(q) == 1
    body = net.calls[0][1]["json"]
    assert body["cabin"] == "business"
    assert body["travelers"]["adult"] == 2
    assert body["trips"] == [
        {"departure": "BEG", "arrival": "MOW", "date": "2026-11-14"},
        {"departure": "MOW", "arrival": "BEG", "date": "2026-11-16"},
    ]


async def test_children_without_ages_are_rejected_before_network():
    q = FlightQuery(QUERY.origins, QUERY.destinations, QUERY.depart, adults=2, children=1)
    with pytest.raises(NotConfigured):
        await Source().fetch(q, Context(None, None, lambda: NOW))


def recorded():
    from pathlib import Path

    return (Path(__file__).parents[1] / "fixtures/kupibilet/beg-mow-2026-11-14-0.json").read_bytes()


def test_recorded_all_variants_and_fare():
    from travelops.core.money import Money

    parsed = Source().parse([recorded()], QUERY, NOW)
    assert len(parsed.offers) == 535
    first = parsed.offers[0]
    assert [s.flight for s in first.itinerary.outbound] == ["JU134"]
    assert first.itinerary.outbound[0].departs.isoformat() == "2026-11-14T18:30:00+01:00"
    assert first.itinerary.duration().total_seconds() == 195 * 60
    assert first.fare.price == Money("37154", "RUB")
    assert first.fare.baggage.checked == 0 and first.fare.baggage.checked_kg == 0
    assert first.fare.baggage.carry_on is True
    assert first.fare.cabin == "economy", "the cabin each flight states"
    assert first.fare.link.kind == "results" and first.fare.link.url.startswith("https://www.kupibilet.ru/search?")
    assert "route%5B0%5D=iatax%3ABEG_2026-11-14_date_2026-11-14_iatax%3AMOW" in first.fare.link.url
    business = FlightQuery(QUERY.origins, QUERY.destinations, QUERY.depart, cabin="business")
    assert kupibilet.results_url(business, "BEG", "MOW") is None, "another cabin's link is not verified"


def test_empty_garbage_and_unknown_baggage():
    import json
    from travelops.sources.base import ParseError

    assert Source().parse([b'{"variants":[],"flights":{}}'], QUERY, NOW).offers == []
    with pytest.raises(ParseError):
        Source().parse([b"{}"], QUERY, NOW)
    x = json.loads(recorded())
    x["variants"] = x["variants"][:1]
    x["variants"][0].pop("baggage")
    x["variants"][0].pop("hand_luggage")
    fare = Source().parse([json.dumps(x).encode()], QUERY, NOW).offers[0].fare
    assert fare.baggage.checked is None and fare.baggage.carry_on is None


@pytest.mark.live
async def test_live_search():
    from tests.live import check_source

    offers = await check_source(Source(), QUERY)
    assert offers[0].fare.price.currency == "RUB"
