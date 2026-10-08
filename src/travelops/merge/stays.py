"""One card per property. Each source names a hotel its own way and gives it its own id, so the same hotel from
Booking and from trivago is found by its name and its place: comparing their prices is the point of asking both.
A pair that is not plainly the same stays apart — a wrong merge shows a price for another hotel, a missed one only
loses a comparison."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field, replace
from decimal import Decimal

from ..core.money import Rates, UnknownCurrency
from ..core.stays import Rate, Stay, StayOffer
from ..geo import distance_km

# Words that do not tell one property from another; the place's own name is added per search.
GENERIC = frozenset({"hotel", "hotels", "the", "and", "by", "at", "of", "a", "an"})
SAME_SPOT_KM = 1.0  # coordinates of one building differ between sources by a few hundred metres at most
SAME_CENTER_KM = 2.0  # each source measures from its own idea of the centre


@dataclass
class StayCard:
    stay: Stay
    rates: list[Rate]
    best: Decimal
    listed: list[Stay] = field(default_factory=list)  # the property as each source lists it, `stay`'s source first


def name_words(name: str, ignore: frozenset[str] = frozenset()) -> frozenset[str]:
    """"Swissôtel Grand Shanghai" and "Swissotel Grand" are one name once accents, apostrophes (Jing'an), the
    place and generic words are gone."""
    folded = "".join(c for c in unicodedata.normalize("NFKD", name.casefold()) if not unicodedata.combining(c))
    folded = re.sub(r"['’`]", "", folded)
    return frozenset(w for w in re.findall(r"\w+", folded) if w not in GENERIC and w not in ignore)


def place_words(place: str) -> frozenset[str]:
    return name_words(place.split(",")[0])


SAME_BUILDING_KM = 0.1  # a name with words more ("- Nanjing Road", "Hostel") is the same only at the same spot


def _variant(a: Stay, b: Stay, wa: frozenset[str], wb: frozenset[str], km: float, least: int) -> bool:
    """One name is the other with words more, and both stand within `km`: "Grand Central Shanghai" and "Grand
    Central Hotel Shanghai - Nanjing Road". Without coordinates a longer name may be another branch."""
    if not (wa < wb or wb < wa) or min(len(wa), len(wb)) < least:
        return False
    if None in (a.lat, a.lon, b.lat, b.lon):
        return False
    return distance_km(a.lat, a.lon, b.lat, b.lon) <= km


def same_stay(a: Stay, b: Stay, ignore: frozenset[str] = frozenset()) -> bool:
    if a.source == b.source:
        return False  # within one source its own id decides
    wa, wb = name_words(a.name, ignore), name_words(b.name, ignore)
    if not wa or not wb:
        return False
    if wa != wb:
        return _variant(a, b, wa, wb, SAME_BUILDING_KM, 2)
    if None not in (a.lat, a.lon, b.lat, b.lon):
        return distance_km(a.lat, a.lon, b.lat, b.lon) <= SAME_SPOT_KM
    if a.center_km is not None and b.center_km is not None:
        return abs(a.center_km - b.center_km) <= SAME_CENTER_KM
    return True


LOOKED_UP_SPOT_KM = 0.2


def looked_up(card: Stay, found: Stay, ignore: frozenset[str] = frozenset()) -> str | None:
    """Whether a property another source gave for a name asked by name is the card's own. The same name is
    enough, as in a search; a name with a word more or less ("Okura Garden" and "Okura Garden Shanghai Huaihai")
    passes only at the same spot, within 200 m."""
    if card.source == found.source:
        return None
    a, b = name_words(card.name, ignore), name_words(found.name, ignore)
    if a and a == b and same_stay(card, found, ignore):
        return "same_name"
    if a and b and _variant(card, found, a, b, LOOKED_UP_SPOT_KM, 1):
        return "similar_name_same_spot"
    return None


def _completed(stay: Stay, others: list[Stay]) -> Stay:
    """What the first listing leaves unsaid, taken from the others: a Booking card has no coordinates, trivago's
    does."""
    for other in others:
        if stay.lat is None and other.lat is not None:
            stay = replace(stay, lat=other.lat, lon=other.lon)
        if stay.center_km is None and other.center_km is not None:
            stay = replace(stay, center_km=other.center_km)
        if stay.rating is None and other.rating is not None:
            stay = replace(stay, rating=other.rating, reviews=other.reviews)
    return stay


def list_stays(offers: list[StayOffer], rates: Rates, currency: str, place: str = "") -> list[StayCard]:
    one_currency = len({o.rate.total.currency for o in offers}) == 1
    ignore = place_words(place)

    def value(rate: Rate) -> Decimal:
        # With the stated taxes and charges when a source states them: that is what the human pays.
        price = rate.all_in() or rate.total
        try:
            return rates.convert(price, currency).amount
        except UnknownCurrency:
            return price.amount if one_currency else Decimal("Infinity")

    listings: dict[tuple[str, str], tuple[Stay, list[Rate]]] = {}
    for o in offers:
        listings.setdefault((o.stay.source, o.stay.source_id), (o.stay, []))[1].append(o.rate)
    groups: list[list[tuple[Stay, list[Rate]]]] = []
    for stay, found in listings.values():
        home = next(
            (g for g in groups if all(same_stay(stay, s, ignore) for s, _ in g)),
            None,
        )
        if home is None:
            groups.append([(stay, found)])
        else:
            home.append((stay, found))
    cards = []
    for group in groups:
        stays = [s for s, _ in group]
        rs = sorted((r for _, found in group for r in found), key=value)
        cards.append(StayCard(_completed(stays[0], stays[1:]), rs, min(map(value, rs)), stays))
    return sorted(cards, key=lambda c: c.best)
