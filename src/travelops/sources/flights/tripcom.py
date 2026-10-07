"""Trip.com flights. Its search requests are signed by its own page, so they are not sent from here: a browser
opens the results page as a person would, and the list the page receives (`FlightListSearchSSE`) is read. No
key, no captcha, nothing forged; about half a minute per route. Not a published API: see docs/sources/tripcom.md.

One way only. On a round trip the list shows the outbound flights at the price of the cheapest round trip, and
the returns appear only once one is chosen; separate tickets search each way one way and do include Trip.com."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from itertools import product
from urllib.parse import urlencode

from ...core.common import Link
from ...core.flights import Baggage, Fare, FlightOffer, FlightQuery, Itinerary, Segment, at_airport, flight_number
from ...core.money import Money
from ..base import Context, NotConfigured, Parsed, ParseError

PAGE = "https://www.trip.com/flights/showfarefirst"
LIST = r"/restapi/soa2/\d+/FlightListSearchSSE"
CLASSES = {"economy": "y", "premium_economy": "s", "business": "c", "first": "f"}
GRADES = {1: "economy", 2: "premium_economy", 3: "business", 4: "first"}


def page_url(query: FlightQuery, origin: str, destination: str) -> str:
    return f"{PAGE}?" + urlencode(
        {
            "dcity": origin.lower(),
            "acity": destination.lower(),
            "ddate": query.depart.isoformat(),
            "triptype": "ow",
            "class": CLASSES[query.cabin],
            "quantity": query.adults,
            "locale": "en-XX",
            "curr": "EUR",
        }
    )


def lists_of(body: bytes) -> list[dict]:
    """The flight lists in one answer of the page: a stream of `data:` frames, or one JSON object."""
    text = body.decode("utf-8", "replace").strip()
    frames = [line[5:] for line in text.splitlines() if line.startswith("data:")] or [text]
    found = []
    for frame in frames:
        try:
            value = json.loads(frame)
        except ValueError:
            continue
        if isinstance(value, dict) and isinstance(value.get("itineraryList"), list):
            found.append(value)
    return found


class Source:
    name = "tripcom"

    def max_requests(self, query: FlightQuery) -> int:
        return len(query.origins) * len(query.destinations)

    async def fetch(self, query: FlightQuery, ctx: Context) -> list[bytes]:
        if query.return_:
            raise NotConfigured("Trip.com is searched one way: its round trips list the returns only once chosen")
        if query.children or query.infants:
            raise NotConfigured("Trip.com children's fares are not verified")
        if query.cabin not in CLASSES:
            raise ValueError(f"unsupported cabin: {query.cabin}")
        raws = []
        for origin, destination in product(query.origins, query.destinations):
            url = page_url(query, origin, destination)
            lists = [found for body in await ctx.browser.search(self.name, url, LIST) for found in lists_of(body)]
            if not lists:
                raise ParseError("the Trip.com page received no flight list")
            # Only what is read is kept: the rest names the visitor's session and the server's own logs.
            latest = lists[-1]
            raws.append(
                json.dumps(
                    {
                        "route": [origin, destination],
                        "link": url,
                        "currency": latest["basicInfo"]["currency"],
                        "count": latest["basicInfo"].get("recordCount"),
                        "itineraries": [
                            {"journeyList": i["journeyList"], "policies": i["policies"]}
                            for i in latest["itineraryList"]
                        ],
                    }
                ).encode()
            )
        return raws

    def parse(self, raws: list[bytes], query: FlightQuery, seen_at: datetime) -> Parsed:
        offers, notes, skipped = [], [], 0
        for raw in raws:
            try:
                answer = json.loads(raw)
                currency = answer["currency"]
                for item in answer["itineraries"]:
                    (journey,) = item["journeyList"]  # one way: one journey
                    sections = journey["transSectionList"]
                    if any(s.get("transportType") != "FLIGHT" for s in sections):
                        skipped += 1  # a train or bus part: not a flight
                        continue
                    segments = tuple(
                        Segment(
                            s["flightInfo"]["airlineCode"],
                            flight_number(s["flightInfo"]["airlineCode"], s["flightInfo"]["flightNo"]),
                            s["departPoint"]["airportCode"],
                            s["arrivePoint"]["airportCode"],
                            at_airport(s["departDateTime"], s["departPoint"]["airportCode"]),
                            at_airport(s["arriveDateTime"], s["arrivePoint"]["airportCode"]),
                        )
                        for s in sections
                    )
                    for policy in item["policies"]:
                        price = policy["price"]
                        # One adult's ticket with taxes, times the adults: `totalPrice` is the same sum, rounded.
                        each = Decimal(str(price["adult"]["totalPrice"]))
                        tags = {tag["key"] for tag in policy.get("tagList") or []}
                        grades = {GRADES.get(g.get("grade")) for g in policy.get("gradeInfoList") or []}
                        offers.append(
                            FlightOffer(
                                Itinerary(segments),
                                Fare(
                                    Money(each * query.adults, currency),
                                    self.name,
                                    self.name,
                                    grades.pop() if len(grades) == 1 else None,
                                    Baggage(
                                        checked=1 if "FREE_CHECKED_BAGGAGE" in tags else None,
                                        carry_on=True if "FREE_CARRY_ON_BAGGAGE" in tags else None,
                                    ),
                                    Link(answer["link"], "results"),
                                    seen_at,
                                ),
                            )
                        )
            except (ValueError, KeyError, TypeError) as exc:
                raise ParseError(f"Trip.com itinerary fields: {exc}") from exc
        if skipped:
            notes.append(f"{skipped} journeys with a train or bus part left out")
        if offers:
            notes.append("one way only; a checked bag is stated only when included, otherwise unknown")
        return Parsed(offers, notes)
