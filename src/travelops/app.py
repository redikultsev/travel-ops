"""Application resources shared by CLI and MCP."""

import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from .core.flights import CABINS, FlightQuery
from .core.ground import MODES, GroundQuery
from .core.stays import StayQuery
from .memory import Results
from .net.client import Net
from .net.browser import BrowserSessions
from .net.limiter import Limiter
from .profile import Profile, data_dir, load_profile
from .sources import FLIGHT_SOURCES, GROUND_SOURCES, STAY_SOURCES, RULES
from .sources.base import Context
from .watch import Watches


def _sources(names, registry):
    if isinstance(names, str):
        names = names.split(",")
    names = list(registry) if names is None else names
    if not names or any(n not in registry for n in names):
        raise ValueError(f"unknown or empty sources; known sources: {', '.join(registry)}")
    return [registry[n]() for n in dict.fromkeys(names)]


def flight_sources(names=None):
    return _sources(names, FLIGHT_SOURCES)


def stay_sources(names=None):
    return _sources(names, STAY_SOURCES)


def ground_sources(names=None):
    return _sources(names, GROUND_SOURCES)


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
    return FlightQuery(
        _airports(origin), _airports(destination), depart, return_date, flex_days, adults, children, infants, cabin
    )


def stay_query(profile, place, checkin, checkout, adults=None, children_ages=None):
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
    )


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


@dataclass
class App:
    root: Path
    net: Net
    browser: BrowserSessions
    ctx: Context
    limiter: Limiter
    profile: Profile
    results: Results
    replay: bool = False  # answers come from a recording; nothing goes to the network
    watches: Watches | None = None  # saved searches that `travelops watch run` repeats
    closed: bool = False

    async def close(self):
        if not self.closed:
            try:
                await self.net.close()
            finally:
                self.net.cache.db.close()
                self.limiter.db.close()
                self.results.db.close()
                if self.watches is not None:
                    self.watches.db.close()
                self.closed = True


def build(root: Path, proxy: str | None = None) -> App:
    if os.environ.get("TRAVELOPS_REPLAY"):
        from .replay import build_replay

        return build_replay(Path(os.environ["TRAVELOPS_REPLAY"]))
    root = Path(root).resolve()
    profile = load_profile(root)
    data = data_dir(root)
    data.mkdir(parents=True, exist_ok=True)
    limiter = Limiter(data / "limiter.sqlite", RULES)
    net = Net(data, proxy=proxy, limiter=limiter)
    browser = BrowserSessions(data, exit_=net.exit, proxy=net.proxy, limiter=limiter, counts=net.counts)
    return App(
        root,
        net,
        browser,
        Context(net, browser, lambda: datetime.now(timezone.utc)),
        limiter,
        profile,
        Results(data / "results.sqlite"),
        watches=Watches(data / "watches.sqlite"),
    )
