"""Read-only MCP tools over one application lifetime."""

import asyncio
import functools
import inspect
import json
import os
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any
from mcp.server import MCPServer
from mcp.server.mcpserver import Image
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from . import recall
from .app import App, build, flight_query, ground_query, ground_sources, stay_query, flight_sources, stay_sources
from .combine import separate_tickets as pair_tickets
from .details import read_details, read_photos
from .geo import airports_near, locate, place_json, with_roads
from .rates import load_rates
from .search import estimate_flights, estimate_ground, estimate_stays
from .trip import plan_trip, search_trip
from .views import flights_view, ground_view, stays_view
from .watch import checked_arguments, watch_json

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
        children_ages: list[int] | None = None,
        cabin: str | None = None,
        sources: list[str] | None = None,
        currency: str | None = None,
        confirm: bool = False,
        limit: int = 10,
        max_stops: int | None = None,
        separate_tickets: bool = False,
        refresh: bool = False,
    ) -> dict[str, Any]:
        app = self.app
        try:
            self._limit(limit)
            query = flight_query(
                app.profile, origin, destination, depart, return_date, flex_days, adults, cabin, children_ages
            )
            queries = [query]
            if separate_tickets:
                if query.return_ is None:
                    raise ValueError("separate tickets need a return date: a one-way trip is one ticket already")
                if query.flex_days:
                    raise ValueError("separate tickets with flexible dates are too many searches at once: choose one")
                queries += [
                    replace(query, return_=None),
                    replace(
                        query,
                        origins=query.destinations,
                        destinations=query.origins,
                        depart=query.return_,
                        return_=None,
                    ),
                ]
            selected = flight_sources(sources)
            currency = self._currency(currency)
            # Searches of one call wait in the same queues: their times add up.
            estimate = sum(
                estimate_flights(q, selected, app.limiter, app.net.exit)
                for q in queries
                if refresh or not recall.remembered(app, "flights", q, selected, currency)
            )
            if estimate > app.profile.confirm_over_seconds and not confirm:
                return confirmation(estimate)
            rates = await load_rates(app.net)
            found = await asyncio.gather(
                *(
                    recall.flights(
                        app, q, selected, rates, currency, refresh=refresh, selection=sources, sharing=len(queries)
                    )
                    for q in queries
                )
            )
            now = app.results.clock()

            def view(stored, cards):
                shown = flights_view(
                    stored.result,
                    limit=cards,
                    max_stops=app.profile.max_stops if max_stops is None else max_stops,
                    max_leg_hours=app.profile.max_leg_hours,
                    avoid_airlines=app.profile.avoid_airlines,
                )
                return stored.stamp(shown, now)

            result = view(found[0], limit)
            if separate_tickets:
                result["separate_tickets"] = pair_tickets(view(found[1], 12), view(found[2], 12), result, limit)
            return result
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
        children_ages: list[int] | None = None,
        sources: list[str] | None = None,
        currency: str | None = None,
        confirm: bool = False,
        limit: int = 10,
        min_rating: float | None = None,
        max_center_km: float | None = None,
        refresh: bool = False,
    ) -> dict[str, Any]:
        app = self.app
        try:
            self._limit(limit)
            query = stay_query(app.profile, place, checkin, checkout, adults, children_ages)
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
                max_center_km=app.profile.stays.max_center_km if max_center_km is None else max_center_km,
                details=app.results.details,
            )
            return stored.stamp(view, app.results.clock())
        except ValueError as exc:
            raise ToolError(f"Invalid stay search: {exc}") from exc
        except TimeoutError as exc:
            raise ToolError("Exchange-rate feed timed out before search; try later") from exc

    async def search_ground(
        self,
        origin: str,
        destination: str,
        depart: str,
        adults: int | None = None,
        children_ages: list[int] | None = None,
        modes: list[str] | None = None,
        sources: list[str] | None = None,
        currency: str | None = None,
        confirm: bool = False,
        limit: int = 10,
        refresh: bool = False,
    ) -> dict[str, Any]:
        app = self.app
        try:
            self._limit(limit)
            query = ground_query(app.profile, origin, destination, depart, adults, children_ages, modes)
            selected = ground_sources(sources)
            currency = self._currency(currency)
            if refresh or not recall.remembered(app, "ground", query, selected, currency):
                estimate = estimate_ground(query, selected, app.limiter, app.net.exit)
                if estimate > app.profile.confirm_over_seconds and not confirm:
                    return confirmation(estimate)
            stored = await recall.ground(
                app, query, selected, await load_rates(app.net), currency, refresh=refresh, selection=sources
            )
            return stored.stamp(ground_view(stored.result, limit=limit), app.results.clock())
        except ValueError as exc:
            raise ToolError(f"Invalid ground search: {exc}") from exc
        except TimeoutError as exc:
            raise ToolError("Exchange-rate feed timed out before search; try later") from exc

    async def refine_ground(
        self,
        search_id: str,
        limit: int = 10,
        modes: list[str] | None = None,
        depart_after: str | None = None,
        depart_before: str | None = None,
        max_changes: int | None = None,
        max_price: float | None = None,
        sources: list[str] | None = None,
        sort: str = "price",
    ) -> dict[str, Any]:
        try:
            self._limit(limit)
            stored = self._stored(search_id, "ground")
            view = ground_view(
                stored.result,
                limit=limit,
                modes=modes,
                depart_after=depart_after,
                depart_before=depart_before,
                max_changes=max_changes,
                max_price=max_price,
                sources=sources,
                sort=sort,
            )
            return stored.stamp(view, self.app.results.clock())
        except ValueError as exc:
            raise ToolError(f"Invalid ground view: {exc}") from exc

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
        max_connection_hours: float | None = None,
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
                max_connection_hours=max_connection_hours,
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
        no_hostels: bool = False,
        min_bedrooms: int | None = None,
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
                max_center_km=self.app.profile.stays.max_center_km if max_center_km is None else max_center_km,
                min_reviews=min_reviews,
                max_total=max_total,
                kinds=kinds,
                exclude_kinds=exclude_kinds,
                no_hostels=no_hostels,
                min_bedrooms=min_bedrooms,
                sources=sources,
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
        airports, roads = await with_roads(
            self.app.net, here.lat, here.lon, airports_near(here.lat, here.lon, radius_km)
        )
        return {
            "place": place_json(here),
            "other_places_with_this_name": [place_json(p) for p in places[1:4]],
            "airports": airports,
            "roads": roads,
            "note": "Sorted by straight-line distance. Scheduled service is not known here; a flight search tells.",
        }

    async def search_trip(
        self,
        origin: str,
        place: str,
        depart: str,
        return_date: str | None = None,
        checkout: str | None = None,
        flex_days: int = 0,
        separate_tickets: bool = False,
        country: str | None = None,
        airports: str | None = None,
        max_airports: int = 2,
        adults: int | None = None,
        stay_adults: int | None = None,
        children_ages: list[int] | None = None,
        cabin: str | None = None,
        max_stops: int | None = None,
        min_rating: float | None = None,
        max_center_km: float | None = None,
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
                checkout=checkout,
                flex_days=flex_days,
                separate=separate_tickets,
                country=country,
                airports=airports,
                max_airports=max_airports,
                adults=adults,
                stay_adults=stay_adults,
                children_ages=children_ages,
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
                max_center_km=max_center_km,
                refresh=refresh,
            )
        except ValueError as exc:
            raise ToolError(f"Invalid trip search: {exc}") from exc

    def _currency(self, value):
        value = self.app.profile.currency if value is None else value
        if not isinstance(value, str) or len(value) != 3 or not value.isalpha():
            raise ValueError("currency must be a three-letter code")
        return value.upper()

    def _watches(self):
        if self.app.watches is None:
            raise ValueError("this server keeps no watches (a replay has none)")
        return self.app.watches

    async def watch_price(
        self,
        kind: str,
        arguments: dict[str, Any],
        filters: dict[str, Any] | None = None,
        below: float | None = None,
        drop_percent: float = 5.0,
        every_hours: float = 6.0,
        currency: str | None = None,
    ) -> dict[str, Any]:
        try:
            watches = self._watches()
            if kind not in ("flights", "stays", "ground"):
                raise ValueError("kind must be flights, stays or ground")
            search, refine = getattr(self, f"search_{kind}"), getattr(self, f"refine_{kind}")
            arguments = checked_arguments(search, arguments, "arguments")
            filters = checked_arguments(refine, filters or {}, "filters", partial=True)
            if "search_id" in filters:
                raise ValueError("filters cannot set search_id: each check is a new search")
            # The query must make sense now, not at three in the morning.
            if kind == "flights":
                flight_query(
                    self.app.profile,
                    arguments["origin"],
                    arguments["destination"],
                    arguments["depart"],
                    arguments.get("return_date"),
                    arguments.get("flex_days", 0),
                    arguments.get("adults"),
                    arguments.get("cabin"),
                    arguments.get("children_ages"),
                )
            elif kind == "ground":
                ground_query(
                    self.app.profile,
                    arguments["origin"],
                    arguments["destination"],
                    arguments["depart"],
                    arguments.get("adults"),
                    arguments.get("children_ages"),
                    arguments.get("modes"),
                )
            else:
                stay_query(
                    self.app.profile,
                    arguments["place"],
                    arguments["checkin"],
                    arguments["checkout"],
                    arguments.get("adults"),
                    arguments.get("children_ages"),
                )
            watch = watches.add(kind, arguments, filters, self._currency(currency), below, drop_percent, every_hours)
        except (ValueError, KeyError) as exc:
            raise ToolError(f"Invalid watch: {exc}") from exc
        return dict(
            watch_json(watch, watches),
            note="Saved. It runs only where `travelops watch run` is scheduled; the first check sets the price "
            "the next alerts are measured against.",
        )

    async def watches(self, include_stopped: bool = False) -> dict[str, Any]:
        try:
            watches = self._watches()
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        return {"watches": [watch_json(w, watches) for w in watches.list(ended=include_stopped)]}

    async def watch_alerts(self, take: bool = True) -> dict[str, Any]:
        try:
            watches = self._watches()
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        return {
            "alerts": watches.pending(take),
            "note": "Each alert is given out once with take=true: tell the human now. Its price is as old as "
            "`seen_at`; search again before recommending a booking.",
        }

    async def stop_watch(self, watch_id: str) -> dict[str, Any]:
        try:
            watches = self._watches()
            return watch_json(watches.stop(watch_id), watches)
        except ValueError as exc:
            raise ToolError(f"Invalid watch: {exc}") from exc

    async def sources(self) -> dict[str, Any]:
        import time

        return {
            "flight_sources": [s.name for s in flight_sources()],
            "stay_sources": [s.name for s in stay_sources()],
            "ground_sources": [s.name for s in ground_sources()],
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


WATCH = (
    "Save a search to repeat on its own and alert when the price falls. `kind` is flights, stays or ground; "
    "`arguments` are exactly what that search tool takes (without refresh, confirm, limit, currency); `filters` "
    "are what its refine tool takes (without search_id), applied to every check, so the watched "
    "price is the cheapest that passes them. An alert comes when the cheapest is `drop_percent` below the price "
    "last told (the first check, then each alert), or first reaches `below` (in `currency`). Checks run every "
    "`every_hours` (at least 3) until the departure or check-in day, one search at a time, only where "
    "`travelops watch run` is scheduled. Alerts wait for `watch_alerts`. Saving makes no request to any site. "
    "Ask the human before saving one. "
)
GROUND = (
    "Trains and buses between two places on one day, one way: for a return, search the other way on its day. "
    "Places are names in Latin script (Belgrade, Sarajevo, Moscow); each source says which place it understood, "
    "and stations name where a ride really starts and ends. `modes`: train, bus, ferry, van (a shared minibus), "
    "default train and bus. Prices are for the whole party. A train price is `price_from`: the cheapest class "
    "for everyone, which the party may not all get; `classes_per_seat` gives one seat in each class (seat, "
    "open_berth, compartment, sleeper). Sources: tutu (Russia and the CIS, some international buses), 12go "
    "(Turkey, south-east Asia, parts of the Balkans, ferries). Flights are not here: use the flight tools. "
)
TRIP = (
    "Whole trip in one call: flights from `origin` (IATA codes, e.g. BEG or a city code such as MOW) to the airports "
    "nearest to `place` and back, and stays in `place` for the same dates, searched side by side. `place` in Latin "
    "script, with `country` when the name is ambiguous. Returns the place it understood, the airports in reach with "
    "distances and which were searched, flight and stay shortlists each with its own `search_id`, and every source "
    "report. Shapes: omit `return_date` for a one-way trip (then `checkout` is the last day of the stay, or no stay "
    "is searched); `checkout` also sets a stay that ends on another day than the flight back; `flex_days` (1 to 3) "
    "tries the same trip shifted by that many days each way, flights only; `separate_tickets=true` also searches "
    "each direction one way and returns the cheapest pairs of two tickets, including into one airport and out of "
    "another, which roughly triples the time; `children_ages` lists the age of every child on the travel dates. "
)
PARTY = (
    "`children_ages` lists the age of every child (0 to 17) on the travel dates; `adults` counts the grown-ups only. "
    "Sources that cannot price that party say so in their status. "
)
SEPARATE = (
    "`separate_tickets=true` (round trips only) also searches each direction one way and adds `separate_tickets`: "
    "the cheapest pairs of two tickets, with what they save against the cheapest round trip. About three times "
    "the waiting. "
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
    server.add_tool(
        traced(tools.search_flights, trace), description=DESCRIPTION + " " + SEPARATE + PARTY, annotations=READ_ONLY
    )
    server.add_tool(traced(tools.search_stays, trace), description=DESCRIPTION + " " + PARTY, annotations=READ_ONLY)
    server.add_tool(traced(tools.search_ground, trace), description=GROUND + DESCRIPTION, annotations=READ_ONLY)
    server.add_tool(
        traced(tools.refine_ground, trace),
        description=REFINE + "Times are local to the station, HH:MM. `modes` keeps only these (train, bus, ferry, "
        "van); `max_changes` 0 keeps direct rides; `max_price` is in the currency of the result. `sort`: price, "
        "duration or departure; a ride whose source gives local times only has no duration and sorts last.",
        annotations=LOCAL,
    )
    server.add_tool(
        traced(tools.refine_flights, trace),
        description=REFINE + "Times are local to the departure airport, HH:MM. `airlines` keeps only these carrier "
        "codes, `destination` only flights landing at these airports, `checked_bag=true` only fares that include "
        "one, `max_price` is in the currency of the result, `max_connection_hours` drops itineraries with a longer "
        "wait between two flights of one leg. `sort`: price, duration or departure. On a one-way "
        "search the `depart_*` filters are its times, whichever direction it flies.",
        annotations=LOCAL,
    )
    server.add_tool(
        traced(tools.refine_stays, trace),
        description=REFINE + "`max_total` is for the whole stay in the currency of the result, with the stated "
        "taxes and charges. `min_reviews` drops ratings that rest on a handful of reviews. Kinds: hotel, apartment, "
        "room, house, shared_room (a bed in a dormitory), other (the source does not say). `no_hostels=true` drops "
        "every stay whose name or link says hostel, private rooms included. `min_bedrooms` reads the bedrooms from "
        "the room name (a studio has none apart); a name that does not say is `hidden_unknown`. `max_center_km` is the "
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
        description="Find a place and the airports near it, nearest first, with the straight-line distance and, for "
        "the nearest four, the drive (`km_road`, `minutes_road`). Use it instead of guessing which airport serves a "
        "town. One request to a geocoder and one to a routing service, none to travel sites.",
        annotations=ToolAnnotations(
            read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True
        ),
    )
    server.add_tool(
        traced(tools.watch_price, trace),
        description=WATCH,
        annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False
        ),
    )
    server.add_tool(
        traced(tools.watches, trace),
        description="List the price watches with their last checks (newest first). No network requests.",
        annotations=LOCAL,
    )
    server.add_tool(
        traced(tools.watch_alerts, trace),
        description="Price drops found by the watches since the last collection, oldest first: what, the price "
        "and the one last told, why it is worth telling, seller, link, `search_id`, when it was seen. With "
        "take=true (default) each alert is given out once, so the assistant that collects them is the one that "
        "tells the human; take=false only looks. No network requests.",
        annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False
        ),
    )
    server.add_tool(
        traced(tools.stop_watch, trace),
        description="Stop a price watch by `watch_id`. Its checks stay listed with include_stopped=true.",
        annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False
        ),
    )
    server.add_tool(
        traced(tools.sources, trace),
        description="List known sources, request spacing and remaining quarantine. No network requests.",
        annotations=LOCAL,
    )
    return server
