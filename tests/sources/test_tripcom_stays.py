import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from travelops.core.stays import StayQuery
from travelops.net.cache import RawCache
from travelops.sources.base import NotConfigured, ParseError
from travelops.sources.stays import tripcom
from travelops.sources.stays.tripcom import Source

FIXTURES = Path(__file__).parents[1] / "fixtures" / "tripcom"
PAGE = (FIXTURES / "shanghai-hotels-2026-12-01.html").read_bytes()
KEYWORDS = (FIXTURES / "keywords-okura.json").read_bytes()
NOW = datetime(2026, 10, 8, tzinfo=timezone.utc)
QUERY = StayQuery("Shanghai, China", date(2026, 12, 1), date(2026, 12, 30), adults=1)


def test_the_list_inside_the_page_is_read_with_its_total_and_taxes():
    parsed = Source().parse([PAGE], QUERY, NOW)
    assert [o.stay.name for o in parsed.offers] == [
        "Orange Hotel (Shanghai Bund South Zhongshan Road)",
        "Dayin International Youth Hostel(East Nanjing Road & The Bund)",
    ]
    hotel, hostel = parsed.offers
    assert (hotel.rate.total.amount, hotel.rate.charges.amount) == (Decimal("1777.14"), Decimal("106.61"))
    assert hotel.rate.all_in().amount == Decimal("1883.75"), "what Trip.com says the stay costs in all"
    assert hotel.stay.kind == "hotel" and hotel.stay.rating == 9.6 and hotel.stay.reviews == 4925
    assert (hotel.stay.lat, hotel.stay.lon) == (31.21470220009834, 121.4956524570794), "WGS84, not China's GCJ-02"
    assert hotel.rate.free_cancellation is True and hotel.rate.room.startswith("Deluxe Double Bed Room")
    assert hotel.rate.link.url.startswith(tripcom.DETAIL) and "hotelId=83740608" in hotel.rate.link.url
    assert hostel.stay.kind == "shared_room", "priced per bed"
    assert any("1 stays without a price" in note for note in parsed.notes)


def test_a_page_without_its_list_is_not_an_empty_city():
    with pytest.raises(ParseError):
        Source().parse([b"<html>Verify you are human</html>"], QUERY, NOW)


def test_the_search_box_names_the_hotel_and_its_city():
    found = tripcom.keywords_of([b"{}", KEYWORDS])
    assert tripcom.hotel_of(found) == ("369832", 2, "Okura Garden Hotel Shanghai")
    assert tripcom.city_of(found, "Shanghai, China") is None, "a hotel is not a city"
    city = {"keyword": {}, "controlInfo": {"regionInfo": {"displayCityModel": {"countryName": "China"},
                                                          "basicCityModel": {"cityId": 2}}}}
    assert tripcom.city_of([city], "Shanghai, China") == 2
    assert tripcom.city_of([city], "Shanghai, Peru") is None


def test_the_city_is_the_one_near_the_place_or_in_its_country_by_any_of_its_names():
    def city(cid, country, lat, lon):
        return {"keyword": {"keywordContentInfo": {"coordinateItemList": [
            {"coordinateType": "NORMAL", "latitude": str(lat), "longitude": str(lon)}]}},
            "controlInfo": {"regionInfo": {"displayCityModel": {"countryName": country},
                                           "basicCityModel": {"cityId": cid}}}}

    istanbul = city(532, "Türkiye", 41.00527, 28.97696)
    assert tripcom.city_of([istanbul], "Istanbul, Turkey") == 532, "Trip.com writes Türkiye"
    namesake = city(9, "Türkiye", 41.18, 32.35)
    assert tripcom.city_of([namesake, istanbul], "Istanbul, Turkey", (41.01, 28.95)) == 532, "the one near"
    assert tripcom.city_of([namesake], "Istanbul", (41.01, 28.95)) is None


def test_the_list_url_carries_the_party_and_the_hotel():
    url = tripcom.list_url(StayQuery("Shanghai", date(2026, 12, 1), date(2026, 12, 30), adults=2, rooms=1), 2,
                           ("369832", "Okura Garden Hotel Shanghai"))
    assert "city=2" in url and "adult=2" in url and "crn=1" in url and "optionId=369832" in url


async def test_a_city_is_typed_once_and_kept(tmp_path):
    typed = []

    class Browser:
        async def search(self, source, url, pattern, typed_=None, **kwargs):
            if kwargs.get("typed"):
                typed.append(kwargs["typed"][1])
                return [json.dumps({"data": {"mainKeywordList": {"keywords": [
                    {"keyword": {}, "controlInfo": {"regionInfo": {"displayCityModel": {"countryName": "China"},
                                                                    "basicCityModel": {"cityId": 2}}}}
                ]}}}).encode()]
            assert "city=2" in url
            return [PAGE]

    ctx = SimpleNamespace(browser=Browser(), net=SimpleNamespace(cache=RawCache(None)))
    assert len(await Source().fetch(QUERY, ctx)) == 1
    await Source().fetch(QUERY, ctx)
    assert typed == ["Shanghai"], "the city's id is kept"


async def test_children_are_not_configured():
    with pytest.raises(NotConfigured):
        await Source().fetch(StayQuery("Shanghai", date(2026, 12, 1), date(2026, 12, 30), children=1), None)


def test_later_pages_come_as_json_and_a_hotel_seen_twice_is_one():
    first = tripcom.list_data(PAGE)["hotelList"]
    later = json.dumps({"data": {"hotelList": first[:1] + [dict(first[0], hotelInfo=dict(
        first[0]["hotelInfo"], summary={"hotelId": "777"}))]}}).encode()
    parsed = Source().parse([PAGE, later], QUERY, NOW)
    assert [o.stay.source_id for o in parsed.offers] == ["83740608", tripcom.list_data(PAGE)["hotelList"][1][
        "hotelInfo"]["summary"]["hotelId"], "777"]
    assert parsed.notes[0].startswith("3 stays from 2 pages")
