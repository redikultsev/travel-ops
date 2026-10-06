"""Views of a search result: filters, order and the shortlist. A view never goes to the network. Every filter
says how many cards it hid, and separately how many it hid only because the value is unknown."""

from __future__ import annotations

import re
from datetime import datetime

from .core.report import brief
from .core.stays import bedrooms_of, has_amenity
from .geo import distance_km
from .serialize import _amount, leg_options, shortlist

FLIGHT_SORTS = ("price", "duration", "departure")
STAY_SORTS = ("price", "rating", "reviews", "center")
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
    # A stay is compared by what it costs with the stated taxes and charges, when the source states them.
    if offer.get("all_in_converted") or offer.get("converted"):
        return float((offer.get("all_in_converted") or offer["converted"])["amount"])
    money = offer.get("all_in") or offer.get("price") or offer.get("total")
    return float(money["amount"]) if money["currency"] == currency else None


def flights_view(
    result: dict,
    *,
    limit: int = 10,
    max_stops: int | None = None,
    max_leg_hours: float | None = None,
    max_connection_hours: float | None = None,
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

    # A source can add other cabins to an answer: a business fare is not a cheaper or dearer economy one.
    asked = (result.get("query") or {}).get("cabin")
    if asked:

        def in_cabin(card):
            groups = [g for g in card["groups"] if g["cabin"] in (asked, None)]
            if groups:
                card["groups"] = groups
                return True
            return False

        cards = hidden.apply(cards, "cabin", asked, in_cabin)
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
    if max_connection_hours is not None:
        if (
            not isinstance(max_connection_hours, (int, float))
            or isinstance(max_connection_hours, bool)
            or max_connection_hours <= 0
        ):
            raise ValueError("max_connection_hours must be a positive number")

        def short_waits(card):
            for leg in (card["outbound"], card["inbound"]):
                for before, after in zip(leg, leg[1:]):
                    wait = datetime.fromisoformat(after["departs"]) - datetime.fromisoformat(before["arrives"])
                    if wait.total_seconds() > max_connection_hours * 3600:
                        return False
            return True

        cards = hidden.apply(cards, "max_connection_hours", max_connection_hours, short_waits)
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
    no_hostels: bool = False,
    min_bedrooms: int | None = None,
    sources=None,
    max_center_km: float | None = None,
    must_have=None,
    free_cancellation: bool | None = None,
    details=None,
    sort: str = "price",
) -> dict:
    """`details(source, source_id)` returns what a property page said, if it was read: a card is shown with it."""
    if sort not in STAY_SORTS:
        raise ValueError(f"sort must be one of: {', '.join(STAY_SORTS)}")
    cards, hidden = result["cards"], Hidden()
    center = result.get("center")
    for card in cards:
        stay = card["stay"]
        known = details(stay["source"], stay["source_id"]) if details else None
        stay["details_read"] = bool(known)
        # A hostel also lets private rooms, so this is apart from `kind`: it is the house, not the bed.
        link = card["rates"][0]["link"]["url"].split("?")[0] if card["rates"] and card["rates"][0].get("link") else ""
        stay["bedrooms"] = bedrooms_of(card["rates"][0].get("room") if card["rates"] else None)
        stay["hostel"] = any(
            word in text.lower() for text in (stay["name"], stay["source_id"], link) for word in ("hostel", "homestel")
        )
        if known:
            stay["amenities"] = known["amenities"]
            for name in ("address", "not_available", "check_in", "check_out", "rules", "scores"):
                if known.get(name):
                    stay[name] = known[name]
            if stay.get("lat") is None and known.get("lat") is not None:
                stay["lat"], stay["lon"] = known["lat"], known["lon"]
            if known.get("kind") and known["kind"] != "other":
                stay["kind"] = known["kind"]
            stay["photos"] = list(dict.fromkeys(stay["photos"] + known.get("photos", [])))
        # Where the source gives no distance but gives coordinates, measure from the centre of the place.
        if stay.get("center_km") is None and center and stay.get("lat") is not None:
            stay["center_km"] = round(distance_km(center["lat"], center["lon"], stay["lat"], stay["lon"]), 2)
            stay["center_km_measured"] = "straight line from the centre of the place"

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
    if min_bedrooms is not None:
        if type(min_bedrooms) is not int or min_bedrooms < 1:
            raise ValueError("min_bedrooms must be an integer >= 1")

        def roomy(card):
            count = card["stay"].get("bedrooms")
            return None if count is None else count >= min_bedrooms

        cards = hidden.apply(cards, "min_bedrooms", min_bedrooms, roomy)
    if no_hostels:
        cards = hidden.apply(cards, "no_hostels", True, lambda c: not c["stay"].get("hostel"))
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
        # A price whose taxes the source does not state may end above the ceiling.
        untaxed = sum(1 for card in cards if not card["rates"][0].get("all_in"))
        if untaxed:
            hidden.by[-1]["kept_without_stated_taxes"] = untaxed

    if max_center_km is not None:
        if not isinstance(max_center_km, (int, float)) or isinstance(max_center_km, bool) or max_center_km <= 0:
            raise ValueError("max_center_km must be a positive number")

        def near(card):
            km = card["stay"].get("center_km")
            return True if km is None else km <= max_center_km

        cards = hidden.apply(cards, "max_center_km", max_center_km, near)
        # A stay whose place no source states is kept: the bar is there to drop the next town, not the unknown.
        unplaced = sum(1 for card in cards if card["stay"].get("center_km") is None)
        if unplaced:
            hidden.by[-1]["kept_without_distance"] = unplaced
    if free_cancellation is not None:
        if free_cancellation is not True:
            raise ValueError("free_cancellation can only be true: stays that state free cancellation")

        def cancellable(card):
            rates = [rate for rate in card["rates"] if rate.get("free_cancellation") is True]
            if rates:
                card["rates"] = rates
                return True
            return None  # a card that does not say so may still allow it

        cards = hidden.apply(cards, "free_cancellation", True, cancellable)
    if must_have:
        wanted = sorted(
            {str(item).strip().lower() for item in ([must_have] if isinstance(must_have, str) else must_have)}
        )
        if not all(wanted):
            raise ValueError("must_have cannot hold an empty name")

        def equipped(card):
            stay = card["stay"]
            found = all(has_amenity(stay["amenities"], item) for item in wanted)
            # What a card lists is there. What it does not list is unknown until `stay_details` reads the page:
            # a search card carries a few amenities at most.
            return True if found else (False if stay.get("details_read") else None)

        cards = hidden.apply(cards, "must_have", wanted, equipped)
    if sort == "rating":
        cards.sort(key=lambda c: (c["stay"]["rating"] is None, -(c["stay"]["rating"] or 0)))
    elif sort == "reviews":
        cards.sort(key=lambda c: -(c["stay"]["reviews"] or 0))
    elif sort == "center":
        cards.sort(key=lambda c: (c["stay"].get("center_km") is None, c["stay"].get("center_km") or 0))
    result["cards"] = cards
    if hidden.by:
        result["filtered"] = hidden.report()
    shortlist(result, limit)
    result["shown"]["sorted_by"] = sort
    result["report"] = brief(result.get("sources", []))
    return result
