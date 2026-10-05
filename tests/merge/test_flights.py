from datetime import datetime, timezone

from travelops.core.flights import Baggage, Fare, FlightOffer, Itinerary, Segment, at_airport
from travelops.core.money import Money, Rates
from travelops.merge.flights import merge_flights

NOW = datetime(2026, 10, 4, tzinfo=timezone.utc)
RATES = Rates("EUR", {"RUB": "100"}, day="d")


def trip(flight="JU130"):
    return Itinerary(
        (
            Segment(
                "JU", flight, "BEG", "SVO", at_airport("2026-11-15T10:00", "BEG"), at_airport("2026-11-15T14:00", "SVO")
            ),
        )
    )


def offer(price, cur, seller, checked=1, flight="JU130"):
    return FlightOffer(
        trip(flight),
        Fare(Money(price, cur), seller, seller.split(":")[0], "economy", Baggage(checked=checked), None, NOW),
    )


def test_same_flight_from_two_sellers_is_one_card_sorted_by_converted_price():
    cards = merge_flights([offer(30000, "RUB", "kupibilet"), offer(290, "EUR", "onetwotrip")], RATES, "EUR")
    assert len(cards) == 1
    assert [f.seller for f in cards[0].groups[0].fares] == ["onetwotrip", "kupibilet"]


def test_bag_and_no_bag_are_separate_groups():
    cards = merge_flights([offer(300, "EUR", "a", checked=1), offer(200, "EUR", "b", checked=0)], RATES, "EUR")
    assert len(cards[0].groups) == 2


def test_cards_sorted_by_best_price_and_seller_dedup():
    cards = merge_flights(
        [offer(500, "EUR", "a", flight="JU138"), offer(300, "EUR", "a"), offer(320, "EUR", "a")], RATES, "EUR"
    )
    assert [c.itinerary.outbound[0].flight for c in cards] == ["JU130", "JU138"]
    assert len(cards[0].groups[0].fares) == 1, "one seller keeps its cheapest fare in a group"


def test_unknown_currency_sorts_last_but_stays_visible():
    cards = merge_flights([offer(1, "XYZ", "a"), offer(400, "EUR", "b")], RATES, "EUR")
    assert [f.seller for f in cards[0].groups[0].fares] == ["b", "a"]


def test_without_rates_one_currency_still_sorts_by_amount():
    cards = merge_flights([offer(30000, "RUB", "a"), offer(29000, "RUB", "b")], Rates("EUR", {}, "unavailable"), "EUR")
    assert [f.seller for f in cards[0].groups[0].fares] == ["b", "a"]
