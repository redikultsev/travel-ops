"""Views of a search result: filters, order and the shortlist. A view never goes to the network. Every filter
says how many cards it hid, and separately how many it hid only because the value is unknown."""

from __future__ import annotations

import re

from .core.report import brief
from .serialize import _amount, leg_options, shortlist

FLIGHT_SORTS = ("price", "duration", "departure")
STAY_SORTS = ("price", "rating", "reviews")
STAY_KINDS = ("hotel", "apartment", "room", "house", "shared_room", "other")


class Hidden:
    """Counts what each filter hides. A filter that hides nothing is still listed: it was applied."""

    def __init__(self) -> None:
        self.by: list[dict] = []

    def apply(self, cards: list[dict], name: str, value, verdict) -> list[dict]:
        """`verdict(card)` is True to keep, False to hide, None when the card does not say."""
        kept, hidden, unknown = [], 0, 0
        for card in cards:
            answer = verdict(card)
            if answer:
                kept.append(card)
            elif answer is None:
                unknown += 1
            else:
                hidden += 1
        entry = {"filter": name, "value": value, "hidden": hidden}
        if unknown:
            entry["hidden_unknown"] = unknown
        self.by.append(entry)
        return kept

    def report(self) -> dict:
        total = sum(e["hidden"] + e.get("hidden_unknown", 0) for e in self.by)
        return {"hidden_cards": total, "by": self.by}


def _clock(value: str, name: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value):
        raise ValueError(f"{name} must be a local time as HH:MM")
    return value


def _codes(value, name: str) -> set[str]:
    items = value.split(",") if isinstance(value, str) else list(value)
    codes = {str(item).strip().upper() for item in items if str(item).strip()}
    if not codes:
        raise ValueError(f"{name} cannot be empty")
    return codes


def _comparable(offer: dict, currency: str) -> float | None:
    """An amount in the currency of the result, or None when the offer cannot be compared in it."""
    if offer.get("converted"):
        return float(offer["converted"]["amount"])
    money = offer.get("price") or offer.get("total")
    return float(money["amount"]) if money["currency"] == currency else None


def flights_view(
    result: dict,
    *,
    limit: int = 10,
    max_stops: int | None = None,
    max_leg_hours: float | None = None,
    depart_after: str | None = None,
    depart_before: str | None = None,
    return_after: str | None = None,
    return_before: str | None = None,
    airlines=None,
    avoid_airlines=None,
    destination=None,
    checked_bag: bool | None = None,
    max_price: float | None = None,
    sort: str = "price",
) -> dict:
    if sort not in FLIGHT_SORTS:
        raise ValueError(f"sort must be one of: {', '.join(FLIGHT_SORTS)}")
    cards, hidden = result["cards"], Hidden()
    round_trip = any(card["inbound"] for card in cards)
    if (return_after or return_before) and cards and not round_trip:
        raise ValueError("this search has no return flights to filter")

    if max_stops is not None:
        if type(max_stops) is not int or max_stops < 0:
            raise ValueError("max_stops must be an integer >= 0")
        cards = hidden.apply(cards, "max_stops", max_stops, lambda c: c["stops"] <= max_stops)
    if max_leg_hours is not None:
        if not isinstance(max_leg_hours, (int, float)) or isinstance(max_leg_hours, bool) or max_leg_hours <= 0:
            raise ValueError("max_leg_hours must be a positive number")
        cards = hidden.apply(
            cards,
            "max_leg_hours",
            max_leg_hours,
            lambda c: max(c["duration_min"], c["return_duration_min"] or 0) <= max_leg_hours * 60,
        )
    # Times are local to the airport of departure, as printed on a ticket.
    for name, value, leg, keep in (
        ("depart_after", depart_after, "outbound", lambda at, bar: at >= bar),
        ("depart_before", depart_before, "outbound", lambda at, bar: at <= bar),
        ("return_after", return_after, "inbound", lambda at, bar: at >= bar),
        ("return_before", return_before, "inbound", lambda at, bar: at <= bar),
    ):
        if value is not None:
            bar = _clock(value, name)
            cards = hidden.apply(
                cards, name, bar, lambda c, leg=leg, keep=keep, bar=bar: keep(c[leg][0]["departs"][11:16], bar)
            )
    if airlines is not None:
        only = _codes(airlines, "airlines")
        cards = hidden.apply(
            cards, "airlines", sorted(only), lambda c: all(s["carrier"] in only for s in c["outbound"] + c["inbound"])
        )
    if avoid_airlines:
        avoid = _codes(avoid_airlines, "avoid_airlines")
        cards = hidden.apply(
            cards,
            "avoid_airlines",
            sorted(avoid),
            lambda c: not any({s["carrier"], s["operating"]} & avoid for s in c["outbound"] + c["inbound"]),
        )
    if destination is not None:
        where = _codes(destination, "destination")
        cards = hidden.apply(cards, "destination", sorted(where), lambda c: c["outbound"][-1]["destination"] in where)

    # The next two look inside a card: it survives with the fares that pass.
    if checked_bag is not None:
        if checked_bag is not True:
            raise ValueError("checked_bag can only be true: fares that include a checked bag")

        def with_bag(card):
            groups = [g for g in card["groups"] if g["checked_bag"] is True]
            if groups:
                card["groups"] = groups
                return True
            return None if any(g["checked_bag"] is None for g in card["groups"]) else False

        cards = hidden.apply(cards, "checked_bag", True, with_bag)
    if max_price is not None:
        if not isinstance(max_price, (int, float)) or isinstance(max_price, bool) or max_price <= 0:
            raise ValueError("max_price must be a positive number in the currency of the result")
        currency = result["currency"]

        def affordable(card):
            groups, unknown = [], False
            for group in card["groups"]:
                amounts = [(fare, _comparable(fare, currency)) for fare in group["fares"]]
                unknown = unknown or any(amount is None for _, amount in amounts)
                fares = [fare for fare, amount in amounts if amount is not None and amount <= max_price]
                if fares:
                    groups.append(dict(group, fares=fares))
            if groups:
                card["groups"] = groups
                return True
            return None if unknown else False

        cards = hidden.apply(cards, "max_price", max_price, affordable)

    for card in cards:
        card["groups"].sort(key=lambda g: _amount(g["fares"][0]))
    cards.sort(key=lambda c: _amount(c["groups"][0]["fares"][0]))
    result["cards"] = cards
    if hidden.by:
        result["filtered"] = hidden.report()
    leg_options(result)
    if sort == "duration":
        cards.sort(key=lambda c: c["duration_min"] + (c["return_duration_min"] or 0))
    elif sort == "departure":
        cards.sort(key=lambda c: c["outbound"][0]["departs"])
    shortlist(result, limit, varied=sort == "price")
    result["shown"]["sorted_by"] = sort
    result["report"] = brief(result.get("sources", []))
    return result


def stays_view(
    result: dict,
    *,
    limit: int = 10,
    min_rating: float | None = None,
    min_reviews: int | None = None,
    max_total: float | None = None,
    kinds=None,
    exclude_kinds=None,
    sources=None,
    sort: str = "price",
) -> dict:
    if sort not in STAY_SORTS:
        raise ValueError(f"sort must be one of: {', '.join(STAY_SORTS)}")
    cards, hidden = result["cards"], Hidden()

    if min_rating is not None:
        if not isinstance(min_rating, (int, float)) or isinstance(min_rating, bool) or not 0 <= min_rating <= 10:
            raise ValueError("min_rating must be a number from 0 to 10")

        def rated(card):
            rating = card["stay"]["rating"]
            return None if rating is None else rating >= min_rating

        cards = hidden.apply(cards, "min_rating", min_rating, rated)
    if min_reviews is not None:
        if type(min_reviews) is not int or min_reviews < 1:
            raise ValueError("min_reviews must be an integer >= 1")

        def reviewed(card):
            reviews = card["stay"]["reviews"]
            return None if reviews is None else reviews >= min_reviews

        cards = hidden.apply(cards, "min_reviews", min_reviews, reviewed)
    for name, value, keep in (("kinds", kinds, True), ("exclude_kinds", exclude_kinds, False)):
        if value is not None:
            chosen = {k.lower() for k in _codes(value, name)}
            if chosen - set(STAY_KINDS):
                raise ValueError(f"{name} must be among: {', '.join(STAY_KINDS)}")
            cards = hidden.apply(
                cards, name, sorted(chosen), lambda c, chosen=chosen, keep=keep: (c["stay"]["kind"] in chosen) is keep
            )
    if sources is not None:
        chosen = {s.lower() for s in _codes(sources, "sources")}
        cards = hidden.apply(cards, "sources", sorted(chosen), lambda c: c["stay"]["source"] in chosen)
    if max_total is not None:
        if not isinstance(max_total, (int, float)) or isinstance(max_total, bool) or max_total <= 0:
            raise ValueError("max_total must be a positive number in the currency of the result")
        currency = result["currency"]

        def affordable(card):
            amounts = [(rate, _comparable(rate, currency)) for rate in card["rates"]]
            rates = [rate for rate, amount in amounts if amount is not None and amount <= max_total]
            if rates:
                card["rates"] = rates
                return True
            return None if any(amount is None for _, amount in amounts) else False

        cards = hidden.apply(cards, "max_total", max_total, affordable)

    if sort == "rating":
        cards.sort(key=lambda c: (c["stay"]["rating"] is None, -(c["stay"]["rating"] or 0)))
    elif sort == "reviews":
        cards.sort(key=lambda c: -(c["stay"]["reviews"] or 0))
    result["cards"] = cards
    if hidden.by:
        result["filtered"] = hidden.report()
    shortlist(result, limit)
    result["shown"]["sorted_by"] = sort
    result["report"] = brief(result.get("sources", []))
    return result
