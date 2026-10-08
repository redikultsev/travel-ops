"""The kinds of search: flights, stays, and trains and buses. Everything that differs between them lives in its
Kind: how a query is read from the arguments of a search tool, which sources answer it, how it is searched,
stored and shown, and what a watch says of it. Code that serves every kind asks the Kind instead of branching on
its name, so a new kind is one entry here."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Awaitable, Callable

from .core.flights import CABINS, FlightQuery
from .core.ground import MODES, GroundQuery
from .core.stays import StayQuery
from .geo import locate
from .profile import Profile
from .search import estimate, estimate_flights, search_flights, search_ground, search_stays
from .serialize import flight_search_json, ground_search_json, stay_search_json
from .sources import FLIGHT_SOURCES, GROUND_SOURCES, STAY_SOURCES
from .views import flights_view, ground_view, stays_view


def _airports(value):
    codes = tuple(part.strip().upper() for part in value.split(","))
    if not codes or any(len(x) != 3 or not x.isalpha() for x in codes):
        raise ValueError("airports must be three-letter IATA codes, optionally comma-separated")
    return codes


def _positive(value, label, minimum=1):
    if type(value) is not int or value < minimum:
        raise ValueError(f"{label} must be an integer >= {minimum}")
    return value


def _ages(children_ages) -> tuple[int, ...] | None:
    if children_ages is None:
        return None
    if not isinstance(children_ages, (list, tuple)) or any(
        type(a) is not int or not 0 <= a <= 17 for a in children_ages
    ):
        raise ValueError("children_ages must be a list of ages from 0 to 17")
    return tuple(children_ages)


def flight_query(
    profile, origin, destination, depart, return_date=None, flex_days=0, adults=None, cabin=None, children_ages=None
):
    depart = date.fromisoformat(depart) if isinstance(depart, str) else depart
    return_date = date.fromisoformat(return_date) if isinstance(return_date, str) else return_date
    if return_date and return_date < depart:
        raise ValueError("return date must not precede departure")
    cabin = profile.cabin if cabin is None else cabin
    if cabin not in CABINS:
        raise ValueError(f"unknown cabin: {cabin}")
    _positive(flex_days, "flex_days", 0)
    if flex_days > 3:
        raise ValueError("flex_days must be at most 3: every shifted date is a search of its own")
    adults = _positive(profile.travellers.adults if adults is None else adults, "adults")
    ages = _ages(children_ages)
    if ages is None:
        children, infants = profile.travellers.children, profile.travellers.infants
    else:
        # As airlines count: under 2 is an infant on a lap, 2 to 11 a child, 12 and over pays as an adult.
        infants = sum(1 for age in ages if age < 2)
        children = sum(1 for age in ages if 2 <= age < 12)
        adults += sum(1 for age in ages if age >= 12)
    if infants > adults:
        raise ValueError("infants cannot exceed adults")
    if origin is None:  # not named: the profile's home airports, as modes/trip.md asks
        if not profile.home_airports:
            raise ValueError("origin is needed: no home_airports in the profile, so ask the human where from")
        origin = ",".join(profile.home_airports)
    return FlightQuery(
        _airports(origin), _airports(destination), depart, return_date, flex_days, adults, children, infants, cabin
    )


def stay_query(profile, place, checkin, checkout, adults=None, children_ages=None, min_rating=None, max_total=None):
    checkin = date.fromisoformat(checkin) if isinstance(checkin, str) else checkin
    checkout = date.fromisoformat(checkout) if isinstance(checkout, str) else checkout
    if checkout <= checkin:
        raise ValueError("checkout must be after checkin")
    if not isinstance(place, str) or not place.strip():
        raise ValueError("place cannot be empty")
    ages = _ages(children_ages)
    return StayQuery(
        place.strip(),
        checkin,
        checkout,
        _positive(profile.stays.adults if adults is None else adults, "adults"),
        profile.travellers.children if ages is None else len(ages),
        children_ages=ages or (),
        min_rating=_bar(profile.stays.min_rating if min_rating is None else min_rating, "min_rating", 10),
        max_total=_bar(max_total, "max_total"),
    )


def _bar(value, name, top=None):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0 or (top and value > top):
        raise ValueError(f"{name} must be a positive number" + (f" up to {top}" if top else ""))
    return float(value)


def ground_query(profile, origin, destination, depart, adults=None, children_ages=None, modes=None):
    depart = date.fromisoformat(depart) if isinstance(depart, str) else depart
    for label, place in (("origin", origin), ("destination", destination)):
        if not isinstance(place, str) or not place.strip():
            raise ValueError(f"{label} must be a place name")
    modes = ("train", "bus") if modes is None else tuple(dict.fromkeys(modes))
    if not modes or any(m not in MODES for m in modes):
        raise ValueError(f"modes must be some of {', '.join(MODES)}")
    ages = _ages(children_ages)
    return GroundQuery(
        origin.strip(),
        destination.strip(),
        depart,
        _positive(profile.travellers.adults if adults is None else adults, "adults"),
        profile.travellers.children if ages is None else len(ages),
        modes,
    )


async def _center(app, query: StayQuery) -> dict | None:
    """Where the place is, for distances. A geocoder that does not answer costs the distances, not the search."""
    try:
        found = await locate(app.net, query.place)
    except Exception:
        return None
    return {"center": {"name": found[0].label(), "lat": found[0].lat, "lon": found[0].lon}} if found else None


@dataclass(frozen=True)
class Kind:
    name: str  # as tools and stored searches call it
    noun: str  # as a sentence says it: "Invalid flight search"
    registry: dict[str, type]
    read: Callable[[Profile, dict], Any]  # a query from the arguments of the search tool
    search: Callable[..., Awaitable[Any]]  # (query, sources, ctx, rates, currency, *, sharing)
    to_json: Callable[..., dict]  # (search, rates)
    estimate: Callable[..., float]  # (query, sources, limiter, exit_)
    view: Callable[..., dict]  # (app, result, *, limit, **filters)
    bars: Callable[[Profile], dict]  # the filters every view starts from, unless the caller sets them
    label: Callable[[dict], str]  # the search in a few words, from the arguments of its tool
    last_day: str  # the argument holding the day the trip starts: after it there is nothing left to buy
    sample: Callable[[date], Any]  # a query every source should answer, for `doctor --live`
    context: Callable[..., Awaitable[dict | None]] | None = None  # (app, query): kept with a new result

    @property
    def tool(self) -> str:
        return f"search_{self.name}"

    @property
    def refine_tool(self) -> str:
        return f"refine_{self.name}"

    def sources(self, names=None) -> list:
        """Fresh instances of the sources asked for, by name; all of them when none are named."""
        if isinstance(names, str):
            names = names.split(",")
        names = list(self.registry) if names is None else names
        if not names or any(n not in self.registry for n in names):
            raise ValueError(f"unknown or empty sources; known sources: {', '.join(self.registry)}")
        return [self.registry[n]() for n in dict.fromkeys(names)]


def _flight_label(a: dict) -> str:
    return f"{a['origin']}→{a['destination']} {a['depart']}" + (f"/{a['return_date']}" if a.get("return_date") else "")


FLIGHTS = Kind(
    name="flights",
    noun="flight",
    registry=FLIGHT_SOURCES,
    read=lambda profile, a: flight_query(
        profile,
        a["origin"],
        a["destination"],
        a["depart"],
        a.get("return_date"),
        a.get("flex_days", 0),
        a.get("adults"),
        a.get("cabin"),
        a.get("children_ages"),
    ),
    search=search_flights,
    to_json=flight_search_json,
    estimate=estimate_flights,
    view=lambda app, result, **filters: flights_view(result, **filters),
    # A view starts from the same bars as the search, or it would quietly show what the search hid.
    bars=lambda p: {"max_stops": p.max_stops, "max_leg_hours": p.max_leg_hours, "avoid_airlines": p.avoid_airlines},
    label=_flight_label,
    last_day="depart",
    sample=lambda day: FlightQuery(("MOW",), ("LED",), day),
)

STAYS = Kind(
    name="stays",
    noun="stay",
    registry=STAY_SOURCES,
    read=lambda profile, a: stay_query(
        profile, a["place"], a["checkin"], a["checkout"], a.get("adults"), a.get("children_ages")
    ),
    search=search_stays,
    to_json=stay_search_json,
    estimate=estimate,
    # A card is shown with what its property page said, once `stay_details` has read it.
    view=lambda app, result, **filters: stays_view(result, details=app.results.details, **filters),
    bars=lambda p: {"min_rating": p.stays.min_rating, "max_center_km": p.stays.max_center_km},
    label=lambda a: f"{a['place']} {a['checkin']}/{a['checkout']}",
    last_day="checkin",
    sample=lambda day: StayQuery("Istanbul", day, day + timedelta(days=2)),
    # Stays that give coordinates but no distance are measured from the centre of the place.
    context=_center,
)

GROUND = Kind(
    name="ground",
    noun="ground",
    registry=GROUND_SOURCES,
    read=lambda profile, a: ground_query(
        profile, a["origin"], a["destination"], a["depart"], a.get("adults"), a.get("children_ages"), a.get("modes")
    ),
    search=search_ground,
    to_json=ground_search_json,
    estimate=estimate,
    view=lambda app, result, **filters: ground_view(result, **filters),
    bars=lambda p: {},
    label=lambda a: f"{a['origin']}→{a['destination']} {a['depart']} by {'/'.join(a.get('modes') or ['train', 'bus'])}",
    last_day="depart",
    sample=lambda day: GroundQuery("Belgrade", "Vienna", day),
)

KINDS: dict[str, Kind] = {kind.name: kind for kind in (FLIGHTS, STAYS, GROUND)}
