import json
from datetime import date, datetime, timezone
import pytest
from travelops.core.flights import FlightQuery
from travelops.net.client import Response
from travelops.net.browser import Session
from travelops.sources.base import Context, NotConfigured
from travelops.sources.flights.wildberries import Source

QUERY = FlightQuery(("MOW",), ("LED",), date(2026, 11, 14))
NOW = datetime(2026, 10, 5, tzinfo=timezone.utc)


async def test_fetch_session_and_one_summary_request():
    class Browser:
        async def get(self, *args, **kw):
            assert kw["engine"] == "camoufox"
            return Session({"test": "value"}, "test-agent", "camoufox", 0)

    class Net:
        calls = []

        async def request(self, *args, **kw):
            self.calls.append((args, kw))
            return Response(200, b'{"result":{"data":{"flights":[]}}}')

    net = Net()
    await Source().fetch(QUERY, Context(net, Browser(), lambda: NOW))
    assert len(net.calls) == 1 and Source().max_requests(QUERY) == 2
    kw = net.calls[0][1]
    body = json.loads(kw["data"])
    assert body["serviceClass"] == "ECONOMY" and body["beginDate_at"] == "2026-11-14T00:00:00.000Z"
    assert body["beginLocationCode"] == "MOW" and body["endLocationCode"] == "LED"
    assert kw["cookies"] == {"test": "value"} and kw["impersonate"] == "firefox"


async def test_unverified_party_is_not_requested():
    with pytest.raises(NotConfigured):
        await Source().fetch(
            FlightQuery(QUERY.origins, QUERY.destinations, QUERY.depart, adults=2), Context(None, None, lambda: NOW)
        )


def recorded():
    from pathlib import Path

    return (Path(__file__).parents[1] / "fixtures/wildberries/mow-led-2026-11-14.ndjson").read_bytes()


def test_recorded_stream_minor_price_and_moscow_wall_time():
    from travelops.core.money import Money

    result = Source().parse([recorded()], QUERY, NOW)
    assert len(result.offers) == 6
    offer = result.offers[0]
    assert offer.fare.price == Money(6121, "RUB")
    segment = offer.itinerary.outbound[0]
    assert (segment.flight, segment.origin, segment.destination, segment.operating) == ("SU6215", "SVO", "LED", "FV")
    assert segment.departs.isoformat() == "2026-11-14T11:30:00+03:00"
    assert segment.arrives.isoformat() == "2026-11-14T13:00:00+03:00"
    assert offer.fare.baggage.checked == 0 and offer.fare.baggage.carry_on is True
    assert offer.fare.cabin == "economy" and offer.fare.link is None
    assert "summary fares only; detailed tariff terms unknown" in result.notes


def test_a_later_chunk_replaces_an_earlier_version_of_a_flight():
    first = json.loads(recorded())
    cheaper = json.loads(recorded())
    cheaper["result"]["data"]["flights"] = cheaper["result"]["data"]["flights"][:1]
    cheaper["result"]["data"]["flights"][0]["fullPrice"] = 100150
    stream = json.dumps(first).encode() + b"\n" + json.dumps(cheaper).encode()
    from travelops.core.money import Money

    result = Source().parse([stream], QUERY, NOW)
    assert len(result.offers) == 6 and result.offers[0].fare.price == Money("1001.50", "RUB")


def test_other_airport_zone_empty_and_garbage():
    from travelops.sources.base import ParseError

    first = json.loads(recorded())
    first["result"]["data"]["flights"] = first["result"]["data"]["flights"][:1]
    segment = first["result"]["data"]["flights"][0]["legs"][0]["segments"][0]
    segment["airportBeginCode"] = "SVX"
    result = Source().parse([json.dumps(first).encode()], QUERY, NOW)
    assert result.offers[0].itinerary.outbound[0].departs.isoformat() == "2026-11-14T13:30:00+05:00"
    assert Source().parse([b'{"result":{"data":{"flights":[]}}}'], QUERY, NOW).offers == []
    with pytest.raises(ParseError):
        Source().parse([b"{}"], QUERY, NOW)


@pytest.mark.live
async def test_live_search():
    from tests.live import check_source

    offers = await check_source(Source(), QUERY)
    assert offers[0].fare.price.currency == "RUB"
