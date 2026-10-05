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
                    {
                        # Fares in one group are the same product: same cabin, same answer to "is a checked bag
                        # included" (true, false, or null when the source did not say).
                        "cabin": group.key[0],
                        "checked_bag": group.key[1],
                        "fares": [fare_json(f, rates, search.currency) for f in group.fares],
                    }
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


def _leg_key(segments: list[dict]) -> tuple:
    return tuple((s["flight"], s["departs"]) for s in segments)


def _amount(offer: dict) -> float:
    money = offer.get("converted") or offer.get("price") or offer.get("total")
    return float(money["amount"])


def _varied(cards: list[dict], count: int) -> list[dict]:
    """The cheapest cards, but each must bring a flight not shown yet: five round trips that differ only in one
    leg are one choice, not five. Cards arrive cheapest first; what is left over fills the remaining places."""
    if not cards or "inbound" not in cards[0]:
        return cards[:count]
    picked, rest, seen_out, seen_back = [], [], set(), set()
    for card in cards:
        out, back = _leg_key(card["outbound"]), _leg_key(card["inbound"])
        if len(picked) < count and (out not in seen_out or (back and back not in seen_back)):
            picked.append(card)
            seen_out.add(out)
            seen_back.add(back)
        else:
            rest.append(card)
    return sorted(picked + rest[: count - len(picked)], key=lambda c: _amount(c["groups"][0]["fares"][0]))


def leg_options(result: dict, limit: int = 8) -> dict:
    """For a round trip: every outbound and every return on offer, each with the cheapest round trip it is part
    of. The shortlist shows pairs; this shows what the pairs are made of. Call it before `shortlist`."""
    if not any(card["inbound"] for card in result["cards"]):
        return result
    for name, legs in (("outbound_options", "outbound"), ("return_options", "inbound")):
        best: dict[tuple, dict] = {}
        for card in result["cards"]:
            fare = card["groups"][0]["fares"][0]
            key = _leg_key(card[legs])
            if key and (key not in best or _amount(fare) < _amount(best[key]["cheapest_round_trip"])):
                segments = card[legs]
                best[key] = {
                    "flights": [s["flight"] for s in segments],
                    "origin": segments[0]["origin"],
                    "destination": segments[-1]["destination"],
                    "departs": segments[0]["departs"],
                    "arrives": segments[-1]["arrives"],
                    "stops": len(segments) - 1,
                    "cheapest_round_trip": {k: fare[k] for k in ("price", "converted", "seller", "link", "seen_at")},
                }
        options = sorted(best.values(), key=lambda o: _amount(o["cheapest_round_trip"]))[:limit]
        result[name] = sorted(options, key=lambda o: o["departs"])
        result[f"{name}_total"] = len(best)
    return result


def shortlist(result: dict, cards: int, offers: int = 5, photos: int = 3, varied: bool = True) -> dict:
    """Cut a result to its first cards and sellers, saying how much was left out. Cards arrive sorted; `varied`
    is for an order by price, where near-copies of one round trip would otherwise fill the list."""
    total = len(result["cards"])
    by_source: dict[str, int] = {}
    for card in result["cards"]:
        if "stay" in card:
            by_source[card["stay"]["source"]] = by_source.get(card["stay"]["source"], 0) + 1
    result["cards"] = _varied(result["cards"], cards) if varied else result["cards"][:cards]
    for card in result["cards"]:
        for holder in card.get("groups", [card]):
            key = "fares" if "fares" in holder else "rates"
            holder[f"{key}_total"] = len(holder[key])
            holder[key] = holder[key][:offers]
        if "stay" in card:
            card["stay"]["photos_total"] = len(card["stay"]["photos"])
            card["stay"]["photos"] = card["stay"]["photos"][:photos]
    result["shown"] = {"cards": len(result["cards"]), "of": total, "offers_per_group_at_most": offers}
    if "filtered" in result:
        result["shown"]["of_counts"] = "cards left after `filtered`"
    if by_source:
        result["shown"]["of_by_source"] = by_source
    return result
