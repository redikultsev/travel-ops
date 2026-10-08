"""Trip.com stays. Its requests are signed by its own page, so none is sent from here: a browser opens the pages a
person would. The list of a city's hotels comes inside the results page itself (`initListData`), with each
hotel's cheapest room and its total with taxes. A place or a hotel is turned into Trip.com's own id by typing it
into the page's search box and reading the suggestions the page receives. Not a published API: see
docs/sources/tripcom.md."""

from __future__ import annotations

import json
import re
from urllib.parse import urlencode

from ...core.common import Link
from ...core.money import Money
from ...core.stays import Rate, Stay, StayOffer, StayQuery, kind_of_room, rating_out_of_10
from ...geo import distance_km, same_country
from ...net.cache import RawCache
from ..base import NotConfigured, Parsed, ParseError
from ._html import money

HOME = "https://www.trip.com/hotels/?locale=en-XX&curr=EUR"
LIST = "https://www.trip.com/hotels/list"
DETAIL = "https://www.trip.com/hotels/detail/"
FIELD = "input#destinationInput"
KEYWORDS = r"/restapi/soa2/\d+/getHotelKeywords"
PAGE = r"trip\.com/hotels/list\?|/restapi/soa2/\d+/fetchHotelList"
# The results page carries ten or so hotels; each scroll to its end makes the page ask for the next ones
# (`fetchHotelList`, signed by the page). SCROLLS is how far one search goes.
SCROLLS = 12
CITY_DAYS = 30
# A suggested city this far from the place's centre is another place of the same name.
CITY_KM = 60


def keywords_of(bodies: list[bytes]) -> list[dict]:
    """The suggestions for the whole text: the box asks again on every key, and the last answer is the last
    text."""
    for body in reversed(bodies):
        try:
            found = json.loads(body)["data"]["mainKeywordList"]["keywords"]
        except (ValueError, KeyError, TypeError):
            continue
        if isinstance(found, list):
            return found
    return []


def region(keyword: dict) -> dict:
    info = keyword.get("controlInfo", {}).get("regionInfo", {})
    return {**info.get("displayCityModel", {}), **info.get("basicCityModel", {})}


def spot_of(keyword: dict) -> tuple[float, float] | None:
    info = (keyword.get("keyword") or {}).get("keywordContentInfo") or {}
    for item in info.get("coordinateItemList") or []:
        if item.get("coordinateType") == "NORMAL":
            try:
                return float(item["latitude"]), float(item["longitude"])
            except (KeyError, TypeError, ValueError):
                return None
    return None


def city_of(keywords: list[dict], place: str, center: tuple[float, float] | None = None) -> int | None:
    """The first suggestion that is a city: near the place's centre when it is known, else in the country the place
    names when it names one ("Turkey" is Trip.com's "Türkiye")."""
    country = place.split(",", 1)[1].strip() if "," in place else ""
    for keyword in keywords:
        if (keyword.get("keyword") or {}).get("hotelInfo"):
            continue
        where = region(keyword)
        if not where.get("cityId"):
            continue
        spot = spot_of(keyword)
        if center and spot:
            if distance_km(*center, *spot) <= CITY_KM:
                return int(where["cityId"])
            continue
        if not country or same_country(country, str(where.get("countryName", ""))):
            return int(where["cityId"])
    return None


def hotel_of(keywords: list[dict]) -> tuple[str, int, str] | None:
    for keyword in keywords:
        hotel = ((keyword.get("keyword") or {}).get("hotelInfo") or {}).get("hotelId")
        city = region(keyword).get("cityId")
        if hotel and city:
            return str(hotel), int(city), keyword["keyword"]["keywordContentInfo"].get("keyword", "")
    return None


def list_url(query: StayQuery, city: int, hotel: tuple[str, str] | None = None) -> str:
    params = {
        "city": city,
        "checkin": query.checkin.isoformat(),
        "checkout": query.checkout.isoformat(),
        "adult": query.adults,
        "crn": query.rooms,
        "children": 0,
        "curr": "EUR",
        "locale": "en-XX",
    }
    if hotel:
        params.update(optionId=hotel[0], optionType="Hotel", optionName=hotel[1])
    return f"{LIST}?{urlencode(params)}"


def detail_url(query: StayQuery, hotel_id: str) -> str:
    return f"{DETAIL}?" + urlencode(
        {
            "hotelId": hotel_id,
            "checkIn": query.checkin.isoformat(),
            "checkOut": query.checkout.isoformat(),
            "adult": query.adults,
            "crn": query.rooms,
            "curr": "EUR",
            "locale": "en-XX",
        }
    )


def list_data(html: bytes) -> dict | None:
    """`initListData` from the page's own data, which Next.js writes as chunks of a string in script tags; or a
    later page of the list, which the page fetches as JSON in the same shape."""
    if html.lstrip().startswith(b"{"):
        try:
            data = json.loads(html).get("data")
        except ValueError:
            return None
        return data if isinstance(data, dict) and isinstance(data.get("hotelList"), list) else None
    text = html.decode("utf-8", "replace")
    chunks = []
    for match in re.finditer(r"self\.__next_f\.push\((\[.*?\])\)</script>", text, re.S):
        try:
            value = json.loads(match[1])
        except ValueError:
            continue
        if len(value) > 1 and isinstance(value[1], str):
            chunks.append(value[1])
    data = "".join(chunks)
    start = data.find('"initListData":')
    if start < 0:
        return None
    try:
        return json.JSONDecoder().raw_decode(data[start + len('"initListData":') :].lstrip())[0]
    except ValueError:
        return None


def stay_total(explained: str, layer: dict) -> Money | None:
    """What the stay costs in all. The card states it ("Total price: €284 … incl. taxes & fees"), rounded to the
    euro; the price layer has it to the cent, after the discounts its other lines leave out. The layer's is taken
    when it agrees with the card's: a stay paid at the hotel can leave the taxes out of it."""
    stated = money(explained.split("\n")[0]) if "incl. taxes" in explained else None
    content = (layer.get("total") or {}).get("content")
    exact = money(content) if content else None
    if stated is None:
        return exact
    if exact is not None and exact.currency == stated.currency and abs(exact.amount - stated.amount) <= 1:
        return exact
    return stated


def _kind(category: str, room: str, unit: str) -> str:
    if "bed" in unit.split("×")[0].lower():
        return "shared_room"
    room_kind = kind_of_room(room)
    if room_kind == "shared_room":
        return room_kind
    category = category.lower()
    if "apartment" in category:
        return "apartment"
    if any(word in category for word in ("hotel", "hostel", "inn", "resort", "motel", "guesthouse")):
        return "hotel"
    return "other"


class Source:
    name = "tripcom"

    def max_requests(self, query):
        return 2  # the typing and the page; the page's own requests as it scrolls are one visit

    async def _keywords(self, text: str, ctx) -> list[dict]:
        bodies = await ctx.browser.search(self.name, HOME, KEYWORDS, typed=(FIELD, text), queue="lookup")
        return keywords_of(bodies)

    async def _city(self, place: str, ctx, center=None) -> int:
        key = RawCache.key("CITY", "tripcom", place.casefold(), None)
        if ctx.net and (hit := ctx.net.cache.get(key, CITY_DAYS * 86400)):
            return int(hit.body)
        city = city_of(await self._keywords(place.split(",")[0].strip(), ctx), place, center)
        if city is None:
            raise ParseError(f"Trip.com suggested no city for {place!r}")
        if ctx.net:
            ctx.net.cache.put(key, self.name, HOME, 200, str(city).encode())
        return city

    def _check(self, query):
        if query.children:
            raise NotConfigured("Trip.com child prices are not verified")

    async def fetch(self, query, ctx):
        self._check(query)
        city = await self._city(query.place, ctx, query.center)
        return await ctx.browser.search(self.name, list_url(query, city), PAGE, scrolls=SCROLLS)

    async def lookup(self, query, name, ctx, seen_at):
        """One property by its name: the search box names it, and the list for that one hotel shows it first."""
        self._check(query)
        hit = hotel_of(await self._keywords(name, ctx))
        if hit is None:
            return []
        hotel, city, title = hit
        raws = await ctx.browser.search(self.name, list_url(query, city, (hotel, title)), PAGE)
        return [o for o in self.parse(raws, query, seen_at).offers if o.stay.source_id == hotel]

    async def fetch_details(self, url, place, ctx):
        raise NotConfigured("Trip.com property pages are not read yet: open the link to see amenities and rules")

    def parse_details(self, raw):
        raise NotConfigured("Trip.com property pages are not read yet")

    def parse(self, raws, query, seen_at):
        offers, sold_out, pages, seen = [], 0, 0, set()
        try:
            for raw in raws:
                data = list_data(raw)
                if data is None:
                    continue
                pages += 1
                for item in data.get("hotelList") or []:
                    hotel, rooms = item["hotelInfo"], item.get("roomInfo") or []
                    if not rooms or not (rooms[0].get("priceInfo") or {}).get("price"):
                        sold_out += 1
                        continue
                    room = rooms[0]
                    hotel_id = str(hotel["summary"]["hotelId"])
                    if hotel_id in seen:
                        continue
                    seen.add(hotel_id)
                    names = hotel.get("nameInfo") or {}
                    where = hotel.get("positionInfo") or {}
                    spot = next(
                        (c for c in where.get("mapCoordinate") or [] if c.get("gcoordType") == "WGS84"), None
                    )
                    comment = hotel.get("commentInfo") or {}
                    score = comment.get("commentScore")
                    reviews = re.sub(r"\D", "", str(comment.get("commenterNumber") or ""))
                    price = room["priceInfo"]
                    explained = str(price.get("priceExplanation") or "")
                    layer = (room.get("priceInfoLayer") or {}).get("payInfo") or {}
                    total = stay_total(explained, layer)
                    if total is None:
                        raise ParseError(f"no total for the stay of hotel {hotel_id}")
                    paid = str((layer.get("total") or {}).get("title") or "")
                    room_name = str((room.get("summary") or {}).get("physicsName") or "")
                    tags = [t.get("tagTitle", "") for t in (room.get("roomTags") or {}).get("advantageTags") or []]
                    image = str((hotel.get("hotelImages") or {}).get("url") or "")
                    offers.append(
                        StayOffer(
                            Stay(
                                self.name,
                                hotel_id,
                                str(names.get("enName") or names.get("name")),
                                _kind(
                                    str((hotel.get("hotelCategory") or {}).get("categoryName") or ""),
                                    room_name,
                                    explained.split("\n")[-1],
                                ),
                                float(spot["latitude"]) if spot else None,
                                float(spot["longitude"]) if spot else None,
                                rating_out_of_10(float(score), float(comment.get("fullRating") or 10))
                                if score
                                else None,
                                int(reviews) if reviews else None,
                                (image,) if image.startswith("https://") else (),
                                district=(where.get("zoneNames") or [None])[0],
                            ),
                            Rate(
                                total,
                                self.name,
                                self.name,
                                Link(detail_url(query, hotel_id), "property"),
                                seen_at,
                                meals="breakfast included" if any("breakfast" in t.lower() for t in tags) else None,
                                room=room_name or None,
                                charges=Money("0", total.currency),
                                free_cancellation=True if any("free cancellation" in t.lower() for t in tags) else None,
                                pay_at_property=True if paid == "Pay at Hotel" else (False if paid else None),
                            ),
                        )
                    )
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            raise ParseError(f"Trip.com hotel list fields: {exc}") from exc
        if raws and not pages:
            title = re.search(rb"<title>(.*?)</title>", raws[0], re.S)
            raise ParseError(
                f"Trip.com results page without its hotel list: {len(raws)} answers of "
                f"{', '.join(str(len(raw)) for raw in raws[:3])} bytes"
                + (f", titled {title[1][:80].decode('utf-8', 'replace')!r}" if title else "")
            )
        notes = [
            f"{len(offers)} stays from {pages} pages of the list as Trip.com ranks it (it gives no total); a search "
            f"scrolls {SCROLLS} times at most",
            "one room per stay, the one the list shows; the total Trip.com states, with its taxes and fees and after "
            "its discounts",
        ]
        if sold_out:
            notes.append(f"{sold_out} stays without a price at these dates left out")
        return Parsed(offers, notes)
