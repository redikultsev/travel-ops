"""FlixBus through the JSON its own shop searches with (global.api.flixbus.com): no key, no browser. FlixBus also
sells partner carriers' buses and trains on the same search, so it is a marketplace across Europe and North
America, not one company's timetable. Not a published API; see docs/sources/flixbus.md."""

from __future__ import annotations

import json
from datetime import datetime
from urllib.parse import urlencode

from ...core.common import Link
from ...core.ground import GroundOffer, GroundQuery, Ride, folded
from ...core.money import Money
from ..base import Context, NotConfigured, Parsed, ParseError

API = "https://global.api.flixbus.com/search"
SHOP = "https://shop.global.flixbus.com/search"
MODES = {"bus": "bus", "train": "train"}
# What a trip says of itself that is read here. Free text (`messages`) is the carrier talking to travellers and is
# not passed on.
TRIP = ("uid", "status", "price", "legs", "departure", "arrival")


class Source:
    name = "flixbus"

    def max_requests(self, query: GroundQuery) -> int:
        return 3

    def typical_requests(self, query: GroundQuery) -> int:
        return 1  # the two city lookups wait in a short line of their own

    async def _city(self, ctx: Context, place: str) -> dict | None:
        response = await ctx.net.request(
            self.name,
            "GET",
            f"{API}/autocomplete/cities",
            params={"q": place, "lang": "en", "country": "en", "flixbus_cities_only": "false", "stations": "false"},
            queue="lookup",
            cache_ttl=30 * 86400,
        )
        if response.status != 200:
            raise ParseError(f"FlixBus city lookup answered HTTP {response.status}")
        found = response.json()
        if not isinstance(found, list):
            raise ParseError("FlixBus city lookup answered without a list")
        return {k: found[0].get(k) for k in ("id", "name", "country")} if found else None

    async def fetch(self, query: GroundQuery, ctx: Context) -> list[bytes]:
        if query.children:
            raise NotConfigured("FlixBus prices a child by age group; the query has no ages")
        if not set(query.modes) & set(MODES):
            return []
        origin, destination = await self._city(ctx, query.origin), await self._city(ctx, query.destination)
        if not origin or not destination:
            missing = query.origin if not origin else query.destination
            return [json.dumps({"unknown": missing}).encode()]
        day = query.depart.strftime("%d.%m.%Y")
        response = await ctx.net.request(
            self.name,
            "GET",
            f"{API}/service/v4/search",
            params={
                "from_city_id": origin["id"],
                "to_city_id": destination["id"],
                "departure_date": day,
                "products": json.dumps({"adult": query.adults}),
                "currency": "EUR",
                "locale": "en",
                "search_by": "cities",
                "include_after_midnight_rides": "1",
                "disable_distribusion_trips": "0",
                "disable_global_trips": "0",
            },
        )
        if response.status != 200:
            raise ParseError(f"FlixBus search answered HTTP {response.status}")
        answer = response.json()
        trips = [
            {k: v for k, v in trip.items() if k in TRIP}
            for day_trips in answer.get("trips") or []
            for trip in (day_trips.get("results") or {}).values()
        ]
        shop = f"{SHOP}?" + urlencode(
            {
                "departureCity": origin["id"],
                "arrivalCity": destination["id"],
                "rideDate": day,
                "adult": query.adults,
                "_locale": "en",
            }
        )
        kept = {
            "from": origin,
            "to": destination,
            "link": shop,
            "trips": trips,
            "stations": {k: {"name": v.get("name")} for k, v in (answer.get("stations") or {}).items()},
            "operators": {k: {"label": v.get("label")} for k, v in (answer.get("operators") or {}).items()},
            "fee_in_price": answer.get("platform_fee_in_price_required"),
        }
        return [json.dumps(kept).encode()]

    def parse(self, raws: list[bytes], query: GroundQuery, seen_at: datetime) -> Parsed:
        offers, notes, other_modes, full = [], [], set(), 0
        for raw in raws:
            try:
                answer = json.loads(raw)
                if "unknown" in answer:
                    notes.append(f"FlixBus does not know a place named {answer['unknown']}")
                    continue
                for end, asked in (("from", query.origin), ("to", query.destination)):
                    city = answer[end]
                    said = f"{city['name']} ({str(city.get('country') or '?').upper()})"
                    # A name FlixBus read as another place is said as a limit, so the agent checks the place.
                    if folded(city["name"]) != folded(asked):
                        notes.append(f"{asked}: understood as {said}")
                stations, operators = answer["stations"], answer["operators"]
                for trip in answer["trips"]:
                    if trip["status"] != "available":
                        full += 1
                        continue
                    rides = tuple(
                        Ride(
                            MODES.get(leg.get("means_of_transport"), leg.get("means_of_transport") or "bus"),
                            stations.get(leg["departure"]["station_id"], {}).get("name") or "?",
                            stations.get(leg["arrival"]["station_id"], {}).get("name") or "?",
                            datetime.fromisoformat(leg["departure"]["date"]),
                            datetime.fromisoformat(leg["arrival"]["date"]),
                            operators.get(str(leg.get("operator_id")), {}).get("label"),
                        )
                        for leg in trip["legs"]
                    )
                    if not rides:
                        raise ValueError("a trip without legs")
                    modes = {ride.mode for ride in rides}
                    if not modes <= set(query.modes):
                        other_modes |= modes - set(query.modes)
                        continue
                    price = trip["price"]
                    # The service fee is part of what is paid when FlixBus says so; the total is for the party.
                    total = price["total_with_platform_fee"] if answer["fee_in_price"] else price["total"]
                    offers.append(
                        GroundOffer(
                            rides,
                            Money(str(total), "EUR"),
                            self.name,
                            self.name,
                            Link(answer["link"], "results"),
                            seen_at,
                        )
                    )
            except (ValueError, KeyError, TypeError) as exc:
                raise ParseError(f"FlixBus trip fields: {exc}") from exc
        if full:
            notes.append(f"{full} sold-out trips left out")
        if other_modes:
            notes.append(f"also found by {', '.join(sorted(other_modes))}, not asked for")
        if offers:
            notes.append("price for the whole party with FlixBus's service fee; carriers include FlixBus partners")
        return Parsed(offers, notes)
