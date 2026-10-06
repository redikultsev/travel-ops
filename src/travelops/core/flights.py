"""Flight model. Sources disagree on how they write a cabin, a flight number and a time; everything is normalized
here, at the boundary, because merging identical flights across sellers is impossible otherwise."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import airportsdata

from .common import Link
from .money import Money

CABINS = ("economy", "premium_economy", "business", "first")
_CABIN_ALIASES = {
    "y": "economy",
    "e": "economy",
    "economy": "economy",
    "econom": "economy",
    "economic": "economy",
    "w": "premium_economy",
    "premium": "premium_economy",
    "premium_economy": "premium_economy",
    "comfort": "premium_economy",
    "c": "business",
    "j": "business",
    "business": "business",
    "f": "first",
    "first": "first",
}
_AIRPORTS = airportsdata.load("IATA")


class UnknownAirport(KeyError):
    pass


def cabin(value: str | None) -> str | None:
    if not value:
        return None
    return _CABIN_ALIASES.get(value.strip().lower().replace(" ", "_").replace("-", "_"))


def flight_number(carrier: str, number: str | int) -> str:
    carrier = carrier.strip().upper()
    raw = str(number).strip().upper().replace(" ", "").replace("-", "")
    if raw.startswith(carrier):
        raw = raw[len(carrier) :]
    return f"{carrier}{raw.lstrip('0') or '0'}"


def airport_tz(code: str) -> ZoneInfo:
    try:
        return ZoneInfo(_AIRPORTS[code.upper()]["tz"])
    except KeyError:
        raise UnknownAirport(code) from None


def at_airport(value: str | datetime, airport: str) -> datetime:
    """Naive input is wall-clock time at the airport; aware input is converted to it."""
    moment = datetime.fromisoformat(value) if isinstance(value, str) else value
    tz = airport_tz(airport)
    return moment.astimezone(tz) if moment.tzinfo else moment.replace(tzinfo=tz)


def serves(asked: str, airport: str) -> bool:
    """Whether a flight to `airport` answers a question about `asked`: the same airport, or another one of the
    same country within 80 km (Sabiha Gokcen for Istanbul). City names in the data differ by airport, so place
    decides, not name. A code that is not an airport (a city code such as MOW) cannot be checked and passes."""
    asked, airport = asked.upper(), airport.upper()
    if asked == airport or asked not in _AIRPORTS:
        return True
    a, b = _AIRPORTS[asked], _AIRPORTS.get(airport)
    if not b or a["country"] != b["country"]:
        return False
    p = math.pi / 180
    h = (
        0.5
        - math.cos((b["lat"] - a["lat"]) * p) / 2
        + math.cos(a["lat"] * p) * math.cos(b["lat"] * p) * (1 - math.cos((b["lon"] - a["lon"]) * p)) / 2
    )
    return 12742 * math.asin(math.sqrt(h)) <= 80


def on_route(itinerary: "Itinerary", origins, destinations) -> bool:
    """A source can answer another question than it was asked: a resolver that takes one city for another."""
    out, back = itinerary.outbound, itinerary.inbound
    ok = any(serves(o, out[0].origin) for o in origins) and any(serves(d, out[-1].destination) for d in destinations)
    if back:
        ok = (
            ok
            and any(serves(d, back[0].origin) for d in destinations)
            and any(serves(o, back[-1].destination) for o in origins)
        )
    return ok


@dataclass(frozen=True)
class Segment:
    carrier: str
    flight: str
    origin: str
    destination: str
    departs: datetime
    arrives: datetime
    operating: str | None = None


@dataclass(frozen=True)
class Itinerary:
    outbound: tuple[Segment, ...]
    inbound: tuple[Segment, ...] = ()

    def segments(self) -> tuple[Segment, ...]:
        return self.outbound + self.inbound

    def key(self) -> tuple:
        """The whole chain of flights with dates: one key, one physical trip."""
        return (*((s.flight, s.departs.date().isoformat()) for s in self.segments()), len(self.outbound))

    def stops(self) -> int:
        return max(len(self.outbound) - 1, len(self.inbound) - 1 if self.inbound else 0)

    def airport_changes(self) -> list[tuple[str, str]]:
        changes = []
        for leg in (self.outbound, self.inbound):
            changes += [(a.destination, b.origin) for a, b in zip(leg, leg[1:]) if a.destination != b.origin]
        return changes

    def duration(self, inbound: bool = False) -> timedelta:
        """Door to door, waiting at connections included — not the sum of flying time."""
        leg = self.inbound if inbound else self.outbound
        return leg[-1].arrives - leg[0].departs


@dataclass(frozen=True)
class Baggage:
    """Per traveller. A source that counts for the whole party is divided at its boundary."""

    checked: int | None = None  # pieces included; 0 = none; None = the source did not say
    checked_kg: int | None = None
    carry_on: bool | None = None


@dataclass(frozen=True)
class Fare:
    price: Money  # always for the whole party in the query
    seller: str  # who takes the money, e.g. "aviasales:biletix"
    source: str
    cabin: str | None
    baggage: Baggage
    link: Link | None
    seen_at: datetime
    refundable: bool | None = None
    fare_name: str | None = None

    def comparable_key(self) -> tuple:
        checked = None if self.baggage.checked is None else self.baggage.checked > 0
        return (self.cabin, checked)


@dataclass(frozen=True)
class FlightOffer:
    itinerary: Itinerary
    fare: Fare


@dataclass(frozen=True)
class FlightQuery:
    origins: tuple[str, ...]
    destinations: tuple[str, ...]
    depart: date
    return_: date | None = None
    flex_days: int = 0
    adults: int = 1
    children: int = 0
    infants: int = 0
    cabin: str = "economy"

    def date_pairs(self) -> list[tuple[date, date | None]]:
        """Every shift moves both dates, so the trip length stays the same."""
        pairs = []
        for shift in range(-self.flex_days, self.flex_days + 1):
            d = timedelta(days=shift)
            pairs.append((self.depart + d, self.return_ + d if self.return_ else None))
        return pairs

    def on(self, depart: date, return_: date | None) -> "FlightQuery":
        return replace(self, depart=depart, return_=return_, flex_days=0)
