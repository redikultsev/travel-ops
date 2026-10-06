"""Read-only Tutu MCP transport based on docs/sources/tutu.md."""

from __future__ import annotations

import asyncio
import json
import re
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
from ..base import Context, ParseError, Parsed

URL = "https://mcp.tutu.ru/mcp"
CLASSES = {"economy": "Y", "premium_economy": "S", "business": "C", "first": "F"}
CITIES = {"MOW": "Moscow", "LED": "Saint Petersburg"}


def envelope(raw: bytes) -> dict:
    try:
        text = raw.decode().strip()
        if text.startswith("{"):
            value = json.loads(text)
        else:
            frames = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")]
            value = next(json.loads(frame) for frame in reversed(frames) if frame.startswith("{"))
        if not isinstance(value, dict):
            raise ValueError("not an object")
        if "error" in value:
            raise ParseError(f"Tutu RPC error: {value['error'].get('message', 'unknown error')}")
        return value
    except (ValueError, TypeError, StopIteration) as exc:
        raise ParseError("Tutu response is neither JSON-RPC JSON nor an SSE envelope") from exc


class Source:
    name = "tutu"

    def max_requests(self, query: FlightQuery) -> int:
        return 2 + len(query.origins) * len(query.destinations)

    def typical_requests(self, query: FlightQuery) -> int:
        # The handshake is made once for a whole search, so a route usually costs one request and a share of it.
        return 1 + len(query.origins) * len(query.destinations)

    def __init__(self) -> None:
        self._opened: asyncio.Task | None = None

    async def _open(self, ctx: Context) -> dict:
        """The MCP handshake, giving the headers every later call needs."""
        headers = {"content-type": "application/json", "accept": "application/json, text/event-stream"}
        response = await ctx.net.request(
            self.name,
            "POST",
            URL,
            headers=dict(headers),
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "travel-ops", "version": "0.1.0"},
                },
            },
        )
        initialized = envelope(response.body)
        protocol = initialized.get("result", {}).get("protocolVersion")
        if protocol:
            headers["mcp-protocol-version"] = protocol
        session = next((value for key, value in response.headers.items() if key.lower() == "mcp-session-id"), None)
        if session:
            headers["mcp-session-id"] = session
        await ctx.net.request(
            self.name,
            "POST",
            URL,
            headers=dict(headers),
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )
        return headers

    async def fetch(self, query: FlightQuery, ctx: Context) -> list[bytes]:
        if query.cabin not in CLASSES:
            raise ValueError(f"unsupported cabin: {query.cabin}")
        # One handshake serves every route and date of a search: they are runs of this same object. A handshake
        # that failed is not kept, so the next run tries again.
        if self._opened is None or (self._opened.done() and (self._opened.cancelled() or self._opened.exception())):
            self._opened = asyncio.ensure_future(self._open(ctx))
        headers = await asyncio.shield(self._opened)
        raws = []
        for index, (origin, destination) in enumerate(product(query.origins, query.destinations), 2):
            arguments = {
                "origin": CITIES.get(origin, origin),
                "destination": CITIES.get(destination, destination),
                "departure_date": query.depart.isoformat(),
                "adults": query.adults,
                "children": query.children,
                "infants": query.infants,
                "service_class": CLASSES[query.cabin],
                "page": 1,
                "page_size": 30,
                "sort": "price_asc",
                "view": "full",
            }
            if query.return_:
                arguments["return_date"] = query.return_.isoformat()
            response = await ctx.net.request(
                self.name,
                "POST",
                URL,
                headers=dict(headers),
                json={
                    "jsonrpc": "2.0",
                    "id": index,
                    "method": "tools/call",
                    "params": {"name": "search_avia", "arguments": arguments},
                },
            )
            result = envelope(response.body).get("result", {})
            if result.get("isError"):
                message = "; ".join(item.get("text", "") for item in result.get("content", []))
                raise ParseError(f"Tutu search refused the query: {message}")
            payload = result.get("structuredContent")
            if payload is None:
                for item in result.get("content", []):
                    if item.get("type") == "text":
                        try:
                            payload = json.loads(item["text"])
                            break
                        except ValueError:
                            continue
            if not isinstance(payload, dict) or "offers" not in payload:
                raise ParseError("Tutu search result has no offers field")
            raws.append(json.dumps(payload).encode())
        return raws

    def parse(self, raws: list[bytes], query: FlightQuery, seen_at: datetime) -> Parsed:
        offers, notes = [], []
        routes = [f"{a}-{b}: " for a, b in product(query.origins, query.destinations)]
        for index, raw in enumerate(raws):
            route = routes[index] if len(routes) == len(raws) else ""
            try:
                payload = json.loads(raw)
                found = payload["offers"]
                if not isinstance(found, list):
                    raise ValueError("offers must be a list")
                meta = payload.get("meta", {})
                if found and meta.get("pricing", {}).get("basis") != "party_total":
                    raise ValueError("whole-party price basis was not confirmed")
                if meta.get("has_more"):
                    total = meta.get("total_matched", "unknown")
                    notes.append(f"{route}results truncated: {len(found)} of {total} itineraries")
                if meta.get("total_matched_exact") is False:
                    notes.append("matched itinerary count is a lower bound")
                for key, count in meta.items():
                    if key.startswith("post_filter_dropped_") and isinstance(count, int) and count:
                        notes.append(f"{key.replace('_', ' ')}: {count}")
                if meta.get("airport_note"):
                    notes.append("the source attached a remark about this route's airports; open its results page")
                for direction in ("from", "to"):
                    resolved = meta.get(direction, {})
                    if resolved.get("match"):
                        notes.append(f"resolved {direction}: {resolved.get('query')} to {resolved.get('name')}")
                for offer in found:
                    legs = {}
                    for leg in offer["legs"]:
                        chain = []
                        for segment in leg["segments"]:
                            endpoints = []
                            for field in ("from", "to"):
                                match = re.search(r"\(([A-Z]{3})\)", segment[field])
                                if not match:
                                    match = re.search(r",\s*([A-Z]{3})\b", segment[field])
                                if not match:
                                    raise ValueError(f"missing airport IATA in {field}")
                                endpoints.append(match.group(1))
                            voyage = segment["voyage_no"].replace(" ", "")
                            designator = re.fullmatch(r"([A-Z0-9]{2})-?(\d+[A-Z]?)", voyage.upper())
                            if not designator:
                                raise ValueError("unknown flight designator")
                            carrier, number = designator.groups()
                            origin, destination = endpoints
                            chain.append(
                                Segment(
                                    carrier,
                                    flight_number(carrier, number),
                                    origin,
                                    destination,
                                    at_airport(segment["departure_at"], origin),
                                    at_airport(segment["arrival_at"], destination),
                                )
                            )
                        if not chain:
                            raise ValueError("missing detailed segments")
                        label = leg["label"]
                        if label not in ("outbound", "return") or label in legs:
                            raise ValueError("unexpected itinerary direction")
                        legs[label] = tuple(chain)
                    itinerary = Itinerary(legs["outbound"], legs.get("return", ()))
                    if query.return_ and not itinerary.inbound:
                        raise ValueError("return itinerary missing")
                    link_url = offer.get("search_results_url")
                    link = (
                        Link(link_url, "results")
                        if isinstance(link_url, str) and link_url.startswith(("https://", "http://"))
                        else None
                    )
                    if offer.get("is_multi_pnr") or offer.get("has_self_transfer"):
                        # In our words, not the source's: its note is prose addressed to an assistant, and text
                        # from a website must reach the agent as data, never as something to act on.
                        notes.append(
                            "some itineraries are separate tickets with a self-transfer, each booked on its own"
                        )
                    for variant in offer["variants"]:
                        price = variant["price"]
                        conditions = variant.get("conditions") or {}
                        bag, hand = conditions.get("baggage") or {}, conditions.get("cabin_baggage") or {}
                        carry = hand.get("pieces")
                        if carry is None:
                            carry = hand.get("kg")
                        normalized = cabin(variant.get("service_class"))
                        if variant.get("service_class") == "S":
                            normalized = "premium_economy"
                        offers.append(
                            FlightOffer(
                                itinerary,
                                Fare(
                                    Money(price["amount"], price["currency"]),
                                    self.name,
                                    self.name,
                                    normalized,
                                    Baggage(bag.get("pieces"), bag.get("kg"), carry > 0 if carry is not None else None),
                                    link,
                                    seen_at,
                                    refundable=conditions.get("refundable"),
                                    fare_name=conditions.get("fare_family"),
                                ),
                            )
                        )
            except (KeyError, TypeError, ValueError) as exc:
                raise ParseError(f"invalid Tutu search result: {exc}") from exc
        return Parsed(offers, list(dict.fromkeys(notes)))
