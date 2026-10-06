"""What a card of a result holds, whatever its kind: its offers, and what one of them costs in the currency of the
result. Sorting, filters, shortlists, pairs of tickets and watches all price an offer here, so they agree on which
price counts and on which offer is the cheapest."""

from __future__ import annotations


def offers(card: dict) -> list[dict]:
    """The priced offers of a card: the fares of every group of a flight, the rates of a stay, the fare of a ride."""
    if "groups" in card:
        return [fare for group in card["groups"] for fare in group["fares"]]
    return card["fares"] if "fares" in card else card["rates"]


def amount(offer: dict, currency: str) -> float | None:
    """What the offer costs in `currency`, with the taxes and charges its source states; None when that cannot be
    said: an amount in another currency without a rate is not comparable with the rest."""
    for key in ("all_in_converted", "converted"):
        if offer.get(key):
            return float(offer[key]["amount"])
    money = offer.get("all_in") or offer.get("price") or offer.get("total")
    return float(money["amount"]) if money and money["currency"] == currency else None


def order(offer: dict, currency: str) -> tuple[bool, float]:
    """A sort key: cheapest first, and offers that cannot be priced in the currency after all the others."""
    value = amount(offer, currency)
    return (value is None, value or 0.0)


def cheapest(card: dict, currency: str) -> tuple[float, dict] | None:
    """The cheapest offer of a card that can be priced in the currency, with that price."""
    priced = [(value, offer) for offer in offers(card) if (value := amount(offer, currency)) is not None]
    return min(priced, key=lambda item: item[0]) if priced else None
