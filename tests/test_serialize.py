import json
from datetime import date, datetime, timezone

from travelops.core.flights import Baggage, Fare, FlightOffer, FlightQuery, Itinerary, Segment, at_airport
from travelops.core.money import Money, Rates
from travelops.core.common import Link
from travelops.core.report import SourceReport, Status
from travelops.merge.flights import merge_flights
from travelops.search import FlightSearch
from travelops.serialize import flight_search_json

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)


def test_flight_card_json_is_plain_and_complete():
    seg = Segment(
        "JU", "JU130", "BEG", "SVO", at_airport("2026-11-15T10:00", "BEG"), at_airport("2026-11-15T14:00", "SVO")
    )
    fare = Fare(
        Money(30000, "RUB"), "kupibilet", "kupibilet", "economy", Baggage(checked=1), Link("https://k", "results"), NOW
    )
    rates = Rates("EUR", {"RUB": "100"}, "d")
    search = FlightSearch(
        FlightQuery(("BEG",), ("SVO",), date(2026, 11, 15)),
        "EUR",
        merge_flights([FlightOffer(Itinerary((seg,)), fare)], rates, "EUR"),
        [SourceReport("kupibilet", Status.OK, offers=1)],
    )
    data = flight_search_json(search, rates)
    json.dumps(data)  # must be plain JSON
    card = data["cards"][0]
    assert card["route"] == ["BEG", "SVO"] and card["stops"] == 0 and card["duration_min"] == 120
    first = card["groups"][0]["fares"][0]
    assert first["price"] == {"amount": "30000", "currency": "RUB"}
    assert first["converted"] == {"amount": "300.00", "currency": "EUR"}
    assert first["link"] == {"url": "https://k", "kind": "results"} and first["seen_at"] == "2026-10-04T12:00:00+00:00"
    assert data["sources"] == [
        {"source": "kupibilet", "status": "ok", "reason": "", "notes": [], "offers": 1, "requests": 0, "seconds": 0.0}
    ]


def test_shortlist_says_what_it_left_out():
    from travelops.serialize import shortlist

    flights = {"cards": [{"groups": [{"fares": list(range(8))}]} for _ in range(30)]}
    cut = shortlist(flights, 10, 5)
    assert len(cut["cards"]) == 10 and cut["shown"] == {"cards": 10, "of": 30, "offers_per_card_at_most": 5}
    assert cut["cards"][0]["groups"][0]["fares"] == [0, 1, 2, 3, 4] and cut["cards"][0]["groups"][0]["fares_total"] == 8
    stays = shortlist({"cards": [{"rates": [1, 2, 3]}]}, 10, 2)
    assert stays["cards"][0]["rates"] == [1, 2] and stays["cards"][0]["rates_total"] == 3 and stays["shown"]["of"] == 1
