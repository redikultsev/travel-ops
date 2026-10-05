from datetime import date, datetime, timezone
import pytest
from travelops.core.stays import StayQuery
from travelops.net.browser import Session
from travelops.net.client import Response
from travelops.sources.base import Context, NotConfigured
from travelops.sources.stays.booking import Source, challenge

QUERY = StayQuery("Belgrade", date(2026, 11, 14), date(2026, 11, 16))
NOW = datetime(2026, 10, 5, tzinfo=timezone.utc)


async def test_bounded_price_ranges_and_same_session():
    class Browser:
        async def get(self, *args, **kw):
            assert kw["engine"] == "chromium" and kw["ready_cookie"] == "aws-waf-token"
            return Session({"aws-waf-token": "test"}, "test-agent", "chromium", 0)

    class Net:
        calls = []

        async def request(self, *args, **kw):
            self.calls.append(kw)
            return Response(200, b"<html>searchresults</html>")

    net = Net()
    raws = await Source().fetch(QUERY, Context(net, Browser(), lambda: NOW))
    assert len(raws) == len(net.calls) == 4 and Source().max_requests(QUERY) == 5
    assert [c["params"].get("nflt") for c in net.calls] == [
        None,
        "price=EUR-0-100-1",
        "price=EUR-100-250-1",
        "price=EUR-250-2000-1",
    ]
    assert all(c["cookies"] == {"aws-waf-token": "test"} and c["impersonate"] == "chrome" for c in net.calls)


async def test_children_need_actual_ages():
    with pytest.raises(NotConfigured):
        await Source().fetch(
            StayQuery(QUERY.place, QUERY.checkin, QUERY.checkout, children=1), Context(None, None, lambda: NOW)
        )


def test_waf_is_blocked_but_scripts_on_real_pages_are_allowed():
    assert challenge(Response(202, b"Challenge"))
    assert challenge(Response(200, b"normal", {"x-amzn-waf-action": "challenge"}))
    assert challenge(Response(200, b"<html>" + b"normal " * 1000 + b"<script>challenge()</script>")) is None


def recorded():
    from pathlib import Path

    return (Path(__file__).parents[1] / "fixtures/booking/belgrade-2026-11-14.html").read_bytes()


def test_recorded_cards_total_rating_and_query_links():
    from travelops.core.money import Money

    result = Source().parse([recorded()] * 4, QUERY, NOW)
    assert len(result.offers) == 6
    first = result.offers[0]
    assert first.stay.name == 'Rooms for rent "SARA"' and first.stay.lat is None and first.stay.lon is None
    assert first.stay.rating == 9.0 and first.stay.reviews == 252
    assert len(first.stay.photos) == 1 and first.stay.photos[0].startswith("https://cf.bstatic.com/xdata/images/hotel/")
    assert first.rate.total == Money(45, "EUR") and first.rate.per_night(2) == Money("22.50", "EUR")
    assert first.rate.room == "Twin Room" and first.rate.meals is None
    assert first.rate.free_cancel_until is None
    assert first.rate.link.kind == "property"
    assert first.rate.link.url == (
        "https://www.booking.com/hotel/rs/hostel-sara-beograd1.en-gb.html"
        "?checkin=2026-11-14&checkout=2026-11-16&group_adults=2&group_children=0&no_rooms=1"
    )
    assert any("deduplicated: 6" in note for note in result.notes)
    assert result.offers[4].stay.reviews == 1521


def test_known_empty_and_invalid_html():
    from travelops.sources.base import ParseError

    assert Source().parse([b'<main data-testid="search-results">No properties found</main>'], QUERY, NOW).offers == []
    with pytest.raises(ParseError):
        Source().parse([b"<html>unrelated</html>"], QUERY, NOW)
    with pytest.raises(ParseError, match="price"):
        Source().parse([recorded().replace(b"price-and-discounted-price", b"no-price")], QUERY, NOW)


@pytest.mark.live
async def test_live_search():
    from tests.live import check_source

    offers = await check_source(Source(), QUERY)
    assert offers[0].rate.link.kind == "property"
