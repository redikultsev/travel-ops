"""Kiwi.com through its public MCP server (https://mcp.kiwi.com): no key, no session, one call per route.

Kiwi sells in EUR and outside Russia, and it joins flights of airlines that do not sell together ("virtual
interlining"): such a connection is several tickets under Kiwi's own guarantee, not one through ticket."""

from __future__ import annotations

import json
from datetime import datetime
from itertools import product

from ...core.common import Link
from ...core.flights import (
    Baggage,
    Fare,
    FlightOffer,
    FlightQuery,
    Itinerary,
    Segment,
    at_airport,
    cabin,
    flight_number,
)
from ...core.money import Money
from .._mcp import Server
from ..base import Context, Parsed, ParseError

URL = "https://mcp.kiwi.com"
CLASSES = {"economy": "M", "premium_economy": "W", "business": "C", "first": "F"}
AT_MOST = 15  # what one answer holds


def each(total, seats: int) -> int | None:
    """A party's total as a count per traveller, when it divides evenly; otherwise not known per traveller."""
    if total is None or seats < 1 or total % seats:
        return None
    return total // seats


def cabin_bag(total, seats: int) -> bool | None:
    if total is None:
        return None
    return True if total >= seats else (False if total == 0 else None)


class Source:
    name = "kiwi"

    def __init__(self) -> None:
        self.server = Server(self.name, URL, handshake=False)  # Kiwi keeps no session

    def max_requests(self, query: FlightQuery) -> int:
        return len(query.origins) * len(query.destinations)

    async def fetch(self, query: FlightQuery, ctx: Context) -> list[bytes]:
        if query.cabin not in CLASSES:
            raise ValueError(f"unsupported cabin: {query.cabin}")
        raws = []
        for origin, destination in product(query.origins, query.destinations):
            arguments = {
                "flyFrom": origin,
                "flyTo": destination,
                "departureDate": query.depart.strftime("%d/%m/%Y"),
                "adults": query.adults,
                "children": query.children,
                "infants": query.infants,
                "cabinClass": CLASSES[query.cabin],
                "currency": "EUR",
                "locale": "en",
                "sort": "price",
                # An answer holds fifteen itineraries: without this, chains of three low-cost flights fill it.
                "max_sector_stopovers": 1,
            }
            if query.return_:
                arguments["returnDate"] = query.return_.strftime("%d/%m/%Y")
            payload = await self.server.call(ctx, "search-flight", arguments)
            if not isinstance(payload, dict):
                raise ParseError("kiwi answered without a search result")
            raws.append(json.dumps(payload).encode())
        return raws

    def parse(self, raws: list[bytes], query: FlightQuery, seen_at: datetime) -> Parsed:
        offers, notes = (
            [],
            ["at most one stop each way was asked", "the price is for the whole party, as Kiwi states it"],
        )
        joined = False
        for raw in raws:
            try:
                payload = json.loads(raw)
                found = payload["itineraries"]
                if not isinstance(found, list):
                    raise ValueError("itineraries must be a list")
                if payload.get("error"):
                    raise ValueError(f"Kiwi reported: {str(payload['error'])[:120]}")
                asked = payload.get("passengers") or {}
                if found and (asked.get("adults"), asked.get("children", 0), asked.get("infants", 0)) != (
                    query.adults,
                    query.children,
                    query.infants,
                ):
                    raise ValueError("answer is for another party than was asked")
                currency = payload.get("currency") or "EUR"
                if len(found) >= AT_MOST:
                    notes.append(f"results truncated: the {AT_MOST} cheapest itineraries of a route, total unknown")
                for item in found:
                    legs = []
                    for name in ("outbound", "inbound"):
                        leg = item.get(name)
                        if not leg:
                            continue
                        chain = []
                        for part in leg["segments"]:
                            carrier = part["carrier"].upper()
                            chain.append(
                                Segment(
                                    carrier,
                                    flight_number(carrier, part["flightNumber"]),
                                    part["from"],
                                    part["to"],
                                    at_airport(part["departureTime"], part["from"]),
                                    at_airport(part["arrivalTime"], part["to"]),
                                )
                            )
                        if not chain:
                            raise ValueError("empty leg")
                        joined = joined or len({segment.carrier for segment in chain}) > 1
                        legs.append((tuple(chain), leg.get("cabinClass")))
                    if not legs or bool(query.return_) != (len(legs) == 2):
                        raise ValueError("legs do not match the asked trip")
                    # Kiwi counts bags for the whole party; the model counts them per traveller with a seat.
                    bags, seats = item.get("baggage") or {}, query.adults + query.children
                    classes = {cabin(value) for _, value in legs}
                    offers.append(
                        FlightOffer(
                            Itinerary(legs[0][0], legs[1][0] if len(legs) == 2 else ()),
                            Fare(
                                Money(str(item["price"]), currency),
                                self.name,
                                self.name,
                                next(iter(classes)) if len(classes) == 1 else None,
                                Baggage(
                                    each(bags.get("checkedBag"), seats),
                                    None,
                                    cabin_bag(bags.get("cabinBag"), seats),
                                ),
                                Link(item["bookingUrl"], "ticket")
                                if str(item.get("bookingUrl", "")).startswith("https://")
                                else None,
                                seen_at,
                            ),
                        )
                    )
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                raise ParseError(f"invalid Kiwi result: {exc}") from exc
        if joined:
            notes.append(
                "some connections join airlines that do not sell together: several tickets under Kiwi's own guarantee"
            )
        return Parsed(list(dict.fromkeys(offers)), list(dict.fromkeys(notes)))
