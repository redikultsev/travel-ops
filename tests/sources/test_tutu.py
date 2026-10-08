import json
from datetime import date, datetime, timezone

import pytest

from travelops.core.flights import FlightQuery
from travelops.net.client import Response
from travelops.sources.base import Context, ParseError
from travelops.sources.flights.tutu import Source

NOW = datetime(2026, 10, 5, tzinfo=timezone.utc)
QUERY = FlightQuery(("BEG",), ("MOW",), date(2026, 11, 14))


class FakeNet:
    def __init__(self):
        self.calls = []

    async def request(self, source, method, url, **kwargs):
        self.calls.append(kwargs)
        message = kwargs["json"]
        if message["method"] == "initialize":
            return Response(
                200,
                b'{"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2025-03-26"}}',
                {"mcp-session-id": "test-session"},
            )
        if message["method"] == "notifications/initialized":
            return Response(202, b"")
        return Response(
            200, b'{"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text","text":"{\\"offers\\":[]}"}]}}'
        )


async def test_fetch_calls_only_search_and_keeps_session():
    net = FakeNet()
    raws = await Source().fetch(QUERY, Context(net, None, lambda: NOW))
    assert json.loads(raws[0]) == {"offers": []}
    assert len(net.calls) == 3 and Source().max_requests(QUERY) == 2 + 4, "no further page when it has no more"
    call = net.calls[-1]
    assert call["headers"]["mcp-session-id"] == "test-session"
    assert call["json"]["params"]["name"] == "search_avia"
    assert call["json"]["params"]["arguments"]["destination"] == "Moscow"
    assert call["json"]["params"]["arguments"]["sort"] == "price_asc"
    assert [c.get("queue") for c in net.calls] == ["handshake", "handshake", None], "only the search waits as a search"


async def test_one_handshake_serves_every_route_of_a_search():
    import asyncio
    from dataclasses import replace

    net, source = FakeNet(), Source()
    routes = [replace(QUERY, destinations=(code,)) for code in ("MOW", "LED", "KZN")]
    await asyncio.gather(*(source.fetch(route, Context(net, None, lambda: NOW)) for route in routes))
    methods = [c["json"].get("method") for c in net.calls]
    assert methods.count("initialize") == 1 and methods.count("tools/call") == 3
    assert source.typical_requests(QUERY) == 4, "a busy route reads every page"


def recorded():
    from pathlib import Path

    return (Path(__file__).parents[1] / "fixtures/tutu/beg-mow-2026-11-14-0.json").read_bytes()


def test_recorded_search_preserves_every_fare_and_connection():
    from travelops.core.money import Money

    parsed = Source().parse([recorded()], QUERY, NOW)
    assert len(parsed.offers) == 134
    first = parsed.offers[0]
    assert [s.flight for s in first.itinerary.outbound] == ["JU426", "PC386"]
    assert first.itinerary.airport_changes() == [("IST", "SAW")]
    assert first.itinerary.duration().total_seconds() == 1395 * 60
    assert first.fare.price == Money("25560.44", "RUB")
    assert first.fare.cabin == "economy" and first.fare.baggage.checked == 0
    assert first.fare.baggage.carry_on is True and first.fare.refundable is False
    assert first.fare.link.kind == "results" and "14112026" in first.fare.link.url
    assert "BEG-MOW: results truncated: 30 of 45 itineraries" in parsed.notes
    assert "some itineraries are separate tickets with a self-transfer, each booked on its own" in parsed.notes
    assert not any("tell the user" in note or "LINK" in note for note in parsed.notes), "source prose stays out"


def test_empty_and_unknown_payload():
    assert Source().parse([b'{"offers":[]}'], QUERY, NOW).offers == []
    with pytest.raises(ParseError):
        Source().parse([b"{}"], QUERY, NOW)


@pytest.mark.live
async def test_live_search():
    from tests.live import check_source

    offers = await check_source(Source(), QUERY)
    assert offers[0].fare.link.kind == "results"
    assert all(
        segment.departs.tzinfo and segment.arrives.tzinfo for offer in offers for segment in offer.itinerary.segments()
    )


async def test_further_pages_are_read_while_it_has_more():
    pages = iter([
        {"offers": [{"n": 1}], "meta": {"has_more": True, "total_matched": 70}},
        {"offers": [{"n": 2}], "meta": {"has_more": True, "total_matched": 70}},
        {"offers": [{"n": 3}], "meta": {"has_more": False, "total_matched": 70}},
    ])

    class Pages(FakeNet):
        async def request(self, *args, **kwargs):
            if kwargs["json"]["method"] != "tools/call":
                return await super().request(*args, **kwargs)
            self.calls.append(kwargs)
            text = json.dumps(next(pages))
            return Response(200, json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"content": [{"type": "text", "text": text}]}}).encode())

    net = Pages()
    (raw,) = await Source().fetch(QUERY, Context(net, None, lambda: NOW))
    assert [o["n"] for o in json.loads(raw)["offers"]] == [1, 2, 3]
    assert [c["json"]["params"]["arguments"]["page"] for c in net.calls if c["json"].get("method") == "tools/call"] == [1, 2, 3]
