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
# One answer holds 25 stays and the tool has no pages. Asking once as is and once per star class reaches more of
# them: each star class has its own first 25. Stays without stars (flats) come only in the first answer.
STARS = ("1star", "2star", "3star", "4star", "5star")
RATINGS = ((8.5, "rating85"), (8.0, "rating80"), (7.5, "rating75"), (7.0, "rating70"))


class Source:
    name = "trivago"

    def __init__(self) -> None:
        self.server = Server(self.name, URL)

    def max_requests(self, query):
        return 2 + len(STARS) + 1

    async def fetch(self, query, ctx, every_star: bool = True):
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
        rating = next((name for bar, name in RATINGS if query.min_rating and query.min_rating >= bar), None)
        if rating:
            arguments["review_rating"] = {rating: True}
        raws = []
        for star in (None, *(STARS if every_star else ())):
            asked = dict(arguments, hotel_rating={star: True}) if star else arguments
            try:
                payload = await self.server.call(ctx, "trivago-accommodation-search", asked)
            except ParseError:
                if star is None:
                    raise
                continue  # a star class it will not answer for costs that class only
            if not isinstance(payload, dict):
                raise ParseError("trivago answered without a search result")
            # Only the list of stays is kept: `system_message` is the server talking to a model.
            raws.append(json.dumps({"accommodations": payload.get("accommodations")}).encode())
        return raws

    async def lookup(self, query, name, ctx, seen_at):
        """One property by its name, at the dates of `query`: trivago's search takes a hotel's name as well as a
        place's, and answers with that hotel alone (Okura Garden Hotel Shanghai, 2026-10-08)."""
        from dataclasses import replace

        raws = await self.fetch(replace(query, place=name, min_rating=None), ctx, every_star=False)
        return self.parse(raws, query, seen_at).offers

    def parse(self, raws, query, seen_at):
        offers, seen = [], set()
        try:
            for raw in raws:
                found = json.loads(raw)["accommodations"]
                if found is None:
                    found = []
                if not isinstance(found, list):
                    raise ValueError("accommodations must be a list")
                for item in found:
                    if str(item.get("accommodation_id")) in seen:
                        continue  # the same stay in the plain list and in its star class's
                    seen.add(str(item.get("accommodation_id")))
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
                f"{len(offers)} stays from {len(raws)} lists of up to 25 (as ranked, then one per star class); "
                "trivago gives no total and no further pages",
                "one advertiser's price per stay; other advertisers may differ",
                "amenities are the stay's top ones, not the whole list",
                "taxes and fees inside or on top of the total are not stated",
            ],
        )

    async def fetch_details(self, url, place, ctx):
        raise NotConfigured("trivago has no property page of its own to read; its card already lists the top amenities")

    def parse_details(self, raw):
        raise NotConfigured("trivago has no property page of its own to read")
