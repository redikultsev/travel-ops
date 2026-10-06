import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from travelops.core.flights import FlightQuery
from travelops.core.money import Money
from travelops.net.client import Response
from travelops.sources.base import Context, ParseError, SourceFault
from travelops.sources.flights.kiwi import Source, payload_of

NOW = datetime(2026, 10, 6, tzinfo=timezone.utc)
FIXTURES = Path(__file__).parents[1] / "fixtures/kiwi"
ROUND = FlightQuery(("BEG",), ("TGD",), date(2026, 10, 22), date(2026, 10, 23))
ONE_WAY = FlightQuery(("BEG",), ("TIV",), date(2026, 10, 22))


def stream(payload):
    answer = {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": json.dumps(payload)}]}}
    return b"event: message\r\ndata: " + json.dumps(answer).encode() + b"\r\n\r\n"


async def test_one_call_per_route_with_no_handshake():
    class Net:
        calls = []

        async def request(self, source, method, url, **kw):
            self.calls.append(kw["json"])
            return Response(200, stream(json.loads((FIXTURES / "beg-tgd-round-trip-2026-10-22.json").read_text())))

    query = FlightQuery(("BEG",), ("TGD", "TIV"), date(2026, 10, 22), date(2026, 10, 23), adults=2, children=1)
    raws = await Source().fetch(query, Context(Net(), None, lambda: NOW))
    assert len(raws) == len(Net.calls) == 2 == Source().max_requests(query)
    sent = Net.calls[1]["params"]
    assert sent["name"] == "search-flight" and sent["arguments"] == {
        "flyFrom": "BEG",
        "flyTo": "TIV",
        "departureDate": "22/10/2026",
        "returnDate": "23/10/2026",
        "adults": 2,
        "children": 1,
        "infants": 0,
        "cabinClass": "M",
        "currency": "EUR",
        "locale": "en",
        "sort": "price",
        "max_sector_stopovers": 1,
    }
    assert all(call["method"] == "tools/call" for call in Net.calls)


async def test_a_server_fault_is_not_a_block_and_not_our_bug():
    class Net:
        async def request(self, *args, **kw):
            return Response(503, b"down")

    with pytest.raises(SourceFault, match="503"):
        await Source().fetch(ROUND, Context(Net(), None, lambda: NOW))


def test_recorded_round_trip_is_in_euros_with_a_ticket_link():
    parsed = Source().parse([(FIXTURES / "beg-tgd-round-trip-2026-10-22.json").read_bytes()], ROUND, NOW)
    assert len(parsed.offers) == 4
    first = parsed.offers[0]
    assert first.fare.price == Money(110, "EUR") and first.fare.seller == "kiwi" and first.fare.cabin == "economy"
    assert [s.flight for s in first.itinerary.outbound] == ["4O103"]
    assert [s.flight for s in first.itinerary.inbound] == ["JU665"]
    assert first.itinerary.outbound[0].departs.isoformat() == "2026-10-22T19:55:00+02:00"
    assert first.fare.link.kind == "ticket" and first.fare.link.url.startswith("https://kiwi.com/u/")
    assert first.fare.baggage.checked == 0 and first.fare.baggage.carry_on is True
    assert "the price is for the whole party, as Kiwi states it" in parsed.notes
    assert not any("truncated" in note for note in parsed.notes)


def test_a_connection_of_two_airlines_is_named_as_several_tickets():
    parsed = Source().parse([(FIXTURES / "beg-tiv-one-way-2026-10-22.json").read_bytes()], ONE_WAY, NOW)
    assert [s.flight for s in parsed.offers[0].itinerary.outbound] == ["JU426", "4O401"]
    assert parsed.offers[0].itinerary.inbound == ()
    assert any("several tickets under Kiwi's own guarantee" in note for note in parsed.notes)


def test_answers_that_do_not_fit_the_question_are_refused():
    payload = json.loads((FIXTURES / "beg-tgd-round-trip-2026-10-22.json").read_text())
    with pytest.raises(ParseError, match="another party"):
        Source().parse(
            [json.dumps(payload).encode()], FlightQuery(("BEG",), ("TGD",), ROUND.depart, ROUND.return_, adults=2), NOW
        )
    with pytest.raises(ParseError, match="legs do not match"):
        Source().parse([json.dumps(payload).encode()], FlightQuery(("BEG",), ("TGD",), ROUND.depart), NOW)
    full = dict(payload, itineraries=payload["itineraries"] * 4)
    assert any("truncated: the 15 cheapest" in n for n in Source().parse([json.dumps(full).encode()], ROUND, NOW).notes)
    with pytest.raises(ParseError):
        payload_of(b'{"jsonrpc":"2.0","id":1,"error":{"message":"bad"}}', "Kiwi")
    assert Source().parse([b'{"itineraries": []}'], ROUND, NOW).offers == []
