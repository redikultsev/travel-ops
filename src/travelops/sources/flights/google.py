"""Google Flights through its public search page, read the way the open-source `fli` library reads it (MIT, see
NOTICE): the query is a `tfs` protobuf in the URL, and the page carries its result rows inline in a `ds:1` block.
No key and no browser. Google publishes no API for this and its terms do not allow automated access; see
docs/sources/google.md.

Google is a metasearch. A price is the cheapest a seller shows there, for the whole party; which seller it is,
Google says only on its own page, so the link leads to that page for the same flights."""

from __future__ import annotations

import base64
import json
import re
from datetime import datetime
from itertools import product

from ...core.common import Link
from ...core.flights import Baggage, Fare, FlightOffer, FlightQuery, Itinerary, Segment, at_airport, flight_number
from ...core.money import Money
from ..base import Context, Parsed, ParseError

PAGE = "https://www.google.com/travel/flights"
CABINS = {"economy": 1, "premium_economy": 2, "business": 3, "first": 4}
CABIN_NAMES = {code: name for name, code in CABINS.items()}
# Return flights are found by pinning one outbound flight and asking again: one page per outbound tried.
OUTBOUNDS_TRIED = 3
# A consent already answered, as `fli` sends it: an address in the EU is otherwise shown the consent page.
CONSENT = {"SOCS": "CAISNQgQEitib3FfaWRlbnRpdHlmcm9udGVuZHVpc2VydmVyXzIwMjQwMzE3LjA5X3AwGgJlbiADGgYIgLC_rwY"}
_BLOCK = re.compile(r"AF_initDataCallback\((\{.*?\})\);", re.S)
_KEY = re.compile(r"key:\s*'([^']+)'")
_DATA = re.compile(r"data:(.*?), sideChannel", re.S)


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte, value = value & 0x7F, value >> 7
        out.append(byte | 0x80 if value else byte)
        if not value:
            return bytes(out)


def _field(number: int, payload: bytes | int) -> bytes:
    if isinstance(payload, int):
        return _varint(number << 3) + _varint(payload)
    return _varint(number << 3 | 2) + _varint(len(payload)) + payload


def _direction(origin: str, destination: str, day: str, pinned: list[dict] = ()) -> bytes:
    body = _field(2, day.encode())
    for leg in pinned:
        body += _field(
            4,
            _field(1, leg["from"].encode())
            + _field(2, leg["date"].encode())
            + _field(3, leg["to"].encode())
            + _field(5, leg["carrier"].encode())
            + _field(6, leg["number"].encode()),
        )
    body += _field(13, _field(1, 1) + _field(2, origin.encode()))
    body += _field(14, _field(1, 1) + _field(2, destination.encode()))
    return _field(3, body)


def tfs(query: FlightQuery, origin: str, destination: str, pinned: list[dict] = ()) -> str:
    """The `tfs` parameter of a search: the directions, the party and the cabin. `pinned` holds the outbound
    flights of a round trip, which makes the page list the returns that go with them."""
    directions = _direction(origin, destination, query.depart.isoformat(), pinned)
    if query.return_:
        directions += _direction(destination, origin, query.return_.isoformat())
    party = [1] * query.adults + [2] * query.children + [3] * query.infants  # 3: an infant on a lap
    message = _field(1, 28) + _field(2, 2) + directions
    for kind in party:
        message += _field(8, kind)
    message += _field(9, CABINS[query.cabin]) + _field(14, 1) + _field(19, 1 if query.return_ else 2)
    return base64.urlsafe_b64encode(message).decode().rstrip("=")


def page_url(code: str) -> str:
    return f"{PAGE}?tfs={code}&hl=en&gl=US&curr=EUR"


def payload_of(html: str):
    """The `ds:1` block of a search page: the rows of its result, or None when the page has none."""
    for block in _BLOCK.finditer(html):
        key = _KEY.search(block.group(1))
        data = _DATA.search(block.group(1))
        if key and key.group(1) == "ds:1" and data:
            try:
                return json.loads(data.group(1))
            except ValueError:
                return None
    return None


def rows_of(payload) -> list:
    if not isinstance(payload, list) or len(payload) < 4:
        raise ParseError("Google's result block is not in the shape we know")
    return [row for index in (2, 3) if isinstance(payload[index], list) for row in payload[index][0] or []]


def _read_varint(data: bytes, offset: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        byte = data[offset]
        offset, value, shift = offset + 1, value | (byte & 0x7F) << shift, shift + 7
        if not byte & 0x80:
            return value, offset


def currency_of(token: str | None) -> str | None:
    """The currency a price token names (field 3.3 of its protobuf)."""
    if not token:
        return None
    try:
        data = base64.urlsafe_b64decode(token.replace("+", "-").replace("/", "_") + "=" * (-len(token) % 4))
        offset = 0
        while offset < len(data):
            tag, offset = _read_varint(data, offset)
            wire, number = tag & 7, tag >> 3
            if wire == 0:
                _, offset = _read_varint(data, offset)
                continue
            if wire != 2:
                return None
            length, offset = _read_varint(data, offset)
            chunk, offset = data[offset : offset + length], offset + length
            if number == 3:
                inner = 0
                while inner < len(chunk):
                    tag, inner = _read_varint(chunk, inner)
                    if tag & 7 == 0:
                        _, inner = _read_varint(chunk, inner)
                        continue
                    size, inner = _read_varint(chunk, inner)
                    if tag >> 3 == 3:
                        return chunk[inner : inner + size].decode().upper()
                    inner += size
    except (IndexError, ValueError, UnicodeDecodeError):
        return None
    return None


def _moment(day, clock, airport: str) -> datetime:
    year, month, date = day
    hour, minute = (list(clock or []) + [0, 0])[:2]
    return at_airport(datetime(year, month, date, hour or 0, minute or 0), airport)


def legs_of(row: list) -> list[dict]:
    """The flights of a result row, as Google lists them."""
    flights = []
    for leg in row[0][2] or []:
        carrier = leg[22] or []
        flights.append(
            {
                "from": leg[3],
                "to": leg[6],
                "date": "-".join(f"{part:02d}" for part in leg[20]),
                "departs": _moment(leg[20], leg[8], leg[3]),
                "arrives": _moment(leg[21], leg[10], leg[6]),
                "carrier": carrier[0],
                "number": str(carrier[1]),
                "operating": carrier[2] if len(carrier) > 2 and carrier[2] else None,
                "cabin": CABIN_NAMES.get(leg[16]) if len(leg) > 16 else None,
            }
        )
    return flights


def price_of(row: list) -> Money | None:
    """The price of a row, or None where Google has not worked one out for the list."""
    block = row[1] if len(row) > 1 and isinstance(row[1], list) else None
    if not block or not isinstance(block[0], list):
        raise ValueError("a row without a price block")
    if not block[0]:
        return None
    currency = currency_of(block[1] if len(block) > 1 else None)
    if not currency:
        raise ValueError("a price without its currency")
    return Money(block[0][-1], currency)


class Source:
    name = "google"

    def max_requests(self, query: FlightQuery) -> int:
        return len(query.origins) * len(query.destinations) * (1 + (OUTBOUNDS_TRIED if query.return_ else 0))

    def typical_requests(self, query: FlightQuery) -> int:
        return self.max_requests(query)

    async def _page(self, ctx: Context, code: str):
        response = await ctx.net.request(self.name, "GET", page_url(code), cookies=CONSENT)
        if response.status != 200:
            raise ParseError(f"Google answered HTTP {response.status}")
        if "consent.google" in response.url:
            raise ParseError("Google showed its cookie consent page instead of results")
        return payload_of(response.text())

    async def fetch(self, query: FlightQuery, ctx: Context) -> list[bytes]:
        if query.cabin not in CABINS:
            raise ValueError(f"unsupported cabin: {query.cabin}")
        raws = []
        for origin, destination in product(query.origins, query.destinations):
            code = tfs(query, origin, destination)
            payload = await self._page(ctx, code)
            pages = [{"tfs": code, "payload": payload}]
            if query.return_ and payload is not None:
                # The first page lists outbound flights at the price of the cheapest round trip they are part
                # of; the returns that go with one are listed when it is pinned. The cheapest few are tried.
                priced = [row for row in rows_of(payload) if row[1] and row[1][0]]
                for row in sorted(priced, key=lambda r: r[1][0][-1])[:OUTBOUNDS_TRIED]:
                    pinned = tfs(query, origin, destination, legs_of(row))
                    pages.append({"tfs": pinned, "outbound": row, "payload": await self._page(ctx, pinned)})
            raws.append(json.dumps({"route": [origin, destination], "pages": pages}).encode())
        return raws

    def parse(self, raws: list[bytes], query: FlightQuery, seen_at: datetime) -> Parsed:
        offers, notes, unpriced, empty = [], [], 0, 0
        for raw in raws:
            try:
                pages = json.loads(raw)["pages"]
                if pages[0]["payload"] is None:
                    empty += 1
                    continue
                if query.return_:
                    found = [
                        (page["outbound"], row, page["tfs"])
                        for page in pages[1:]
                        if page["payload"]
                        for row in rows_of(page["payload"])
                    ]
                else:
                    found = [(None, row, pages[0]["tfs"]) for row in rows_of(pages[0]["payload"])]
                for outbound, row, code in found:
                    price = price_of(row)
                    if price is None:
                        unpriced += 1
                        continue
                    legs = [legs_of(outbound), legs_of(row)] if outbound else [legs_of(row)]
                    itinerary = Itinerary(
                        *(
                            tuple(
                                Segment(
                                    f["carrier"],
                                    flight_number(f["carrier"], f["number"]),
                                    f["from"],
                                    f["to"],
                                    f["departs"],
                                    f["arrives"],
                                    f["operating"],
                                )
                                for f in leg
                            )
                            for leg in legs
                        )
                    )
                    cabins = {f["cabin"] for leg in legs for f in leg}
                    fare = Fare(
                        price,
                        self.name,
                        self.name,
                        cabins.pop() if len(cabins) == 1 else None,
                        Baggage(),
                        Link(page_url(code), "results"),
                        seen_at,
                    )
                    offers.append(FlightOffer(itinerary, fare))
            except (ValueError, KeyError, TypeError, IndexError) as exc:
                raise ParseError(f"Google result rows: {exc}") from exc
        if empty:
            notes.append(f"{empty} of {len(raws)} search pages carried no results: Google may have changed the page")
        if unpriced:
            notes.append(f"{unpriced} itineraries listed without a price were left out")
        if query.return_:
            notes.append(f"returns were looked up for the {OUTBOUNDS_TRIED} cheapest outbound flights of each route")
        if offers:
            notes.append(
                "a metasearch: the price is the cheapest a seller shows on Google, which names the seller only on "
                "its own page; baggage is not stated"
            )
        return Parsed(offers, notes)
