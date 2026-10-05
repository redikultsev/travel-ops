"""Plain JSON search results shared by the command line and MCP tools."""

from __future__ import annotations

from dataclasses import asdict

from .core.common import Link
from .core.flights import Fare, Segment
from .core.money import Money, Rates, UnknownCurrency
from .core.stays import Rate
from .search import FlightSearch, StaySearch


def money_json(money: Money) -> dict:
    return {"amount": str(money.amount), "currency": money.currency}


def converted_json(money: Money, rates: Rates, currency: str) -> dict | None:
    try:
        return money_json(rates.convert(money, currency))
    except UnknownCurrency:
        return None


def link_json(link: Link | None) -> dict | None:
    return {"url": link.url, "kind": link.kind} if link else None


def segment_json(segment: Segment) -> dict:
    return {
        "carrier": segment.carrier,
        "flight": segment.flight,
        "origin": segment.origin,
        "destination": segment.destination,
        "departs": segment.departs.isoformat(),
        "arrives": segment.arrives.isoformat(),
        "operating": segment.operating,
    }


def fare_json(fare: Fare, rates: Rates, currency: str) -> dict:
    return {
        "price": money_json(fare.price),
        "converted": converted_json(fare.price, rates, currency),
        "seller": fare.seller,
        "source": fare.source,
        "cabin": fare.cabin,
        "baggage": {
            "checked": fare.baggage.checked,
            "checked_kg": fare.baggage.checked_kg,
            "carry_on": fare.baggage.carry_on,
        },
        "link": link_json(fare.link),
        "seen_at": fare.seen_at.isoformat(),
        "refundable": fare.refundable,
        "fare_name": fare.fare_name,
    }


def flight_search_json(search: FlightSearch, rates: Rates) -> dict:
    query = search.query
    cards = []
    for card in search.cards:
        itinerary = card.itinerary
        route = []
        for segment in itinerary.segments():
            if not route or route[-1] != segment.origin:
                route.append(segment.origin)
            route.append(segment.destination)
        cards.append(
            {
                "outbound": [segment_json(s) for s in itinerary.outbound],
                "inbound": [segment_json(s) for s in itinerary.inbound],
                "route": route,
                "stops": itinerary.stops(),
                "airport_changes": [list(pair) for pair in itinerary.airport_changes()],
                "duration_min": int(itinerary.duration().total_seconds() / 60),
                "return_duration_min": int(itinerary.duration(inbound=True).total_seconds() / 60)
                if itinerary.inbound
                else None,
                "groups": [
                    {"key": list(group.key), "fares": [fare_json(f, rates, search.currency) for f in group.fares]}
                    for group in card.groups
                ],
            }
        )
    return {
        "query": {
            "origins": list(query.origins),
            "destinations": list(query.destinations),
            "depart": query.depart.isoformat(),
            "return": query.return_.isoformat() if query.return_ else None,
            "flex_days": query.flex_days,
            "adults": query.adults,
            "children": query.children,
            "infants": query.infants,
            "cabin": query.cabin,
        },
        "currency": search.currency,
        "rates_day": rates.day,
        "cards": cards,
        "sources": [asdict(report) for report in search.reports],
    }


def rate_json(rate: Rate, nights: int, rates: Rates, currency: str) -> dict:
    per_night = rate.per_night(nights)
    return {
        "total": money_json(rate.total),
        "converted": converted_json(rate.total, rates, currency),
        "per_night": money_json(per_night),
        "per_night_converted": converted_json(per_night, rates, currency),
        "seller": rate.seller,
        "source": rate.source,
        "link": link_json(rate.link),
        "seen_at": rate.seen_at.isoformat(),
        "free_cancel_until": rate.free_cancel_until.isoformat() if rate.free_cancel_until else None,
        "meals": rate.meals,
        "room": rate.room,
    }


def stay_search_json(search: StaySearch, rates: Rates) -> dict:
    query = search.query
    return {
        "query": {
            "place": query.place,
            "checkin": query.checkin.isoformat(),
            "checkout": query.checkout.isoformat(),
            "adults": query.adults,
            "children": query.children,
            "rooms": query.rooms,
        },
        "currency": search.currency,
        "rates_day": rates.day,
        "cards": [
            {
                "stay": {
                    "source": card.stay.source,
                    "source_id": card.stay.source_id,
                    "name": card.stay.name,
                    "kind": card.stay.kind,
                    "lat": card.stay.lat,
                    "lon": card.stay.lon,
                    "rating": card.stay.rating,
                    "reviews": card.stay.reviews,
                    "photos": list(card.stay.photos),
                    "amenities": list(card.stay.amenities),
                },
                "nights": query.nights,
                "rates": [rate_json(rate, query.nights, rates, search.currency) for rate in card.rates],
            }
            for card in search.cards
        ],
        "sources": [asdict(report) for report in search.reports],
    }


def shortlist(result: dict, cards: int, offers: int = 5) -> dict:
    """Cut a result to its cheapest cards and sellers, saying how much was left out. Cards arrive sorted."""
    total = len(result["cards"])
    result["cards"] = result["cards"][:cards]
    for card in result["cards"]:
        for holder in card.get("groups", [card]):
            key = "fares" if "fares" in holder else "rates"
            holder[f"{key}_total"] = len(holder[key])
            holder[key] = holder[key][:offers]
    result["shown"] = {"cards": len(result["cards"]), "of": total, "offers_per_card_at_most": offers}
    return result
