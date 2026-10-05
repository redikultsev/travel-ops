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
    assert len(cut["cards"]) == 10 and cut["shown"] == {"cards": 10, "of": 30, "offers_per_group_at_most": 5}
    assert cut["cards"][0]["groups"][0]["fares"] == [0, 1, 2, 3, 4] and cut["cards"][0]["groups"][0]["fares_total"] == 8
    stays = shortlist({"cards": [{"rates": [1, 2, 3]}]}, 10, 2)
    assert stays["cards"][0]["rates"] == [1, 2] and stays["cards"][0]["rates_total"] == 3 and stays["shown"]["of"] == 1


def _round_trip(out, back, price):
    def leg(flight, departs):
        return [{"flight": flight, "origin": "AAA", "destination": "BBB", "departs": departs, "arrives": departs}]

    fare = {
        "price": {"amount": str(price), "currency": "EUR"},
        "converted": None,
        "seller": "s",
        "link": None,
        "seen_at": "now",
    }
    return {"outbound": leg(*out), "inbound": leg(*back), "groups": [{"fares": [fare]}]}


def test_round_trip_shortlist_prefers_flights_not_shown_yet():
    from travelops.serialize import leg_options, shortlist

    morning, evening, late = ("A1", "T08"), ("A2", "T18"), ("A3", "T22")
    early_back, late_back = ("B1", "T07"), ("B2", "T20")
    cards = [
        _round_trip(morning, early_back, 100),
        _round_trip(evening, early_back, 101),
        _round_trip(late, early_back, 102),
        _round_trip(morning, late_back, 130),
    ]
    result = leg_options({"cards": list(cards)})
    assert [o["flights"] for o in result["outbound_options"]] == [["A1"], ["A2"], ["A3"]]
    assert [(o["flights"], o["cheapest_round_trip"]["price"]["amount"]) for o in result["return_options"]] == [
        (["B1"], "100"),
        (["B2"], "130"),
    ]
    cut = shortlist(result, 3)
    shown = [(c["outbound"][0]["flight"], c["inbound"][0]["flight"]) for c in cut["cards"]]
    assert ("A1", "B2") not in shown and len(shown) == 3, "all three bring a new outbound; nothing to prefer"
    two = shortlist(leg_options({"cards": [cards[0], _round_trip(morning, ("B9", "T09"), 100.5), cards[3]]}), 2)
    assert len(two["cards"]) == 2


def test_stay_photos_are_capped_with_a_count():
    from travelops.serialize import shortlist

    cut = shortlist({"cards": [{"stay": {"source": "airbnb", "photos": list("abcdefg")}, "rates": [1]}]}, 5)
    assert cut["cards"][0]["stay"]["photos"] == ["a", "b", "c"] and cut["cards"][0]["stay"]["photos_total"] == 7


def test_stay_shortlist_counts_cards_by_source_after_filters():
    from travelops.views import stays_view

    cards = [
        {"stay": {"source": src, "rating": r, "photos": []}, "rates": [1]}
        for src, r in (("booking", 9), ("booking", 5), ("airbnb", 8.5), ("airbnb", None))
    ]
    cut = stays_view({"cards": cards}, limit=1, min_rating=8.0)
    assert cut["shown"]["of"] == 2 and cut["shown"]["of_by_source"] == {"booking": 1, "airbnb": 1}
    assert cut["shown"]["of_counts"] == "cards left after `filtered`"
