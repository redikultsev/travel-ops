import json
from datetime import date, datetime, timezone

import pytest

from travelops.core.flights import FlightQuery
from travelops.net.client import Response
from travelops.sources.base import Context, ParseError
from travelops.sources.flights.onetwotrip import Source, challenge

NOW = datetime(2026, 10, 5, tzinfo=timezone.utc)
QUERY = FlightQuery(("BEG",), ("MOW",), date(2026, 11, 14))


async def test_fetch_one_requested_cabin_and_whole_party():
    class FakeNet:
        calls = []

        async def request(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return Response(200, b'{"prices":{},"trips":{},"transportationVariants":{}}')

    net = FakeNet()
    query = FlightQuery(QUERY.origins, QUERY.destinations, QUERY.depart, adults=2, cabin="business")
    raws = await Source().fetch(query, Context(net, None, lambda: NOW))
    assert len(raws) == 1 and len(net.calls) == Source().max_requests(query) == 1
    params = net.calls[0][1]["params"]
    assert params["route"] == "1411BEGMOW" and params["cs"] == "B" and params["ad"] == "2"
    assert params["showDeeplink"] == "true"
    assert "sc=B&p=2_0_0" in net.calls[0][1]["headers"]["referer"]


def test_success_status_can_still_be_rate_limited():
    assert challenge(Response(200, b'{"error":"ATTEMPTS_EXCEEDED"}'))


def recorded():
    from pathlib import Path

    return (Path(__file__).parents[1] / "fixtures/onetwotrip/beg-mow-2026-11-14-0.json").read_bytes()


def test_recorded_prices_and_complete_chain():
    from travelops.core.money import Money

    parsed = Source().parse([recorded()], QUERY, NOW)
    assert len(parsed.offers) == 102
    first = parsed.offers[0]
    assert [s.flight for s in first.itinerary.outbound] == ["JU426", "DP996"]
    assert first.itinerary.outbound[0].departs.isoformat() == "2026-11-14T00:50:00+01:00"
    assert first.itinerary.outbound[-1].arrives.isoformat() == "2026-11-14T23:25:00+03:00"
    assert first.itinerary.duration().total_seconds() == 1235 * 60
    assert first.fare.price == Money("29472.75", "RUB")
    assert first.fare.cabin == "economy"
    assert first.fare.baggage.checked == 0
    assert first.fare.baggage.carry_on is None
    assert first.fare.refundable is None
    assert first.fare.link.kind == "ticket" and first.fare.link.url.startswith("https://www.onetwotrip.com/ru/f/book/")
    assert "requested cabin: economy" in parsed.notes


def test_empty_garbage_missing_return_and_results_link():
    assert Source().parse([b'{"prices":{},"transportationVariants":{},"trips":{}}'], QUERY, NOW).offers == []
    with pytest.raises(ParseError):
        Source().parse([b"{}"], QUERY, NOW)
    with pytest.raises(ParseError, match="return"):
        Source().parse(
            [recorded()], FlightQuery(QUERY.origins, QUERY.destinations, QUERY.depart, date(2026, 11, 16)), NOW
        )
    data = json.loads(recorded())
    for price in data["prices"].values():
        price.pop("deeplink", None)
    first = Source().parse([json.dumps(data).encode()], QUERY, NOW).offers[0]
    assert first.fare.link.kind == "results" and "sc=E&p=1_0_0" in first.fare.link.url


@pytest.mark.live
async def test_live_search():
    from tests.live import check_source

    offers = await check_source(Source(), QUERY)
    assert offers[0].fare.link is not None
