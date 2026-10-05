"""Read-only MCP tools over one application lifetime."""

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from .app import App, build, flight_query, stay_query, flight_sources, stay_sources
from .rates import load_rates
from .search import search_flights, search_stays, estimate_flights, estimate_stays
from .serialize import flight_search_json, shortlist, stay_search_json

DESCRIPTION = (
    "Search live prices with seen_at timestamps. Never present a price absent from this result. "
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
    ) -> dict[str, Any]:
        app = self.app
        try:
            self._limit(limit)
            query = flight_query(app.profile, origin, destination, depart, return_date, flex_days, adults, cabin)
            selected = flight_sources(sources)
            estimate = estimate_flights(query, selected, app.limiter, app.net.exit)
            if estimate > 120 and not confirm:
                return confirmation(estimate)
            currency = self._currency(currency)
            rates = await load_rates(app.net)
            result = shortlist(
                flight_search_json(await search_flights(query, selected, app.ctx, rates, currency), rates), limit
            )
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
    ) -> dict[str, Any]:
        app = self.app
        try:
            self._limit(limit)
            query = stay_query(app.profile, place, checkin, checkout, adults)
            selected = stay_sources(sources)
            estimate = estimate_stays(query, selected, app.limiter, app.net.exit)
            if estimate > 120 and not confirm:
                return confirmation(estimate)
            currency = self._currency(currency)
            rates = await load_rates(app.net)
            result = shortlist(
                stay_search_json(await search_stays(query, selected, app.ctx, rates, currency), rates), limit
            )
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
    ) -> dict[str, Any]:
        return await Tools(ctx.request_context.lifespan_context).search_flights(
            origin, destination, depart, return_date, flex_days, adults, cabin, sources, currency, confirm, limit
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
    ) -> dict[str, Any]:
        return await Tools(ctx.request_context.lifespan_context).search_stays(
            place, checkin, checkout, adults, sources, currency, confirm, limit
        )

    @server.tool(
        description="List known sources, request spacing and remaining quarantine. No network requests.",
        annotations=ToolAnnotations(
            read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
        ),
    )
    async def sources(ctx: Context[App]) -> dict[str, Any]:
        return Tools(ctx.request_context.lifespan_context).sources()

    return server
