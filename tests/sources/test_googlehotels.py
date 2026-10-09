import json
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import unquote

import pytest

from travelops.core.stays import StayQuery
from travelops.net.client import Response
from travelops.sources.base import NotConfigured, ParseError
from travelops.sources.stays import googlehotels
from travelops.sources.stays.googlehotels import Source

FIXTURES = Path(__file__).parents[1] / "fixtures" / "googlehotels"
SEARCH = (FIXTURES / "istanbul-2026-11-14.txt").read_bytes()
DETAIL = (FIXTURES / "santa-sophia-detail.txt").read_bytes()
NOW = datetime(2026, 10, 8, tzinfo=timezone.utc)
QUERY = StayQuery("Istanbul, Turkey", date(2026, 11, 14), date(2026, 11, 18))


def inner(body: str) -> list:
    return json.loads(json.loads(unquote(body[len("f.req="):]))[0][0][1])


def test_a_search_card_gives_the_stays_total_rating_out_of_10_and_place():
    parsed = Source().parse([SEARCH], QUERY, NOW)
    assert len(parsed.offers) == 18
    santa = next(o for o in parsed.offers if o.stay.name == "Santa Sophia Hotel")
    assert santa.rate.total.amount == Decimal("101") and santa.rate.total.currency == "EUR", "four nights, as shown"
    assert santa.stay.rating == 7.8 and santa.stay.reviews == 1609, "3.9 of 5"
    assert round(santa.stay.lat, 3) == 41.007 and santa.rate.charges is None, "taxes are not stated"
    assert santa.rate.link.url.startswith("https://www.google.com/travel/search?q=Santa%20Sophia%20Hotel")


def test_a_hotels_own_answer_lists_each_sellers_total():
    entry = googlehotels.hotels(googlehotels.answer(DETAIL))[0]
    assert [(name, int(total.amount), room) for name, total, room in googlehotels.sellers(entry)] == [
        ("Expedia.dk", 223, None),
        ("Agoda", 101, None),
        ("TUI.com", 135, None),
        ("Vio.com", 120, None),
    ]


def test_a_seller_names_the_room_its_price_is_for():
    seller = [["Booking.com", 184, "/aclk"], None, None, None, None, None, None,
              [["Deluxe Double Room", []]], True, 1, 3, None,
              [None, None, None, None, ["€54", None, 54], ["€216", None, 216]]]
    entry = [None] * 6 + [[None, None, [None, None, [seller]]]]
    ((name, total, room),) = googlehotels.sellers(entry)
    assert (name, total.amount, room) == ("Booking.com", Decimal("216"), "Deluxe Double Room")


def test_the_request_carries_dates_party_bars_and_the_hotel_asked():
    asked = replace(QUERY, adults=1, children=1, children_ages=(7,), min_rating=8.5, max_night_eur=90.0)
    body = inner(googlehotels.request(asked, "istanbul hotels", stars=4, entity="ChcI"))
    text, params, meta = body
    assert text == "istanbul hotels" and meta[5] == "ChcI"
    assert params[1] == [[[3], [2, 12]], 1], "one adult and a child of seven"
    assert params[2][1][1] == [[2026, 11, 14], [2026, 11, 18], 4]
    details, _, _, price, rating = params[4]
    assert details[1] == [4] and details[4] == 3 and details[6] == "EUR"
    assert price == [None, [None, 90], 1] and rating == 8, "4.0 of 5 and up"
    assert googlehotels.guest_rating(6.0) is None


def test_an_answer_without_its_frame_is_not_an_empty_city():
    with pytest.raises(ParseError):
        Source().parse([b")]}'\n<html>unusual traffic</html>"], QUERY, NOW)


async def test_a_lookup_details_the_closest_name_and_returns_every_seller():
    calls = []

    class Net:
        async def request(self, source, method, url, data=None, headers=None):
            calls.append(inner(data))
            return Response(200, DETAIL if calls[-1][2][5] else SEARCH)

    ctx = type("Ctx", (), {"net": Net()})()
    offers = await Source().lookup(QUERY, "Santa Sophia Hotel", ctx, NOW)
    assert calls[1][2][5] == next(
        e[20] for e in googlehotels.hotels(googlehotels.answer(SEARCH)) if e[1] == "Santa Sophia Hotel"
    ), "the hotel of that name, not the first of the list"
    assert [o.rate.seller for o in offers][:2] == ["googlehotels:Expedia.dk", "googlehotels:Agoda"]


async def test_more_than_one_room_is_not_configured():
    with pytest.raises(NotConfigured):
        await Source().fetch(replace(QUERY, rooms=2), None)
