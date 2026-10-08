from datetime import datetime, timezone
from decimal import Decimal

import pytest

from travelops import compare
from travelops import mcp as api
from travelops.app import build
from travelops.core.common import Link
from travelops.core.money import Money
from travelops.core.stays import Rate, Stay, StayOffer
from travelops.sources.base import ParseError
from tests.fakes import fake_searches

NOW = datetime(2026, 10, 8, tzinfo=timezone.utc)


@pytest.fixture
async def app(tmp_path):
    value = build(tmp_path, None)
    yield value
    await value.close()


def result():
    return {
        "query": {
            "place": "Shanghai, China",
            "checkin": "2026-12-01",
            "checkout": "2026-12-30",
            "adults": 1,
            "children": 0,
            "rooms": 1,
        },
        "currency": "EUR",
        "cards": [
            {
                "stay": {
                    "source": "trivago",
                    "source_id": "2965704",
                    "name": "Okura Garden Hotel Shanghai",
                    "kind": "hotel",
                    "lat": 31.2197,
                    "lon": 121.4600,
                    "rating": 9.3,
                    "reviews": 15738,
                    "photos": [],
                    "amenities": [],
                    "district": None,
                    "center_km": 1.45,
                },
                "listed_on": [
                    {
                        "source": "trivago",
                        "source_id": "2965704",
                        "name": "Okura Garden Hotel Shanghai",
                        "rating": 9.3,
                        "reviews": 15738,
                    }
                ],
                "nights": 29,
                "rates": [
                    {
                        "total": {"amount": "2858", "currency": "EUR"},
                        "converted": {"amount": "2858", "currency": "EUR"},
                        "source": "trivago",
                        "seller": "trivago:trivago Book & Go",
                        "link": {"url": "https://www.trivago.com/x", "kind": "deal"},
                    }
                ],
            }
        ],
        "sources": [],
    }


def offer(source, sid, name, total, charges=None, lat=None, lon=None):
    return StayOffer(
        Stay(source, sid, name, "hotel", lat, lon, 8.7, 842, center_km=1.9),
        Rate(
            Money(Decimal(total), "EUR"),
            source,
            source,
            Link(f"https://{source}.example/{sid}", "property"),
            NOW,
            room="Executive Room",
            charges=Money(Decimal(charges), "EUR") if charges else None,
        ),
    )


class Booking:
    asked = []

    async def lookup(self, query, name, ctx, seen_at):
        self.asked.append(name)
        return [
            offer("booking", "/hotel/cn/okura.html", "Okura Garden Hotel Shanghai", "4829", "802", 31.2196, 121.4599),
            offer("booking", "/hotel/cn/radisson.html", "Radisson Blu Hotel Shanghai New World", "4571", "759"),
        ]


class Nowhere:
    async def lookup(self, query, name, ctx, seen_at):
        return []


class Trivago:
    async def lookup(self, query, name, ctx, seen_at):
        raise AssertionError("trivago listed the stay already")


async def test_a_stay_is_looked_up_by_name_and_its_prices_join_the_card(app, monkeypatch):
    monkeypatch.setattr(compare, "STAY_SOURCES", {"booking": Booking, "trivago": Trivago, "tripcom": Nowhere})
    tools = api.Tools(app, fake_searches(app))
    stored = app.results.put("stays", "k", result())

    found = await tools.compare_stays(stored.id, ["okura"])

    (stay,) = found["stays"]
    assert Booking.asked == ["Okura Garden Hotel Shanghai"], "the place is in the name already"
    assert stay["checked"] == {"booking": "ok", "trivago": "in the search already", "tripcom": "not_found"}
    assert [entry["source"] for entry in stay["listed_on"]] == ["trivago", "booking"]
    assert stay["listed_on"][1]["matched"] == "same_name"
    assert [(r["source"], r["total"]["amount"]) for r in stay["rates"]] == [("trivago", "2858"), ("booking", "4829")]
    assert stay["rates"][1]["all_in"] == {"amount": "5631", "currency": "EUR"}

    again = await tools.refine_stays(stored.id, limit=5)
    assert [r["source"] for r in again["cards"][0]["rates"]] == ["trivago", "booking"], "the search keeps them"
    asked_before = len(Booking.asked)
    second = await tools.compare_stays(stored.id, ["Okura Garden Hotel Shanghai"])
    assert second["stays"][0]["checked"]["booking"] == "in the search already"
    assert len(Booking.asked) == asked_before


async def test_another_hotel_is_not_taken_for_the_one_asked(app, monkeypatch):
    class Elsewhere:
        async def lookup(self, query, name, ctx, seen_at):
            return [offer("booking", "/hotel/cn/express.html", "Okura Garden Express Pudong", "900", lat=31.24, lon=121.5)]

    class Broken:
        async def lookup(self, query, name, ctx, seen_at):
            raise ParseError("page changed")

    monkeypatch.setattr(compare, "STAY_SOURCES", {"booking": Elsewhere, "trivago": Broken})
    data = result()
    del data["cards"][0]["listed_on"]  # a search stored before sources were merged
    data["cards"][0]["stay"]["source"] = "airbnb"
    data["cards"][0]["rates"][0]["source"] = "airbnb"
    stored = app.results.put("stays", "k2", data)

    (stay,) = await compare.compare_stays(app, stored, ["Okura"], None)

    assert stay["checked"]["booking"].startswith("not_found: it answered with Okura Garden Express Pudong")
    assert stay["checked"]["trivago"] == "unparsed: page changed"
    assert [r["source"] for r in stay["rates"]] == ["airbnb"]


async def test_at_most_five_stays_at_once(app):
    stored = app.results.put("stays", "k", result())
    with pytest.raises(ValueError):
        await compare.compare_stays(app, stored, [str(n) for n in range(6)], None)
