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
    district: str | None = None
    center_km: float | None = None  # from the centre of the place, as the source states it


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
    charges: Money | None = None  # taxes and fees on top of `total`; zero when included; None when not stated
    free_cancellation: bool | None = None
    pay_at_property: bool | None = None

    def all_in(self) -> Money | None:
        """What the stay costs with the stated taxes and charges, when the source states them."""
        if self.charges is None or self.charges.currency != self.total.currency:
            return None
        return Money(self.total.amount + self.charges.amount, self.total.currency)

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


def kind_of_listing(title: str | None) -> str:
    """Airbnb names the kind of place before the town: "Apartment in Kotor", "Shared room in hostel"."""
    text = (title or "").lower().split(" in ")[0] if " in " in (title or "").lower() else (title or "").lower()
    whole = (title or "").lower()
    if text.startswith("shared room") or "shared room" in whole:
        return "shared_room"
    if text in ("room", "private room", "hotel room") or text.endswith(" room"):
        return "room"
    if any(word in text for word in ("apartment", "condo", "loft", "flat", "rental unit", "studio")):
        return "apartment"
    if any(
        word in text
        for word in ("home", "house", "villa", "cabin", "cottage", "bungalow", "chalet", "guesthouse", "townhouse")
    ):
        return "house"
    if "hotel" in text or "hostel" in text:
        return "hotel"
    return "other"


AMENITY_WORDS = {
    # What people ask for, and how the sites spell it once punctuation is gone.
    "wifi": ("wifi", "internet"),
    "ac": ("airconditioning", "acsplit", "accentral", "acwindow", "acportable"),
    "air conditioning": ("airconditioning", "acsplit", "accentral", "acwindow", "acportable"),
    "parking": ("parking",),
    "kitchen": ("kitchen",),
    "washer": ("washer", "washingmachine", "laundry"),
    "breakfast": ("breakfast",),
    "pool": ("pool",),
    "balcony": ("balcony", "terrace", "patio"),
    "workspace": ("workspace", "desk"),
    "elevator": ("elevator", "lift"),
    "pets": ("petsallowed", "petfriendly"),
}


def has_amenity(amenities: list[str], wanted: str) -> bool:
    squeeze = lambda text: "".join(ch for ch in text.lower() if ch.isalnum())
    needles = AMENITY_WORDS.get(wanted.strip().lower(), (squeeze(wanted),))
    return any(needle in squeeze(item) for item in amenities for needle in needles)
