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


async def test_round_trips_and_children_are_not_guessed():
    with pytest.raises(NotConfigured, match="one way"):
        await Source().fetch(FlightQuery(("BEG",), ("IST",), date(2026, 11, 14), date(2026, 11, 17)), None)
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
