from datetime import date, datetime, timedelta, timezone

import pytest

from travelops.core.flights import (
    Baggage,
    Fare,
    FlightQuery,
    Itinerary,
    Segment,
    UnknownAirport,
    at_airport,
    cabin,
    flight_number,
)
from travelops.core.money import Money


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Y", "economy"),
        ("ECONOMY", "economy"),
        ("econom", "economy"),
        ("economic", "economy"),
        ("E", "economy"),
        ("C", "business"),
        ("business", "business"),
        ("W", "premium_economy"),
        ("F", "first"),
        (None, None),
        ("??", None),
    ],
)
def test_cabin(raw, expected):
    assert cabin(raw) == expected


@pytest.mark.parametrize(
    "carrier,number,expected",
    [
        ("SU", "1234", "SU1234"),
        ("SU", "SU 1234", "SU1234"),
        ("su", "SU-0012", "SU12"),
        ("5N", "5N123", "5N123"),
        ("JU", 130, "JU130"),
    ],
)
def test_flight_number(carrier, number, expected):
    assert flight_number(carrier, number) == expected


def test_naive_time_is_local_to_the_airport():
    t = at_airport("2026-11-15T10:00:00", "BEG")
    assert t.utcoffset() == timedelta(hours=1)


def test_aware_time_is_converted():
    t = at_airport("2026-11-15T10:00:00+03:00", "BEG")
    assert t.hour == 8


def test_unknown_airport():
    with pytest.raises(UnknownAirport):
        at_airport("2026-11-15T10:00:00", "ZZZ")


def seg(flight, origin, dest, dep, arr):
    return Segment(
        carrier=flight[:2],
        flight=flight,
        origin=origin,
        destination=dest,
        departs=at_airport(dep, origin),
        arrives=at_airport(arr, dest),
    )


def test_itinerary_key_and_times():
    out = (
        seg("W64123", "BEG", "BGY", "2026-11-14T06:00", "2026-11-14T07:50"),
        seg("U27662", "MXP", "LIS", "2026-11-14T13:00", "2026-11-14T14:55"),
    )
    it = Itinerary(out)
    assert it.key() == (("W64123", "2026-11-14"), ("U27662", "2026-11-14"), 2)
    assert it.stops() == 1
    assert it.airport_changes() == [("BGY", "MXP")]
    assert it.duration() == timedelta(hours=9, minutes=55)


def test_same_first_flight_different_connections_are_different_trips():
    a = Itinerary((seg("SU1", "BEG", "SVO", "2026-11-15T10:00", "2026-11-15T14:00"),))
    b = Itinerary(
        (
            seg("SU1", "BEG", "SVO", "2026-11-15T10:00", "2026-11-15T14:00"),
            seg("SU2", "SVO", "LED", "2026-11-15T16:00", "2026-11-15T17:30"),
        )
    )
    assert a.key() != b.key()


def test_fare_comparable_key():
    now = datetime(2026, 10, 4, tzinfo=timezone.utc)
    with_bag = Fare(
        price=Money(100, "EUR"),
        seller="x",
        source="x",
        cabin="economy",
        baggage=Baggage(checked=1),
        link=None,
        seen_at=now,
    )
    no_bag = Fare(
        price=Money(80, "EUR"),
        seller="y",
        source="y",
        cabin="economy",
        baggage=Baggage(checked=0),
        link=None,
        seen_at=now,
    )
    unknown = Fare(
        price=Money(80, "EUR"), seller="y", source="y", cabin="economy", baggage=Baggage(), link=None, seen_at=now
    )
    assert len({with_bag.comparable_key(), no_bag.comparable_key(), unknown.comparable_key()}) == 3


def test_query_date_pairs_keep_trip_length():
    q = FlightQuery(
        origins=("BEG",), destinations=("MOW",), depart=date(2026, 11, 15), return_=date(2026, 11, 20), flex_days=1
    )
    assert q.date_pairs() == [
        (date(2026, 11, 14), date(2026, 11, 19)),
        (date(2026, 11, 15), date(2026, 11, 20)),
        (date(2026, 11, 16), date(2026, 11, 21)),
    ]


def test_mulhouse_and_basel_are_one_airport_not_a_change():
    from datetime import datetime, timezone

    from travelops.core.flights import Itinerary, Segment

    t = datetime(2026, 11, 14, 10, tzinfo=timezone.utc)
    legs = (Segment("U2", "U21", "BEG", "MLH", t, t), Segment("LX", "LX2", "BSL", "ZRH", t, t))
    assert Itinerary(legs).airport_changes() == []
    moved = (Segment("TK", "TK1", "BEG", "IST", t, t), Segment("VF", "VF2", "SAW", "AYT", t, t))
    assert Itinerary(moved).airport_changes() == [("IST", "SAW")]


def test_a_round_trip_into_one_airport_and_out_of_another_is_open_jaw():
    from datetime import datetime, timezone

    from travelops.core.flights import Itinerary, Segment

    t = datetime(2026, 11, 14, 10, tzinfo=timezone.utc)
    out, back = Segment("TK", "TK1", "BEG", "SAW", t, t), Segment("TK", "TK2", "IST", "BEG", t, t)
    assert Itinerary((out,), (back,)).open_jaw() is True
    home = Segment("TK", "TK3", "SAW", "BEG", t, t)
    assert Itinerary((out,), (home,)).open_jaw() is False
    basel = Itinerary((Segment("U2", "U21", "BEG", "MLH", t, t),), (Segment("LX", "LX2", "BSL", "BEG", t, t),))
    assert basel.open_jaw() is False, "one airport under two codes"
