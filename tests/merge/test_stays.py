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
