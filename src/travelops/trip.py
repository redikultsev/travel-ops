"""A whole trip in one search: flights to the airports that serve a place and back, and stays there for the same
dates. Flights and stays run side by side; every source still reports for itself."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from .app import App, flight_query, flight_sources, stay_query, stay_sources
from .core.flights import FlightQuery
from .core.money import Rates
from .core.stays import StayQuery
from .geo import Place, airports_near, locate, place_json
from .search import estimate_flights, estimate_stays, search_flights, search_stays
from .serialize import drop_long_routes, drop_low_rated, flight_search_json, leg_options, shortlist, stay_search_json


@dataclass
class TripPlan:
    place: Place
    alternatives: list[Place]
    airports: list[dict]  # every airport in reach, nearest first
    flights: FlightQuery
    stays: StayQuery

    def estimate(self, app: App) -> float:
        return max(
            estimate_flights(self.flights, flight_sources(), app.limiter, app.net.exit),
            estimate_stays(self.stays, stay_sources(), app.limiter, app.net.exit),
        )


async def plan_trip(
    app: App,
    origin: str,
    place: str,
    depart,
    return_date,
    *,
    country: str | None = None,
    airports: str | None = None,
    max_airports: int = 2,
    radius_km: float = 100,
    adults: int | None = None,
    stay_adults: int | None = None,
    cabin: str | None = None,
) -> TripPlan:
    if return_date is None:
        raise ValueError("a trip needs both dates; search flights alone for a one-way journey")
    if not 1 <= max_airports <= 3:
        raise ValueError("max_airports must be from 1 to 3")
    candidates = await locate(app.net, place, country)
    if not candidates:
        raise ValueError(f"place not found: {place!r}; write it in Latin script and add the country")
    here = candidates[0]
    reach = airports_near(here.lat, here.lon, radius_km)
    chosen = airports or ",".join(a["iata"] for a in reach[:max_airports])
    if not chosen:
        raise ValueError(f"no airport within {radius_km:.0f} km of {here.label()}; pass `airports` yourself")
    flights = flight_query(app.profile, origin, chosen, depart, return_date, 0, adults, cabin)
    # The people who fly are the people who stay, unless the human says otherwise.
    stays = stay_query(
        app.profile,
        here.label(),
        flights.depart,
        flights.return_,
        stay_adults if stay_adults is not None else flights.adults,
    )
    return TripPlan(here, candidates[1:4], reach, flights, stays)


async def search_trip(
    app: App,
    plan: TripPlan,
    rates: Rates,
    currency: str,
    *,
    limit: int = 5,
    max_stops: int | None = None,
    min_rating: float | None = None,
) -> dict:
    flights, stays = await asyncio.gather(
        search_flights(plan.flights, flight_sources(), app.ctx, rates, currency),
        search_stays(plan.stays, stay_sources(), app.ctx, rates, currency),
    )
    flight_result = flight_search_json(flights, rates)
    drop_long_routes(flight_result, app.profile.max_stops if max_stops is None else max_stops)
    leg_options(flight_result)
    stay_result = stay_search_json(stays, rates)
    drop_low_rated(stay_result, app.profile.stays.min_rating if min_rating is None else min_rating)
    searched = set(plan.flights.destinations)
    return {
        "place": place_json(plan.place),
        "other_places_with_this_name": [place_json(p) for p in plan.alternatives],
        "airports_in_reach": [dict(a, searched=a["iata"] in searched) for a in plan.airports],
        "flights": shortlist(flight_result, limit),
        "stays": shortlist(stay_result, limit),
        "not_included": [
            "transfer between the airport and the place (km_straight is a straight line; the road is longer)",
            "anything the sources do not price",
        ],
    }
