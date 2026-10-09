import json
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from travelops.core.flights import FlightQuery
from travelops.net.browser import BrowserSessions
from travelops.net.client import Blocked
from travelops.sources.base import Context, NotConfigured
from travelops.sources.flights import tripcom
from travelops.sources.flights.tripcom import Source

FIXTURE = Path(__file__).parents[1] / "fixtures" / "tripcom" / "beg-ist-2026-11-14.json"
NOW = datetime(2026, 10, 7, tzinfo=timezone.utc)
QUERY = FlightQuery(("BEG",), ("IST",), date(2026, 11, 14))


def test_the_list_is_read_into_one_way_flights_priced_for_the_party():
    pair = FlightQuery(("BEG",), ("IST",), date(2026, 11, 14), adults=2)
    parsed = Source().parse([FIXTURE.read_bytes()], pair, NOW)
    assert len(parsed.offers) == 25
    cheapest = min(parsed.offers, key=lambda o: o.fare.price.amount)
    assert (str(cheapest.fare.price.amount), cheapest.fare.price.currency) == ("165.28", "EUR"), "82.64 a ticket"
    assert cheapest.itinerary.outbound[0].flight == "JU426" and cheapest.itinerary.inbound == ()
    assert cheapest.itinerary.outbound[0].departs.isoformat() == "2026-11-14T00:50:00+01:00"
    assert cheapest.fare.cabin == "economy" and cheapest.fare.baggage.carry_on is True
    assert cheapest.fare.link.url.startswith(tripcom.PAGE) and "triptype=ow" in cheapest.fare.link.url
    assert any(len(o.itinerary.outbound) == 2 for o in parsed.offers), "connections are kept"
    bagged = [o.fare.baggage for o in parsed.offers if o.fare.baggage.checked]
    assert bagged and all(b.checked_kg == 23 for b in bagged if b.checked_kg), "the tag says how heavy"
    assert any(b.checked_kg == 23 for b in bagged)
    assert cheapest.fare.baggage.checked is None and cheapest.fare.baggage.checked_kg is None


async def test_the_page_makes_the_search_and_only_its_list_is_kept():
    stream = (
        b"data:"
        + json.dumps(
            {
                "ResponseStatus": {"Extension": [{"Id": "IP", "Value": "10.0.0.1"}]},
                "head": {"transactionID": "t"},
                "basicInfo": {"currency": "EUR", "recordCount": 0},
                "itineraryList": [],
            }
        ).encode()
    )
    asked = []

    class Browser:
        async def search(self, source, url, pattern):
            asked.append((source, url, pattern))
            return [b"not json", stream]

    (raw,) = await Source().fetch(QUERY, Context(None, Browser(), lambda: NOW))
    assert asked == [("tripcom", tripcom.page_url(QUERY, "BEG", "IST"), tripcom.LIST)]
    assert set(json.loads(raw)) == {"route", "link", "currency", "count", "itineraries"}


def test_a_round_trip_fare_names_its_return_flights_and_their_days():
    from travelops.sources.flights.tripcom import way_back

    raw = (Path(__file__).parents[1] / "fixtures/tripcom/beg-ist-2026-11-14-18-rt.json").read_bytes()
    query = FlightQuery(("BEG",), ("IST",), date(2026, 11, 14), date(2026, 11, 18))
    parsed = Source().parse([raw], query, datetime(2026, 10, 8, tzinfo=timezone.utc))
    direct, via_nis, via_warsaw = parsed.offers
    assert [s.flight for s in direct.itinerary.inbound] == ["JU423"] and direct.itinerary.partial
    assert direct.itinerary.chain()[1] == (("JU423",), "2026-11-18"), "four days after the way out"
    assert [s.flight for s in via_nis.itinerary.outbound] == ["JU1106", "JU1424"]
    assert [(s.flight, s.origin, s.departs.date().isoformat()) for s in via_warsaw.itinerary.inbound] == [
        ("LO134", "IST", "2026-11-18"),
        ("LO571", "WAW", "2026-11-19"),
    ], "the night in Warsaw"
    assert way_back("no tail") == []


async def test_children_are_not_guessed():
    with pytest.raises(NotConfigured, match="children"):
        await Source().fetch(FlightQuery(("BEG",), ("IST",), date(2026, 11, 14), children=1), None)


async def test_a_page_search_is_spaced_counted_and_rests_the_source_when_refused(tmp_path):
    events = []

    class Limiter:
        async def acquire(self, bucket):
            events.append(("acquire", bucket))

        def succeeded(self, bucket):
            events.append(("ok", bucket))

        def blocked(self, bucket):
            events.append(("blocked", bucket))

    async def found(engine, url, pattern, proxy, headless):
        return [b"list"]

    async def refused(engine, url, pattern, proxy, headless):
        raise Blocked("the page did not search")

    counts = Counter()
    browser = BrowserSessions(tmp_path, exit_="", proxy=None, capturer=found, limiter=Limiter(), counts=counts)
    assert await browser.search("tripcom", "https://trip.example", "List") == [b"list"]
    browser.capturer = refused
    with pytest.raises(Blocked):
        await browser.search("tripcom", "https://trip.example", "List")
    assert events == [("acquire", "tripcom"), ("ok", "tripcom"), ("acquire", "tripcom"), ("blocked", "tripcom")]
    assert counts["tripcom"] == 2, "every page opened is a request of the source"
