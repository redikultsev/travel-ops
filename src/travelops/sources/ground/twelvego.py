"""12Go through its public MCP server (https://mcp.12go.asia/mcp): no key, one tool, `search`. Strong in Turkey,
south-east Asia and parts of the Balkans; also ferries and shared minibuses. See docs/sources/12go.md."""

from __future__ import annotations

import json
from datetime import datetime

from ...core.common import Link
from ...core.ground import GroundOffer, GroundQuery, Ride, through_stops
from ...core.money import Money
from ..base import Context, NotConfigured, Parsed, ParseError
from .._mcp import Refused, Server

URL = "https://mcp.12go.asia/mcp"
# 12Go's vehicle classes as our modes; flights are left to the flight sources.
MODES = {"train": "train", "bus": "bus", "ferry": "ferry", "van": "van", "minivan": "van", "shuttle": "van"}


class Source:
    name = "12go"

    def __init__(self) -> None:
        self.server = Server(self.name, URL, queue="handshake")

    def max_requests(self, query: GroundQuery) -> int:
        return 3

    def typical_requests(self, query: GroundQuery) -> int:
        return 3

    async def fetch(self, query: GroundQuery, ctx: Context) -> list[bytes]:
        if query.children:
            raise NotConfigured("12Go shows one adult price per seat; children's fares are not known")
        arguments = {
            "from": query.origin,
            "to": query.destination,
            "date": query.depart.isoformat(),
            "seats": query.adults,
            "fxcode": "EUR",
            "lang": "en",
            "v": "all",
            "sort": "Cheapest",
        }
        try:
            payload = await self.server.call(ctx, "search", arguments)
        except Refused as exc:
            return [json.dumps({"refused": str(exc)}).encode()]
        if not isinstance(payload, list):
            raise ParseError("12Go search answered without a list of trips")
        return [json.dumps({"trips": payload}).encode()]

    def parse(self, raws: list[bytes], query: GroundQuery, seen_at: datetime) -> Parsed:
        offers, notes, other_modes = [], [], set()
        for raw in raws:
            try:
                answer = json.loads(raw)
                if "refused" in answer:
                    notes.append(answer["refused"])
                    continue
                for trip in answer["trips"]:
                    kinds = {MODES.get(v.lower(), v.lower()) for v in trip.get("vehclasses") or []}
                    if len(kinds) != 1:
                        raise ValueError(f"a trip with vehicle classes {trip.get('vehclasses')}")
                    (mode,) = kinds
                    if mode not in query.modes:
                        other_modes.add(mode)
                        continue
                    # The price is one seat; every seat of a party of adults costs the same.
                    each = Money(trip["price"], "EUR")
                    offers.append(
                        GroundOffer(
                            (
                                Ride(
                                    mode,
                                    trip["from"],
                                    trip["to"],
                                    datetime.fromisoformat(trip["departure"]),
                                    datetime.fromisoformat(trip["arrival"]),
                                ),
                            ),
                            Money(each.amount * query.adults, "EUR"),
                            self.name,
                            self.name,
                            Link(trip["booking_url"], "results") if trip.get("booking_url") else None,
                            seen_at,
                        )
                    )
            except (ValueError, KeyError, TypeError) as exc:
                raise ParseError(f"12Go trip fields: {exc}") from exc
        offers, stops = through_stops(offers, query.destination)
        if stops:
            notes.append(f"rides to stops short of {query.destination} left out: {', '.join(sorted(stops))}")
        if other_modes:
            notes.append(f"also found by {', '.join(sorted(other_modes))}, not asked for")
        if offers:
            notes.append("times are local; no carrier names; the price of one seat times the adults")
        return Parsed(offers, notes)
