"""Read-only MCP tools over one application lifetime."""

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from .app import App, build, flight_query, stay_query, flight_sources, stay_sources
from .geo import airports_near, locate, place_json
from .rates import load_rates
from .search import search_flights, search_stays, estimate_flights, estimate_stays
from .serialize import drop_long_routes, drop_low_rated, flight_search_json, leg_options, shortlist, stay_search_json
from .trip import plan_trip, search_trip

DESCRIPTION = (
    "Search live prices with seen_at timestamps. Dates are YYYY-MM-DD; airports are IATA codes; stay ratings are "
    "on a 0-10 scale. Never present a price absent from this result. "
    "Report the sources block and every limitation to the user. No booking, checkout, passenger data or login. "
    "If needs_confirmation is true, ask the human before calling again with confirm=True. "
    "Results are the cheapest `limit` cards; `shown` says how many exist. Raise `limit` only when the human needs more."
)
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=False, open_world_hint=True)


def confirmation(estimate):
    return {
        "needs_confirmation": True,
        "estimate_seconds": round(estimate, 1),
        "message": f"This search needs about {estimate:.0f} seconds of request spacing, plus response time. Ask the human to approve before passing confirm=True.",
    }


class Tools:
    def __init__(self, app: App):
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
    ) -> dict[str, Any]:
        app = self.app
        try:
            self._limit(limit)
            query = flight_query(app.profile, origin, destination, depart, return_date, flex_days, adults, cabin)
            selected = flight_sources(sources)
            estimate = estimate_flights(query, selected, app.limiter, app.net.exit)
            if estimate > app.profile.confirm_over_seconds and not confirm:
                return confirmation(estimate)
            currency = self._currency(currency)
            rates = await load_rates(app.net)
            result = flight_search_json(await search_flights(query, selected, app.ctx, rates, currency), rates)
            if max_stops is not None:
                drop_long_routes(result, max_stops)
            leg_options(result)
            shortlist(result, limit)
            self._selection(result, sources)
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
        sources: list[str] | None = None,
        currency: str | None = None,
        confirm: bool = False,
        limit: int = 10,
        min_rating: float | None = None,
    ) -> dict[str, Any]:
        app = self.app
        try:
            self._limit(limit)
            query = stay_query(app.profile, place, checkin, checkout, adults)
            selected = stay_sources(sources)
            estimate = estimate_stays(query, selected, app.limiter, app.net.exit)
            if estimate > app.profile.confirm_over_seconds and not confirm:
                return confirmation(estimate)
            currency = self._currency(currency)
            rates = await load_rates(app.net)
            result = stay_search_json(await search_stays(query, selected, app.ctx, rates, currency), rates)
            if min_rating is not None:
                drop_low_rated(result, min_rating)
            shortlist(result, limit)
            self._selection(result, sources)
            return result
        except ValueError as exc:
            raise ToolError(f"Invalid stay search: {exc}") from exc
        except TimeoutError as exc:
            raise ToolError("Exchange-rate feed timed out before search; try later") from exc

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
            estimate = plan.estimate(app)
            if estimate > app.profile.confirm_over_seconds and not confirm:
                return dict(
                    confirmation(estimate), place=place_json(plan.place), airports=list(plan.flights.destinations)
                )
            currency = self._currency(currency)
            return await search_trip(
                app, plan, await load_rates(app.net), currency, limit=limit, max_stops=max_stops, min_rating=min_rating
            )
        except ValueError as exc:
            raise ToolError(f"Invalid trip search: {exc}") from exc

    def _currency(self, value):
        value = self.app.profile.currency if value is None else value
        if not isinstance(value, str) or len(value) != 3 or not value.isalpha():
            raise ValueError("currency must be a three-letter code")
        return value.upper()

    @staticmethod
    def _selection(result, sources):
        if sources is not None:
            for report in result["sources"]:
                report["notes"].append("source selection: " + ", ".join(sources))

    def sources(self) -> dict[str, Any]:
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


def create_server(root: Path | None = None, proxy: str | None = None, *, app: App | None = None) -> MCPServer[App]:
    root = Path.cwd() if root is None else Path(root)

    @asynccontextmanager
    async def lifespan(server):
        shared = app if app is not None else build(root, proxy)
        try:
            yield shared
        finally:
            await shared.close()

    server = MCPServer("travel-ops", version="0.1.0", instructions=DESCRIPTION, lifespan=lifespan)

    @server.tool(description=DESCRIPTION, annotations=READ_ONLY)
    async def search_flights(
        origin: str,
        destination: str,
        depart: str,
        ctx: Context[App],
        return_date: str | None = None,
        flex_days: int = 0,
        adults: int | None = None,
        cabin: str | None = None,
        sources: list[str] | None = None,
        currency: str | None = None,
        confirm: bool = False,
        limit: int = 10,
        max_stops: int | None = None,
    ) -> dict[str, Any]:
        return await Tools(ctx.request_context.lifespan_context).search_flights(
            origin,
            destination,
            depart,
            return_date,
            flex_days,
            adults,
            cabin,
            sources,
            currency,
            confirm,
            limit,
            max_stops,
        )

    @server.tool(description=DESCRIPTION, annotations=READ_ONLY)
    async def search_stays(
        place: str,
        checkin: str,
        checkout: str,
        ctx: Context[App],
        adults: int | None = None,
        sources: list[str] | None = None,
        currency: str | None = None,
        confirm: bool = False,
        limit: int = 10,
        min_rating: float | None = None,
    ) -> dict[str, Any]:
        return await Tools(ctx.request_context.lifespan_context).search_stays(
            place, checkin, checkout, adults, sources, currency, confirm, limit, min_rating
        )

    @server.tool(
        description="Whole trip in one call: flights from `origin` (IATA codes, e.g. BEG or a city code such as MOW) to the airports nearest to `place` and "
        "back, and stays in `place` for the same dates, searched side by side. `place` in Latin script, with `country` "
        "when the name is ambiguous. Returns the place it understood, the airports in reach with distances and which "
        "were searched, flight and stay shortlists, and every source report. " + DESCRIPTION,
        annotations=READ_ONLY,
    )
    async def search_trip(
        origin: str,
        place: str,
        depart: str,
        return_date: str,
        ctx: Context[App],
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
    ) -> dict[str, Any]:
        return await Tools(ctx.request_context.lifespan_context).search_trip(
            origin,
            place,
            depart,
            return_date,
            country,
            airports,
            max_airports,
            adults,
            stay_adults,
            cabin,
            max_stops,
            min_rating,
            currency,
            confirm,
            limit,
        )

    @server.tool(
        description="Find a place and the airports near it, nearest first, with distances in km. Use it instead of "
        "guessing which airport serves a town. One request to a geocoder, none to travel sites.",
        annotations=ToolAnnotations(
            read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True
        ),
    )
    async def airports_near(
        place: str, ctx: Context[App], country: str | None = None, radius_km: int = 100
    ) -> dict[str, Any]:
        return await Tools(ctx.request_context.lifespan_context).airports_near(place, country, radius_km)

    @server.tool(
        description="List known sources, request spacing and remaining quarantine. No network requests.",
        annotations=ToolAnnotations(
            read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
        ),
    )
    async def sources(ctx: Context[App]) -> dict[str, Any]:
        return Tools(ctx.request_context.lifespan_context).sources()

    return server
