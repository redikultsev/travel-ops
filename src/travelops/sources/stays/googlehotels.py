"""Google Hotels through the call its own page makes (`batchexecute`, rpc `AtySUc`), read the way the open-source
`stays` library reads it (MIT, see NOTICE). No key and no browser. Google publishes no API for this and its terms
do not allow automated access; see docs/sources/googlehotels.md.

Google is a metasearch. A search card has the cheapest price a seller shows for the stay, without naming the
seller; a hotel's own answer (`lookup`) lists every seller's price for it — Booking.com, Expedia, Agoda, the hotel
itself — which is how a price of a site that refuses us still reaches a comparison."""

from __future__ import annotations

import json
import re
from urllib.parse import quote

from ...core.common import Link
from ...core.money import Money
from ...core.stays import Rate, Stay, StayOffer, rating_out_of_10
from ..base import NotConfigured, Parsed, ParseError
from ...merge.stays import name_words, place_words
from ._html import money

# In English: without `hl` Google answers in the language it guesses, room names included, and on 2026-10-09
# showed one seller fewer for the same hotel.
ENDPOINT = "https://www.google.com/_/TravelFrontendUi/data/batchexecute?hl=en"
RPC = "AtySUc"
SORT_BY_PRICE = 3
# An answer holds about 18 hotels and has no pages. Asking once by price and once per hotel class reaches more:
# each class has its own cheapest 18.
CLASSES = (2, 3, 4, 5)
_FRAME = re.compile(r'"wrb\.fr","AtySUc","((?:\\.|[^"\\])*)"')


def at(tree, *path):
    for index in path:
        try:
            tree = tree[index]
        except (IndexError, KeyError, TypeError):
            return None
    return tree


def request(query, text: str, stars: int | None = None, entity: str | None = None) -> str:
    """The form body of one call: what `stays`' HotelSearchFilters.format() writes, for the fields used here."""
    ci, co = query.checkin, query.checkout
    dates = [None, [[ci.year, ci.month, ci.day], [co.year, co.month, co.day], query.nights], None, None, None,
             [None, len(query.children_ages)]]
    party = None
    if query.adults != 2 or query.children_ages:
        party = [[[3]] * query.adults + [bucket(age) for age in query.children_ages], 1]
    details = [None, [stars] if stars else None, None, None, SORT_BY_PRICE, None, "EUR", None]
    price = [None, [None, int(query.max_night_eur)] if query.max_night_eur else None, 1]
    record = [details, None, [], price]
    if rating := guest_rating(query.min_rating):
        record.append(rating)
    meta = [1, None, None, None, None, entity, 13, None, 0]
    inner = [text, [1, party, [None, dates], None, record], meta]
    outer = [[[RPC, json.dumps(inner, separators=(",", ":")), None, "1"]]]
    return "f.req=" + quote(json.dumps(outer, separators=(",", ":")), safe="")


def bucket(age: int) -> list[int]:
    return [2, 12] if 2 <= age <= 12 else ([0, 1] if age < 2 else [13, 17])


def guest_rating(bar: float | None) -> int | None:
    """Google's rating filter, 3.5+, 4.0+ or 4.5+ of 5, written as twice the rating: the same number as our bar out
    of 10, rounded down to one it has."""
    if bar is None:
        return None
    return next((value for value in (9, 8, 7) if bar >= value), None)


def answer(body: bytes):
    match = _FRAME.search(body.decode("utf-8", "replace"))
    if not match:
        if b"/sorry/" in body or b"unusual traffic" in body.lower():
            raise ParseError("Google answered with its unusual-traffic page")
        raise ParseError("Google's answer has no hotel frame")
    if not match[1]:
        raise ParseError("Google's hotel frame is empty: the request was not understood")
    return json.loads(json.loads(f'"{match[1]}"'))


def hotels(tree) -> list[list]:
    """Every hotel entry: a list of 20 or more with a name at [1], an id at [9] or [20] and [lat, lon] at [2][0]."""
    found = []

    def looks(node) -> bool:
        if not isinstance(node, list) or len(node) < 21 or not isinstance(node[1], str) or not node[1]:
            return False
        if not (isinstance(node[9], str) or isinstance(node[20], str)):
            return False
        spot = at(node, 2, 0)
        return isinstance(spot, list) and len(spot) == 2 and all(isinstance(c, (int, float)) for c in spot)

    def walk(node):
        if looks(node):
            found.append(node)
        elif isinstance(node, list):
            for child in node:
                walk(child)
        elif isinstance(node, dict):
            for child in node.values():
                walk(child)

    walk(tree)
    return found


def stay_of(entry) -> Stay:
    lat, lon = entry[2][0]
    review = at(entry, 7, 0) or []
    score = review[0] if review and isinstance(review[0], (int, float)) else None
    count = review[1] if len(review) > 1 and isinstance(review[1], int) else None
    photos = []

    def images(node):
        if isinstance(node, str) and node.startswith("https://"):
            photos.append(node)
        elif isinstance(node, list):
            for child in node:
                images(child)

    images(at(entry, 12))
    return Stay(
        "googlehotels",
        str(entry[20] or entry[9]),
        entry[1],
        "hotel",
        float(lat),
        float(lon),
        rating_out_of_10(score, 5) if score is not None else None,
        count,
        tuple(dict.fromkeys(photos))[:5],
    )


def stay_total(entry) -> Money | None:
    """The stay's total as the card shows it ("€101" for four nights), the cheapest any seller has."""
    block = at(entry, 6, 2)
    if not isinstance(block, list):
        return None
    for item in reversed(block):
        if isinstance(item, list) and len(item) == 1 and isinstance(item[0], str) and any(c.isdigit() for c in item[0]):
            return money(item[0])
    return None


def sellers(entry) -> list[tuple[str, Money, str | None]]:
    """Each seller of a hotel's own answer: its name, its total for the stay and the room it is for. A seller
    shows one room, not always its cheapest: Booking.com's Deluxe Double at €216 where Booking itself had a
    Superior Double at €170. Its link is an ad click through Google, long and not ours to follow: the link given
    is Google's own search for the hotel."""
    found = []
    for item in at(entry, 6, 2, 2) or []:
        header = at(item, 0)
        if not isinstance(header, list) or not isinstance(header[0], str):
            continue
        prices = next(
            (block for block in item if isinstance(block, list) and len(block) > 5 and isinstance(block[5], list)
             and isinstance(at(block, 5, 0), str)),
            None,
        )
        if prices is None:
            continue
        room = at(item, 7, 0, 0)
        found.append((header[0], money(prices[5][0]), room if isinstance(room, str) and room else None))
    return found


def page(name: str, query) -> str:
    """Google Hotels for the hotel at the dates: the search a person would make."""
    return (
        "https://www.google.com/travel/search?q=" + quote(name)
        + f"&checkin={query.checkin.isoformat()}&checkout={query.checkout.isoformat()}&adults={query.adults}&curr=EUR"
    )


class Source:
    name = "googlehotels"

    def max_requests(self, query):
        return 1 + len(CLASSES)

    def _check(self, query):
        if query.rooms != 1:
            raise NotConfigured("Google Hotels searches are for one room")
        if query.children and len(query.children_ages) != query.children:
            raise NotConfigured("Google Hotels prices a child by age: pass children_ages")

    async def _call(self, ctx, body: str) -> bytes:
        response = await ctx.net.request(
            self.name,
            "POST",
            ENDPOINT,
            data=body,
            headers={"content-type": "application/x-www-form-urlencoded;charset=UTF-8"},
        )
        if response.status >= 400:
            raise ParseError(f"Google answered HTTP {response.status}")
        return response.body

    async def fetch(self, query, ctx):
        self._check(query)
        text = f"{query.place} hotels"
        raws = [await self._call(ctx, request(query, text))]
        for stars in CLASSES:
            try:
                raws.append(await self._call(ctx, request(query, text, stars)))
            except ParseError:
                continue  # a class it will not answer for costs that class only
        return raws

    async def lookup(self, query, name, ctx, seen_at):
        """One property by its name: the search finds it, and its own answer lists every seller's price."""
        self._check(query)
        found = [e for e in hotels(answer(await self._call(ctx, request(query, name)))) if isinstance(e[20], str)]
        if not found:
            return []
        # The answer is a list of hotels near the name, not always with it first: the closest name is detailed.
        words = name_words(name, place_words(query.place))
        entry = max(found, key=lambda e: len(words & name_words(e[1])) / (len(words | name_words(e[1])) or 1))
        detail = hotels(answer(await self._call(ctx, request(query, "hotels", entity=entry[20]))))
        stay = stay_of(entry)
        link = Link(page(stay.name, query), "results")
        return [
            StayOffer(stay, Rate(total, f"googlehotels:{seller}", self.name, link, seen_at, room=room))
            for seller, total, room in (sellers(detail[0]) if detail else [])
        ]

    async def fetch_details(self, url, place, ctx):
        raise NotConfigured("Google Hotels pages are not read: compare_stays lists its sellers")

    def parse_details(self, raw):
        raise NotConfigured("Google Hotels pages are not read")

    def parse(self, raws, query, seen_at):
        offers, seen, unpriced = [], set(), 0
        try:
            for raw in raws:
                for entry in hotels(answer(raw)):
                    stay = stay_of(entry)
                    if stay.source_id in seen:
                        continue
                    seen.add(stay.source_id)
                    total = stay_total(entry)
                    if total is None:
                        unpriced += 1
                        continue
                    offers.append(StayOffer(stay, Rate(total, self.name, self.name, Link(page(stay.name, query),
                                                                                          "results"), seen_at)))
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            raise ParseError(f"Google Hotels fields: {exc}") from exc
        notes = [
            f"{len(offers)} hotels from {len(raws)} answers of about 18 (by price, then one per hotel class from 2 "
            "to 5 stars); Google gives no further pages",
            "a metasearch: the price is the cheapest a seller shows for the stay, which Google names only on its "
            "page; taxes are not stated; compare_stays lists each seller's price for a hotel",
        ]
        if unpriced:
            notes.append(f"{unpriced} hotels without a price at these dates left out")
        return Parsed(offers, notes)
