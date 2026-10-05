from datetime import date, datetime, timezone
from decimal import Decimal

from travelops.core.common import Link
from travelops.core.money import Money
from travelops.core.stays import Rate, StayQuery, rating_out_of_10


def test_nights():
    assert StayQuery("Belgrade", date(2026, 11, 10), date(2026, 11, 12)).nights == 2


def test_rating_scale():
    assert rating_out_of_10(4.9, scale=5) == 9.8
    assert rating_out_of_10(None, scale=5) is None


def test_per_night():
    r = Rate(
        total=Money(240, "EUR"),
        seller="booking",
        source="booking",
        link=Link("https://x", "property"),
        seen_at=datetime(2026, 10, 4, tzinfo=timezone.utc),
    )
    assert r.per_night(2) == Money(Decimal("120.00"), "EUR")
