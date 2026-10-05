"""Two-step grouping: first the physically same trip, then a comparable fare (cabin + checked bag). Otherwise
a fare with a 23 kg bag and one with carry-on only would look like a saving, while they are different products."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..core.flights import Fare, FlightOffer, Itinerary
from ..core.money import Rates, UnknownCurrency

UNKNOWN = Decimal("Infinity")


@dataclass
class FareGroup:
    key: tuple
    fares: list[Fare]


@dataclass
class FlightCard:
    itinerary: Itinerary
    groups: list[FareGroup]
    best: Decimal  # cheapest fare in the user's currency


def price_in(rates: Rates, currency: str, raw_comparable: bool = False):
    def value(fare: Fare) -> Decimal:
        try:
            return rates.convert(fare.price, currency).amount
        except UnknownCurrency:
            # Without a rate, amounts still compare with each other when every price is in one currency.
            return fare.price.amount if raw_comparable else UNKNOWN

    return value


def merge_flights(offers: list[FlightOffer], rates: Rates, currency: str) -> list[FlightCard]:
    value = price_in(rates, currency, raw_comparable=len({o.fare.price.currency for o in offers}) == 1)
    trips: dict[tuple, tuple[Itinerary, list[Fare]]] = {}
    for o in offers:
        trips.setdefault(o.itinerary.key(), (o.itinerary, []))[1].append(o.fare)
    cards = []
    for itinerary, fares in trips.values():
        by_key: dict[tuple, dict[str, Fare]] = {}
        for fare in fares:
            sellers = by_key.setdefault(fare.comparable_key(), {})
            if fare.seller not in sellers or value(fare) < value(sellers[fare.seller]):
                sellers[fare.seller] = fare
        groups = [FareGroup(k, sorted(s.values(), key=value)) for k, s in by_key.items()]
        groups.sort(key=lambda g: value(g.fares[0]))
        cards.append(FlightCard(itinerary, groups, value(groups[0].fares[0])))
    cards.sort(key=lambda c: c.best)
    return cards
