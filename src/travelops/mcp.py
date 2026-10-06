"""Read-only MCP tools over one application lifetime."""

import functools
import inspect
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from mcp.server import MCPServer
from mcp.server.mcpserver import Image
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from . import recall
from .app import App, build, flight_query, stay_query, flight_sources, stay_sources
from .details import read_details, read_photos
from .geo import airports_near, locate, place_json
from .rates import load_rates
from .search import estimate_flights, estimate_stays
from .trip import plan_trip, search_trip
from .views import flights_view, stays_view

DESCRIPTION = (
    "Search live prices with seen_at timestamps. Dates are YYYY-MM-DD; airports are IATA codes; stay ratings are "
    "on a 0-10 scale. Never present a price absent from this result. "
    "Report the sources block and every limitation to the user. No booking, checkout, passenger data or login. "
    "If needs_confirmation is true, ask the human before calling again with confirm=True. "
    "Results are the cheapest `limit` cards; `shown` says how many exist. The same search within 30 minutes is "
    "answered from memory (`from_memory`, `age_minutes`); pass refresh=True only when the human wants new prices. "
    "For more cards, other times or another filter, do not search again: call the refine tool with `search_id`."
)
REFINE = (
    "Another view of a search already made, by its `search_id`: more cards, another order, filters. No request to "
    "any travel site, so it is instant and free. A view starts from the profile's bars, as the search did "
    "(max_stops and max_leg_hours for flights, min_rating for stays); pass a value to loosen or tighten one. "
    "Filters apply in the "
    "order `filtered.by` lists them, each to what the earlier ones left: `hidden` failed the filter, "
    "`hidden_unknown` (not included in `hidden`) was hidden only because the source does not say. Prices are as "
    "old as the search: see `age_minutes`; when `stale` is present, search again before recommending a booking. "
)
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=False, open_world_hint=True)
LOCAL = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)


def confirmation(estimate):
    return {
        "needs_confirmation": True,
        "estimate_seconds": round(estimate, 1),
        "message": f"This search needs about {estimate:.0f} seconds of request spacing, plus response time. Ask the human to approve before passing confirm=True.",
    }


class Tools:
    """Every tool is a coroutine: the SDK runs a plain function in a worker thread, away from the thread that
    owns the app's SQLite connections."""

    def __init__(self, app: App | None):
        self.app = app

    async def search_flights(
        self,
        origin: str,
        destination: str,
        depart: str,
        return_date: str | None = None,
        flex_days: int = 0,
        adults: int | None = None,
        cabin: str | None = None,
        sources: list[str] | None = None,
        currency: str | None = None,
        confirm: bool = False,
        limit: int = 10,
        max_stops: int | None = None,
        refresh: bool = False,
    ) -> dict[str, Any]:
        app = self.app
        try:
            self._limit(limit)
            query = flight_query(app.profile, origin, destination, depart, return_date, flex_days, adults, cabin)
            selected = flight_sources(sources)
            currency = self._currency(currency)
            if refresh or not recall.remembered(app, "flights", query, selected, currency):
                estimate = estimate_flights(query, selected, app.limiter, app.net.exit)
                if estimate > app.profile.confirm_over_seconds and not confirm:
                    return confirmation(estimate)
            stored = await recall.flights(
                app, query, selected, await load_rates(app.net), currency, refresh=refresh, selection=sources
            )
            view = flights_view(
                stored.result,
                limit=limit,
                max_stops=app.profile.max_stops if max_stops is None else max_stops,
                max_leg_hours=app.profile.max_leg_hours,
                avoid_airlines=app.profile.avoid_airlines,
            )
            return stored.stamp(view, app.results.clock())
        except ValueError as exc:
            raise ToolError(f"Invalid flight search: {exc}") from exc
        except TimeoutError as exc:
            raise ToolError("Exchange-rate feed timed out before search; try later") from exc

    async def search_stays(
        self,
        place: str,
        checkin: str,
        checkout: str,
        adults: int | None = None,
        sources: list[str] | None = None,
        currency: str | None = None,
        confirm: bool = False,
        limit: int = 10,
        min_rating: float | None = None,
        refresh: bool = False,
    ) -> dict[str, Any]:
        app = self.app
        try:
            self._limit(limit)
            query = stay_query(app.profile, place, checkin, checkout, adults)
            selected = stay_sources(sources)
            currency = self._currency(currency)
            if refresh or not recall.remembered(app, "stays", query, selected, currency):
                estimate = estimate_stays(query, selected, app.limiter, app.net.exit)
                if estimate > app.profile.confirm_over_seconds and not confirm:
                    return confirmation(estimate)
            stored = await recall.stays(
                app,
                query,
                selected,
                await load_rates(app.net),
                currency,
                refresh=refresh,
                selection=sources,
                center=await self._center(query.place),
            )
            view = stays_view(
                stored.result,
                limit=limit,
                min_rating=app.profile.stays.min_rating if min_rating is None else min_rating,
                details=app.results.details,
            )
            return stored.stamp(view, app.results.clock())
        except ValueError as exc:
            raise ToolError(f"Invalid stay search: {exc}") from exc
        except TimeoutError as exc:
            raise ToolError("Exchange-rate feed timed out before search; try later") from exc

    def _stored(self, search_id: str, kind: str):
        stored = self.app.results.get(search_id) if isinstance(search_id, str) else None
        if stored is None:
            raise ValueError(f"no search {search_id!r} in memory (results are kept for 7 days); search again")
        if stored.kind != kind:
            raise ValueError(f"{search_id!r} is a {stored.kind} search; use the other refine tool")
        return stored

    async def refine_flights(
        self,
        search_id: str,
        limit: int = 10,
        max_stops: int | None = None,
        max_leg_hours: float | None = None,
        depart_after: str | None = None,
        depart_before: str | None = None,
        return_after: str | None = None,
        return_before: str | None = None,
        airlines: list[str] | None = None,
        avoid_airlines: list[str] | None = None,
        destination: str | None = None,
        checked_bag: bool | None = None,
        max_price: float | None = None,
        sort: str = "price",
    ) -> dict[str, Any]:
        try:
            self._limit(limit)
            stored = self._stored(search_id, "flights")
            view = flights_view(
                stored.result,
                limit=limit,
                # A view starts from the same bars as the search, or it would quietly show what the search hid.
                max_stops=self.app.profile.max_stops if max_stops is None else max_stops,
                max_leg_hours=self.app.profile.max_leg_hours if max_leg_hours is None else max_leg_hours,
                depart_after=depart_after,
                depart_before=depart_before,
                return_after=return_after,
                return_before=return_before,
                airlines=airlines,
                avoid_airlines=self.app.profile.avoid_airlines if avoid_airlines is None else avoid_airlines,
                destination=destination,
                checked_bag=checked_bag,
                max_price=max_price,
                sort=sort,
            )
            return stored.stamp(view, self.app.results.clock())
        except ValueError as exc:
            raise ToolError(f"Invalid flight view: {exc}") from exc

    async def refine_stays(
        self,
        search_id: str,
        limit: int = 10,
        min_rating: float | None = None,
        min_reviews: int | None = None,
        max_total: float | None = None,
        kinds: list[str] | None = None,
        exclude_kinds: list[str] | None = None,
        sources: list[str] | None = None,
        max_center_km: float | None = None,
        free_cancellation: bool | None = None,
        must_have: list[str] | None = None,
        sort: str = "price",
    ) -> dict[str, Any]:
        try:
            self._limit(limit)
            stored = self._stored(search_id, "stays")
            view = stays_view(
                stored.result,
                limit=limit,
                min_rating=self.app.profile.stays.min_rating if min_rating is None else min_rating,
                min_reviews=min_reviews,
                max_total=max_total,
                kinds=kinds,
                exclude_kinds=exclude_kinds,
                sources=sources,
                max_center_km=max_center_km,
                free_cancellation=free_cancellation,
                must_have=must_have,
                details=self.app.results.details,
                sort=sort,
            )
            return stored.stamp(view, self.app.results.clock())
        except ValueError as exc:
            raise ToolError(f"Invalid stay view: {exc}") from exc

    async def _center(self, place: str) -> dict | None:
        """Where the place is, for distances. A geocoder that does not answer costs the distances, not the search."""
        try:
            found = await locate(self.app.net, place)
        except Exception:
            return None
        return {"name": found[0].label(), "lat": found[0].lat, "lon": found[0].lon} if found else None

    async def stay_details(self, search_id: str, stays: list[str], refresh: bool = False) -> dict[str, Any]:
        try:
            stored = self._stored(search_id, "stays")
            found = await read_details(self.app, stored, stays, refresh)
        except ValueError as exc:
            raise ToolError(f"Invalid details request: {exc}") from exc
        return {
            "search_id": stored.id,
            "stays": found,
            "note": "Amenities, rules and scores are the property page's own lists. `not_available` is what the "
            "page marks as absent; anything in neither list is not stated. Prices are from the search.",
        }

    async def stay_photos(self, search_id: str, stays: list[str], per_stay: int = 2) -> list[Any]:
        try:
            stored = self._stored(search_id, "stays")
            found = await read_photos(self.app, stored, stays, per_stay)
        except ValueError as exc:
            raise ToolError(f"Invalid photo request: {exc}") from exc
        content: list[Any] = []
        for entry in found:
            if entry.get("status") == "not_found":
                content.append(f"{entry['asked']}: no single stay of this search has this name")
                continue
            content.append(f"{entry['name']} ({entry['source']}): {entry['photos_total']} photos known")
            for number, photo in enumerate(entry["photos"], 1):
                if photo["status"] == "ok":
                    content.append(f"photo {number}: {photo['url']}")
                    content.append(Image(data=photo["data"], format=photo["format"]))
                else:
                    content.append(f"photo {number} could not be loaded ({photo['reason']}): {photo['url']}")
        return content

    @staticmethod
    def _limit(value):
        if type(value) is not int or not 1 <= value <= 100:
            raise ValueError("limit must be an integer from 1 to 100")

    async def airports_near(self, place: str, country: str | None = None, radius_km: int = 100) -> dict[str, Any]:
        try:
            if not 10 <= radius_km <= 400:
                raise ValueError("radius_km must be from 10 to 400")
            places = await locate(self.app.net, place, country)
        except ValueError as exc:
            raise ToolError(f"Invalid place lookup: {exc}") from exc
        if not places:
            return {
                "place": None,
                "airports": [],
                "message": "Place not found; write it in Latin script with its country.",
            }
        here = places[0]
        return {
            "place": place_json(here),
            "other_places_with_this_name": [place_json(p) for p in places[1:4]],
            "airports": airports_near(here.lat, here.lon, radius_km),
            "note": "Sorted by distance. Scheduled service is not known here; a flight search tells.",
        }

    async def search_trip(
        self,
        origin: str,
        place: str,
        depart: str,
        return_date: str,
        country: str | None = None,
        airports: str | None = None,
        max_airports: int = 2,
        adults: int | None = None,
        stay_adults: int | None = None,
        cabin: str | None = None,
        max_stops: int | None = None,
        min_rating: float | None = None,
        currency: str | None = None,
        confirm: bool = False,
        limit: int = 5,
        refresh: bool = False,
    ) -> dict[str, Any]:
        app = self.app
        try:
            self._limit(limit)
            plan = await plan_trip(
                app,
                origin,
                place,
                depart,
                return_date,
                country=country,
                airports=airports,
                max_airports=max_airports,
                adults=adults,
                stay_adults=stay_adults,
                cabin=cabin,
            )
            currency = self._currency(currency)
            estimate = plan.estimate(app, currency, refresh)
            if estimate > app.profile.confirm_over_seconds and not confirm:
                return dict(
                    confirmation(estimate), place=place_json(plan.place), airports=list(plan.flights.destinations)
                )
            return await search_trip(
                app,
                plan,
                await load_rates(app.net),
                currency,
                limit=limit,
                max_stops=max_stops,
                min_rating=min_rating,
                refresh=refresh,
            )
        except ValueError as exc:
            raise ToolError(f"Invalid trip search: {exc}") from exc

    def _currency(self, value):
        value = self.app.profile.currency if value is None else value
        if not isinstance(value, str) or len(value) != 3 or not value.isalpha():
            raise ValueError("currency must be a three-letter code")
        return value.upper()

    async def sources(self) -> dict[str, Any]:
        import time

        return {
            "flight_sources": [s.name for s in flight_sources()],
            "stay_sources": [s.name for s in stay_sources()],
            "buckets": [
                {
                    "bucket": bucket,
                    "interval_seconds": interval,
                    "quarantine_seconds": round(max(0, until - time.time()), 1),
                }
                for bucket, interval, until in self.app.limiter.overview()
            ],
        }


def traced(tool, path: str | None):
    """Write every call, its arguments and its answer to a file: what the agent asked and what it was told."""
    if not path:
        return tool

    @functools.wraps(tool)
    async def wrapper(*args, **kwargs):
        entry = {"tool": tool.__name__, "arguments": inspect.signature(tool).bind(*args, **kwargs).arguments}
        try:
            result = await tool(*args, **kwargs)
            entry["result"] = (
                [x if isinstance(x, str) else "<image>" for x in result] if isinstance(result, list) else result
            )
            return result
        except Exception as exc:
            entry["error"] = str(exc)
            raise
        finally:
            with open(path, "a") as file:
                file.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")

    return wrapper


TRIP = (
    "Whole trip in one call: flights from `origin` (IATA codes, e.g. BEG or a city code such as MOW) to the airports "
    "nearest to `place` and back, and stays in `place` for the same dates, searched side by side. `place` in Latin "
    "script, with `country` when the name is ambiguous. Returns the place it understood, the airports in reach with "
    "distances and which were searched, flight and stay shortlists each with its own `search_id`, and every source "
    "report. "
)


def create_server(root: Path | None = None, proxy: str | None = None, *, app: App | None = None) -> MCPServer[App]:
    root = Path.cwd() if root is None else Path(root)
    tools = Tools(app)

    @asynccontextmanager
    async def lifespan(server):
        tools.app = app if app is not None else build(root, proxy)
        try:
            yield tools.app
        finally:
            await tools.app.close()

    server = MCPServer("travel-ops", version="0.1.0", instructions=DESCRIPTION, lifespan=lifespan)
    trace = os.environ.get("TRAVELOPS_TRACE")
    server.add_tool(traced(tools.search_trip, trace), description=TRIP + DESCRIPTION, annotations=READ_ONLY)
    server.add_tool(traced(tools.search_flights, trace), description=DESCRIPTION, annotations=READ_ONLY)
    server.add_tool(traced(tools.search_stays, trace), description=DESCRIPTION, annotations=READ_ONLY)
    server.add_tool(
        traced(tools.refine_flights, trace),
        description=REFINE + "Times are local to the departure airport, HH:MM. `airlines` keeps only these carrier "
        "codes, `destination` only flights landing at these airports, `checked_bag=true` only fares that include "
        "one, `max_price` is in the currency of the result. `sort`: price, duration or departure.",
        annotations=LOCAL,
    )
    server.add_tool(
        traced(tools.refine_stays, trace),
        description=REFINE + "`max_total` is for the whole stay in the currency of the result, with the stated "
        "taxes and charges. `min_reviews` drops ratings that rest on a handful of reviews. Kinds: hotel, apartment, "
        "room, house, shared_room (a bed in a dormitory), other (the source does not say). `max_center_km` is the "
        "distance from the centre. `free_cancellation=true` keeps stays that state it. `must_have` names amenities "
        "(wifi, kitchen, parking, ac, washer, breakfast, pool, balcony, workspace, elevator, pets, or any word of a "
        "page's list); only a stay whose page `stay_details` has read can pass, the rest are `hidden_unknown`. "
        "`sort`: price, rating, reviews or center.",
        annotations=LOCAL,
    )
    server.add_tool(
        traced(tools.stay_details, trace),
        description="What the search cards do not say about up to five stays of a search: every amenity, exact "
        "address and coordinates, check-in times, house rules, review subscores, more photo links. `stays` are "
        "names or source ids from that search. One request to a property page per stay, about ten seconds apart, "
        "so ask only for the stays you are about to recommend; a page already read is answered from memory. Each "
        "stay has its own `status`. No booking, no login. After it, `refine_stays` can filter by `must_have`.",
        annotations=READ_ONLY,
    )
    server.add_tool(
        traced(tools.stay_photos, trace),
        description="Look at the photos of up to five stays of a search: returns the images themselves, each after "
        "a line with its link, so that you can judge what the human asked about (light, view, state of the "
        "bathroom, what the bed is). `per_stay` from 1 to 4. A search card carries one to a few photos; a stay "
        "whose page `stay_details` has read has its whole gallery. Describe only what a photo shows, and give the "
        "human the links: they cannot see the images you were sent.",
        annotations=READ_ONLY,
        structured_output=False,
    )
    server.add_tool(
        traced(tools.airports_near, trace),
        description="Find a place and the airports near it, nearest first, with distances in km. Use it instead of "
        "guessing which airport serves a town. One request to a geocoder, none to travel sites.",
        annotations=ToolAnnotations(
            read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True
        ),
    )
    server.add_tool(
        traced(tools.sources, trace),
        description="List known sources, request spacing and remaining quarantine. No network requests.",
        annotations=LOCAL,
    )
    return server
