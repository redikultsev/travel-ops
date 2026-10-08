from datetime import datetime, timezone

from travelops.core.common import Link
from travelops.core.money import Money, Rates
from travelops.core.stays import Rate, Stay, StayOffer
from travelops.merge.stays import list_stays

NOW = datetime(2026, 10, 4, tzinfo=timezone.utc)


def offer(sid, price, source="booking"):
    stay = Stay(source, sid, f"Hotel {sid}", "hotel", 44.8, 20.4, 8.5, 100)
    return StayOffer(stay, Rate(Money(price, "EUR"), source, source, Link("https://x", "property"), NOW))


def test_rooms_of_one_stay_are_one_card_sorted_cheapest_first():
    cards = list_stays([offer("1", 200), offer("1", 150), offer("2", 100)], Rates("EUR", {}, "d"), "EUR")
    assert [c.stay.source_id for c in cards] == ["2", "1"]
    assert [r.total.amount for r in cards[1].rates] == [150, 200]


def listing(source, sid, name, price, lat=None, lon=None, center_km=None):
    stay = Stay(source, sid, name, "hotel", lat, lon, 9.0, 100, center_km=center_km)
    rate = Rate(Money(price, "EUR"), f"{source}:seller", source, Link(f"https://{source}/{sid}", "property"), NOW)
    return StayOffer(stay, rate)


def merged(*offers, place="Shanghai, China"):
    return list_stays(list(offers), Rates("EUR", {}, "d"), "EUR", place)


def test_one_hotel_from_two_sources_is_one_card_with_both_prices():
    cards = merged(
        listing("booking", "b1", "Swissôtel Grand Shanghai", 2800, center_km=2.1),
        listing("trivago", "t1", "Swissotel Grand", 2700, 31.22, 121.44, center_km=2.3),
    )
    assert len(cards) == 1
    card = cards[0]
    assert [r.source for r in card.rates] == ["trivago", "booking"]
    assert card.stay.source == "booking" and (card.stay.lat, card.stay.lon) == (31.22, 121.44)
    assert [s.source for s in card.listed] == ["booking", "trivago"]


def test_apostrophes_and_the_word_hotel_do_not_tell_hotels_apart():
    cards = merged(
        listing("booking", "b1", "The Longemont Hotel Shanghai", 2400),
        listing("trivago", "t1", "The Longemont Shanghai", 2332),
        listing("tripcom", "c1", "Pullman Shanghai Jing'an", 2600),
        listing("trivago", "t2", "Pullman Shanghai Jingan", 2700),
    )
    assert sorted(len(c.listed) for c in cards) == [2, 2]


def test_a_brand_with_one_more_word_is_another_hotel():
    cards = merged(
        listing("booking", "b1", "Holiday Inn Jingan", 2400),
        listing("trivago", "t1", "Holiday Inn Express Jingan", 1900),
    )
    assert len(cards) == 2


def test_one_name_far_apart_is_two_hotels():
    near = merged(
        listing("booking", "b1", "Ibis Centre", 900, 31.20, 121.40),
        listing("trivago", "t1", "Ibis Centre", 950, 31.30, 121.50),
    )
    assert len(near) == 2
    by_centre = merged(
        listing("booking", "b1", "Ibis Centre", 900, center_km=1.0),
        listing("trivago", "t1", "Ibis Centre", 950, center_km=9.0),
    )
    assert len(by_centre) == 2


def test_two_listings_of_one_source_are_never_merged():
    assert len(merged(listing("booking", "b1", "Okura Garden", 1), listing("booking", "b2", "Okura Garden", 2))) == 2


def test_a_name_that_is_only_the_place_matches_nothing():
    assert len(merged(listing("booking", "b1", "Shanghai Hotel", 1), listing("trivago", "t1", "Hotel Shanghai", 2))) == 2
