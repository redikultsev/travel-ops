"""A whole trip in one search: flights to the airports that serve a place and back, and stays there for the same
dates. Flights and stays run side by side; every source still reports for itself."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date

from .app import App
from .combine import separate_tickets
from .core.flights import FlightQuery
from .core.stays import StayQuery
from .geo import Place, airports_near, centre, locate, place_json, with_roads
from .kinds import flight_query, stay_query
from .searches import Searches


@dataclass
class TripPlan:
    place: Place
    alternatives: list[Place]
    airports: list[dict]  # every airport in reach, nearest first
    flights: FlightQuery
    stays: StayQuery | None  # None when no night is asked for: a way there with no date to leave
    one_ways: tuple[FlightQuery, FlightQuery] | None = None  # out and back, when separate tickets are compared
    roads: str = ""  # where the road distances come from, or why there are none
    center: dict | None = None  # where stays are measured from (`geo.centre`); the place's own point when unset

    def flight_queries(self) -> list[FlightQuery]:
        return [self.flights, *(self.one_ways or ())]

    def estimate(self, searches: Searches, currency: str | None = None, refresh: bool = False) -> float:
        """Seconds of request spacing ahead. A part that memory already answers costs nothing. Flight searches
        of one trip wait in the same queues, so their times add up; stays run beside them."""
        flights = searches.estimate("flights", self.flight_queries(), currency=currency, refresh=refresh)
        stays = searches.estimate("stays", [self.stays], currency=currency, refresh=refresh) if self.stays else 0.0
        return max(flights, stays)


async def plan_trip(
    app: App,
    origin: str | None,
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
    candidates = await locate(app.net, place, country)
    if not candidates:
        raise ValueError(
            f"place not found: {place!r}"
            + (f" in {country!r}; check the country, in English or as a two-letter code" if country else "")
            + "; write the place in Latin script"
        )
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
    middle = await centre(app.net, here) if stays else None
    return TripPlan(
        here, candidates[1:4], reach, flights, stays, flights.one_ways() if separate else None, roads, middle
    )


async def search_trip(
    searches: Searches,
    plan: TripPlan,
    currency: str,
    *,
    limit: int = 5,
    max_stops: int | None = None,
    min_rating: float | None = None,
    max_center_km: float | None = None,
    refresh: bool = False,
) -> dict:
    """The plan searched: flights and stays side by side, each from memory when it can be. The plan was confirmed
    as a whole, so neither part asks again."""
    flights = searches.run("flights", plan.flight_queries(), currency=currency, refresh=refresh, confirm=True)
    if plan.stays:
        center = plan.center or {"name": plan.place.label(), "lat": plan.place.lat, "lon": plan.place.lon}
        stays = searches.run(
            "stays", [plan.stays], currency=currency, refresh=refresh, confirm=True, context={"center": center}
        )
        found, (stayed,) = await asyncio.gather(flights, stays)
    else:
        found, stayed = await flights, None
    now = searches.app.results.clock()

    def flight_view(stored, cards):
        return searches.view("flights", stored, cards, now=now, max_stops=max_stops)

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
    if stayed:
        result["stays"] = searches.view(
            "stays", stayed, limit, now=now, min_rating=min_rating, max_center_km=max_center_km
        )
        if plan.flights.flex_days:
            not_included.append("stays for the shifted dates: stays were searched for the asked dates only")
    else:
        result["stays"] = None
        not_included.append("a stay: no date to leave was given; pass `checkout` to search one")
    result["not_included"] = not_included
    return result
