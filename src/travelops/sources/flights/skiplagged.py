"""Skiplagged through its public MCP server (https://mcp.skiplagged.com/mcp): no key, no session.

An answer holds up to 100 itineraries with paging. A card names each leg's flights (in its id: `trip=JU1106-JU1424`
out, after a comma the way back) and where and when the leg starts and ends, but not where a connection changes
planes: a nonstop leg is whole, a connection is a chain that another source's itinerary of the same flights
completes (see `search.completed`). "Hidden city" fares, which end at a stop before the ticketed destination, are
left out: such a ticket forbids a checked bag and breaks if a flight is missed. Skiplagged also joins airlines that
do not sell together ("virtual interlining"): several tickets, booked on its site."""

from __future__ import annotations

import json
import re
from datetime import datetime
from itertools import product

from ...core.common import Link
from ...core.flights import Baggage, Fare, FlightOffer, FlightQuery, Itinerary, Segment, at_airport, flight_number
from ...core.money import Money
from .._mcp import Server
from ..base import Context, Parsed, ParseError

URL = "https://mcp.skiplagged.com/mcp"
TOOL = "sk_flights_search"
CLASSES = {"economy": "economy", "premium_economy": "premium", "business": "business", "first": "first"}
AT_MOST = 100  # what one answer holds; `limit` plus `offset` may not pass 100, so there are no further pages
FLIGHT = re.compile(r"^([A-Z0-9]{2})(\d{1,4}[A-Z]?)$")


def legs_of(card_id: str) -> list[list[str]]:
    """The flight numbers of each leg, from a card's id: `BEG-LIS-2026-11-20-trip=W64123-FR820,JU563`."""
    if "trip=" not in card_id:
        raise ValueError(f"card id without flights: {card_id}")
    return [leg.split("-") for leg in card_id.split("trip=", 1)[1].split(",")]


def leg(numbers: list[str], start: dict, end: dict) -> tuple[tuple[Segment, ...], bool]:
    """A leg's segments and whether they are whole. A connection's stops are not known: its middle is left blank
    and the itinerary is marked partial, to be completed or left out before anyone sees it."""
    departs = at_airport(start["dateTime"], start["airport"])
    arrives = at_airport(end["dateTime"], end["airport"])
    flights = []
    for number in numbers:
        match = FLIGHT.match(number)
        if not match:
            raise ValueError(f"not a flight number: {number}")
        flights.append((match[1], flight_number(match[1], match[2])))
    if len(flights) == 1:
        ((carrier, flight),) = flights
        return (Segment(carrier, flight, start["airport"], end["airport"], departs, arrives),), True
    chain = []
    for index, (carrier, flight) in enumerate(flights):
        first, last = index == 0, index == len(flights) - 1
        chain.append(
            Segment(
                carrier,
                flight,
                start["airport"] if first else "",
                end["airport"] if last else "",
                departs if first else arrives,
                arrives if last else departs,
            )
        )
    return tuple(chain), False


class Source:
    name = "skiplagged"

    def __init__(self) -> None:
        self.server = Server(self.name, URL, handshake=False)

    def max_requests(self, query: FlightQuery) -> int:
        return len(query.origins) * len(query.destinations) * 2

    def typical_requests(self, query: FlightQuery) -> int:
        return self.max_requests(query)

    async def fetch(self, query: FlightQuery, ctx: Context) -> list[bytes]:
        if query.cabin not in CLASSES:
            raise ValueError(f"unsupported cabin: {query.cabin}")
        raws = []
        for origin, destination in product(query.origins, query.destinations):
            arguments = {
                "origin": origin,
                "destination": destination,
                "departureDate": query.depart.isoformat(),
                "adults": query.adults,
                "children": query.children,
                "infantsLap": query.infants,
                "fareClass": CLASSES[query.cabin],
                "sort": "price",
                "limit": AT_MOST,
                # Up to one stop, as the other sources are asked: chains of three low-cost flights fill an answer.
                # `includeHiddenCity: false` is not sent: on 2026-10-08 it emptied a route that had 300 fares.
                "maxStops": "one",
            }
            if query.return_:
                arguments["returnDate"] = query.return_.isoformat()
            payload = await self.server.call(ctx, TOOL, arguments)
            if not isinstance(payload, dict):
                raise ParseError("Skiplagged answered without a search result")
            raws.append(json.dumps(payload).encode())
            # No pages: where Skiplagged counts more than one answer holds, the shortest are asked for as well, which
            # reaches past the cheapest hundred.
            if int((payload.get("pagination") or {}).get("totalAvailable") or 0) > AT_MOST:
                more = await self.server.call(ctx, TOOL, dict(arguments, sort="duration"))
                if isinstance(more, dict):
                    raws.append(json.dumps(more).encode())
        return raws

    def parse(self, raws: list[bytes], query: FlightQuery, seen_at: datetime) -> Parsed:
        offers, hidden, joined, total = {}, 0, False, 0
        try:
            for raw in raws:
                payload = json.loads(raw)
                cards = payload["flights"]
                if not isinstance(cards, list):
                    raise ValueError("flights must be a list")
                total = max(total, int((payload.get("pagination") or {}).get("totalAvailable") or 0))
                for card in cards:
                    attributes = set(card.get("attributes") or [])
                    if card.get("returnFlight"):
                        attributes |= set(card["returnFlight"].get("attributes") or [])
                    if any("hidden" in a for a in attributes):
                        hidden += 1
                        continue
                    joined = joined or "virtual-interline" in attributes
                    numbers = legs_of(card["id"])
                    asked_back = bool(query.return_)
                    if asked_back != (len(numbers) == 2) or asked_back != bool(card.get("returnFlight")):
                        raise ValueError("legs do not match the asked trip")
                    out, whole_out = leg(numbers[0], card["departure"], card["arrival"])
                    back, whole_back = (), True
                    if query.return_:
                        ret = card["returnFlight"]
                        back, whole_back = leg(numbers[1], ret["departure"], ret["arrival"])
                    itinerary = Itinerary(out, back, partial=not (whole_out and whole_back))
                    price = card["price"]
                    fare = Fare(
                        Money(str(price["amount"]), price["currency"]),
                        self.name,
                        self.name,
                        query.cabin,
                        Baggage(),
                        Link(card["deepLink"], "results") if str(card.get("deepLink", "")).startswith("https://")
                        else None,
                        seen_at,
                    )
                    key = (itinerary.chain(), fare.price.amount)
                    offers.setdefault(key, FlightOffer(itinerary, fare))
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ParseError(f"invalid Skiplagged result: {exc}") from exc
        notes = [
            "at most one stop each way was asked; the price is for the whole party; baggage is not stated",
        ]
        if total > len(offers) + hidden:
            notes.append(
                f"{len(offers) + hidden} of {total} itineraries Skiplagged counts were read: the cheapest and the "
                "shortest, up to 100 each"
            )
        if hidden:
            notes.append(f"{hidden} hidden-city fares were left out: they end before the ticketed destination")
        if joined:
            notes.append("some connections join airlines that do not sell together: several tickets, booked on Skiplagged")
        return Parsed(list(offers.values()), notes)
