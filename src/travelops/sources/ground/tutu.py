"""Trains and buses through Tutu's read-only MCP server (docs/sources/tutu-ground.md). Only `search_rail` and
`search_bus` are called; the server's checkout and passenger tools never are."""

from __future__ import annotations

import json
from datetime import datetime

from ...core.common import Link
from ...core.ground import GroundOffer, GroundQuery, Ride
from ...core.money import Money
from ..base import Context, Parsed, ParseError
from .._mcp import Refused, Server
from ...geo import locate

URL = "https://mcp.tutu.ru/mcp"
TOOLS = {"train": "search_rail", "bus": "search_bus"}
PAGE = 30
PAGES = 3  # read while Tutu says there are more
META = ("pricing", "from", "to", "has_more", "total_matched")
# Tutu's seat categories, by the names a traveller uses.
CLASSES = {
    "SEDENTARY": "seat",
    "PLATZKART": "open_berth",
    "COMMON": "common",
    "COMPARTMENT": "compartment",
    "SOFT": "sleeper",
    "LUX": "sleeper",
    "RESERVED_SEAT": "open_berth",
}


async def russian(ctx: Context, place: str) -> str:
    """The place's Russian name. Tutu's index is in Russian and a Latin name goes through its suggest, which took
    Saint Petersburg for Kolpino; the geocoder knows the Russian name of a city in any country. Outside Russia the
    country follows the name, as Tutu reads "<name>, <region>": a bare Сараево is a village in Bashkortostan. A
    place the geocoder does not find is sent as written."""
    try:
        found = await locate(ctx.net, place, language="ru")
    except Exception:  # the name as written still has a chance
        return place
    if not found:
        return place
    first = found[0]
    return first.name if first.country_code in ("RU", "") else f"{first.name}, {first.country}"


def where(place: dict | None) -> str:
    """The place Tutu searched, with its region and the places of the same name it did not take: a Latin name
    can resolve to a village of that name in Russia."""
    if not place:
        return "an unstated place"
    text = place.get("name", "?") + (f" ({place['region']})" if place.get("region") else "")
    others = [p.get("region") or "region not given" for p in place.get("also_named") or []]
    return text + (f", not the {len(others)} others of that name" if others else "")


class Source:
    name = "tutu"

    def __init__(self) -> None:
        self.server = Server(self.name, URL, queue="handshake")

    def max_requests(self, query: GroundQuery) -> int:
        return 2 + sum(mode in TOOLS for mode in query.modes) * PAGES

    def typical_requests(self, query: GroundQuery) -> int:
        return sum(mode in TOOLS for mode in query.modes)  # a day's trains or buses on one route seldom fill a page

    async def fetch(self, query: GroundQuery, ctx: Context) -> list[bytes]:
        modes = [mode for mode in query.modes if mode in TOOLS]
        if not modes:
            return []
        origin, destination = await russian(ctx, query.origin), await russian(ctx, query.destination)
        raws = []
        for mode in modes:
            arguments = {
                "origin": origin,
                "destination": destination,
                "departure_date": query.depart.isoformat(),
                "page": 1,
                "page_size": PAGE,
                "sort": "price_asc",
            }
            if mode == "train":
                if query.children:
                    # A rail fare is one seat and a child's is priced by age at booking: not known here.
                    raws.append(json.dumps({"mode": mode, "skipped": "children's train fares are not priced"}).encode())
                    continue
                arguments["passengers"] = query.adults
            else:
                arguments["adults"], arguments["children"] = query.adults, query.children
            try:
                payload = await self.server.call(ctx, TOOLS[mode], arguments)
            except Refused as exc:
                # An unknown place is an answer about the place, not a broken source.
                raws.append(json.dumps({"mode": mode, "refused": str(exc)}).encode())
                continue
            if not isinstance(payload, dict) or "offers" not in payload:
                raise ParseError(f"Tutu {TOOLS[mode]} answered without offers")
            for page in range(2, PAGES + 1):
                if not (payload.get("meta") or {}).get("has_more"):
                    break
                more = await self.server.call(ctx, TOOLS[mode], dict(arguments, page=page))
                if not isinstance(more, dict) or not isinstance(more.get("offers"), list) or not more["offers"]:
                    break
                payload = dict(more, offers=payload["offers"] + more["offers"])
            # Only what is read: the rest of `meta` includes text the server addresses to a model.
            meta = {k: v for k, v in (payload.get("meta") or {}).items() if k in META}
            raws.append(json.dumps({"mode": mode, "offers": payload["offers"], "meta": meta}).encode())
        return raws

    def parse(self, raws: list[bytes], query: GroundQuery, seen_at: datetime) -> Parsed:
        offers, notes = [], []
        for raw in raws:
            try:
                answer = json.loads(raw)
                mode = answer["mode"]
                if "skipped" in answer:
                    notes.append(f"{mode}: not searched: {answer['skipped']}")
                    continue
                if "refused" in answer:
                    notes.append(f"{mode}: {answer['refused']}")
                    continue
                meta = answer["meta"]
                basis = meta.get("pricing", {}).get("basis")
                if answer["offers"] and basis != ("per_seat" if mode == "train" else "party_total"):
                    raise ValueError(f"unexpected price basis {basis!r} for {mode}")
                if meta.get("has_more"):
                    notes.append(f"{mode}: results truncated: {len(answer['offers'])} of {meta.get('total_matched')}")
                ends = meta.get("from") or {}, meta.get("to") or {}
                # Said as a limit only when the name was not plain to Tutu: then the agent must check the place.
                plain = all(not e.get("also_named") and e.get("match") in (None, "qualified") for e in ends)
                verb = "searched" if plain else "understood as"
                notes.append(f"{mode}: {verb} {where(ends[0])} to {where(ends[1])}")
                for item in answer["offers"]:
                    offers.append(self._offer(item, mode, query, seen_at))
            except (ValueError, KeyError, TypeError, IndexError) as exc:
                raise ParseError(f"Tutu ground answer fields: {exc}") from exc
        return Parsed(offers, notes)

    def _offer(self, item: dict, mode: str, query: GroundQuery, seen_at: datetime) -> GroundOffer:
        rides = tuple(
            Ride(
                mode,
                s["from"],
                s["to"],
                datetime.fromisoformat(s["departure_at"]),
                datetime.fromisoformat(s["arrival_at"]),
                s.get("carrier"),
                s.get("voyage_no"),
            )
            for leg in item["legs"]
            for s in leg["segments"]
        )
        if not rides:
            raise ValueError("offer without segments")
        if mode == "train":
            # One seat each; Tutu's party price assumes everyone finds a seat in the cheapest class.
            total = item.get("price_party") or item["price"]
            price = Money(total["amount"], total["currency"])
            if query.adults > 1 and "price_party" not in item:
                raise ValueError("a train fare for a party came without the party price")
            fares = (item.get("fares") or {}).get("seat_categories") or {}
            currency = (item.get("fares") or {}).get("currency", total["currency"])
            classes: dict[str, Money] = {}
            for key, value in fares.items():
                name = CLASSES.get(key, key.lower())
                money = Money(value["price_from"], currency)
                if name not in classes or money.amount < classes[name].amount:
                    classes[name] = money
        else:
            price, classes = Money(item["price"]["amount"], item["price"]["currency"]), {}
        review = item.get("review_summary") or {}
        url = item.get("search_results_url")
        return GroundOffer(
            rides,
            price,
            self.name,
            self.name,
            Link(url, "results") if url else None,
            seen_at,
            price_from=mode == "train",
            classes=classes,
            rating=review.get("rating"),
            reviews=review.get("review_count") or None,
        )
