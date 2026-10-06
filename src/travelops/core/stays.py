"""Stay model. Ratings are kept on one 0–10 scale; Airbnb's 0–5 is doubled at the boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from .common import Link
from .money import Money


def rating_out_of_10(value: float | None, scale: float) -> float | None:
    return None if value is None else round(float(value) * 10 / scale, 1)


@dataclass(frozen=True)
class StayQuery:
    place: str
    checkin: date
    checkout: date
    adults: int = 2
    children: int = 0
    rooms: int = 1

    @property
    def nights(self) -> int:
        return (self.checkout - self.checkin).days


@dataclass(frozen=True)
class Stay:
    source: str
    source_id: str
    name: str
    kind: Literal["hotel", "apartment", "room", "house", "shared_room", "other"]
    lat: float | None
    lon: float | None
    rating: float | None  # 0–10
    reviews: int | None
    photos: tuple[str, ...] = ()
    amenities: tuple[str, ...] = ()


@dataclass(frozen=True)
class Rate:
    total: Money
    seller: str
    source: str
    link: Link
    seen_at: datetime
    free_cancel_until: date | None = None
    meals: str | None = None
    room: str | None = None

    def per_night(self, nights: int) -> Money:
        return Money((self.total.amount / nights).quantize(Decimal("0.01")), self.total.currency)


@dataclass(frozen=True)
class StayOffer:
    stay: Stay
    rate: Rate


def kind_of_room(room: str | None) -> str:
    """What the human gets, as far as a room name tells: a bed among strangers is not a room of one's own."""
    text = (room or "").lower()
    if "dormitory" in text or "bunk bed" in text or text.startswith("bed in") or "shared room" in text:
        return "shared_room"
    if "apartment" in text or "studio" in text or "suite" in text or "flat" in text:
        return "apartment"
    if any(word in text for word in ("house", "home", "villa", "chalet", "bungalow", "cottage", "cabin")):
        return "house"
    if "room" in text:
        return "room"
    return "other"
