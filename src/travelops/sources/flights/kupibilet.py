"""Kupibilet search transport from docs/sources/kupibilet.md."""

from itertools import product
from urllib.parse import urlencode

from ...core.flights import CABINS
from ..base import NotConfigured

URL = "https://api-rs-lb.kupibilet.ru/frontend_search"
SITE = "https://www.kupibilet.ru/search"


def results_url(query, origin: str, destination: str) -> str | None:
    """Kupibilet's own results page for the same search, as its search form builds it (seen 2026-10-07). Only
    economy is verified, so another cabin has no link rather than a guessed one."""
    if query.cabin != "economy":
        return None
    routes = [(origin, query.depart, destination)] + ([(destination, query.return_, origin)] if query.return_ else [])
    params = {"adult": query.adults, "child": 0, "infant": 0, "childrenAges": "[]", "cabinClass": "Y"}
    for index, (a, day, b) in enumerate(routes):
        params[f"route[{index}]"] = f"iatax:{a}_{day.isoformat()}_date_{day.isoformat()}_iatax:{b}"
    params["v"] = 2
    return f"{SITE}?{urlencode(params)}"


def challenge(response):
    body = response.body.lower()
    if b"<html" in body and (b"captcha" in body or b"challenge" in body):
        return "Kupibilet returned an anti-bot challenge"
    return None


class Source:
    name = "kupibilet"

    def max_requests(self, query):
        return len(query.origins) * len(query.destinations)

    async def fetch(self, query, ctx):
        if query.children or query.infants:
            raise NotConfigured("Kupibilet prices a child by age, and the query has no ages")
        if query.cabin not in CABINS:
            raise ValueError(f"unsupported cabin: {query.cabin}")
        raws = []
        for origin, destination in product(query.origins, query.destinations):
            trips = [{"departure": origin, "arrival": destination, "date": query.depart.isoformat()}]
            if query.return_:
                trips.append({"departure": destination, "arrival": origin, "date": query.return_.isoformat()})
            response = await ctx.net.request(
                self.name,
                "POST",
                URL,
                json={
                    "trips": trips,
                    "travelers": {
                        "adult": query.adults,
                        "child": query.children,
                        "infant": query.infants,
                        "childrenAges": [],
                    },
                    "cabin": query.cabin,
                    "agent": "context_b",
                    "language": "RU",
                    "currency": "RUB",
                    "client_platform": "web",
                    "filters": {"transport_kind": ["airplane"]},
                    "sort_by": "aggregated_cost",
                    "short_response": False,
                },
                headers={"origin": "https://www.kupibilet.ru", "referer": "https://www.kupibilet.ru/"},
                blocked_if=challenge,
            )
            raws.append(response.body)
        return raws

    def parse(self, raws, query, seen_at):
        import json
        from ...core.common import Link
        from ...core.flights import Baggage, Fare, FlightOffer, Itinerary, Segment, at_airport, cabin, flight_number
        from ...core.money import Money
        from ..base import Parsed, ParseError

        offers = []
        routes = list(product(query.origins, query.destinations))
        try:
            for index, raw in enumerate(raws):
                url = results_url(query, *routes[index]) if len(routes) == len(raws) else None
                data = json.loads(raw)
                variants, flights = data["variants"], data["flights"]
                if not isinstance(variants, list) or not isinstance(flights, dict):
                    raise ParseError("variants must be a list and flights a map")
                for variant in variants:
                    directions, cabins = [], set()
                    for leg in variant["segments"]:
                        segments = []
                        for fid in leg["flights"]:
                            f = flights[fid]
                            cabins.add(cabin(f.get("cabin")))
                            carrier = f.get("marketing_carrier") or f["operating_carrier"]
                            segments.append(
                                Segment(
                                    carrier,
                                    flight_number(carrier, f["number"]),
                                    f["departure"],
                                    f["arrival"],
                                    at_airport(f["departure_datetime"], f["departure"]),
                                    at_airport(f["arrival_datetime"], f["arrival"]),
                                    f.get("operating_carrier"),
                                )
                            )
                        if not segments:
                            raise ParseError("empty direction")
                        directions.append(tuple(segments))
                    if len(directions) != (2 if query.return_ else 1):
                        raise ParseError("outbound/return directions do not match query")
                    baggage = variant.get("baggage") or {}
                    hand = variant.get("hand_luggage") or {}
                    weight = baggage.get("weight")
                    count = baggage.get("count", 0 if weight == 0 else None)
                    carry = None if hand.get("count") is None else hand["count"] > 0
                    price = variant["price"]
                    offers.append(
                        FlightOffer(
                            Itinerary(directions[0], directions[1] if query.return_ else ()),
                            Fare(
                                Money(price["amount"], price.get("currency", "RUB")),
                                self.name,
                                self.name,
                                # Each flight says its cabin; a mixed or unstated one is not the cabin asked for.
                                cabins.pop() if len(cabins) == 1 else None,
                                Baggage(count, weight, carry),
                                Link(url, "results") if url else None,
                                seen_at,
                            ),
                        )
                    )
        except (ValueError, KeyError, TypeError, IndexError, AttributeError) as exc:
            raise ParseError(f"Kupibilet response fields: {exc}") from exc
        notes = [f"requested cabin: {query.cabin}"]
        if query.cabin != "economy":
            notes.append("no results link: Kupibilet's link for this cabin is not verified")
        return Parsed(offers, notes if offers else [])
