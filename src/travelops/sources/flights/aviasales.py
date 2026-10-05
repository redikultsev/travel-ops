"""Aviasales transport implemented from docs/sources/aviasales.md."""

from __future__ import annotations

import json
from itertools import product
from urllib.parse import urlsplit

from datetime import datetime

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
from ...net.client import Blocked
from ..base import Context, ParseError, Parsed

START = "https://tickets-api.aviasales.ru/search/v2/start"
SITE = "https://www.aviasales.ru"
CLASSES = {"economy": "Y", "premium_economy": "W", "business": "C", "first": "F"}
MAX_POLLS = 12
WAF_COOKIE = "aws-waf-token"


def challenge(response) -> str | None:
    # Search answers are JSON; an HTML page in their place is the anti-bot. Words inside JSON prove nothing.
    if response.body.lstrip()[:1] == b"<":
        return "Aviasales returned an anti-bot page instead of results"
    return None


def results_page(query: FlightQuery, origin: str, destination: str) -> str:
    route = f"{origin}{query.depart:%d%m}{destination}"
    if query.return_:
        route += f"{query.return_:%d%m}"
    return f"{SITE}/search/{route}{query.adults}"


def results_link(query: FlightQuery, origin: str, destination: str) -> Link | None:
    """The results page for this route and dates. Only the party and cabin whose URL form was observed."""
    if query.children or query.infants or query.cabin != "economy":
        return None
    return Link(results_page(query, origin, destination), "results")


class Source:
    name = "aviasales"

    def max_requests(self, query: FlightQuery) -> int:
        return 1 + (1 + MAX_POLLS) * len(query.origins) * len(query.destinations)

    async def fetch(self, query: FlightQuery, ctx: Context) -> list[bytes]:
        if query.cabin not in CLASSES:
            raise ValueError(f"unsupported cabin: {query.cabin}")
        pairs = list(product(query.origins, query.destinations))
        # The site's own results page is where the anti-bot issues its token; the token is short-lived.
        session = await ctx.browser.get(
            self.name, results_page(query, *pairs[0]), engine="camoufox", ready_cookie=WAF_COOKIE, max_age=240
        )
        headers = {
            "origin": SITE,
            "referer": SITE + "/",
            "x-web-client": "web_desktop",
            "x-client-type": "web",
            "user-agent": session.user_agent,
            "x-aws-waf-token": session.cookies[WAF_COOKIE],
            "x-origin-cookie": "; ".join(f"{key}={value}" for key, value in session.cookies.items()),
        }
        common = {
            "headers": headers,
            "cookies": session.cookies,
            "impersonate": session.impersonate,
            "blocked_if": challenge,
        }
        raws = []
        try:
            for origin, destination in pairs:
                raws.append(await self._search(query, origin, destination, ctx, common))
        except Blocked:
            ctx.browser.drop(self.name)
            raise
        return raws

    async def _search(self, query: FlightQuery, origin: str, destination: str, ctx: Context, common: dict) -> bytes:
        directions = [
            {
                "origin": origin,
                "destination": destination,
                "date": query.depart.isoformat(),
                "is_origin_airport": False,
                "is_destination_airport": False,
            }
        ]
        if query.return_:
            directions.append(
                {
                    "origin": destination,
                    "destination": origin,
                    "date": query.return_.isoformat(),
                    "is_origin_airport": False,
                    "is_destination_airport": False,
                }
            )
        response = await ctx.net.request(
            self.name,
            "POST",
            START,
            json={
                "search_params": {
                    "directions": directions,
                    "passengers": {"adults": query.adults, "children": query.children, "infants": query.infants},
                    "trip_class": CLASSES[query.cabin],
                },
                "market_code": "ru",
                "marker": "direct",
                "citizenship": "RU",
                "currency_code": "rub",
                "languages": {"ru": 1},
                "brand": "AS",
            },
            **common,
        )
        try:
            start = response.json()
            search_id, results_host = start["search_id"], start["results_url"]
            address = urlsplit(results_host if "://" in results_host else f"https://{results_host}")
            host = address.hostname or ""
            if address.scheme != "https" or address.username or address.password or not host.endswith(".aviasales.ru"):
                raise ParseError("unexpected Aviasales results host")
            url = f"https://{host}/search/v3.2/results"
        except (KeyError, TypeError, ValueError) as exc:
            raise ParseError("missing Aviasales start identifiers") from exc
        responses, stamp, complete = [], 0, False
        for _ in range(MAX_POLLS):
            response = await ctx.net.request(
                self.name,
                "POST",
                url,
                json={
                    "search_id": search_id,
                    "limit": 1000,
                    "price_per_person": False,
                    "search_by_airport": False,
                    "last_update_timestamp": stamp,
                },
                **common,
            )
            if response.status == 304:
                continue
            try:
                payload = response.json()
                if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
                    raise ValueError("expected a list of result objects")
                responses += payload
                stamp = payload[0].get("last_update_timestamp")
            except (ValueError, TypeError) as exc:
                raise ParseError("Aviasales results are not a list of objects") from exc
            if stamp == 0:
                complete = True
                break
        if not responses:
            raise TimeoutError("Aviasales did not return results within the poll limit")
        # Every answer of one search is kept: later answers may carry only what changed since the stamp.
        return json.dumps(
            {"origin": origin, "destination": destination, "complete": complete, "responses": responses}
        ).encode()

    def parse(self, raws: list[bytes], query: FlightQuery, seen_at: datetime) -> Parsed:
        offers, notes = [], []
        for raw in raws:
            try:
                search = json.loads(raw)
                payloads = search["responses"]
                if not isinstance(payloads, list):
                    raise ValueError("expected a list of responses")
                link = results_link(query, search["origin"], search["destination"])
                if not search["complete"]:
                    notes.append("partial results: polling stopped before the search completed")
                if link is None:
                    notes.append("no results link: its form is known only for an economy search without children")
                for payload in payloads:
                    tickets, flights = payload["tickets"], payload["flight_legs"]
                    if not isinstance(tickets, list) or not isinstance(flights, list):
                        raise ValueError("expected tickets and flight_legs lists")
                    agents = payload.get("agents") or {}
                    if len(tickets) >= 1000:
                        notes.append(f"results cap reached: {len(tickets)} tickets; total unknown")
                    for ticket in tickets:
                        legs, identifiers = [], []
                        for leg in ticket["segments"]:
                            chain = []
                            for index in leg["flights"]:
                                flight = flights[index]
                                designator = flight["operating_carrier_designator"]
                                carrier = designator["carrier"].upper()
                                chain.append(
                                    Segment(
                                        carrier,
                                        flight_number(carrier, designator["number"]),
                                        flight["origin"],
                                        flight["destination"],
                                        at_airport(flight["local_departure_date_time"], flight["origin"]),
                                        at_airport(flight["local_arrival_date_time"], flight["destination"]),
                                        operating=carrier,
                                    )
                                )
                                identifiers.append(str(index))
                            if not chain:
                                raise ValueError("empty ticket leg")
                            legs.append(tuple(chain))
                        if not 1 <= len(legs) <= 2:
                            raise ValueError("expected one or two ticket legs")
                        itinerary = Itinerary(legs[0], legs[1] if len(legs) == 2 else ())
                        for proposal in ticket["proposals"]:
                            price = proposal["price"]
                            terms = [proposal.get("flight_terms", {}).get(index, {}) for index in identifiers]
                            bags = [term.get("baggage", {}) for term in terms]
                            hands = [term.get("handbags", {}).get("count") for term in terms]
                            counts = [bag.get("count") for bag in bags]
                            weights = [bag.get("weight") for bag in bags]
                            checked = min(counts) if all(x is not None for x in counts) else None
                            checked_kg = min(weights) if all(x is not None for x in weights) else None
                            carry_on = all(x > 0 for x in hands) if all(x is not None for x in hands) else None
                            classes = {cabin(term.get("trip_class")) for term in terms}
                            normalized = next(iter(classes)) if len(classes) == 1 else None
                            info = terms[0].get("additional_tariff_info", {})
                            refundable = info.get("return_before_flight", {}).get("available")
                            # Aviasales is a metasearch: the agency that takes the money is the seller.
                            gate = (agents.get(str(proposal.get("agent_id"))) or {}).get("gate_name")
                            seller = f"{self.name}:{gate}" if gate else self.name
                            offers.append(
                                FlightOffer(
                                    itinerary,
                                    Fare(
                                        Money(
                                            price["value"], price.get("currency_code") or price.get("currency", "RUB")
                                        ),
                                        seller,
                                        self.name,
                                        normalized,
                                        Baggage(checked, checked_kg, carry_on),
                                        link,
                                        seen_at,
                                        refundable=refundable,
                                        fare_name=info.get("fare_name"),
                                    ),
                                )
                            )
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                raise ParseError(f"invalid Aviasales result: {exc}") from exc
        # One ticket can arrive in several answers of the same search.
        return Parsed(list(dict.fromkeys(offers)), list(dict.fromkeys(notes)))
