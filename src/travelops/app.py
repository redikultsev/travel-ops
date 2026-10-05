"""Application resources shared by CLI and MCP."""

from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from .core.flights import CABINS, FlightQuery
from .core.stays import StayQuery
from .net.client import Net
from .net.browser import BrowserSessions
from .net.limiter import Limiter
from .profile import Profile, data_dir, load_profile
from .sources import FLIGHT_SOURCES, STAY_SOURCES, RULES
from .sources.base import Context


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


def _airports(value):
    codes = tuple(part.strip().upper() for part in value.split(","))
    if not codes or any(len(x) != 3 or not x.isalpha() for x in codes):
        raise ValueError("airports must be three-letter IATA codes, optionally comma-separated")
    return codes


def _positive(value, label, minimum=1):
    if type(value) is not int or value < minimum:
        raise ValueError(f"{label} must be an integer >= {minimum}")
    return value


def flight_query(profile, origin, destination, depart, return_date=None, flex_days=0, adults=None, cabin=None):
    depart = date.fromisoformat(depart) if isinstance(depart, str) else depart
    return_date = date.fromisoformat(return_date) if isinstance(return_date, str) else return_date
    if return_date and return_date < depart:
        raise ValueError("return date must not precede departure")
    cabin = profile.cabin if cabin is None else cabin
    if cabin not in CABINS:
        raise ValueError(f"unknown cabin: {cabin}")
    _positive(flex_days, "flex_days", 0)
    return FlightQuery(
        _airports(origin),
        _airports(destination),
        depart,
        return_date,
        flex_days,
        _positive(profile.travellers.adults if adults is None else adults, "adults"),
        profile.travellers.children,
        profile.travellers.infants,
        cabin,
    )


def stay_query(profile, place, checkin, checkout, adults=None):
    checkin = date.fromisoformat(checkin) if isinstance(checkin, str) else checkin
    checkout = date.fromisoformat(checkout) if isinstance(checkout, str) else checkout
    if checkout <= checkin:
        raise ValueError("checkout must be after checkin")
    if not isinstance(place, str) or not place.strip():
        raise ValueError("place cannot be empty")
    return StayQuery(
        place.strip(),
        checkin,
        checkout,
        _positive(profile.stays.adults if adults is None else adults, "adults"),
        profile.travellers.children,
    )


@dataclass
class App:
    root: Path
    net: Net
    browser: BrowserSessions
    ctx: Context
    limiter: Limiter
    profile: Profile
    closed: bool = False

    async def close(self):
        if not self.closed:
            try:
                await self.net.close()
            finally:
                self.net.cache.db.close()
                self.limiter.db.close()
                self.closed = True


def build(root: Path, proxy: str | None = None) -> App:
    root = Path(root).resolve()
    profile = load_profile(root)
    data = data_dir(root)
    data.mkdir(parents=True, exist_ok=True)
    limiter = Limiter(data / "limiter.sqlite", RULES)
    net = Net(data, proxy=proxy, limiter=limiter)
    browser = BrowserSessions(data, exit_=net.exit, proxy=net.proxy, limiter=limiter, counts=net.counts)
    return App(root, net, browser, Context(net, browser, lambda: datetime.now(timezone.utc)), limiter, profile)
