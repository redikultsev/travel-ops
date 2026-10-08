"""trivago through its public MCP server (https://mcp.trivago.com/mcp): no key. A metasearch: each stay comes with
the price of one advertiser (Expedia, Booking.com, the hotel itself), so the seller is that advertiser and the
link leads to trivago's page of the deal. The server also sends text addressed to a model; it is not passed on."""

from __future__ import annotations

import json
import re

from ...core.common import Link
from ...core.stays import Rate, Stay, StayOffer
from .._mcp import Server
from ..base import NotConfigured, Parsed, ParseError
from ._html import money

URL = "https://mcp.trivago.com/mcp"


class Source:
    name = "trivago"

    def __init__(self) -> None:
        self.server = Server(self.name, URL)

    def max_requests(self, query):
        return 3

    async def fetch(self, query, ctx):
        if query.children and len(query.children_ages) != query.children:
            raise NotConfigured("trivago prices a child by age: pass children_ages")
        arguments = {
            "query": query.place,
            "arrival": query.checkin.isoformat(),
            "departure": query.checkout.isoformat(),
            "adults": query.adults,
            "rooms": query.rooms,
            "currency": "EUR",
            "language": "en",
        }
        if query.children_ages:
            arguments["children"] = query.children
            arguments["children_ages"] = "-".join(map(str, query.children_ages))
        payload = await self.server.call(ctx, "trivago-accommodation-search", arguments)
        if not isinstance(payload, dict):
            raise ParseError("trivago answered without a search result")
        # Only the list of stays is kept: `system_message` is the server talking to a model.
        return [json.dumps({"accommodations": payload.get("accommodations")}).encode()]

    async def lookup(self, query, name, ctx, seen_at):
        """One property by its name, at the dates of `query`: trivago's search takes a hotel's name as well as a
        place's, and answers with that hotel alone (Okura Garden Hotel Shanghai, 2026-10-08)."""
        from dataclasses import replace

        raws = await self.fetch(replace(query, place=name), ctx)
        return self.parse(raws, query, seen_at).offers

    def parse(self, raws, query, seen_at):
        offers = []
        try:
            for raw in raws:
                found = json.loads(raw)["accommodations"]
                if found is None:
                    found = []
                if not isinstance(found, list):
                    raise ValueError("accommodations must be a list")
                for item in found:
                    if (item.get("arrival"), item.get("departure")) != (
                        query.checkin.isoformat(),
                        query.checkout.isoformat(),
                    ):
                        raise ValueError("answer is for other dates than were asked")
                    total = money(str(item["price_per_stay"]))
                    stars = item.get("hotel_rating") or 0
                    rating = float(item["review_rating"]) if item.get("review_rating") not in (None, "") else None
                    if rating is not None and not 0 <= rating <= 10:
                        raise ValueError("rating is outside 0-10")
                    count = re.sub(r"\D", "", str(item.get("review_count") or ""))
                    place = str(item.get("distance") or "")
                    reach = re.search(r"([\d.]+)\s*(miles?|km|ft|m)\b\s+to\s+City center", place, re.I)
                    scale = {"mile": 1.609, "km": 1.0, "ft": 0.0003048, "m": 0.001}
                    center_km = None
                    if reach:
                        center_km = round(
                            float(reach[1]) * scale[reach[2].lower().rstrip("s") if reach[2].lower() != "m" else "m"], 2
                        )
                    link = str(item.get("accommodation_url") or "")
                    if not link.startswith("https://www.trivago."):
                        raise ValueError("stay link is not a trivago page")
                    image = str(item.get("main_image") or "")
                    advertiser = str(item.get("advertisers") or "").split(",")[0].strip()
                    offers.append(
                        StayOffer(
                            Stay(
                                self.name,
                                str(item["accommodation_id"]),
                                str(item["accommodation_name"]),
                                # The page name in the link tells an entire flat from a hotel when stars do not.
                                "apartment"
                                if "entire-house-apartment" in link or "apartment" in link.split("?")[0]
                                else ("hotel" if stars else "other"),
                                item.get("latitude"),
                                item.get("longitude"),
                                rating,
                                int(count) if count else None,
                                (image,) if image.startswith("https://") else (),
                                tuple(a.strip() for a in str(item.get("top_amenities") or "").split(",") if a.strip()),
                                district=place.split(",")[0].strip() or None,
                                center_km=center_km,
                            ),
                            Rate(
                                total,
                                f"{self.name}:{advertiser}" if advertiser else self.name,
                                self.name,
                                Link(link, "property"),
                                seen_at,
                            ),
                        )
                    )
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ParseError(f"invalid trivago result: {exc}") from exc
        if not offers:
            return Parsed()
        return Parsed(
            offers,
            [
                f"first page: {len(offers)} stays; total count not provided",
                "one advertiser's price per stay; other advertisers may differ",
                "amenities are the stay's top ones, not the whole list",
                "taxes and fees inside or on top of the total are not stated",
            ],
        )

    async def fetch_details(self, url, place, ctx):
        raise NotConfigured("trivago has no property page of its own to read; its card already lists the top amenities")

    def parse_details(self, raw):
        raise NotConfigured("trivago has no property page of its own to read")
