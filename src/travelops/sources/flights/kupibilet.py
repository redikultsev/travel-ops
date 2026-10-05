"""Kupibilet search transport from docs/sources/kupibilet.md."""

from itertools import product

from ...core.flights import CABINS
from ..base import NotConfigured

URL = "https://api-rs-lb.kupibilet.ru/frontend_search"


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
        if query.adults != 1 or query.children or query.infants:
            raise NotConfigured("Kupibilet party pricing is verified only for one adult")
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
        from ...core.flights import Baggage, Fare, FlightOffer, Itinerary, Segment, at_airport, flight_number
        from ...core.money import Money
        from ..base import Parsed, ParseError

        offers = []
        try:
            for raw in raws:
                data = json.loads(raw)
                variants, flights = data["variants"], data["flights"]
                if not isinstance(variants, list) or not isinstance(flights, dict):
                    raise ParseError("variants must be a list and flights a map")
                for variant in variants:
                    directions = []
                    for leg in variant["segments"]:
                        segments = []
                        for fid in leg["flights"]:
                            f = flights[fid]
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
                                query.cabin,
                                Baggage(count, weight, carry),
                                None,
                                seen_at,
                            ),
                        )
                    )
        except (ValueError, KeyError, TypeError, IndexError, AttributeError) as exc:
            raise ParseError(f"Kupibilet response fields: {exc}") from exc
        return Parsed(
            offers, [f"requested cabin: {query.cabin}", "no validated ticket or results URL"] if offers else []
        )
