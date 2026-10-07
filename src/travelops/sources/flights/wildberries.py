"""Wildberries summary search from docs/sources/wildberries.md."""

import json
from itertools import product
from uuid import uuid4

from ..base import NotConfigured

URL = "https://travel.wildberries.ru/stream/api/avia-service/v3/stream/getFlights"
CLASSES = {"economy": "ECONOMY", "premium_economy": "COMFORT", "business": "BUSINESS", "first": "FIRST"}
SITE = "https://www.wildberries.ru/travel/avia/results"


def results_url(query, origin: str, destination: str) -> str | None:
    """WB Travel's own results page for the same search: `BEG141126IST171126Y200` is from, day, to, the day back,
    the class and the adults, children and infants (seen 2026-10-07). Only economy is verified."""
    if query.cabin != "economy":
        return None
    back = query.return_.strftime("%d%m%y") if query.return_ else ""
    return f"{SITE}?token={origin}{query.depart.strftime('%d%m%y')}{destination}{back}Y{query.adults}00"


def challenge(response):
    low = response.body.lower()
    if b"<html" in low and (b"captcha" in low or b"challenge" in low):
        return "Wildberries returned an anti-bot challenge"
    return None


class Source:
    name = "wildberries"

    def max_requests(self, query):
        return 1 + len(query.origins) * len(query.destinations)

    async def fetch(self, query, ctx):
        if query.children or query.infants:
            raise NotConfigured("Wildberries child and infant fares are not verified")
        if query.cabin not in CLASSES:
            raise ValueError(f"unsupported cabin: {query.cabin}")
        session = await ctx.browser.get(self.name, "https://www.wildberries.ru/", engine="camoufox")
        raws = []
        for origin, destination in product(query.origins, query.destinations):
            payload = {
                "beginDate_at": query.depart.isoformat() + "T00:00:00.000Z",
                "beginLocationCode": origin,
                "endLocationCode": destination,
                "serviceClass": CLASSES[query.cabin],
                "seats": [
                    {"passenger": "ADT", "number": query.adults},
                    {"passenger": "CHD", "number": 0},
                    {"passenger": "INF", "number": 0},
                ],
                "preferredAirlinesCodes": None,
                "providers": ["ot", "ac", "fs", "pb"],
                "turnOnFolding": False,
                "filter": {},
                "promoUUID": "",
                "includeCorporateTariffs": False,
            }
            if query.return_:
                payload["endDate_at"] = query.return_.isoformat() + "T00:00:00.000Z"
            response = await ctx.net.request(
                self.name,
                "POST",
                URL,
                data=json.dumps(payload, separators=(",", ":")),
                headers={
                    "origin": "https://www.wildberries.ru",
                    "referer": "https://www.wildberries.ru/",
                    "x-platform": "web",
                    "client-session-id": "site_" + uuid4().hex,
                    "access-token": "null",
                    "content-type": "text/plain;charset=UTF-8",
                    "user-agent": session.user_agent,
                },
                cookies=session.cookies,
                impersonate=session.impersonate,
                blocked_if=challenge,
                timeout=120,
            )  # the answer is a stream that ends only when the search does
            if response.status == 404 and b"FLIGHT_OPTIONS_NOT_FOUND" in response.body:
                raws.append(b'{"result":{"data":{"flights":[]}}}')
            else:
                raws.append(response.body)
        return raws

    def parse(self, raws, query, seen_at):
        from datetime import datetime
        from decimal import Decimal
        from ...core.common import Link
        from ...core.flights import Baggage, Fare, FlightOffer, Itinerary, Segment, at_airport, cabin, flight_number
        from ...core.money import Money
        from ..base import Parsed, ParseError

        def moment(value, airport):
            # The Z is not UTC: the time is the airport's own wall clock (Air Serbia JU426 leaves Belgrade at
            # 00:50 and is written 00:50Z). On Moscow routes that looked like Moscow time.
            return at_airport(datetime.fromisoformat(value[:-1]) if value.endswith("Z") else value, airport)

        offers = []
        routes = list(product(query.origins, query.destinations))
        try:
            for index, raw in enumerate(raws):
                url = results_url(query, *routes[index]) if len(routes) == len(raws) else None
                latest = {}
                for line in raw.splitlines():
                    if not line.strip():
                        continue
                    flights = json.loads(line)["result"]["data"]["flights"]
                    if not isinstance(flights, list):
                        raise ParseError("flights is not a list")
                    for flight in flights:
                        latest[flight["id"]] = flight
                if not raw.strip():
                    raise ParseError("empty stream body")
                for flight in latest.values():
                    legs, cabins = [], []
                    for leg in flight["legs"]:
                        segments = []
                        for f in leg["segments"]:
                            carrier = f.get("airlineCode") or f["operationAirlineCode"]
                            origin, destination = f["airportBeginCode"], f["airportEndCode"]
                            segments.append(
                                Segment(
                                    carrier,
                                    flight_number(carrier, f["flightNumber"]),
                                    origin,
                                    destination,
                                    moment(f["dateBeginAt"], origin),
                                    moment(f["dateEndAt"], destination),
                                    f.get("operationAirlineCode"),
                                )
                            )
                            cabins.append(cabin(f.get("serviceClass")))
                        if not segments:
                            raise ParseError("empty direction")
                        legs.append(tuple(segments))
                    if len(legs) != (2 if query.return_ else 1):
                        raise ParseError("outbound/return directions do not match query")
                    bag = flight.get("baggage") or {}
                    included = bag.get("isIncluded")
                    checked = 0 if included is False else bag.get("count")
                    weight = bag.get("weightKg", 0 if included is False else None)
                    carry = (flight.get("luggageMeta") or {}).get("cabin")
                    if isinstance(carry, dict):
                        carry = carry.get("isIncluded")
                    carry = carry if isinstance(carry, bool) else None
                    normalized = cabins[0] if cabins and all(c == cabins[0] for c in cabins) else None
                    offers.append(
                        FlightOffer(
                            Itinerary(legs[0], legs[1] if query.return_ else ()),
                            Fare(
                                Money(Decimal(str(flight["fullPrice"])) / 100, "RUB"),
                                self.name,
                                self.name,
                                normalized,
                                Baggage(checked, weight, carry),
                                Link(url, "results") if url else None,
                                seen_at,
                            ),
                        )
                    )
        except (ValueError, KeyError, TypeError, IndexError, AttributeError) as exc:
            raise ParseError(f"Wildberries stream fields: {exc}") from exc
        return Parsed(
            offers,
            [
                f"requested cabin: {query.cabin}",
                "summary fares only; detailed tariff terms unknown",
                *(
                    ["no results link: WB Travel's link for this cabin is not verified"]
                    if query.cabin != "economy"
                    else []
                ),
                "included baggage piece count unknown when only inclusion is reported",
            ]
            if offers
            else [],
        )
