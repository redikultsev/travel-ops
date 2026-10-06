from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..core.money import Rates, UnknownCurrency
from ..core.stays import Rate, Stay, StayOffer


@dataclass
class StayCard:
    stay: Stay
    rates: list[Rate]
    best: Decimal


def list_stays(offers: list[StayOffer], rates: Rates, currency: str) -> list[StayCard]:
    one_currency = len({o.rate.total.currency for o in offers}) == 1

    def value(rate: Rate) -> Decimal:
        # With the stated taxes and charges when a source states them: that is what the human pays.
        price = rate.all_in() or rate.total
        try:
            return rates.convert(price, currency).amount
        except UnknownCurrency:
            return price.amount if one_currency else Decimal("Infinity")

    stays: dict[tuple[str, str], tuple[Stay, list[Rate]]] = {}
    for o in offers:
        stays.setdefault((o.stay.source, o.stay.source_id), (o.stay, []))[1].append(o.rate)
    cards = [StayCard(stay, sorted(rs, key=value), min(map(value, rs))) for stay, rs in stays.values()]
    return sorted(cards, key=lambda c: c.best)
