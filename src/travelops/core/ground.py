"""Trains and buses: one way between two places on one day. Places are names, not codes: each source resolves them
itself and its stations say what it understood."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime

from .common import Link
from .money import Money

MODES = ("train", "bus", "ferry", "van")  # a van is a shared minibus with a timetable, sold like a bus


@dataclass(frozen=True)
class GroundQuery:
    origin: str
    destination: str
    depart: date
    adults: int = 1
    children: int = 0
    modes: tuple[str, ...] = ("train", "bus")


@dataclass(frozen=True)
class Ride:
    """One vehicle from one station to another. A time without a zone is the local time the source printed."""

    mode: str
    origin: str
    destination: str
    departs: datetime
    arrives: datetime
    carrier: str | None = None
    number: str | None = None


@dataclass(frozen=True)
class GroundOffer:
    rides: tuple[Ride, ...]
    price: Money  # for the whole party
    seller: str
    source: str
    link: Link | None
    seen_at: datetime
    # True when the price is the cheapest class, from which the party may not all find a seat.
    price_from: bool = False
    # Per-seat prices by class, for trains: seat, open berth, compartment, sleeper.
    classes: dict[str, Money] = field(default_factory=dict)
    rating: float | None = None  # 0-10, of the carrier or the train
    reviews: int | None = None

    def duration_min(self) -> int | None:
        first, last = self.rides[0].departs, self.rides[-1].arrives
        if first.tzinfo is None or last.tzinfo is None:
            return None  # local times of two places say nothing of the hours between them
        return int((last - first).total_seconds() // 60)


def folded(text: str) -> str:
    """Lower case without accents, for comparing a place with a station name."""
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


def through_stops(offers: list[GroundOffer], destination: str) -> tuple[list[GroundOffer], set[str]]:
    """Drop the offers that are a ride to a stop short of the place, when the same vehicle also has an offer to the
    place: one bus sold to every stop on its way is one choice, and the earlier stop is not where the human goes.
    Returns what is kept and the stops dropped."""
    place = folded(destination)
    reaching = {(o.rides[0].origin, o.rides[0].departs) for o in offers if place in folded(o.rides[-1].destination)}
    kept, dropped = [], set()
    for offer in offers:
        if (
            place not in folded(offer.rides[-1].destination)
            and (offer.rides[0].origin, offer.rides[0].departs) in reaching
        ):
            dropped.add(offer.rides[-1].destination)
        else:
            kept.append(offer)
    return kept, dropped
