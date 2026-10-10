from dataclasses import replace
from datetime import date, datetime, timezone
import pytest
from travelops.core.stays import StayQuery
from travelops.net.browser import Session
from travelops.net.client import Response
from travelops.sources.base import Context, NotConfigured, ParseError, SourceFault
from travelops.sources.stays.booking import Source, challenge

QUERY = StayQuery("Belgrade", date(2026, 11, 14), date(2026, 11, 16))
NOW = datetime(2026, 10, 5, tzinfo=timezone.utc)


def walk_page(prices, counted):
    """A results page Booking would send: `counted` is its "properties found", one card per nightly price."""
    cards = "".join(
        f'<div data-testid="property-card"><div data-testid="title">Stay {p}</div>'
        f'<a href="https://www.booking.com/hotel/rs/s{p}.html">x</a>'
        f'<span data-testid="price-and-discounted-price">€ {p * 2}</span></div>'
        for p in prices
    )
    return f'<html><div data-testid="search-results"><h1>Belgrade: {counted:,} properties found</h1>{cards}</div></html>'.encode()


class Browser:
    async def get(self, *args, **kw):
        assert kw["engine"] == "chromium" and kw["ready_cookie"] == "aws-waf-token"
        return Session({"aws-waf-token": "test"}, "test-agent", "chromium", 0)


async def test_the_search_walks_up_the_price_until_what_passes_is_seen():
    pages = iter([walk_page(range(10, 35), 60), walk_page(range(33, 58), 37), walk_page(range(58, 70), 12)])

    class Net:
        calls = []

        async def request(self, *args, **kw):
            self.calls.append(kw["params"])
            return Response(200, next(pages))

    query = StayQuery("Belgrade", date(2026, 11, 14), date(2026, 11, 16), min_rating=8.4)
    raws = await Source().fetch(query, Context(Net(), Browser(), lambda: NOW))
    assert len(raws) == 3 and Source().max_requests(query) == 11
    assert [c["nflt"] for c in Net.calls] == [
        "review_score=80;price=EUR-0-10000-1",
        "review_score=80;price=EUR-33-10000-1",
        "review_score=80;price=EUR-55-10000-1",
    ], "each page starts a little below the dearest night of the last; a rating of 8.4 asks Booking for 8+"
    assert all(c["order"] == "price" for c in Net.calls)
    result = Source().parse(raws, query, NOW)
    assert len(result.offers) == 60 and "deduplicated: 60 properties of 60 Booking counts for these filters" in result.notes


async def test_a_ceiling_and_a_short_list_take_one_page():
    class Net:
        calls = []

        async def request(self, *args, **kw):
            self.calls.append(kw["params"])
            return Response(200, walk_page(range(10, 20), 10))

    query = replace(QUERY, max_night_eur=60.4)
    source = Source()
    raws = await source.fetch(query, Context(Net(), Browser(), lambda: NOW))
    assert [c["nflt"] for c in Net.calls] == ["price=EUR-0-61-1"]
    coverage = source.parse(raws, query, NOW).coverage
    assert coverage.complete and (coverage.read, coverage.counted, coverage.ceiling_eur) == (10, 10, 120.8)


async def test_under_a_ceiling_every_band_is_read_whole_against_its_count():
    import re

    nights = list(range(10, 70))  # 60 properties, one a night's price each, shuffled within a page as Booking does

    class Net:
        calls = []

        async def request(self, *args, **kw):
            self.calls.append(kw["params"]["nflt"])
            low, high = map(int, re.search(r"price=EUR-(\d+)-(\d+)", kw["params"]["nflt"]).groups())
            inside = [p for p in nights if low <= p <= high]
            shown = sorted(inside)[:25]
            return Response(200, walk_page(shown[::-1], len(inside)))

    query = replace(QUERY, max_night_eur=500.0)
    source = Source()
    raws = await source.fetch(query, Context(Net(), Browser(), lambda: NOW))
    assert source.max_requests(query) == 21
    parsed = source.parse(raws, query, NOW)
    assert (parsed.coverage.read, parsed.coverage.counted) == (60, 60) and parsed.coverage.complete
    assert Net.calls[0].endswith("price=EUR-0-500-1") and len(Net.calls) <= 21
    assert Source().parse(raws, QUERY, NOW).coverage.ceiling_eur is None, "no ceiling, no claim"


async def test_a_band_that_cannot_be_read_whole_is_not_complete():
    class Net:
        calls = []

        async def request(self, *args, **kw):
            self.calls.append(kw["params"]["nflt"])
            return Response(200, walk_page(range(10, 35), 60))  # always more than a page, whatever the band

    query = replace(QUERY, max_night_eur=500.0)
    source = Source()
    raws = await source.fetch(query, Context(Net(), Browser(), lambda: NOW))
    parsed = source.parse(raws, query, NOW)
    assert len(Net.calls) == 20 and not parsed.coverage.complete
    assert any("not all of it" in n for n in parsed.notes)


async def test_children_need_actual_ages():
    with pytest.raises(NotConfigured):
        await Source().fetch(
            StayQuery(QUERY.place, QUERY.checkin, QUERY.checkout, children=1), Context(None, None, lambda: NOW)
        )


async def test_children_are_sent_with_one_age_each():
    class Browser:
        async def get(self, name, url, **kw):
            assert "group_children=2" in url and "age=3&age=9" in url
            return Session({"aws-waf-token": "test"}, "test-agent", "chromium", 0)

    class Net:
        calls = []

        async def request(self, *args, **kw):
            self.calls.append(kw["params"])
            return Response(200, walk_page(range(10, 12), 2))

    family = StayQuery(QUERY.place, QUERY.checkin, QUERY.checkout, adults=2, children=2, children_ages=(3, 9))
    await Source().fetch(family, Context(Net(), Browser(), lambda: NOW))
    assert all(call["age"] == [3, 9] and call["group_children"] == 2 for call in Net.calls)


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
    assert first.rate.charges == Money(5, "EUR") and first.rate.all_in() == Money(50, "EUR"), "taxes are on top"
    assert first.rate.free_cancellation is True and first.rate.pay_at_property is True
    assert first.stay.district == "Voždovac, Belgrade" and first.stay.center_km == 6.7
    assert {o.rate.free_cancellation for o in result.offers} == {True, None}, "silence is not a refusal"
    assert result.offers[4].stay.reviews == 1521


def test_known_empty_and_invalid_html():
    from travelops.sources.base import ParseError

    assert Source().parse([b'<main data-testid="search-results">No properties found</main>'], QUERY, NOW).offers == []
    with pytest.raises(ParseError):
        Source().parse([b"<html>unrelated</html>"], QUERY, NOW)
    with pytest.raises(ParseError, match="price"):
        Source().parse([recorded().replace(b"price-and-discounted-price", b"no-price")], QUERY, NOW)


def test_one_stray_page_loses_its_properties_not_the_search():
    home = b"<html><head><title>Booking.com | Official site</title></head><body>home</body></html>"
    result = Source().parse([recorded(), home], QUERY, NOW)
    assert len(result.offers) == 6
    assert "page 2: not a results page, its properties are missing" in result.notes
    with pytest.raises(ParseError, match="no page"):
        Source().parse([home, home], QUERY, NOW)


@pytest.mark.live
async def test_live_search():
    from tests.live import check_source

    offers = await check_source(Source(), QUERY)
    assert offers[0].rate.link.kind == "property"


async def test_a_server_fault_stops_the_walk_and_is_said():
    class Net:
        def __init__(self, statuses):
            self.statuses = iter(statuses)

        async def request(self, *args, **kw):
            status = next(self.statuses)
            return Response(status, walk_page(range(10, 35), 80) if status == 200 else b"<html>fault</html>")

    raws = await Source().fetch(QUERY, Context(Net([200, 502]), Browser(), lambda: NOW))
    result = Source().parse(raws, QUERY, NOW)
    assert len(result.offers) == 25
    assert any("HTTP 5xx" in note for note in result.notes)
    assert any(note.startswith("the 55 not seen are dearer") for note in result.notes)
    with pytest.raises(SourceFault, match="HTTP 502"):
        await Source().fetch(QUERY, Context(Net([502]), Browser(), lambda: NOW))


def test_a_property_page_gives_amenities_place_rules_and_scores():
    from pathlib import Path

    page = (Path(__file__).parents[1] / "fixtures/booking/property-kotor.html").read_bytes()
    details = Source().parse_details(page)
    assert details["amenities"][:2] == ["Free WiFi", "Air conditioning"] and "Kitchenette" in details["amenities"]
    assert (round(details["lat"], 4), round(details["lon"], 4)) == (42.4253, 18.7703)
    assert details["address"].startswith("436 Square of the Arms, Kotor Old Town")
    assert (
        details["check_in"] == "From 14:00 to 22:00"
        and "Cash only: This property only accepts cash payments." in details["rules"]
    )
    assert details["scores"]["Location"] == 9.3 and len(details["photos"]) == 4
    assert "description" not in details, "the property's own prose is not passed on"
    with pytest.raises(ParseError):
        Source().parse_details(b"<html>unrelated</html>")


async def test_one_property_is_found_through_the_search_box():
    from pathlib import Path

    page = (Path(__file__).parents[1] / "fixtures" / "booking" / "belgrade-2026-11-14.html").read_bytes()

    class Browser:
        async def get(self, *args, **kw):
            return Session({"aws-waf-token": "test"}, "test-agent", "chromium", 0)

    class Net:
        calls = []

        async def request(self, source, method, url, **kw):
            self.calls.append((method, url, kw))
            if url.endswith("autocomplete.json"):
                hits = [
                    {"dest_type": "city", "dest_id": "-74897", "label": "Belgrade, Serbia"},
                    {"dest_type": "hotel", "dest_id": "42", "label": 'Rooms for rent "SARA", Belgrade, Serbia',
                     "label1": 'Rooms for rent "SARA"', "latitude": 44.8, "longitude": 20.46},
                ]
                return Response(200, __import__("json").dumps({"results": hits}).encode())
            return Response(200, page)

    net = Net()
    offers = await Source().lookup(QUERY, 'Rooms for rent "SARA", Belgrade', Context(net, Browser(), lambda: NOW), NOW)
    (suggest, results) = net.calls
    assert suggest[2]["queue"] == "lookup" and suggest[2]["json"]["query"] == 'Rooms for rent "SARA", Belgrade'
    assert results[2]["params"]["dest_id"] == "42" and results[2]["params"]["dest_type"] == "hotel"
    assert "order" not in results[2]["params"] and "nflt" not in results[2]["params"]
    first = offers[0].stay
    assert first.name == 'Rooms for rent "SARA"' and (first.lat, first.lon) == (44.8, 20.46)
    assert all(o.stay.lat is None for o in offers if o.stay.source_id != first.source_id), "only the one named"


async def test_a_name_the_search_box_does_not_know_finds_nothing():
    class Browser:
        async def get(self, *args, **kw):
            return Session({}, "test-agent", "chromium", 0)

    class Net:
        async def request(self, source, method, url, **kw):
            return Response(200, b'{"results": [{"dest_type": "city", "dest_id": "1"}]}')

    assert await Source().lookup(QUERY, "Nowhere Inn", Context(Net(), Browser(), lambda: NOW), NOW) == []


def test_a_property_unavailable_at_the_dates_is_left_out_not_an_error():
    from pathlib import Path

    page = (Path(__file__).parents[1] / "fixtures" / "booking" / "belgrade-2026-11-14.html").read_bytes()
    sold = (
        b'<div data-testid="property-card"><div data-testid="title">Holiday Inn Express Jing\'an Temple</div>'
        b"<p>This property is unavailable on our site for your dates</p></div>"
    )
    whole = Source().parse([page], QUERY, NOW)
    parsed = Source().parse([page.replace(b"</body>", sold + b"</body>", 1)], QUERY, NOW)
    assert len(parsed.offers) == len(whole.offers) > 0
    assert "1 properties shown as unavailable at these dates left out" in parsed.notes
