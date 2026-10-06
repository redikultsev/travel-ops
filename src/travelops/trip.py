"""A whole trip in one search: flights to the airports that serve a place and back, and stays there for the same
dates. Flights and stays run side by side; every source still reports for itself."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import date

from . import recall
from .app import App, flight_query, flight_sources, stay_query, stay_sources
from .combine import separate_tickets
from .core.flights import FlightQuery
from .core.money import Rates
from .core.stays import StayQuery
from .geo import Place, airports_near, locate, place_json, with_roads
from .search import estimate_flights, estimate_stays
from .views import flights_view, stays_view


@dataclass
class TripPlan:
    place: Place
    alternatives: list[Place]
    airports: list[dict]  # every airport in reach, nearest first
    flights: FlightQuery
    stays: StayQuery | None  # None when no night is asked for: a way there with no date to leave
    one_ways: tuple[FlightQuery, FlightQuery] | None = None  # out and back, when separate tickets are compared
    roads: str = ""  # where the road distances come from, or why there are none

    def flight_queries(self) -> list[FlightQuery]:
        return [self.flights, *(self.one_ways or ())]

    def estimate(self, app: App, currency: str | None = None, refresh: bool = False) -> float:
        """Seconds of request spacing ahead. A part that memory already answers costs nothing. Flight searches
        of one trip wait in the same queues, so their times add up; stays run beside them."""

        def known(kind, query, sources):
            return currency is not None and not refresh and recall.remembered(app, kind, query, sources, currency)

        flights = sum(
            0.0
            if known("flights", query, flight_sources())
            else estimate_flights(query, flight_sources(), app.limiter, app.net.exit)
            for query in self.flight_queries()
        )
        stays = (
            0.0
            if self.stays is None or known("stays", self.stays, stay_sources())
            else estimate_stays(self.stays, stay_sources(), app.limiter, app.net.exit)
        )
        return max(flights, stays)


async def plan_trip(
    app: App,
    origin: str,
    place: str,
    depart,
    return_date=None,
    *,
    checkout=None,
    flex_days: int = 0,
    separate: bool = False,
    country: str | None = None,
    airports: str | None = None,
    max_airports: int = 2,
    radius_km: float = 100,
    adults: int | None = None,
    stay_adults: int | None = None,
    children_ages: list[int] | None = None,
    cabin: str | None = None,
) -> TripPlan:
    if not 1 <= max_airports <= 3:
        raise ValueError("max_airports must be from 1 to 3")
    if separate and return_date is None:
        raise ValueError("separate tickets need a return date: a one-way trip is one ticket already")
    if separate and flex_days:
        raise ValueError("separate tickets with flexible dates are too many searches at once: choose one")
    candidates = await locate(app.net, place, country)
    if not candidates:
        raise ValueError(f"place not found: {place!r}; write it in Latin script and add the country")
    here = candidates[0]
    reach, roads = await with_roads(app.net, here.lat, here.lon, airports_near(here.lat, here.lon, radius_km))
    chosen = airports or ",".join(a["iata"] for a in reach[:max_airports])
    if not chosen:
        raise ValueError(f"no airport within {radius_km:.0f} km of {here.label()}; pass `airports` yourself")
    flights = flight_query(app.profile, origin, chosen, depart, return_date, flex_days, adults, cabin, children_ages)
    leave = date.fromisoformat(checkout) if isinstance(checkout, str) else checkout
    leave = leave or flights.return_
    stays = None
    if leave is not None:
        # The people who fly are the people who stay, unless the human says otherwise. With children, the adults
        # of a stay are those older than seventeen: an airline's "adult" starts at twelve.
        grown = flights.adults - sum(1 for age in children_ages or () if age >= 12)
        stays = stay_query(
            app.profile,
            here.label(),
            flights.depart,
            leave,
            stay_adults if stay_adults is not None else grown,
            children_ages,
        )
    one_ways = None
    if separate:
        out = replace(flights, return_=None)
        back = replace(
            flights, origins=flights.destinations, destinations=flights.origins, depart=flights.return_, return_=None
        )
        one_ways = (out, back)
    return TripPlan(here, candidates[1:4], reach, flights, stays, one_ways, roads)


async def search_trip(
    app: App,
    plan: TripPlan,
    rates: Rates,
    currency: str,
    *,
    limit: int = 5,
    max_stops: int | None = None,
    min_rating: float | None = None,
    max_center_km: float | None = None,
    refresh: bool = False,
) -> dict:
    # One list of sources for every flight search of the trip: a source can then keep one session for all of them.
    sources = flight_sources()
    queries = plan.flight_queries()
    found = await asyncio.gather(
        *(recall.flights(app, q, sources, rates, currency, refresh=refresh, sharing=len(queries)) for q in queries),
        *(
            [
                recall.stays(
                    app,
                    plan.stays,
                    stay_sources(),
                    rates,
                    currency,
                    refresh=refresh,
                    center={"name": plan.place.label(), "lat": plan.place.lat, "lon": plan.place.lon},
                )
            ]
            if plan.stays
            else []
        ),
    )
    now = app.results.clock()

    def flight_view(stored, cards):
        view = flights_view(
            stored.result,
            limit=cards,
            max_stops=app.profile.max_stops if max_stops is None else max_stops,
            max_leg_hours=app.profile.max_leg_hours,
            avoid_airlines=app.profile.avoid_airlines,
        )
        return stored.stamp(view, now)

    searched = set(plan.flights.destinations)
    result = {
        "place": place_json(plan.place),
        "other_places_with_this_name": [place_json(p) for p in plan.alternatives],
        "airports_in_reach": [dict(a, searched=a["iata"] in searched) for a in plan.airports],
        "roads": plan.roads,
        "flights": flight_view(found[0], limit),
    }
    not_included = [
        "transfer between the airport and the place: no price; `minutes_road` is a drive without traffic, border "
        "or ferry waiting, and where it is missing `km_straight` is a straight line",
        "anything the sources do not price",
    ]
    if plan.one_ways:
        # Pairs are made from more one-way flights than a shortlist shows, then the views are cut for the answer.
        out, back = flight_view(found[1], 12), flight_view(found[2], 12)
        result["separate_tickets"] = separate_tickets(out, back, result["flights"], limit)
    if plan.stays:
        stays = found[-1]
        result["stays"] = stays.stamp(
            stays_view(
                stays.result,
                limit=limit,
                min_rating=app.profile.stays.min_rating if min_rating is None else min_rating,
                max_center_km=app.profile.stays.max_center_km if max_center_km is None else max_center_km,
                details=app.results.details,
            ),
            now,
        )
        if plan.flights.flex_days:
            not_included.append("stays for the shifted dates: stays were searched for the asked dates only")
    else:
        result["stays"] = None
        not_included.append("a stay: no date to leave was given; pass `checkout` to search one")
    result["not_included"] = not_included
    return result
