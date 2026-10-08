import json
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

from travelops.core.flights import Baggage, Fare, FlightOffer, FlightQuery, Itinerary, Segment, at_airport
from travelops.core.money import Money
from travelops.search import completed
from travelops.sources.flights import skiplagged
from travelops.sources.flights.skiplagged import Source

FIXTURES = Path(__file__).parents[1] / "fixtures" / "skiplagged"
NOW = datetime(2026, 10, 8, tzinfo=timezone.utc)
ONE_WAY = FlightQuery(("BEG",), ("IST",), date(2026, 11, 14))
ROUND = FlightQuery(("BEG",), ("LIS",), date(2026, 11, 20), date(2026, 11, 24))


def parsed(name, query):
    return Source().parse([(FIXTURES / name).read_bytes()], query, NOW)


def test_a_nonstop_is_whole_and_a_connection_is_a_chain():
    found = parsed("beg-ist-2026-11-14.json", ONE_WAY)
    nonstop = next(o for o in found.offers if o.itinerary.outbound[0].flight == "JU426")
    assert not nonstop.itinerary.partial and nonstop.itinerary.outbound[0].destination == "IST"
    assert (nonstop.fare.price.amount, nonstop.fare.price.currency) == (88, "USD")
    assert nonstop.fare.link.url.endswith("#trip=JU426")
    chain = next(o for o in found.offers if len(o.itinerary.outbound) == 2)
    assert chain.itinerary.partial and chain.itinerary.chain()[0][0] == ("JU1106", "JU1424")
    assert "several tickets" in found.notes[-1]


def test_a_round_trip_names_the_flights_back():
    found = parsed("beg-lis-2026-11-20-24.json", ROUND)
    first = found.offers[0]
    assert [s.flight for s in first.itinerary.inbound] == ["JU563"] and first.itinerary.inbound[0].origin == "LIS"
    assert first.itinerary.partial, "the way out changes planes somewhere unnamed"


def test_hidden_city_fares_are_left_out():
    data = json.loads((FIXTURES / "beg-ist-2026-11-14.json").read_text())
    data["flights"][0]["attributes"] = ["hidden-city", "nonstop"]
    found = Source().parse([json.dumps(data).encode()], ONE_WAY, NOW)
    assert all(o.itinerary.outbound[0].flight != "JU426" for o in found.offers)
    assert any("1 hidden-city fares were left out" in n for n in found.notes)


def test_a_chain_takes_the_stops_of_another_sources_same_flights_or_is_left_out():
    found = parsed("beg-ist-2026-11-14.json", ONE_WAY)
    chain = next(o for o in found.offers if o.itinerary.partial)
    first, last = chain.itinerary.outbound[0], chain.itinerary.outbound[-1]
    stop = at_airport("2026-11-14T10:00:00", "VIE")
    whole = Itinerary(
        (
            Segment(first.carrier, first.flight, "BEG", "VIE", first.departs, stop),
            Segment(last.carrier, last.flight, "VIE", "IST", stop.replace(hour=11), last.arrives),
        )
    )
    other = FlightOffer(
        whole, Fare(Money("100", "EUR"), "kupibilet", "kupibilet", "economy", Baggage(), None, NOW)
    )
    kept, dropped = completed([other, chain, replace(chain, itinerary=replace(chain.itinerary, outbound=(
        replace(first, flight="ZZ1"), last)))])
    assert [o.itinerary for o in kept] == [whole, whole], "completed with the stop at Vienna"
    assert dropped == {"skiplagged": 1}


async def test_one_call_per_route_and_the_shortest_when_there_are_more():
    calls = []

    class Server:
        async def call(self, ctx, tool, arguments):
            calls.append(arguments)
            return {"flights": [], "pagination": {"totalAvailable": 140}}

    source = Source()
    source.server = Server()
    await source.fetch(ONE_WAY, None)
    assert [c["sort"] for c in calls] == ["price", "duration"] and calls[0]["limit"] == skiplagged.AT_MOST
    assert calls[0]["maxStops"] == "one" and "offset" not in calls[1]
